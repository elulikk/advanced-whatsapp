"""
Advanced WhatsApp - Grabación de notas de voz
===================================
Graba el micrófono con Qt (PCM 16 bits, 48 kHz, mono) y lo convierte con FFmpeg a OGG/Opus,
el formato que WhatsApp exige para las notas de voz (el FFmpeg que trae Qt no codifica Opus).
También calcula la duración y la onda que verá el destinatario, y dibuja el medidor en vivo.
"""

import array
import glob
import os
import shutil
import subprocess
import sys
from collections import deque
from typing import List, Optional

from PySide6.QtCore import QObject, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtMultimedia import QAudioFormat, QAudioSource, QMediaDevices
try:   # Qt 6.7+: los estados y errores de audio son QtAudio (comparar con QAudio da siempre distinto)
    from PySide6.QtMultimedia import QtAudio
except ImportError:
    from PySide6.QtMultimedia import QAudio as QtAudio
from PySide6.QtWidgets import QWidget

from app.config import MEDIA_DIR
from app.ui.theme import C

SAMPLE_RATE = 48000
BYTES_PER_SECOND = SAMPLE_RATE * 2
MIN_SECONDS = 1.0          # más corto que esto se descarta, como en WhatsApp
MAX_SECONDS = 15 * 60
WAVEFORM_POINTS = 64       # puntos de la onda que WhatsApp dibuja en la burbuja del destinatario
VOICE_DIR = os.path.join(MEDIA_DIR, "enviados")

MIC_PRIVACY_HINT = ("Revisa en Windows: Configuración → Privacidad y seguridad → Micrófono, "
                    "que esté permitido el acceso para las aplicaciones de escritorio.")


def find_ffmpeg() -> Optional[str]:
    """FFmpeg del PATH o de las ubicaciones habituales en Windows (winget, C:\\ffmpeg)."""
    from app.config import RUNTIME_DIR
    bundled = os.path.join(RUNTIME_DIR, "ffmpeg.exe")   # versión portable
    if os.path.isfile(bundled):
        return bundled
    found = shutil.which("ffmpeg")
    if found:
        return found
    local = os.environ.get("LOCALAPPDATA", "")
    candidates = [os.path.join(local, "Microsoft", "WinGet", "Links", "ffmpeg.exe")]
    candidates += glob.glob(os.path.join(local, "Microsoft", "WinGet", "Packages", "*FFmpeg*", "*", "bin", "ffmpeg.exe"))
    candidates += [os.path.join(local, "AdvancedWhatsapp-Tools", "ffmpeg", "bin", "ffmpeg.exe"),  # INSTALAR_ADVANCEDWHATSAPP.ps1
                   r"C:\ffmpeg\bin\ffmpeg.exe",
                   os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"), "ffmpeg", "bin", "ffmpeg.exe")]
    return next((c for c in candidates if os.path.isfile(c)), None)


def encode_ogg_opus(pcm: bytes, dest: str) -> Optional[str]:
    """Convierte PCM s16le mono 48 kHz a OGG/Opus en `dest`. Devuelve un mensaje de error o None."""
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        return ("Para enviar notas de voz hace falta FFmpeg.\n"
                "Instálalo con:  winget install Gyan.FFmpeg\ny vuelve a abrir Advanced WhatsApp.")
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    src = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
           "-f", "s16le", "-ar", str(SAMPLE_RATE), "-ac", "1", "-i", "pipe:0"]
    out = ["-ac", "1", "-ar", "48000", "-b:a", "32k", "-f", "ogg", dest]
    err = ""
    # libopus es el codificador bueno; el nativo de FFmpeg queda de respaldo
    for codec in (["-c:a", "libopus", "-application", "voip"], ["-c:a", "opus", "-strict", "-2"]):
        try:
            r = subprocess.run(src + codec + out, input=pcm, capture_output=True, timeout=120, creationflags=flags)
        except Exception as e:
            return f"No se pudo ejecutar FFmpeg: {e}"
        if r.returncode == 0 and os.path.isfile(dest) and os.path.getsize(dest) > 0:
            return None
        err = r.stderr.decode("utf-8", "replace").strip()
    return f"FFmpeg no pudo convertir el audio:\n{err[-300:]}"


def _samples(pcm: bytes) -> "array.array":
    return array.array("h", pcm[: len(pcm) // 2 * 2])


def peak_level(pcm: bytes) -> float:
    s = _samples(pcm)
    return max(max(s), -min(s)) / 32768.0 if s else 0.0


def whatsapp_waveform(pcm: bytes, points: int = WAVEFORM_POINTS) -> List[int]:
    """Onda como la genera WhatsApp: promedio del volumen por tramo, normalizado a 0..100."""
    s = _samples(pcm)
    if not s:
        return []
    block = max(1, len(s) // points)
    stride = max(1, block // 2000)   # con ~2000 muestras por tramo alcanza y no tarda en notas largas
    levels = []
    for i in range(points):
        chunk = s[i * block:(i + 1) * block:stride]
        levels.append(sum(abs(v) for v in chunk) / len(chunk) if chunk else 0.0)
    top = max(levels) or 1.0
    return [int(100 * v / top) for v in levels]


class VoiceRecorder(QObject):
    """Graba del micrófono predeterminado a memoria. Una sola grabación a la vez."""
    level = Signal(float)   # pico 0..1 del último tramo, para el medidor en vivo
    failed = Signal(str)    # el micrófono se cortó o dejó de estar disponible

    def __init__(self, parent=None):
        super().__init__(parent)
        self._source: Optional[QAudioSource] = None
        self._io = None
        self._pcm = bytearray()
        self._paused = False
        # Se consulta el estado en vez de usar stateChanged: PySide 6.10 no puede convertir su enum QAudio::State
        self._watchdog = QTimer(self)
        self._watchdog.setInterval(500)
        self._watchdog.timeout.connect(self._check_source)

    @property
    def active(self) -> bool:
        return self._source is not None

    @property
    def paused(self) -> bool:
        return self._paused

    def seconds(self) -> float:
        return len(self._pcm) / BYTES_PER_SECOND

    def start(self) -> Optional[str]:
        """Empieza a grabar. Devuelve un mensaje de error o None."""
        self.cancel()
        device = QMediaDevices.defaultAudioInput()
        if device.isNull():
            return "No se encontró ningún micrófono conectado."
        fmt = QAudioFormat()
        fmt.setSampleRate(SAMPLE_RATE)
        fmt.setChannelCount(1)
        fmt.setSampleFormat(QAudioFormat.Int16)
        if not device.isFormatSupported(fmt):
            return f"El micrófono «{device.description()}» no permite grabar en 48 kHz / 16 bits."
        self._pcm = bytearray()
        self._paused = False
        self._source = QAudioSource(device, fmt, self)
        self._io = self._source.start()
        if self._io is None or self._source.error() != QtAudio.Error.NoError:
            self._release()
            return f"No se pudo abrir el micrófono.\n{MIC_PRIVACY_HINT}"
        self._io.readyRead.connect(self._on_ready)
        self._watchdog.start()
        return None

    def pause(self):
        if self._source and not self._paused:
            self._on_ready()
            self._paused = True
            self._source.suspend()

    def resume(self):
        if self._source and self._paused:
            self._paused = False
            self._source.resume()

    def stop(self) -> bytes:
        """Termina la grabación y devuelve el PCM grabado."""
        if self._source and not self._paused:
            self._on_ready()
        pcm = bytes(self._pcm)
        self._release()
        return pcm

    def cancel(self):
        self._release()

    def _release(self):
        self._watchdog.stop()
        src, self._source, self._io = self._source, None, None
        self._pcm = bytearray()
        self._paused = False
        if src:
            src.stop()
            src.deleteLater()

    def _on_ready(self):
        if not self._io:
            return
        data = bytes(self._io.readAll())
        if not data or self._paused:
            return
        self._pcm += data
        self.level.emit(min(1.0, peak_level(data)))

    def _check_source(self):
        """Detecta si el micrófono se cortó (desconectado o bloqueado por Windows) en plena grabación."""
        src = self._source
        if src and src.state() == QtAudio.State.StoppedState and src.error() != QtAudio.Error.NoError:
            self._release()
            self.failed.emit(f"Se perdió el acceso al micrófono.\n{MIC_PRIVACY_HINT}")


class LevelMeter(QWidget):
    """Barras del volumen reciente: la más nueva entra por la derecha, como en WhatsApp Desktop."""
    BAR_W, GAP = 3, 2

    def __init__(self, parent=None):
        super().__init__(parent)
        self._levels = deque(maxlen=200)
        self.setMinimumWidth(60)
        self.setFixedHeight(26)

    def clear(self):
        self._levels.clear()
        self.update()

    def push(self, level: float):
        self._levels.append(level)
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(C["text_2"]))
        step = self.BAR_W + self.GAP
        n = min(len(self._levels), self.width() // step)
        cy = self.height() / 2
        recent = list(self._levels)[-n:] if n else []
        for i, lv in enumerate(reversed(recent)):
            x = self.width() - (i + 1) * step
            h = max(3.0, min(1.0, lv ** 0.6) * (self.height() - 2))
            p.drawRoundedRect(QRectF(x, cy - h / 2, self.BAR_W, h), 1.5, 1.5)
        p.end()
