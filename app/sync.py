"""Sincronización PC <-> Android por el chat «Mi número» (sin servidores propios) y envío de archivos al celular.

Canal: cada app, cuando tiene cambios propios, manda a su chat «Mi número» **un documento** `awsync-<dispositivo>-<ts>.awsync`
(contenedor cifrado de `app/awcrypto.py` con las secciones sincronizables de `app/backup.py`). La otra app lo reconoce por el
nombre, lo baja, lo descifra con la **clave de sincronización** (la misma en las dos), mezcla ítem por ítem (gana el último
cambio) y no lo muestra como mensaje. Cada envío borra el anterior para todos: es una foto completa, solo importa la última.
"""
import json
import mimetypes
import os
import tempfile
import threading
import time
from typing import Callable, Dict, List, Optional

from app import awcrypto, backup, secret_store
from app.awcrypto import BackupError
from app.config import DATA_DIR, MEDIA_DIR
from app.db import SYNC_DOC_PREFIX, is_sync_doc, save_message

SECRET_NAME = "sync"
META_PATH = os.path.join(DATA_DIR, "sync_meta.json")
MAX_FILES_BYTES = 20 * 1024 * 1024    # tope de stickers nuevos por documento; el resto va en el próximo
_busy = threading.Lock()


# ── Ajustes ──

def enabled() -> bool:
    return backup.get_setting("SYNC", "enabled", "false").lower() == "true" and bool(secret_store.get(SECRET_NAME))


def set_enabled(on: bool) -> None:
    backup.set_setting("SYNC", "enabled", "true" if on else "false")


def set_password(password: str) -> None:
    secret_store.set(SECRET_NAME, password or "")


def has_password() -> bool:
    return bool(secret_store.get(SECRET_NAME))


def _meta() -> dict:
    try:
        with open(META_PATH, "r", encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_meta(meta: dict) -> None:
    tmp = META_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(meta, f)
    os.replace(tmp, META_PATH)


def last_sync_info() -> dict:
    m = _meta()
    return {"sent_at": m.get("sent_at", 0), "received_at": m.get("received_at", 0)}


def own_jid(gateway) -> Optional[str]:
    """JID del chat «Mi número» (el teléfono vinculado), o None si no hay sesión."""
    st = gateway.get_status()
    phone = "".join(ch for ch in str(st.get("phone") or "") if ch.isdigit())
    if st.get("status") != "connected" or not phone:
        return None
    return f"{phone}@s.whatsapp.net"


def is_own_chat(gateway, chat_jid: str) -> bool:
    st = gateway.get_status()
    phone = "".join(ch for ch in str(st.get("phone") or "") if ch.isdigit())
    return bool(phone) and (chat_jid or "").split("@")[0].split(":")[0] == phone


def _current_digest() -> str:
    return backup.digest({s.name: backup.local_items(s) for s in backup.get_sections(sync_only=True, include_secret=False)})


# ── Enviar ──

def send_now(gateway, full: bool = False) -> dict:
    """Manda a «Mi número» las novedades (o todo, con `full`). {sent: bool, reason?, error?}. Bloqueante: usar en un hilo."""
    password = secret_store.get(SECRET_NAME)
    if not password:
        return {"sent": False, "error": "Falta la clave de sincronización."}
    if not _busy.acquire(blocking=False):
        return {"sent": False, "reason": "busy"}
    tmpdir = tempfile.mkdtemp(prefix="awsync_")
    try:
        jid = own_jid(gateway)
        if not jid:
            return {"sent": False, "reason": "offline"}
        meta = _meta()
        sent_files = set(meta.get("sent_files", []))

        def only_new(sec, items):
            return [k for k, it in items.items() if not it.get("d") and (full or f"{sec.name}:{k}" not in sent_files)]

        zpath = os.path.join(tmpdir, "s.zip")
        info = backup.build_zip(zpath, "sync", backup.get_sections(sync_only=True, include_secret=False),
                                file_filter=only_new, max_files_bytes=MAX_FILES_BYTES)
        dig = backup.digest(info["items"])
        if not full and dig == meta.get("last_digest"):
            return {"sent": False, "reason": "nothing"}
        name = f"{SYNC_DOC_PREFIX}{backup.device_id()[:8]}-{int(time.time())}.awsync"
        out = os.path.join(tmpdir, name)
        with open(zpath, "rb") as src, open(out, "wb") as dst:
            awcrypto.encrypt_stream(src, dst, password)
        res = gateway.send_message(to=jid, text="", document_path=out, document_name=name)
        if not res.get("success"):
            return {"sent": False, "error": str(res.get("error", "No se pudo enviar"))}
        old = meta.get("last_msg_id")
        for sec_name, keys in info["files_sent"].items():
            sent_files.update(f"{sec_name}:{k}" for k in keys)
        meta.update(last_digest=dig, last_msg_id=res.get("message_id"), sent_at=int(time.time()),
                    sent_files=sorted(sent_files))
        _save_meta(meta)
        if old:
            try:
                gateway.delete_message_for_all(jid, old)   # la foto anterior ya no sirve: que no se junte basura en el chat
            except Exception:
                pass
        return {"sent": True}
    except BackupError as e:
        return {"sent": False, "error": str(e)}
    finally:
        _busy.release()
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)


def tick(gateway) -> Optional[dict]:
    """Llamada periódica: manda lo nuevo si la sincronización está activa y hay conexión."""
    if not enabled():
        return None
    return send_now(gateway, full=False)


# ── Recibir ──

def handle_incoming(gateway, msg: dict) -> dict:
    """Procesa un documento de sincronización llegado por el webhook. {applied: n} o {ignored: motivo} o {error}."""
    password = secret_store.get(SECRET_NAME)
    if not is_sync_doc(msg.get("media_type"), msg.get("text")):
        return {"ignored": "no es de sincronización"}
    if not password or backup.get_setting("SYNC", "enabled", "false").lower() != "true":
        return {"ignored": "sincronización desactivada"}
    if not is_own_chat(gateway, msg.get("chat_jid") or ""):
        return {"ignored": "no es el chat propio"}   # un documento llamado así que mandó otra persona no se toca
    raw = msg.get("media_raw")
    if not raw:
        return {"ignored": "sin datos de descarga"}
    tmpdir = tempfile.mkdtemp(prefix="awsync_")
    try:
        res = gateway.download_media(msg.get("message_id"), "document", raw)
        path = res.get("media_path") if isinstance(res, dict) else None
        if not path or not os.path.exists(path):
            return {"error": "No se pudo bajar el documento de sincronización."}
        zpath = backup.decrypt_to_zip(path, password, tmpdir)
        with backup.read_zip(zpath) as zf:
            manifest = backup._manifest_of(zf)
            if manifest.get("kind") != "sync":
                return {"ignored": "no es de sincronización"}
            if (manifest.get("device") or {}).get("id") == backup.device_id():
                return {"ignored": "es de este mismo equipo"}
            report = backup.apply_document(zf, only_sync=True, include_secrets=False)
        applied = sum(r.get("applied", 0) for r in report.values() if isinstance(r, dict))
        local_wins = sum(r.get("local_wins", 0) for r in report.values() if isinstance(r, dict))
        meta = _meta()
        meta["received_at"] = int(time.time())
        if local_wins == 0:
            # El otro lado ya tiene todo lo mío: no hace falta contestar (evita el ida y vuelta infinito)
            meta["last_digest"] = _current_digest()
        _save_meta(meta)
        try:
            os.remove(path)
        except OSError:
            pass
        return {"applied": applied, "report": report}
    except BackupError as e:
        return {"error": str(e)}
    finally:
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)


# ── Enviar archivos al celular ──

def send_files_to_phone(gateway, paths: List[str], progress: Optional[Callable[[int, int, str, bool, str], None]] = None) -> Dict:
    """Manda archivos como documentos a «Mi número» (en el celular aparecen con «Guardar en Descargas»).
    progress(i, total, nombre, ok, error). Devuelve {sent, failed}."""
    jid = own_jid(gateway)
    if not jid:
        return {"sent": 0, "failed": len(paths), "error": "WhatsApp no está conectado."}
    sent = failed = 0
    for i, path in enumerate(paths):
        name = os.path.basename(path)
        ok, err = False, ""
        if not os.path.isfile(path):
            err = "No se encontró el archivo."
        else:
            res = gateway.send_message(to=jid, text="", document_path=path, document_name=name)
            ok = bool(res.get("success"))
            err = "" if ok else str(res.get("error", "No se pudo enviar"))
            if ok:
                save_message(msg_id=res.get("message_id") or f"phone_{time.time_ns()}", chat_jid=jid, sender_jid="me",
                             sender_name="Yo", from_me=True, text=name, timestamp=int(time.time()),
                             media_type="document", media_path=path, status="sent")
        sent += ok
        failed += not ok
        if progress:
            progress(i + 1, len(paths), name, ok, err)
    return {"sent": sent, "failed": failed}
