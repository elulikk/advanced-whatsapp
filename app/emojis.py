"""
Advanced WhatsApp - Lógica del selector de emojis (sin Qt)
==========================================================
La lista vive en `emoji_data.TEXT` y se parsea recién la primera vez que se abre el selector.
Los recientes se guardan en el índice de stickers (`app.stickers`), así hay un solo archivo.
"""

import unicodedata
from typing import Dict, List, Optional, Tuple

MAX_RECENTS = 32

_cache: Optional[List[Tuple[str, str, List[Tuple[str, str]]]]] = None   # (categoría, ícono, [(emoji, claves)])


def _fold(text: str) -> str:
    """Minúsculas y sin acentos, para que «corazón» encuentre «corazon»."""
    nfd = unicodedata.normalize("NFD", text.lower())
    return "".join(c for c in nfd if unicodedata.category(c) != "Mn")


def parse(text: str) -> List[Tuple[str, str, List[Tuple[str, str]]]]:
    cats: List[Tuple[str, str, List[Tuple[str, str]]]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("#"):
            name, _, icon = line[1:].strip().partition("|")
            cats.append((name.strip(), icon.strip(), []))
        elif cats:
            emoji, _, keys = line.partition(" ")
            cats[-1][2].append((emoji, _fold(keys)))
    return cats


def categories() -> List[Tuple[str, str, List[Tuple[str, str]]]]:
    """[(nombre, emoji de la pestaña, [(emoji, palabras clave)])]; se arma una sola vez."""
    global _cache
    if _cache is None:
        from app.emoji_data import TEXT
        _cache = parse(TEXT)
    return _cache


def search(query: str, limit: int = 120) -> List[str]:
    """Emojis cuyas palabras clave (o nombre de categoría) empiezan o contienen cada palabra buscada."""
    words = _fold(query).split()
    if not words:
        return []
    found: List[str] = []
    seen = set()
    for cat, _icon, items in categories():
        cat_fold = _fold(cat)
        for emoji, keys in items:
            if emoji in seen:
                continue
            haystack = f"{keys} {cat_fold}"
            if all(w in haystack for w in words):
                seen.add(emoji)
                found.append(emoji)
                if len(found) >= limit:
                    return found
    return found


def push_recent(recents: List[str], emoji: str, limit: int = MAX_RECENTS) -> List[str]:
    """Devuelve la lista de recientes con `emoji` primero y sin repetidos."""
    out = [emoji] + [e for e in recents if e != emoji]
    return out[:limit]
