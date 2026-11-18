"""Álbumes: fotos seguidas del mismo remitente que el chat muestra como un solo mensaje, como WhatsApp.

WhatsApp agrupa las fotos que se mandan juntas (albumMessage + messageAssociation). Acá no hace falta guardar esa
relación: alcanza con juntar las fotos consecutivas del mismo remitente mandadas con poca diferencia de tiempo.
"""
from datetime import datetime
from typing import Dict, List

ALBUM_GAP_SECONDS = 60     # entre una foto y la siguiente del mismo álbum
ALBUM_MIN = 2


def _is_photo(msg: dict) -> bool:
    return (msg.get("media_type") or "") == "image"


def _same_day(a: int, b: int) -> bool:
    try:
        return datetime.fromtimestamp(a).date() == datetime.fromtimestamp(b).date()
    except (OSError, ValueError, OverflowError):
        return False


def _joins(prev: dict, msg: dict) -> bool:
    """`msg` sigue el álbum que viene de `prev`: mismo remitente, enseguida y sin ser respuesta a otro mensaje."""
    if not _is_photo(msg) or msg.get("quoted_text"):
        return False
    if bool(prev.get("from_me")) != bool(msg.get("from_me")):
        return False
    if not prev.get("from_me") and (prev.get("sender_jid") or "") != (msg.get("sender_jid") or ""):
        return False
    t0, t1 = int(prev.get("timestamp") or 0), int(msg.get("timestamp") or 0)
    return 0 <= t1 - t0 <= ALBUM_GAP_SECONDS and _same_day(t0, t1)


def group_albums(messages: List[dict]) -> Dict[str, List[dict]]:
    """{id de la primera foto: [fotos del álbum]} para cada tanda de 2 o más fotos seguidas (mensajes en orden)."""
    albums: Dict[str, List[dict]] = {}
    i, n = 0, len(messages)
    while i < n:
        if not _is_photo(messages[i]):
            i += 1
            continue
        j = i + 1
        while j < n and _joins(messages[j - 1], messages[j]):
            j += 1
        if j - i >= ALBUM_MIN:
            albums[messages[i]["id"]] = messages[i:j]
        i = j
    return albums
