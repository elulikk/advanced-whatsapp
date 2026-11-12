"""
Plantillas / atajos de texto: escribir `/atajo` en el campo de mensaje y se reemplaza por el texto guardado.

Se guardan en ~/.magsapp/plantillas.json (JSON simple: {"items": [{"shortcut", "text"}]}) escrito de forma atómica,
para que el backup/sincronización lo copie como está. Android usa el mismo formato en su carpeta de datos.
"""

import json
import os
import re
import threading
from typing import Dict, List, Optional

from app.config import DATA_DIR

TEMPLATES_FILE = os.path.join(DATA_DIR, "plantillas.json")
_SHORTCUT_RE = re.compile(r"[^\w\-]", re.UNICODE)
_TRAILING_SLASH = re.compile(r"(?:^|\s)/([\w\-]+)$", re.UNICODE)
_lock = threading.Lock()
_cache: Optional[List[Dict[str, str]]] = None


def clean_shortcut(s: str) -> str:
    """'/Hola Mundo' -> 'holamundo' (sin barra, sin espacios, en minúsculas)."""
    return _SHORTCUT_RE.sub("", (s or "").strip().lstrip("/").lower())


def load_templates(path: str = TEMPLATES_FILE) -> List[Dict[str, str]]:
    global _cache
    if _cache is not None and path == TEMPLATES_FILE:
        return list(_cache)
    items: List[Dict[str, str]] = []
    try:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f).get("items", [])
        seen = set()
        for it in raw:
            sc, text = clean_shortcut(str(it.get("shortcut", ""))), str(it.get("text", "")).strip()
            if sc and text and sc not in seen:
                seen.add(sc)
                items.append({"shortcut": sc, "text": text})
    except (OSError, ValueError, AttributeError):
        pass
    if path == TEMPLATES_FILE:
        _cache = items
    return list(items)


def save_templates(items: List[Dict[str, str]], path: str = TEMPLATES_FILE) -> bool:
    global _cache
    clean, seen = [], set()
    for it in items:
        sc, text = clean_shortcut(it.get("shortcut", "")), (it.get("text") or "").strip()
        if sc and text and sc not in seen:
            seen.add(sc)
            clean.append({"shortcut": sc, "text": text})
    with _lock:
        try:
            tmp = f"{path}.tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"items": clean}, f, ensure_ascii=False, indent=1)
            os.replace(tmp, path)
        except OSError:
            return False
        if path == TEMPLATES_FILE:
            _cache = clean
    return True


def find_template(shortcut: str, items: Optional[List[Dict[str, str]]] = None) -> Optional[Dict[str, str]]:
    sc = clean_shortcut(shortcut)
    for it in (items if items is not None else load_templates()):
        if it["shortcut"] == sc:
            return it
    return None


def suggestions(prefix: str, items: Optional[List[Dict[str, str]]] = None, limit: int = 6) -> List[Dict[str, str]]:
    """Atajos que empiezan con lo escrito después de la barra (para sugerir mientras se tipea)."""
    p = clean_shortcut(prefix)
    pool = items if items is not None else load_templates()
    return [it for it in pool if it["shortcut"].startswith(p)][:limit]


def trailing_shortcut(text: str) -> Optional[str]:
    """El '/atajo' que está al final del texto (al principio o tras un espacio), sin la barra."""
    m = _TRAILING_SLASH.search(text or "")
    return m.group(1) if m else None


def expand_trailing(text: str, items: Optional[List[Dict[str, str]]] = None) -> Optional[str]:
    """Si el texto termina en un /atajo existente, devuelve el texto con la plantilla puesta; si no, None."""
    sc = trailing_shortcut(text)
    if not sc:
        return None
    tpl = find_template(sc, items)
    if not tpl:
        return None
    return text[: len(text) - len(sc) - 1] + tpl["text"]
