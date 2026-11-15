"""
Advanced WhatsApp - Vista previa de links
============================================
Título, descripción e imagen de los links de los mensajes. Prioridad:
  1. el preview que ya trae el mensaje de WhatsApp (el gateway lo manda en `link_preview`),
  2. el que está en la caché local (tabla link_previews de la base, por URL),
  3. si no, se le pide al gateway (GET /link-preview, con timeouts cortos) en un hilo aparte; la UI se entera por señal.
Nunca se bloquea la UI: `lookup` solo mira memoria/base y, si falta, deja el trabajo en cola.
"""

import base64
import hashlib
import html
import os
import queue
import re
import threading
import time
from typing import Any, Callable, Dict, Optional
from urllib.parse import urlparse

from PySide6.QtCore import QObject, Signal

from app.config import MEDIA_DIR, get_pref
from app.db import db_conn

PREVIEWS_DIR = os.path.join(MEDIA_DIR, "previews")
URL_RE = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
FAILED_RETRY_SECONDS = 3600          # un link que no dio preview no se vuelve a probar durante una hora
MAX_PENDING = 40
_EXT = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp", "image/gif": ".gif"}


class PreviewSignals(QObject):
    preview_ready = Signal(str)      # url


signals = PreviewSignals()

_lock = threading.Lock()
_mem: Dict[str, Optional[Dict[str, Any]]] = {}        # url -> preview (None = falló)
_failed_at: Dict[str, float] = {}
_queue: "queue.Queue[str]" = queue.Queue(maxsize=MAX_PENDING)
_queued: set = set()
_worker: Optional[threading.Thread] = None
_fetcher: Optional[Callable[[str], Optional[Dict[str, Any]]]] = None   # inyectable (tests)
_table_ready = False


def first_url(text: str) -> Optional[str]:
    """Primer link http(s) del texto, sin la puntuación final."""
    m = URL_RE.search(text or "")
    if not m:
        return None
    return m.group(0).rstrip(".,;:!?)]}'\"")


def ensure_table(conn=None) -> None:
    global _table_ready
    sql = ("CREATE TABLE IF NOT EXISTS link_previews ("
           "url TEXT PRIMARY KEY, title TEXT, description TEXT, image_path TEXT, fetched_at INTEGER)")
    if conn is not None:
        conn.execute(sql)
        _table_ready = True
        return
    with db_conn() as c:
        c.execute(sql)
    _table_ready = True


def _save_image(url: str, image_b64: Optional[str], mime: Optional[str]) -> Optional[str]:
    if not image_b64:
        return None
    try:
        data = base64.b64decode(image_b64)
        os.makedirs(PREVIEWS_DIR, exist_ok=True)
        path = os.path.join(PREVIEWS_DIR, hashlib.sha1(url.encode("utf-8")).hexdigest() + _EXT.get(mime or "", ".jpg"))
        with open(path, "wb") as f:
            f.write(data)
        return path
    except Exception:
        return None


def store(url: str, title: str = "", description: str = "", image_b64: Optional[str] = None,
          image_mime: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Guarda un preview (de WhatsApp o bajado) en memoria y en la base."""
    if not url or not (title or description or image_b64):
        return None
    prev = {"url": url, "title": (title or "").strip(), "description": (description or "").strip(),
            "image_path": _save_image(url, image_b64, image_mime)}
    with db_conn() as conn:
        if not _table_ready:
            ensure_table(conn)
        conn.execute("INSERT OR REPLACE INTO link_previews (url, title, description, image_path, fetched_at) "
                     "VALUES (?, ?, ?, ?, ?)", (url, prev["title"], prev["description"], prev["image_path"], int(time.time())))
    with _lock:
        _mem[url] = prev
        _failed_at.pop(url, None)
    return prev


def store_event(data: Dict[str, Any]) -> None:
    """Preview que viene dentro del mensaje entrante (lo llama el webhook). Se indexa por la URL del texto."""
    lp = data.get("link_preview") if isinstance(data, dict) else None
    if not isinstance(lp, dict):
        return
    url = first_url(data.get("text") or "") or lp.get("url") or ""
    try:
        if store(url, lp.get("title") or "", lp.get("description") or "", lp.get("image_b64"), lp.get("image_mime")):
            signals.preview_ready.emit(url)
    except Exception as e:
        print(f"[Advanced WhatsApp LinkPreview] no se pudo guardar el preview: {e}")


def _from_db(url: str) -> Optional[Dict[str, Any]]:
    try:
        with db_conn() as conn:
            if not _table_ready:
                ensure_table(conn)
            row = conn.execute("SELECT url, title, description, image_path FROM link_previews WHERE url = ?",
                               (url,)).fetchone()
    except Exception:
        return None
    if not row:
        return None
    prev = dict(row)
    if prev.get("image_path") and not os.path.exists(prev["image_path"]):
        prev["image_path"] = None
    return prev


def lookup(url: Optional[str], fetch: bool = True) -> Optional[Dict[str, Any]]:
    """Preview ya disponible para `url` o None. Si no está y `fetch`, lo encola (la señal avisa cuando llega)."""
    if not url or not get_pref("link_previews"):
        return None
    with _lock:
        if url in _mem:
            return _mem[url]
        failed = _failed_at.get(url)
    if failed is not None and time.time() - failed < FAILED_RETRY_SECONDS:
        return None
    prev = _from_db(url)
    if prev:
        with _lock:
            _mem[url] = prev
        return prev
    if fetch:
        _enqueue(url)
    return None


def _enqueue(url: str) -> None:
    global _worker
    with _lock:
        if url in _queued:
            return
        try:
            _queue.put_nowait(url)
        except queue.Full:
            return
        _queued.add(url)
        if _worker is None or not _worker.is_alive():
            _worker = threading.Thread(target=_run, name="advancedwhatsapp-linkpreview", daemon=True)
            _worker.start()


def _default_fetch(url: str) -> Optional[Dict[str, Any]]:
    from app.gateway_manager import GatewayManager
    res = GatewayManager.get_instance().fetch_link_preview(url)
    return res if res and res.get("success") else None


def _run() -> None:
    while True:
        try:
            url = _queue.get(timeout=30)
        except queue.Empty:
            return
        try:
            data = (_fetcher or _default_fetch)(url)
            prev = None
            if data:
                p = data.get("preview") or data
                prev = store(url, p.get("title") or "", p.get("description") or "", p.get("image_b64"),
                             p.get("image_mime"))
            if prev is None:
                with _lock:
                    _failed_at[url] = time.time()
            else:
                signals.preview_ready.emit(url)
        except Exception as e:
            with _lock:
                _failed_at[url] = time.time()
            print(f"[Advanced WhatsApp LinkPreview] {url}: {e}")
        finally:
            with _lock:
                _queued.discard(url)


def domain_of(url: str) -> str:
    host = urlparse(url).netloc.lower()
    return host[4:] if host.startswith("www.") else host


def card_html(prev: Dict[str, Any], panel: Callable[..., str], colors: Dict[str, str]) -> str:
    """Tarjeta HTML (QTextBrowser) con imagen, título, descripción y dominio; todo el bloque abre el link."""
    from app.thumb_cache import fit_size, thumb_for
    url = prev["url"]
    parts = []
    path = prev.get("image_path")
    if path and os.path.exists(path):
        tpath, w, h = thumb_for(path, max_side=480)
        dw, dh = fit_size(w, h, 280, 160)
        src = "file:///" + os.path.abspath(tpath).replace("\\", "/")
        parts.append(f'<img src="{src}" width="{dw}" height="{dh}" border="0"/><br/>')
    if prev.get("title"):
        parts.append(f'<b>{html.escape(prev["title"][:140])}</b><br/>')
    if prev.get("description"):
        desc = prev["description"]
        parts.append(f'<span style="color: {colors["text_2"]}; font-size: 12px;">'
                     f'{html.escape(desc[:160] + ("…" if len(desc) > 160 else ""))}</span><br/>')
    parts.append(f'<span style="color: {colors["text_2"]}; font-size: 11px;">{html.escape(domain_of(url))}</span>')
    inner = f'<a href="{html.escape(url)}" style="text-decoration: none; color: {colors["text"]};">{"".join(parts)}</a>'
    return panel(inner, accent=colors.get("accent"))
