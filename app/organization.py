"""Organización y privacidad de la lista de chats.

- **Carpetas inteligentes** (`smart_folders`): carpetas definidas por reglas que se suman a las pestañas fijas
  (Todos / No leídos / Grupos). Una regla es un dict:
      {"tags": [ids], "kind": "any"|"individual"|"group", "unread": bool, "muted": "any"|"only"|"none", "jids": [jid, ...]}
  Un chat entra si cumple TODAS las condiciones que estén puestas, o si es uno de los contactos elegidos (`jids`).
  Una carpeta sin ninguna regla no muestra nada.
- **Archivado automático**: los chats sin actividad hace X días (apagado por defecto) pasan a Archivados (`chats.is_archived`);
  vuelven solos cuando llega un mensaje, salvo que estén silenciados (`unarchive_on_message`, la llama `db.save_message`).
- **Lectura invisible por chat**: chats donde no se mandan tildes azules aunque se lean (`chat_silent_read`).
- **Bloqueo de la app entera**: ajustes y lógica de cuándo corresponde pedir la contraseña (se reutiliza la de `chat_lock`).
- **Galería**: consultas paginadas de fotos/videos, documentos, links y audios de un chat.
"""
import configparser
import json
import re
import time
from typing import Any, Dict, List, Optional, Set, Tuple

from app.db import db_conn, INDIVIDUAL_CHAT_SQL, GROUP_CHAT_SQL, VISIBLE_CHAT_SQL

KIND_LABELS = {"any": "Individuales y grupos", "individual": "Solo individuales", "group": "Solo grupos"}
MUTED_LABELS = {"any": "Silenciados o no", "only": "Solo silenciados", "none": "Sin silenciar"}
DEFAULT_RULES: Dict[str, Any] = {"tags": [], "kind": "any", "unread": False, "muted": "any", "jids": []}

MUTED_SQL = "(COALESCE(muted_until, 0) = -1 OR COALESCE(muted_until, 0) > ?)"
NEVER_MATCH = "1 = 0"

_silent: Optional[Set[str]] = None


def ensure_tables(conn) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS smart_folders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL DEFAULT '',
            rules TEXT NOT NULL DEFAULT '{}',
            sort_order INTEGER DEFAULT 0,
            created_at INTEGER DEFAULT 0
        )
    """)
    conn.execute("CREATE TABLE IF NOT EXISTS chat_silent_read (chat_jid TEXT PRIMARY KEY)")
    # Los chats archivados se filtran en cada consulta de la lista
    conn.execute("CREATE INDEX IF NOT EXISTS idx_chats_archived ON chats(is_archived)")


# ── Preferencias numéricas (config.ini, sección [GENERAL]) ──

def _get_int(key: str, default: int) -> int:
    from app.config import load_app_config
    try:
        return int(load_app_config().get("GENERAL", key, fallback=str(default)))
    except (ValueError, configparser.Error):
        return default


def _set_int(key: str, value: int) -> bool:
    from app.config import load_app_config, save_app_config
    cfg = load_app_config()
    cfg.set("GENERAL", key, str(int(value)))
    return save_app_config(cfg)


def get_auto_archive_days() -> int:
    """Días sin actividad para archivar solo un chat (0 = apagado)."""
    return max(0, _get_int("auto_archive_days", 0))


def set_auto_archive_days(days: int) -> bool:
    return _set_int("auto_archive_days", max(0, int(days)))


def get_app_lock_minutes() -> int:
    """Minutos en segundo plano tras los que la app pide la contraseña de nuevo (0 = cada vez)."""
    return max(0, _get_int("app_lock_minutes", 1))


def set_app_lock_minutes(minutes: int) -> bool:
    return _set_int("app_lock_minutes", max(0, int(minutes)))


# ── Carpetas inteligentes ──

def normalize_rules(rules: Optional[dict]) -> dict:
    r = {**DEFAULT_RULES, **(rules or {})}
    r["tags"] = sorted({int(t) for t in (r.get("tags") or []) if str(t).lstrip("-").isdigit()})
    r["jids"] = [j for j in dict.fromkeys(r.get("jids") or []) if isinstance(j, str) and j]
    r["kind"] = r["kind"] if r["kind"] in KIND_LABELS else "any"
    r["muted"] = r["muted"] if r["muted"] in MUTED_LABELS else "any"
    r["unread"] = bool(r["unread"])
    return r


def rules_are_empty(rules: dict) -> bool:
    r = normalize_rules(rules)
    return not (r["tags"] or r["kind"] != "any" or r["unread"] or r["muted"] != "any" or r["jids"])


def rules_where(rules: Optional[dict], now: Optional[int] = None) -> Tuple[str, list]:
    """Condición SQL (sobre la tabla `chats`) y sus parámetros para las reglas de una carpeta."""
    r = normalize_rules(rules)
    if rules_are_empty(r):
        return NEVER_MATCH, []
    now = int(now or time.time())
    clauses: List[str] = []
    params: list = []
    if r["tags"]:
        clauses.append(f"jid IN (SELECT chat_jid FROM chat_tags WHERE tag_id IN ({','.join('?' * len(r['tags']))}))")
        params.extend(r["tags"])
    if r["kind"] == "individual":
        clauses.append(INDIVIDUAL_CHAT_SQL)
    elif r["kind"] == "group":
        clauses.append(GROUP_CHAT_SQL)
    if r["unread"]:
        clauses.append("unread_count > 0")
    if r["muted"] == "only":
        clauses.append(MUTED_SQL)
        params.append(now)
    elif r["muted"] == "none":
        clauses.append("NOT " + MUTED_SQL)
        params.append(now)
    rule_sql = ""
    if clauses:
        rule_sql = "(" + " AND ".join(clauses) + ")"
    picked_sql = ""
    picked_params: list = []
    if r["jids"]:
        picked_sql = f"jid IN ({','.join('?' * len(r['jids']))})"
        picked_params = list(r["jids"])
    if rule_sql and picked_sql:
        return f"({rule_sql} OR {picked_sql})", params + picked_params
    return rule_sql or picked_sql, params + picked_params


def describe_rules(rules: Optional[dict], tag_names: Optional[Dict[int, str]] = None) -> str:
    """Resumen corto de las reglas, para mostrar en el administrador de carpetas."""
    r = normalize_rules(rules)
    parts = []
    if r["tags"]:
        names = [(tag_names or {}).get(t, f"#{t}") for t in r["tags"]]
        parts.append("tags: " + ", ".join(names))
    if r["kind"] != "any":
        parts.append(KIND_LABELS[r["kind"]].lower())
    if r["unread"]:
        parts.append("no leídos")
    if r["muted"] != "any":
        parts.append(MUTED_LABELS[r["muted"]].lower())
    if r["jids"]:
        parts.append(f"{len(r['jids'])} contacto(s) elegido(s)")
    return " · ".join(parts) or "sin reglas"


def _folder_row(row) -> dict:
    try:
        rules = normalize_rules(json.loads(row["rules"] or "{}"))
    except (ValueError, TypeError):
        rules = normalize_rules(None)
    return {"id": row["id"], "name": row["name"], "rules": rules, "sort_order": row["sort_order"]}


def list_folders() -> List[dict]:
    with db_conn() as conn:
        rows = conn.execute("SELECT * FROM smart_folders ORDER BY sort_order, id").fetchall()
    return [_folder_row(r) for r in rows]


def get_folder(folder_id: int) -> Optional[dict]:
    return next((f for f in list_folders() if f["id"] == folder_id), None)


def create_folder(name: str, rules: Optional[dict] = None) -> int:
    name = (name or "").strip() or "Carpeta"
    with db_conn() as conn:
        order = conn.execute("SELECT COALESCE(MAX(sort_order), -1) + 1 FROM smart_folders").fetchone()[0]
        cur = conn.execute("INSERT INTO smart_folders (name, rules, sort_order, created_at) VALUES (?, ?, ?, ?)",
                           (name, json.dumps(normalize_rules(rules)), order, int(time.time())))
        return cur.lastrowid


def update_folder(folder_id: int, name: Optional[str] = None, rules: Optional[dict] = None) -> None:
    with db_conn() as conn:
        if name is not None:
            conn.execute("UPDATE smart_folders SET name = ? WHERE id = ?", ((name or "").strip() or "Carpeta", folder_id))
        if rules is not None:
            conn.execute("UPDATE smart_folders SET rules = ? WHERE id = ?", (json.dumps(normalize_rules(rules)), folder_id))


def delete_folder(folder_id: int) -> None:
    with db_conn() as conn:
        conn.execute("DELETE FROM smart_folders WHERE id = ?", (folder_id,))


def reorder_folders(ids: List[int]) -> None:
    with db_conn() as conn:
        for i, fid in enumerate(ids):
            conn.execute("UPDATE smart_folders SET sort_order = ? WHERE id = ?", (i, fid))


def folder_unread_count(folder_id: int) -> int:
    """Chats con mensajes sin leer dentro de la carpeta (para el numerito de la pestaña)."""
    folder = get_folder(folder_id)
    if not folder:
        return 0
    where, params = rules_where(folder["rules"])
    with db_conn() as conn:
        return conn.execute(
            f"SELECT COUNT(*) FROM chats WHERE {VISIBLE_CHAT_SQL} AND COALESCE(is_archived, 0) = 0 AND unread_count > 0 "
            f"AND jid NOT IN (SELECT chat_jid FROM chat_hidden) AND {where}", params).fetchone()[0]


# ── Archivados ──

def set_archived(jid: str, archived: bool) -> None:
    with db_conn() as conn:
        conn.execute("UPDATE chats SET is_archived = ? WHERE jid = ?", (1 if archived else 0, jid))


def archived_count() -> int:
    with db_conn() as conn:
        return conn.execute(
            f"SELECT COUNT(*) FROM chats WHERE is_archived = 1 AND {VISIBLE_CHAT_SQL} "
            "AND jid NOT IN (SELECT chat_jid FROM chat_hidden)").fetchone()[0]


def auto_archive(days: Optional[int] = None, now: Optional[int] = None) -> int:
    """Archiva los chats sin actividad hace `days` días (por omisión, el ajuste). Los fijados no se archivan.
    Devuelve cuántos archivó."""
    days = get_auto_archive_days() if days is None else int(days)
    if days <= 0:
        return 0
    limit = int(now or time.time()) - days * 86400
    with db_conn() as conn:
        cur = conn.execute(
            "UPDATE chats SET is_archived = 1 "
            "WHERE COALESCE(is_archived, 0) = 0 AND COALESCE(is_pinned, 0) = 0 AND last_message_time > 0 AND last_message_time < ?", (limit,))
        return cur.rowcount


def unarchive_on_message(cursor, chat_jid: str, now: Optional[int] = None) -> bool:
    """Llegó (o se mandó) un mensaje: el chat sale de Archivados, salvo que esté silenciado.
    Se llama dentro de la transacción de `save_message`."""
    now = int(now or time.time())
    cur = cursor.execute(
        "UPDATE chats SET is_archived = 0 WHERE jid = ? AND is_archived = 1 "
        "AND NOT (COALESCE(muted_until, 0) = -1 OR COALESCE(muted_until, 0) > ?)", (chat_jid, now))
    return cur.rowcount > 0


# ── Lectura invisible por chat ──

def _load_silent() -> Set[str]:
    global _silent
    if _silent is None:
        with db_conn() as conn:
            _silent = {r[0] for r in conn.execute("SELECT chat_jid FROM chat_silent_read").fetchall()}
    return _silent


def reset_cache() -> None:
    global _silent
    _silent = None


def is_silent_read(jid: Optional[str]) -> bool:
    """¿En este chat no se mandan tildes azules?"""
    return bool(jid) and jid in _load_silent()


def set_silent_read(jid: str, on: bool) -> None:
    if not jid:
        return
    with db_conn() as conn:
        if on:
            conn.execute("INSERT OR IGNORE INTO chat_silent_read (chat_jid) VALUES (?)", (jid,))
        else:
            conn.execute("DELETE FROM chat_silent_read WHERE chat_jid = ?", (jid,))
    (_load_silent().add if on else _load_silent().discard)(jid)


# ── Bloqueo de la app entera ──

def app_lock_enabled() -> bool:
    from app.config import get_pref
    from app import chat_lock
    return get_pref("app_lock_enabled") and chat_lock.has_password()


def app_lock_due(left_at: Optional[float], now: Optional[float] = None) -> bool:
    """¿Corresponde pedir la contraseña al volver? `left_at` = momento en que la app pasó a segundo plano
    (None = recién abierta, siempre se pide). Con 0 minutos se pide cada vez."""
    if not app_lock_enabled():
        return False
    if left_at is None:
        return True
    return (now if now is not None else time.time()) - left_at >= get_app_lock_minutes() * 60


# ── Galería del chat ──

GALLERY_KINDS = {
    "media": ("image", "video"),
    "docs": ("document",),
    "audio": ("audio",),
}
_URL_RE = re.compile(r"https?://\S+")


def gallery_page(chat_jid: str, kind: str, limit: int = 60, offset: int = 0) -> Tuple[List[dict], int]:
    """Una página de la galería, del más nuevo al más viejo. `kind`: media (fotos y videos), docs, audio o links.
    Para links cada elemento es un enlace ({id, url, text, timestamp}); `offset` cuenta mensajes, no enlaces.
    Devuelve (elementos, mensajes_leídos): hay más páginas mientras mensajes_leídos == limit."""
    cols = "id, text, media_type, media_path, media_raw, timestamp, from_me, transcript"
    with db_conn() as conn:
        if kind == "links":
            rows = conn.execute(
                "SELECT id, text, timestamp FROM messages WHERE chat_jid = ? AND text LIKE '%http%' "
                "ORDER BY timestamp DESC LIMIT ? OFFSET ?", (chat_jid, limit, offset)).fetchall()
            out = []
            for r in rows:
                for url in _URL_RE.findall(r["text"] or ""):
                    out.append({"id": r["id"], "url": url.rstrip(".,);"), "text": r["text"], "timestamp": r["timestamp"]})
            return out, len(rows)
        types = GALLERY_KINDS.get(kind)
        if not types:
            return [], 0
        rows = conn.execute(
            f"SELECT {cols} FROM messages WHERE chat_jid = ? AND media_type IN ({','.join('?' * len(types))}) "
            "ORDER BY timestamp DESC LIMIT ? OFFSET ?", [chat_jid, *types, limit, offset]).fetchall()
    return [dict(r) for r in rows], len(rows)
