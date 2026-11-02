"""Tags visuales de los chats: definiciones (forma, color, texto, expiración, efectos) y asignaciones.

Se guardan en la base SQLite de la app (tablas `tags` y `chat_tags`). La prioridad de un tag es su lugar
en la lista (sort_order): si un chat tiene varios tags con el mismo efecto, gana el que está más arriba.

Expiración de una asignación:
  - "none":     no vence (fijo).
  - "hours":    se borra `expire_value` horas después de poner el tag.
  - "days":     se borra `expire_value` días después de poner el tag.
  - "inactive": se borra si el chat pasa `expire_value` días sin mensajes (contando desde el último mensaje
                o desde que se puso el tag, lo que sea más reciente).
Con `expire_business` los días de "days" e "inactive" se cuentan hábiles (lunes a viernes; los feriados no
se descuentan).
"""
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from PySide6.QtCore import QObject, Signal

from app.db import db_conn

SHAPES = {
    "circle":   "Círculo",
    "square":   "Cuadrado",
    "diamond":  "Rombo",
    "triangle": "Triángulo",
    "hexagon":  "Hexágono",
    "octagon":  "Octógono (pare)",
    "star":     "Estrella",
    "heart":    "Corazón",
    "house":    "Casa",
    "alert":    "Advertencia (⚠)",
    "exclaim":  "Admiración (!)",
}

EXPIRE_MODES = {
    "none":     "Fijo (no vence)",
    "hours":    "Vence X horas después de ponerlo",
    "days":     "Vence X días después de ponerlo",
    "inactive": "Vence tras X días sin mensajes en el chat",
}
DAY_MODES = ("days", "inactive")

TAG_FIELDS = ("name", "shape", "color", "text", "text_color", "expire_mode", "expire_value", "expire_business",
              "fx_bg", "fx_bg_strength", "fx_text", "fx_header", "fx_row", "fx_name")

DEFAULT_TAG = {
    "name": "Nuevo tag", "shape": "circle", "color": "#2f7bf5", "text": "", "text_color": "#ffffff",
    "expire_mode": "none", "expire_value": 24, "expire_business": 0,
    "fx_bg": "", "fx_bg_strength": 18, "fx_text": "", "fx_header": "", "fx_row": "", "fx_name": "",
}

# Tags que trae la app (se pueden editar o borrar; si se borran, no vuelven)
_EMPLOYEE = {"shape": "circle", "expire_mode": "days", "expire_value": 10, "expire_business": 1}
DEFAULT_TAGS = [
    {"name": "Cliente problemático", "shape": "octagon", "color": "#e5534b", "text": "!",
     "fx_header": "#e5534b", "fx_row": "#e5534b"},
    {"name": "Colega de la casa", "shape": "house", "color": "#1fa37f", "text": "",
     "fx_bg": "#1fa37f", "fx_bg_strength": 16},
    {"name": "Venta", "shape": "circle", "color": "#1fa37f", "text": "$"},
    {"name": "CELE", "color": "#4fb3e8", "text": "C", **_EMPLOYEE},
    {"name": "ULI", "color": "#a36be0", "text": "U", **_EMPLOYEE},
    {"name": "DANI", "color": "#f08a4b", "text": "D", **_EMPLOYEE},
    {"name": "MARIAN", "color": "#d6457a", "text": "M", **_EMPLOYEE},
]
DEFAULTS_VERSION = 2

# Tags de ejemplo de la primera versión: si siguen tal cual y sin usar, se reemplazan por los de arriba
_OLD_STARTERS = {
    "servicio técnico": {"shape": "circle", "color": "#2f7bf5", "text": "ST", "expire_mode": "inactive",
                         "expire_value": 30, "fx_bg": "", "fx_text": "", "fx_header": ""},
    "urgente": {"shape": "alert", "color": "#e5534b", "text": "", "expire_mode": "hours", "expire_value": 24,
                "fx_bg": "#e5534b", "fx_text": "#f5d76e", "fx_header": "#e5534b"},
    "venta": {"shape": "diamond", "color": "#1fa37f", "text": "$", "expire_mode": "inactive",
              "expire_value": 30, "fx_bg": "", "fx_text": "", "fx_header": ""},
}


class _TagSignals(QObject):
    changed = Signal()   # cambió una definición o una asignación (repintar lista, cabecera y efectos)


tag_signals = _TagSignals()

_cache: Optional[List[dict]] = None


def _insert(conn, data: dict, order: int) -> int:
    d = {**DEFAULT_TAG, **data}
    cur = conn.execute(
        f"INSERT INTO tags ({', '.join(TAG_FIELDS)}, sort_order, created_at) "
        f"VALUES ({', '.join('?' * len(TAG_FIELDS))}, ?, ?)",
        [d[f] for f in TAG_FIELDS] + [order, int(time.time())])
    return cur.lastrowid


def ensure_tables(conn) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS tags (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL DEFAULT '',
            shape TEXT NOT NULL DEFAULT 'circle',
            color TEXT NOT NULL DEFAULT '#2f7bf5',
            text TEXT DEFAULT '',
            text_color TEXT DEFAULT '#ffffff',
            expire_mode TEXT NOT NULL DEFAULT 'none',
            expire_value INTEGER DEFAULT 0,
            fx_bg TEXT DEFAULT '',
            fx_bg_strength INTEGER DEFAULT 18,
            fx_text TEXT DEFAULT '',
            fx_header TEXT DEFAULT '',
            sort_order INTEGER DEFAULT 0,
            created_at INTEGER DEFAULT 0
        )
    """)
    # Columnas agregadas después: días hábiles y efectos sobre la fila de la lista de chats
    for column in ("expire_business INTEGER DEFAULT 0", "fx_row TEXT DEFAULT ''", "fx_name TEXT DEFAULT ''"):
        try:
            conn.execute(f"ALTER TABLE tags ADD COLUMN {column}")
        except Exception:
            pass
    # Sin FK al chat: si un chat '@lid' se une a su chat de teléfono, la asignación se mueve (ver db.py)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS chat_tags (
            chat_jid TEXT NOT NULL,
            tag_id INTEGER NOT NULL,
            assigned_at INTEGER NOT NULL,
            PRIMARY KEY (chat_jid, tag_id),
            FOREIGN KEY (tag_id) REFERENCES tags(id) ON DELETE CASCADE
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_chat_tags_tag ON chat_tags(tag_id)")
    conn.execute("CREATE TABLE IF NOT EXISTS tag_meta (key TEXT PRIMARY KEY, value TEXT)")
    row = conn.execute("SELECT value FROM tag_meta WHERE key = 'defaults_version'").fetchone()
    if not row or int(row[0] or 0) < DEFAULTS_VERSION:
        _install_defaults(conn)
        conn.execute("INSERT OR REPLACE INTO tag_meta (key, value) VALUES ('defaults_version', ?)",
                     (str(DEFAULTS_VERSION),))


def _install_defaults(conn) -> None:
    """Agrega los tags que trae la app. Los de ejemplo de la versión anterior que nadie tocó ni usó se sacan
    (o se actualizan, si tienen el mismo nombre); los tags creados por el usuario quedan como están, después."""
    rows = [dict(r) for r in conn.execute("SELECT * FROM tags ORDER BY sort_order, id").fetchall()]
    used = {r[0] for r in conn.execute("SELECT DISTINCT tag_id FROM chat_tags").fetchall()}
    by_name = {}
    for r in rows:
        old = _OLD_STARTERS.get((r["name"] or "").strip().casefold())
        pristine = old is not None and all(r.get(k) == v for k, v in old.items())
        if pristine and r["id"] not in used:
            conn.execute("DELETE FROM tags WHERE id = ?", (r["id"],))
            continue
        by_name.setdefault((r["name"] or "").strip().casefold(), r)
    order_ids = []
    for t in DEFAULT_TAGS:
        existing = by_name.pop(t["name"].casefold(), None)
        order_ids.append(existing["id"] if existing else _insert(conn, t, 0))
    rest = [r["id"] for r in rows if r["id"] in {x["id"] for x in by_name.values()}]
    for i, tid in enumerate(order_ids + rest):
        conn.execute("UPDATE tags SET sort_order = ? WHERE id = ?", (i, tid))


def _invalidate():
    global _cache
    _cache = None


def notify_changed():
    _invalidate()
    tag_signals.changed.emit()


# ── Definiciones ──

def list_tags() -> List[dict]:
    """Todos los tags en orden de prioridad (el primero es el más importante)."""
    global _cache
    if _cache is None:
        with db_conn() as conn:
            rows = conn.execute("SELECT * FROM tags ORDER BY sort_order, id").fetchall()
        _cache = [dict(r) for r in rows]
    return [dict(t) for t in _cache]


def get_tag(tag_id: int) -> Optional[dict]:
    return next((t for t in list_tags() if t["id"] == tag_id), None)


def create_tag(data: Optional[dict] = None) -> int:
    d = {**DEFAULT_TAG, **(data or {})}
    with db_conn() as conn:
        order = conn.execute("SELECT COALESCE(MAX(sort_order), -1) + 1 FROM tags").fetchone()[0]
        cur = conn.execute(
            f"INSERT INTO tags ({', '.join(TAG_FIELDS)}, sort_order, created_at) "
            f"VALUES ({', '.join('?' * len(TAG_FIELDS))}, ?, ?)",
            [d[f] for f in TAG_FIELDS] + [order, int(time.time())])
        tag_id = cur.lastrowid
    notify_changed()
    return tag_id


def update_tag(tag_id: int, data: dict) -> None:
    fields = [f for f in TAG_FIELDS if f in data]
    if not fields:
        return
    with db_conn() as conn:
        conn.execute(f"UPDATE tags SET {', '.join(f + ' = ?' for f in fields)} WHERE id = ?",
                     [data[f] for f in fields] + [tag_id])
    notify_changed()


def delete_tag(tag_id: int) -> None:
    with db_conn() as conn:
        conn.execute("DELETE FROM chat_tags WHERE tag_id = ?", (tag_id,))
        conn.execute("DELETE FROM tags WHERE id = ?", (tag_id,))
    notify_changed()


def reorder_tags(ids: List[int]) -> None:
    with db_conn() as conn:
        for i, tid in enumerate(ids):
            conn.execute("UPDATE tags SET sort_order = ? WHERE id = ?", (i, tid))
    notify_changed()


def count_chats_with_tag(tag_id: int) -> int:
    with db_conn() as conn:
        return conn.execute("SELECT COUNT(*) FROM chat_tags WHERE tag_id = ?", (tag_id,)).fetchone()[0]


# ── Asignaciones ──

def assign_tag(chat_jid: str, tag_id: int) -> bool:
    """Pone el tag en el chat. Si ya lo tenía, reinicia su reloj de expiración. True si cambió algo."""
    if not chat_jid or get_tag(tag_id) is None:
        return False
    with db_conn() as conn:
        conn.execute("INSERT OR REPLACE INTO chat_tags (chat_jid, tag_id, assigned_at) VALUES (?, ?, ?)",
                     (chat_jid, tag_id, int(time.time())))
    notify_changed()
    return True


def remove_tag(chat_jid: str, tag_id: Optional[int] = None) -> None:
    """Quita un tag del chat (o todos si tag_id es None)."""
    with db_conn() as conn:
        if tag_id is None:
            conn.execute("DELETE FROM chat_tags WHERE chat_jid = ?", (chat_jid,))
        else:
            conn.execute("DELETE FROM chat_tags WHERE chat_jid = ? AND tag_id = ?", (chat_jid, tag_id))
    notify_changed()


def add_days(ts: int, days: int, business: bool = False) -> int:
    """`ts` + `days` días; con business, solo cuentan lunes a viernes (se conserva la hora del día)."""
    if not business:
        return int(ts) + days * 86400
    dt = datetime.fromtimestamp(int(ts))
    counted = 0
    while counted < days:
        dt += timedelta(days=1)
        if dt.weekday() < 5:
            counted += 1
    return int(dt.timestamp())


def expires_at(tag: dict, assigned_at: int, last_message_time: int) -> Optional[int]:
    """Momento (epoch) en que vence la asignación, o None si es fija."""
    mode, value = tag.get("expire_mode"), int(tag.get("expire_value") or 0)
    business = bool(tag.get("expire_business"))
    if value <= 0:
        return None
    if mode == "hours":
        return assigned_at + value * 3600
    if mode == "days":
        return add_days(assigned_at, value, business)
    if mode == "inactive":
        return add_days(max(assigned_at, int(last_message_time or 0)), value, business)
    return None


def purge_expired(now: Optional[int] = None) -> int:
    """Borra las asignaciones vencidas. Devuelve cuántas borró (y avisa si hubo alguna)."""
    now = int(now or time.time())
    tags = {t["id"]: t for t in list_tags()}
    if not any(t["expire_mode"] in EXPIRE_MODES and t["expire_mode"] != "none" for t in tags.values()):
        return 0
    with db_conn() as conn:
        rows = conn.execute("""
            SELECT ct.chat_jid, ct.tag_id, ct.assigned_at, COALESCE(c.last_message_time, 0) AS lmt
            FROM chat_tags ct LEFT JOIN chats c ON c.jid = ct.chat_jid
        """).fetchall()
        gone = []
        for r in rows:
            tag = tags.get(r["tag_id"])
            exp = expires_at(tag, r["assigned_at"], r["lmt"]) if tag else now
            if exp is not None and exp <= now:
                gone.append((r["chat_jid"], r["tag_id"]))
        if gone:
            conn.executemany("DELETE FROM chat_tags WHERE chat_jid = ? AND tag_id = ?", gone)
    if gone:
        notify_changed()
    return len(gone)


def tags_for_chats(jids: List[str]) -> Dict[str, List[dict]]:
    """jid -> lista de tags (con 'assigned_at') en orden de prioridad."""
    jids = [j for j in jids if j]
    if not jids:
        return {}
    order = {t["id"]: (i, t) for i, t in enumerate(list_tags())}
    out: Dict[str, List[dict]] = {}
    with db_conn() as conn:
        for start in range(0, len(jids), 400):
            chunk = jids[start:start + 400]
            rows = conn.execute(
                f"SELECT chat_jid, tag_id, assigned_at FROM chat_tags WHERE chat_jid IN ({','.join('?' * len(chunk))})",
                chunk).fetchall()
            for r in rows:
                if r["tag_id"] in order:
                    out.setdefault(r["chat_jid"], []).append({**order[r["tag_id"]][1], "assigned_at": r["assigned_at"]})
    for lst in out.values():
        lst.sort(key=lambda t: order[t["id"]][0])
    return out


def tags_for_chat(jid: str) -> List[dict]:
    return tags_for_chats([jid]).get(jid, [])


def chat_effects(tags: List[dict]) -> dict:
    """Efectos de la ventana de chat y de su fila en la lista: para cada efecto gana el primer tag
    (el de más prioridad) que lo define."""
    fx = {"bg": None, "bg_strength": 0, "text": None, "header": None, "row": None, "name": None}
    for t in tags:
        if fx["bg"] is None and t.get("fx_bg"):
            fx["bg"], fx["bg_strength"] = t["fx_bg"], int(t.get("fx_bg_strength") or 18)
        for key in ("text", "header", "row", "name"):
            if fx[key] is None and t.get("fx_" + key):
                fx[key] = t["fx_" + key]
    return fx


def short_expiry(tag: dict) -> str:
    """Vencimiento en pocas letras para la vista lista del panel: 'fijo', '24 h', '10 d háb.', '30 d sin msj'."""
    mode, value = tag.get("expire_mode"), int(tag.get("expire_value") or 0)
    if mode not in EXPIRE_MODES or mode == "none" or value <= 0:
        return "fijo"
    if mode == "hours":
        return f"{value} h"
    d = f"{value} d háb." if tag.get("expire_business") else f"{value} d"
    return d + (" sin msj" if mode == "inactive" else "")


def describe_expiry(tag: dict, assigned_at: Optional[int] = None, last_message_time: int = 0) -> str:
    """Texto corto para tooltips: 'fijo', 'vence en 5 h', 'vence tras 30 días sin mensajes'…"""
    mode, value = tag.get("expire_mode"), int(tag.get("expire_value") or 0)
    if mode not in EXPIRE_MODES or mode == "none" or value <= 0:
        return "fijo"
    dias = "días hábiles" if tag.get("expire_business") else "días"
    if assigned_at is None:
        if mode == "hours":
            return f"vence {value} h después de ponerlo"
        if mode == "days":
            return f"vence {value} {dias} después de ponerlo"
        return f"vence tras {value} {dias} sin mensajes"
    left = expires_at(tag, assigned_at, last_message_time) - time.time()
    if left <= 0:
        return "vencido"
    if left < 3600:
        txt = f"{max(1, int(left // 60))} min"
    elif left < 2 * 86400:
        txt = f"{int(left // 3600)} h"
    else:
        txt = f"{int(left // 86400)} días"
    return f"vence en {txt}" + (" si no hay mensajes" if mode == "inactive" else "")
