"""
Advanced WhatsApp - Reproducción continua de notas de voz
============================================================
Al terminar una nota de voz, la siguiente del chat (si es otro audio) suena sola.
"""

from typing import Any, Dict, Optional

from app.db import db_conn


def next_audio(chat_jid: str, msg_id: str) -> Optional[Dict[str, Any]]:
    """El mensaje que sigue a `msg_id` en el chat, solo si es un audio; si no, None.
    Es «consecutivo» de verdad: si en el medio hay texto, foto, etc., no pasa a ninguno."""
    if not chat_jid or not msg_id:
        return None
    with db_conn() as conn:
        row = conn.execute(
            "SELECT * FROM messages WHERE chat_jid = ? AND media_type != 'revoked' AND "
            "(timestamp > (SELECT timestamp FROM messages WHERE id = ?) OR "
            " (timestamp = (SELECT timestamp FROM messages WHERE id = ?) AND "
            "  rowid > (SELECT rowid FROM messages WHERE id = ?))) "
            "ORDER BY timestamp, rowid LIMIT 1", (chat_jid, msg_id, msg_id, msg_id)).fetchone()
    if row is None or row["media_type"] != "audio":
        return None
    return dict(row)
