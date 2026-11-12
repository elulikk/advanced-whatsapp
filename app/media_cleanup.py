"""
Advanced WhatsApp - Uso y limpieza de la multimedia bajada
=============================================================
Muestra cuánto ocupa lo que se bajó (por chat y por tipo) y libera espacio borrando los archivos más viejos:
por antigüedad (más de X días) o hasta quedar por debajo de X GB. Solo se borra lo que se puede volver a bajar
(el mensaje conserva `media_raw`): al abrir el mensaje el archivo se baja de nuevo bajo demanda. Lo que enviaste
vos (carpetas enviados/, mensajes_programados/, estados_programados/) nunca se toca.
"""

import os
import time
from typing import Any, Dict, List, Optional

from app import config
from app.config import MEDIA_DIR
from app.db import db_conn

# Carpetas con archivos que no se pueden recuperar de WhatsApp: jamás se limpian
PROTECTED_DIRS = ("enviados", "mensajes_programados", "estados_programados", "avatars", "estados_contactos")
TYPE_LABELS = {"image": "Fotos", "video": "Videos", "audio": "Audios", "document": "Documentos",
               "sticker": "Stickers", "other": "Otros"}
AUTO_INTERVAL_SECONDS = 24 * 3600


def ensure_indexes(conn=None) -> None:
    """Índice parcial: solo los mensajes con archivo (una fracción de la tabla)."""
    sql = "CREATE INDEX IF NOT EXISTS idx_messages_media ON messages(timestamp) WHERE media_path IS NOT NULL"
    if conn is not None:
        conn.execute(sql)
        return
    with db_conn() as c:
        c.execute(sql)


def _is_protected(path: str, media_dir: str) -> bool:
    try:
        rel = os.path.relpath(os.path.abspath(path), os.path.abspath(media_dir))
    except ValueError:
        return True
    if rel.startswith(".."):
        return True   # fuera de la carpeta de multimedia: no es nuestro para borrar
    return rel.split(os.sep, 1)[0] in PROTECTED_DIRS


def _entries(media_dir: str = MEDIA_DIR) -> List[Dict[str, Any]]:
    """Una fila por mensaje con archivo que existe en disco."""
    with db_conn() as conn:
        ensure_indexes(conn)
        rows = conn.execute(
            "SELECT m.id, m.chat_jid, m.media_type, m.media_path, m.timestamp, "
            "(m.media_raw IS NOT NULL AND m.media_raw != '') AS redownloadable, "
            "COALESCE(NULLIF(c.custom_name, ''), NULLIF(c.name, ''), NULLIF(c.phone, ''), m.chat_jid) AS chat_name "
            "FROM messages m LEFT JOIN chats c ON c.jid = m.chat_jid "
            "WHERE m.media_path IS NOT NULL AND m.media_path != ''").fetchall()
    out = []
    for r in rows:
        path = r["media_path"]
        try:
            size = os.path.getsize(path)
        except OSError:
            continue   # el archivo ya no está
        mtype = r["media_type"] if r["media_type"] in TYPE_LABELS else "other"
        out.append({"id": r["id"], "jid": r["chat_jid"], "name": r["chat_name"], "type": mtype, "path": path,
                    "size": size, "timestamp": int(r["timestamp"] or 0),
                    "removable": bool(r["redownloadable"]) and not _is_protected(path, media_dir)})
    return out


def usage(media_dir: str = MEDIA_DIR) -> Dict[str, Any]:
    """{total_bytes, total_files, removable_bytes, by_type: {tipo: {bytes, count}}, by_chat: [{jid, name, bytes, count}]}"""
    entries = _entries(media_dir)
    by_type: Dict[str, Dict[str, int]] = {}
    by_chat: Dict[str, Dict[str, Any]] = {}
    total = removable = 0
    for e in entries:
        total += e["size"]
        if e["removable"]:
            removable += e["size"]
        t = by_type.setdefault(e["type"], {"bytes": 0, "count": 0})
        t["bytes"] += e["size"]
        t["count"] += 1
        c = by_chat.setdefault(e["jid"], {"jid": e["jid"], "name": e["name"], "bytes": 0, "count": 0})
        c["bytes"] += e["size"]
        c["count"] += 1
    return {"total_bytes": total, "total_files": len(entries), "removable_bytes": removable,
            "by_type": by_type, "by_chat": sorted(by_chat.values(), key=lambda c: c["bytes"], reverse=True)}


def plan(older_than_days: int = 0, max_gb: float = 0.0, now: Optional[int] = None,
         media_dir: str = MEDIA_DIR, entries: Optional[List[Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
    """Qué archivos se borrarían. Con `older_than_days` > 0: los de mensajes más viejos que eso. Con `max_gb` > 0:
    además, los más viejos que haga falta hasta que lo bajado entre en ese tope. Sin ninguno de los dos, nada."""
    if older_than_days <= 0 and max_gb <= 0:
        return []
    now = int(now if now is not None else time.time())
    entries = entries if entries is not None else _entries(media_dir)
    entries = sorted(entries, key=lambda e: e["timestamp"])           # los más viejos primero
    chosen: Dict[str, Dict[str, Any]] = {}
    if older_than_days > 0:
        cutoff = now - int(older_than_days) * 86400
        for e in entries:
            if e["removable"] and e["timestamp"] < cutoff:
                chosen[e["id"]] = e
    if max_gb > 0:
        limit = int(max_gb * 1024 ** 3)
        total = sum(e["size"] for e in entries) - sum(e["size"] for e in chosen.values())
        for e in entries:
            if total <= limit:
                break
            if e["removable"] and e["id"] not in chosen:
                chosen[e["id"]] = e
                total -= e["size"]
    return sorted(chosen.values(), key=lambda e: e["timestamp"])


def apply(candidates: List[Dict[str, Any]]) -> Dict[str, int]:
    """Borra los archivos y deja el mensaje sin ruta (se vuelve a bajar al abrirlo). {files, bytes, errors}"""
    freed = files = errors = 0
    done_ids = []
    for e in candidates:
        try:
            os.remove(e["path"])
        except FileNotFoundError:
            pass
        except OSError:
            errors += 1
            continue
        freed += e["size"]
        files += 1
        done_ids.append(e["id"])
    if done_ids:
        with db_conn() as conn:
            for i in range(0, len(done_ids), 500):
                chunk = done_ids[i:i + 500]
                conn.execute(f"UPDATE messages SET media_path = NULL WHERE id IN ({','.join('?' * len(chunk))})", chunk)
    return {"files": files, "bytes": freed, "errors": errors}


def cleanup(older_than_days: int = 0, max_gb: float = 0.0, now: Optional[int] = None,
            media_dir: str = MEDIA_DIR) -> Dict[str, int]:
    return apply(plan(older_than_days, max_gb, now, media_dir))


# ── Limpieza automática ──────────────────────────────────────────────────────
def get_auto_settings() -> Dict[str, float]:
    def num(key, cast):
        try:
            return cast(float(config.get_setting(key, "0") or 0))
        except (TypeError, ValueError):
            return cast(0)
    return {"days": max(0, num("media_auto_days", int)), "gb": max(0.0, num("media_auto_gb", float))}


def set_auto_settings(days: int, gb: float) -> None:
    config.set_setting("media_auto_days", max(0, int(days)))
    config.set_setting("media_auto_gb", max(0.0, float(gb)))


def run_auto_if_due(now: Optional[int] = None) -> Optional[Dict[str, int]]:
    """Corre la limpieza programada si está activada y pasó más de un día desde la última. Para un hilo aparte."""
    now = int(now if now is not None else time.time())
    s = get_auto_settings()
    if s["days"] <= 0 and s["gb"] <= 0:
        return None
    try:
        last = int(float(config.get_setting("media_auto_last", "0") or 0))
    except ValueError:
        last = 0
    if now - last < AUTO_INTERVAL_SECONDS:
        return None
    config.set_setting("media_auto_last", now)
    return cleanup(int(s["days"]), float(s["gb"]), now)


def format_size(n: float) -> str:
    n = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"
