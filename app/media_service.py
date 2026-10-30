"""
MAGSAPP - Descarga y transcripción de multimedia en segundo plano
=================================================================
Los archivos del historial no se bajan al sincronizar (son demasiados). Se piden cuando el chat
se abre, y las notas de voz se transcriben a demanda. Todo corre en un hilo, sin bloquear la UI.
"""

import json
import os
import queue
import threading
from typing import Dict, Optional, Tuple

from PySide6.QtCore import QObject, Signal

from app import db
from app.ai.transcription import transcribe_audio
from app.gateway_manager import GatewayManager


class MediaSignals(QObject):
    media_ready = Signal(str)        # message_id
    transcript_ready = Signal(str)   # message_id
    job_failed = Signal(str)         # message_id


media_signals = MediaSignals()

_jobs: "queue.Queue[Tuple[str, dict]]" = queue.Queue()
_lock = threading.Lock()
_pending: set = set()                # (kind, message_id) en cola o en curso
_errors: Dict[Tuple[str, str], str] = {}   # (kind, message_id) -> último error
_worker: Optional[threading.Thread] = None


def _ensure_worker():
    global _worker
    with _lock:
        if _worker is None or not _worker.is_alive():
            _worker = threading.Thread(target=_run, name="magsapp-media", daemon=True)
            _worker.start()


def _enqueue(kind: str, msg: dict):
    key = (kind, msg["id"])
    with _lock:
        if key in _pending:
            return
        # Un archivo que falló no se reintenta en cada refresco de la pantalla
        if kind == "download" and key in _errors:
            return
        _pending.add(key)
    _ensure_worker()
    _jobs.put((kind, dict(msg)))


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


def request_media(msg: dict):
    """Pide el archivo de un mensaje si todavía no está en disco y se puede descargar."""
    if msg.get("media_path") and os.path.exists(msg["media_path"]):
        return
    if not msg.get("media_raw") or unavailable_reason(msg):
        return
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
        kind, msg = _jobs.get()
        msg_id = msg["id"]
        try:
            if kind == "download":
                _download(msg)
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
            print(f"[MAGSAPP Media] {kind} {msg_id} falló: {e}")
            with _lock:
                _errors[(kind, msg_id)] = str(e)
            media_signals.job_failed.emit(msg_id)
        finally:
            with _lock:
                _pending.discard((kind, msg_id))
