"""
MAGSAPP - Reproductor interno de notas de voz
=============================================
Reproduce los audios dentro de la app (sin abrir el reproductor del sistema) y dibuja la onda
de cada nota como una imagen: botón play/pausa, espectro con el avance y el tiempo.
El chat es un QTextBrowser (HTML), así que cada nota es una imagen que se vuelve a dibujar
mientras suena; no hay widgets incrustados.
"""

import array
import os
from typing import Dict, List, Optional, Tuple

from PySide6.QtCore import QObject, QPointF, QRectF, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPainterPath
from PySide6.QtMultimedia import QAudioDecoder, QAudioFormat, QAudioOutput, QMediaPlayer

# Geometría de la imagen (px lógicos). La zona clicable de la onda va de WAVE_X0 a WAVE_X1.
IMG_W, IMG_H = 364, 46
BAR_COUNT = 44
BAR_W, BAR_GAP = 3, 3
BTN_SIZE = 34
WAVE_X0 = 46
WAVE_X1 = WAVE_X0 + BAR_COUNT * (BAR_W + BAR_GAP) - BAR_GAP
# Botón de velocidad (a la derecha de la onda): un clic pasa a la siguiente velocidad
CHIP_X0, CHIP_X1 = WAVE_X1 + 8, WAVE_X1 + 52

SPEEDS = [1.0, 1.5, 2.0, 3.0, 10.0, 50.0]   # 10x y 50x son de broma, pero funcionan


def speed_label(rate: float) -> str:
    return f"{rate:g}x"

DECODE_RATE = 8000   # Hz; de sobra para dibujar la onda

_PALETTE = {
    # from_me: (barras sin reproducir, barras reproducidas, texto/botón)
    True: ("#6f9f95", "#d9f2ec", "#9fc4bb"),
    False: ("#586a74", "#c9d4da", "#8696a0"),
}
_DOT = "#53bdeb"


def format_clock(ms: int) -> str:
    s = max(0, int(round(ms / 1000)))
    return f"{s // 60}:{s % 60:02d}"


def resource_url(msg_id: str) -> QUrl:
    return QUrl(f"voice://{msg_id}.png")


def render_voice_image(peaks: Optional[List[float]], progress: float, playing: bool,
                       clock_text: str, from_me: bool, dpr: float = 2.0, speed: float = 1.0) -> QImage:
    """Dibuja la nota de voz: ▶/⏸ + onda con punto de avance + tiempo."""
    off_color, on_color, text_color = _PALETTE[from_me]
    img = QImage(int(IMG_W * dpr), int(IMG_H * dpr), QImage.Format_ARGB32_Premultiplied)
    img.setDevicePixelRatio(dpr)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)

    # Botón play / pausa
    cy = 17.0
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(text_color))
    if playing:
        p.drawRoundedRect(QRectF(8, cy - 8, 5, 16), 1.5, 1.5)
        p.drawRoundedRect(QRectF(19, cy - 8, 5, 16), 1.5, 1.5)
    else:
        tri = QPainterPath()
        tri.moveTo(9, cy - 10)
        tri.lineTo(9, cy + 10)
        tri.lineTo(26, cy)
        tri.closeSubpath()
        p.drawPath(tri)

    # Onda
    n = BAR_COUNT
    levels = peaks if peaks and len(peaks) == n else [0.12] * n
    played_upto = progress * n
    p.setBrush(QColor(off_color))
    for i, lv in enumerate(levels):
        h = max(3.0, lv * 26.0)
        x = WAVE_X0 + i * (BAR_W + BAR_GAP)
        color = QColor(on_color if i + 0.5 <= played_upto else off_color)
        p.setBrush(color)
        p.drawRoundedRect(QRectF(x, cy - h / 2, BAR_W, h), 1.5, 1.5)

    # Punto de avance (visible si ya empezó la reproducción)
    if playing or progress > 0:
        dot_x = WAVE_X0 + progress * (WAVE_X1 - WAVE_X0)
        p.setBrush(QColor(_DOT))
        p.drawEllipse(QPointF(dot_x, cy), 5.5, 5.5)

    # Tiempo
    p.setPen(QColor(text_color))
    f = QFont("Segoe UI")
    f.setPixelSize(11)
    p.setFont(f)
    p.drawText(QRectF(WAVE_X0 - 4, 33, 80, 13), Qt.AlignLeft | Qt.AlignVCenter, clock_text)

    # Botón de velocidad: pastilla resaltada cuando no es 1x
    fast = speed != 1.0
    chip = QRectF(CHIP_X0, cy - 10, CHIP_X1 - CHIP_X0, 20)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(_DOT if fast else off_color))
    p.drawRoundedRect(chip, 10, 10)
    cf = QFont("Segoe UI")
    cf.setPixelSize(11)
    cf.setBold(True)
    p.setFont(cf)
    p.setPen(QColor("#0b141a" if fast else on_color))
    p.drawText(chip, Qt.AlignCenter, speed_label(speed))
    p.end()
    return img


def downsample_peaks(samples: "array.array", count: int = BAR_COUNT) -> List[float]:
    """Reduce las muestras a `count` niveles 0..1 (pico por tramo, con curva suave y normalizado)."""
    total = len(samples)
    if total == 0:
        return [0.0] * count
    levels = []
    for i in range(count):
        a, b = i * total // count, max((i + 1) * total // count, i * total // count + 1)
        chunk = samples[a:b]
        levels.append(max(max(chunk), -min(chunk)) / 32768.0 if chunk else 0.0)
    top = max(levels) or 1.0
    return [min(1.0, (v / top) ** 0.7) for v in levels]


class VoicePlayer(QObject):
    """Un único reproductor para toda la app: al darle play a otra nota, la anterior se detiene."""
    state_changed = Signal(str)   # msg_id: cambió play/pausa/fin
    progress_changed = Signal(str)  # msg_id: avanzó la posición
    peaks_ready = Signal(str)     # msg_id: ya hay onda y duración
    speed_changed = Signal()      # cambió la velocidad (vale para todas las notas)

    _instance: Optional["VoicePlayer"] = None

    @classmethod
    def instance(cls) -> "VoicePlayer":
        if cls._instance is None:
            cls._instance = VoicePlayer()
        return cls._instance

    def __init__(self):
        super().__init__()
        self._player = QMediaPlayer(self)
        self._output = QAudioOutput(self)
        self._player.setAudioOutput(self._output)
        self._player.playbackStateChanged.connect(self._on_state)
        self._player.mediaStatusChanged.connect(self._on_status)
        self._player.errorOccurred.connect(self._on_error)

        self._tick = QTimer(self)
        self._tick.setInterval(60)
        self._tick.timeout.connect(self._on_tick)

        self.current_id: Optional[str] = None
        self._current_path: Optional[str] = None
        self._pending_seek: Optional[float] = None
        self._resume_after_seek = False
        self.speed = 1.0
        self._cache: Dict[str, Tuple[List[float], int]] = {}   # path -> (peaks, duración ms)
        self._decode_queue: List[Tuple[str, str]] = []         # (msg_id, path)
        self._decoding: Optional[Tuple[str, str]] = None
        self._decoder: Optional[QAudioDecoder] = None
        self._samples = array.array("h")

    # ── Datos para dibujar ────────────────────────────────────────────────────
    def info(self, path: str) -> Optional[Tuple[List[float], int]]:
        return self._cache.get(path)

    def request_peaks(self, msg_id: str, path: str):
        if path in self._cache or (msg_id, path) in self._decode_queue or self._decoding == (msg_id, path):
            return
        self._decode_queue.append((msg_id, path))
        self._decode_next()

    def is_playing(self, msg_id: str) -> bool:
        return self.current_id == msg_id and self._player.playbackState() == QMediaPlayer.PlayingState

    def progress(self, msg_id: str) -> float:
        if self.current_id != msg_id:
            return 0.0
        dur = self._player.duration() or (self._cache.get(self._current_path or "", ([], 0))[1])
        return min(1.0, self._player.position() / dur) if dur > 0 else 0.0

    def position_ms(self, msg_id: str) -> int:
        return self._player.position() if self.current_id == msg_id else 0

    # ── Control ──────────────────────────────────────────────────────────────
    def toggle(self, msg_id: str, path: str):
        if self.current_id == msg_id:
            if self._player.playbackState() == QMediaPlayer.PlayingState:
                self._player.pause()
            else:
                self._player.play()
            return
        self._start(msg_id, path, 0.0)

    def seek(self, msg_id: str, path: str, fraction: float):
        """Salta a una posición (0..1) y reproduce desde ahí."""
        fraction = min(1.0, max(0.0, fraction))
        if self.current_id != msg_id:
            self._start(msg_id, path, fraction)
            return
        dur = self._player.duration() or self._cache.get(path, ([], 0))[1]
        self._player.setPosition(int(dur * fraction))
        if self._player.playbackState() != QMediaPlayer.PlayingState:
            self._player.play()
        self.progress_changed.emit(msg_id)

    def stop(self):
        self._player.stop()

    def cycle_speed(self):
        """Pasa a la siguiente velocidad (1x → 1.5x → 2x → 3x → 10x → 50x → 1x)."""
        i = SPEEDS.index(self.speed) if self.speed in SPEEDS else 0
        self.set_speed(SPEEDS[(i + 1) % len(SPEEDS)])

    def set_speed(self, rate: float):
        self.speed = rate
        self._player.setPlaybackRate(rate)
        self.speed_changed.emit()

    def _start(self, msg_id: str, path: str, fraction: float):
        previous = self.current_id
        self._player.stop()
        self.current_id = msg_id
        self._current_path = path
        self._pending_seek = fraction if fraction > 0 else None
        self._player.setSource(QUrl.fromLocalFile(os.path.abspath(path)))
        self._player.setPlaybackRate(self.speed)
        self._player.play()
        if previous and previous != msg_id:
            self.state_changed.emit(previous)
        self.state_changed.emit(msg_id)

    # ── Eventos del reproductor ──────────────────────────────────────────────
    def _on_state(self, state):
        if state == QMediaPlayer.PlayingState:
            self._tick.start()
        else:
            self._tick.stop()
        if self.current_id:
            self.state_changed.emit(self.current_id)

    def _on_status(self, status):
        if status in (QMediaPlayer.LoadedMedia, QMediaPlayer.BufferedMedia) and self._pending_seek is not None:
            dur = self._player.duration()
            if dur > 0:
                self._player.setPosition(int(dur * self._pending_seek))
            self._pending_seek = None
        elif status == QMediaPlayer.EndOfMedia and self.current_id:
            mid = self.current_id
            self._player.stop()
            self._player.setPosition(0)
            self.progress_changed.emit(mid)
            self.state_changed.emit(mid)

    def _on_error(self, error, message=""):
        print(f"[MAGSAPP Audio] error de reproducción: {message}")
        if self.current_id:
            self.state_changed.emit(self.current_id)

    def _on_tick(self):
        if self.current_id:
            self.progress_changed.emit(self.current_id)

    # ── Decodificación de la onda ────────────────────────────────────────────
    def _decode_next(self):
        if self._decoding or not self._decode_queue:
            return
        msg_id, path = self._decode_queue.pop(0)
        if path in self._cache or not os.path.exists(path):
            self._decode_next()
            return
        self._decoding = (msg_id, path)
        self._samples = array.array("h")
        fmt = QAudioFormat()
        fmt.setSampleRate(DECODE_RATE)
        fmt.setChannelCount(1)
        fmt.setSampleFormat(QAudioFormat.Int16)
        dec = QAudioDecoder(self)
        dec.setAudioFormat(fmt)
        dec.setSource(QUrl.fromLocalFile(os.path.abspath(path)))
        dec.bufferReady.connect(self._on_buffer)
        dec.finished.connect(self._on_decoded)
        dec.error.connect(self._on_decode_error)
        self._decoder = dec
        dec.start()

    def _on_buffer(self):
        buf = self._decoder.read() if self._decoder else None
        if buf is not None and buf.isValid():
            self._samples.frombytes(bytes(buf.constData())[: buf.byteCount()])

    def _on_decoded(self):
        job, self._decoding = self._decoding, None
        self._release_decoder()
        if job:
            msg_id, path = job
            duration_ms = int(len(self._samples) * 1000 / DECODE_RATE)
            self._cache[path] = (downsample_peaks(self._samples), duration_ms)
            self.peaks_ready.emit(msg_id)
        self._decode_next()

    def _on_decode_error(self, *args):
        job, self._decoding = self._decoding, None
        self._release_decoder()
        if job:
            print(f"[MAGSAPP Audio] no se pudo leer la onda de {job[1]}")
            self._cache[job[1]] = ([], 0)   # evita reintentar en cada repintado
        self._decode_next()

    def _release_decoder(self):
        if self._decoder:
            self._decoder.deleteLater()
            self._decoder = None


voice_player = VoicePlayer.instance
