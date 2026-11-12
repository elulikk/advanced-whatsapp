"""
Advanced WhatsApp - Descarga y transcripción de multimedia en segundo plano
=================================================================
Los archivos del historial no se bajan al sincronizar (son demasiados). Se piden cuando el chat
se abre, y las notas de voz se transcriben a demanda. Todo corre en un hilo, sin bloquear la UI.
"""

import json
import os
import itertools
import queue
import shutil
import threading
import time
from typing import Dict, Optional, Tuple

from PySide6.QtCore import QObject, QStandardPaths, Signal

from app import db
from app.ai.transcription import transcribe_audio, has_transcription_key
from app.config import get_pref
from app.gateway_manager import GatewayManager


class MediaSignals(QObject):
    media_ready = Signal(str)        # message_id
    transcript_ready = Signal(str)   # message_id
    job_failed = Signal(str)         # message_id


media_signals = MediaSignals()

# Cola con prioridad: lo que pide el usuario (0) pasa antes que la transcripción automática (1)
_jobs: "queue.PriorityQueue" = queue.PriorityQueue()
_seq = itertools.count()
AUTO_BACKLOG_DAYS = 3        # al conectar se procesan los audios de los últimos días...
AUTO_BACKLOG_LIMIT = 20      # ...hasta esta cantidad, de a uno
_lock = threading.Lock()
_pending: set = set()                # (kind, message_id) en cola o en curso
_errors: Dict[Tuple[str, str], str] = {}   # (kind, message_id) -> último error
_worker: Optional[threading.Thread] = None
_save_copy: set = set()              # message_id de descargas pedidas con el botón: además van a la carpeta Descargas


def _ensure_worker():
    global _worker
    with _lock:
        if _worker is None or not _worker.is_alive():
            _worker = threading.Thread(target=_run, name="advancedwhatsapp-media", daemon=True)
            _worker.start()


def _enqueue(kind: str, msg: dict, auto: bool = False):
    key = (kind, msg["id"])
    with _lock:
        if key in _pending:
            return
        # Un archivo que falló no se reintenta en cada refresco de la pantalla
        if kind == "download" and key in _errors:
            return
        _pending.add(key)
    _ensure_worker()
    _jobs.put((1 if auto else 0, next(_seq), kind, dict(msg), auto))


_WRAPPER_KEYS = ("viewOnceMessage", "viewOnceMessageV2", "viewOnceMessageV2Extension",
                 "ephemeralMessage", "documentWithCaptionMessage")
_unavailable_cache: Dict[str, Optional[str]] = {}


def _inner_media(message: dict) -> Optional[dict]:
    for key, value in (message or {}).items():
        if key in _WRAPPER_KEYS and isinstance(value, dict):
            found = _inner_media(value.get("message") or {})
            if found is not None:
                if key.startswith("viewOnce"):
                    found = {**found, "viewOnce": True}
                return found
        elif key.endswith("Message") and key != "messageContextInfo" and isinstance(value, dict):
            if "mimetype" in value or "fileSha256" in value:
                return value
    return None


def unavailable_reason(msg: dict) -> Optional[str]:
    """Por qué el archivo nunca se podrá bajar: 'view_once' (visualización única, WhatsApp no la entrega
    a dispositivos vinculados) o 'no_key' (el mensaje llegó sin clave de descarga). None si se puede intentar."""
    if msg.get("media_path") and os.path.exists(msg["media_path"]):
        return None
    raw = msg.get("media_raw")
    if not raw:
        return None
    msg_id = msg.get("id")
    if msg_id in _unavailable_cache:
        return _unavailable_cache[msg_id]
    reason = None
    try:
        inner = _inner_media(json.loads(raw).get("message") or {})
        if inner is not None and not inner.get("mediaKey"):
            reason = "view_once" if inner.get("viewOnce") else "no_key"
    except (ValueError, AttributeError):
        pass
    _unavailable_cache[msg_id] = reason
    return reason


def downloads_dir() -> str:
    """Carpeta Descargas del usuario, con una subcarpeta propia de la app."""
    base = QStandardPaths.writableLocation(QStandardPaths.DownloadLocation) or os.path.join(os.path.expanduser("~"), "Downloads")
    return os.path.join(base, "AdvancedWhatsapp")


def save_to_downloads(path: str) -> Optional[str]:
    """Copia un archivo descargado a Descargas\AdvancedWhatsapp sin pisar otro. Devuelve la ruta o None; nunca lanza."""
    try:
        folder = downloads_dir()
        os.makedirs(folder, exist_ok=True)
        name, ext = os.path.splitext(os.path.basename(path))
        dest, n = os.path.join(folder, name + ext), 1
        while os.path.exists(dest):
            if os.path.getsize(dest) == os.path.getsize(path):
                return dest   # ya estaba copiada
            n += 1
            dest = os.path.join(folder, f"{name} ({n}){ext}")
        shutil.copyfile(path, dest)
        return dest
    except Exception as e:
        print(f"[Advanced WhatsApp Media] no se pudo copiar a Descargas: {e}")
        return None


def request_media(msg: dict, save_copy: bool = False):
    """Pide el archivo de un mensaje si todavía no está en disco y se puede descargar.
    Con save_copy (botón «descargar») una imagen también se copia a la carpeta Descargas."""
    if msg.get("media_path") and os.path.exists(msg["media_path"]):
        return
    if not msg.get("media_raw") or unavailable_reason(msg):
        return
    if save_copy and msg.get("media_type") == "image":
        with _lock:
            _save_copy.add(msg["id"])
    _enqueue("download", msg)


def request_transcript(msg: dict):
    """Transcribe una nota de voz (la descarga primero si hace falta)."""
    if msg.get("transcript"):
        return
    if not msg.get("media_path") and not msg.get("media_raw"):
        return
    if unavailable_reason(msg):
        return
    _enqueue("transcribe", msg)


def auto_transcribe_enabled() -> bool:
    return get_pref("auto_transcribe")


def auto_transcribe(msg: dict) -> bool:
    """Encola la transcripción automática de un audio (recibido o enviado). No hace nada si el ajuste está
    apagado, si no hay API key, si ya tiene texto, si falló antes o si ya está en cola. True si se encoló."""
    if msg.get("media_type") != "audio" or not msg.get("id") or msg.get("transcript"):
        return False
    if not auto_transcribe_enabled() or not has_transcription_key():
        return False
    if not msg.get("media_path") and not msg.get("media_raw"):
        return False
    if unavailable_reason(msg):
        return False
    msg_id = msg["id"]
    with _lock:
        if ("transcribe", msg_id) in _pending or ("transcribe", msg_id) in _errors:
            return False
    if db.is_message_transcript_failed(msg_id):
        return False
    _enqueue("transcribe", msg, auto=True)
    return True


def auto_transcribe_backlog(now: Optional[int] = None) -> int:
    """Encola los audios recientes que quedaron sin transcribir (sin los que ya fallaron). Devuelve cuántos."""
    if not auto_transcribe_enabled() or not has_transcription_key():
        return 0
    since = int(now if now is not None else time.time()) - AUTO_BACKLOG_DAYS * 86400
    count = 0
    for row in db.get_audios_pending_transcript(since, AUTO_BACKLOG_LIMIT):
        if auto_transcribe(row):
            count += 1
    return count


def is_pending(kind: str, msg_id: str) -> bool:
    with _lock:
        return (kind, msg_id) in _pending


def get_error(kind: str, msg_id: str) -> Optional[str]:
    with _lock:
        return _errors.get((kind, msg_id))


def _download(msg: dict) -> str:
    result = GatewayManager.get_instance().download_media(msg["id"], msg.get("media_type", ""), msg["media_raw"])
    if not result.get("success") or not result.get("media_path"):
        raise RuntimeError(result.get("error") or "No se pudo descargar el archivo")
    db.set_message_media_path(msg["id"], result["media_path"])
    return result["media_path"]


def _run():
    while True:
        _prio, _n, kind, msg, auto = _jobs.get()
        msg_id = msg["id"]
        try:
            if kind == "download":
                got = _download(msg)
                with _lock:
                    wants_copy = msg_id in _save_copy
                    _save_copy.discard(msg_id)
                if wants_copy:
                    save_to_downloads(got)
                with _lock:
                    _errors.pop((kind, msg_id), None)
                media_signals.media_ready.emit(msg_id)
            else:
                path = msg.get("media_path")
                if not path or not os.path.exists(path):
                    path = _download(msg)
                text = transcribe_audio(path)
                db.set_message_transcript(msg_id, text or "(sin voz detectable)")
                with _lock:
                    _errors.pop((kind, msg_id), None)
                media_signals.transcript_ready.emit(msg_id)
        except Exception as e:
            print(f"[Advanced WhatsApp Media] {kind} {msg_id} falló: {e}")
            with _lock:
                _errors[(kind, msg_id)] = str(e)
            if auto and kind == "transcribe":
                try:
                    db.set_message_transcript_failed(msg_id)   # no se reintenta sola, ni al reabrir la app
                except Exception:
                    pass
            media_signals.job_failed.emit(msg_id)
        finally:
            with _lock:
                _pending.discard((kind, msg_id))
