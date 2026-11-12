"""
Advanced WhatsApp - Estadísticas de mensajes
=============================================
Con quién hablás más, mensajes por día y por hora, y tiempo de respuesta promedio (el tuyo y el de los demás).
Todo se calcula en SQLite con una sola pasada agrupada por consulta (nada de recorrer mensajes en Python) y un
índice por fecha, así que en una base de cientos de miles de mensajes tarda décimas de segundo.
Se llama desde un hilo (StatsDialog lo hace), nunca desde el hilo de la UI.
"""

import time
from typing import Any, Dict, List, Optional

from app.db import db_conn

PERIODS = {            # clave -> (texto, días; 0 = todo)
    "week": ("Última semana", 7),
    "month": ("Último mes", 30),
    "quarter": ("Últimos 3 meses", 90),
    "year": ("Último año", 365),
    "all": ("Todo el historial", 0),
}

# Una respuesta que tarda más que esto se cuenta como una conversación nueva, no como «tardó mucho en contestar»
RESPONSE_CAP_SECONDS = 24 * 3600

# Los borrados no cuentan como mensajes
_COUNTED = "media_type != 'revoked'"


def ensure_indexes(conn=None) -> None:
    """Índice por fecha para filtrar por período sin recorrer toda la tabla (idempotente)."""
    def _run(c):
        c.execute("CREATE INDEX IF NOT EXISTS idx_messages_ts ON messages(timestamp)")
    if conn is not None:
        _run(conn)
        return
    with db_conn() as c:
        _run(c)


def period_start(period: str, now: Optional[int] = None) -> int:
    """Epoch desde el que cuenta el período (0 = desde siempre)."""
    days = PERIODS.get(period, PERIODS["month"])[1]
    return 0 if days <= 0 else int(now if now is not None else time.time()) - days * 86400


def totals(since: int = 0) -> Dict[str, int]:
    with db_conn() as conn:
        row = conn.execute(
            f"SELECT COUNT(*) AS total, COALESCE(SUM(from_me), 0) AS sent, COUNT(DISTINCT chat_jid) AS chats "
            f"FROM messages WHERE timestamp >= ? AND {_COUNTED}", (since,)).fetchone()
    total, sent = int(row["total"]), int(row["sent"])
    return {"total": total, "sent": sent, "received": total - sent, "chats": int(row["chats"])}


def top_chats(since: int = 0, limit: int = 15, include_groups: bool = True) -> List[Dict[str, Any]]:
    """Chats con más mensajes en el período: {jid, name, total, sent, received, is_group}."""
    where = f"m.timestamp >= ? AND m.{_COUNTED}"
    if not include_groups:
        where += " AND m.chat_jid NOT LIKE '%@g.us'"
    with db_conn() as conn:
        rows = conn.execute(
            f"SELECT m.chat_jid AS jid, COUNT(*) AS total, SUM(m.from_me) AS sent, "
            f"COALESCE(NULLIF(c.custom_name, ''), NULLIF(c.name, ''), NULLIF(c.phone, ''), m.chat_jid) AS name, "
            f"COALESCE(c.is_group, 0) AS is_group "
            f"FROM messages m LEFT JOIN chats c ON c.jid = m.chat_jid "
            f"WHERE {where} GROUP BY m.chat_jid ORDER BY total DESC LIMIT ?", (since, limit)).fetchall()
    return [{"jid": r["jid"], "name": r["name"], "total": int(r["total"]), "sent": int(r["sent"] or 0),
             "received": int(r["total"]) - int(r["sent"] or 0), "is_group": bool(r["is_group"])} for r in rows]


def messages_by_day(since: int = 0) -> List[Dict[str, Any]]:
    """[{day: 'YYYY-MM-DD', sent, received}] en orden cronológico (hora local)."""
    with db_conn() as conn:
        rows = conn.execute(
            f"SELECT date(timestamp, 'unixepoch', 'localtime') AS day, COUNT(*) AS total, SUM(from_me) AS sent "
            f"FROM messages WHERE timestamp >= ? AND {_COUNTED} GROUP BY day ORDER BY day", (since,)).fetchall()
    return [{"day": r["day"], "sent": int(r["sent"] or 0), "received": int(r["total"]) - int(r["sent"] or 0)}
            for r in rows]


def messages_by_hour(since: int = 0) -> List[Dict[str, int]]:
    """24 entradas [{hour: 0..23, sent, received}] (hora local), con ceros donde no hubo nada."""
    with db_conn() as conn:
        rows = conn.execute(
            f"SELECT CAST(strftime('%H', timestamp, 'unixepoch', 'localtime') AS INTEGER) AS hour, "
            f"COUNT(*) AS total, SUM(from_me) AS sent "
            f"FROM messages WHERE timestamp >= ? AND {_COUNTED} GROUP BY hour", (since,)).fetchall()
    out = {h: {"hour": h, "sent": 0, "received": 0} for h in range(24)}
    for r in rows:
        sent = int(r["sent"] or 0)
        out[int(r["hour"])] = {"hour": int(r["hour"]), "sent": sent, "received": int(r["total"]) - sent}
    return [out[h] for h in range(24)]


def _response_rows(since: int, cap: int = RESPONSE_CAP_SECONDS) -> List[Any]:
    """Una sola pasada con funciones de ventana: por chat y por lado, cuántas respuestas hubo y cuánto sumaron.
    Una «respuesta» es el primer mensaje de un lado después de un mensaje del otro (solo chats individuales);
    las que pasan de `cap` se descartan (era una conversación nueva, no una demora)."""
    with db_conn() as conn:
        return conn.execute(
            f"""
            WITH seq AS (
                SELECT chat_jid, from_me, timestamp AS ts,
                       LAG(from_me)   OVER w AS prev_me,
                       LAG(timestamp) OVER w AS prev_ts
                FROM messages
                WHERE timestamp >= ? AND {_COUNTED} AND chat_jid NOT LIKE '%@g.us' AND chat_jid NOT LIKE '%@broadcast'
                WINDOW w AS (PARTITION BY chat_jid ORDER BY timestamp, created_at)
            )
            SELECT chat_jid AS jid, from_me, SUM(ts - prev_ts) AS total_s, COUNT(*) AS n
            FROM seq
            WHERE prev_me IS NOT NULL AND prev_me != from_me AND ts - prev_ts BETWEEN 0 AND ?
            GROUP BY chat_jid, from_me
            """, (since, cap)).fetchall()


def _summarize_response(rows) -> Dict[str, Any]:
    out = {"mine": None, "theirs": None, "mine_count": 0, "theirs_count": 0}
    for key, flag in (("mine", 1), ("theirs", 0)):
        total = sum(r["total_s"] for r in rows if r["from_me"] == flag)
        n = sum(r["n"] for r in rows if r["from_me"] == flag)
        out[key + "_count"] = int(n)
        out[key] = float(total) / n if n else None
    return out


def _speed_ranking(rows, since: int, min_replies: int, limit: int) -> Dict[str, List[Dict[str, Any]]]:
    mine = [r for r in rows if r["from_me"] == 1 and r["n"] >= min_replies]
    mine.sort(key=lambda r: r["total_s"] / r["n"])
    if not mine:
        return {"fastest": [], "slowest": []}
    keep = {r["jid"] for r in mine[:limit]} | {r["jid"] for r in mine[-limit:]}
    with db_conn() as conn:
        names = {r["jid"]: r["name"] for r in conn.execute(
            "SELECT jid, COALESCE(NULLIF(custom_name, ''), NULLIF(name, ''), NULLIF(phone, ''), jid) AS name FROM chats")
            if r["jid"] in keep}
    items = [{"jid": r["jid"], "name": names.get(r["jid"], r["jid"]), "avg_seconds": r["total_s"] / r["n"],
              "count": int(r["n"])} for r in mine]
    return {"fastest": items[:limit], "slowest": list(reversed(items[-limit:]))}


def response_times(since: int = 0, cap: int = RESPONSE_CAP_SECONDS) -> Dict[str, Any]:
    """Tiempo promedio (segundos) que tardás vos en contestar y que tardan los demás, en chats individuales.
    Devuelve {mine, theirs, mine_count, theirs_count} (promedio None si no hay datos)."""
    return _summarize_response(_response_rows(since, cap))


def slowest_and_fastest(since: int = 0, min_replies: int = 3, limit: int = 5) -> Dict[str, List[Dict[str, Any]]]:
    """Chats donde más rápido y más lento contestás (promedio propio, con al menos `min_replies` respuestas)."""
    return _speed_ranking(_response_rows(since), since, min_replies, limit)


def format_duration(seconds: Optional[float]) -> str:
    """'3 min', '2 h 15 min', '—' sin datos."""
    if seconds is None:
        return "—"
    s = int(round(seconds))
    if s < 60:
        return f"{s} s"
    if s < 3600:
        return f"{s // 60} min"
    h, m = divmod(s // 60, 60)
    return f"{h} h {m:02d} min" if m else f"{h} h"


def report(period: str = "month") -> Dict[str, Any]:
    """Todo junto, para la pantalla."""
    since = period_start(period)
    ensure_indexes()
    rows = _response_rows(since)       # una sola pasada sirve para el promedio y para el ranking por chat
    return {
        "period": period,
        "totals": totals(since),
        "top_chats": top_chats(since),
        "by_day": messages_by_day(since),
        "by_hour": messages_by_hour(since),
        "response": _summarize_response(rows),
        "speed": _speed_ranking(rows, since, 3, 5),
    }
