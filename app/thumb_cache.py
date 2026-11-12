"""
Advanced WhatsApp - Caché de miniaturas
==========================================
Dos niveles:
  - Disco: la foto original (12 MP, varios MB) se reduce una vez a un JPEG chico en ~/.magsapp/thumbs y el chat usa ese
    en vez de decodificar la original cada vez que se redibuja. Clave = ruta + fecha + tamaño del archivo, así que
    si el archivo cambia se regenera sola. Se poda por tamaño (los menos usados primero).
  - Memoria: LRU de QPixmap ya escalados (avatares de la lista de chats), para no volver a decodificar al recargar.
Se puede usar desde cualquier hilo salvo `scaled_pixmap` (QPixmap solo en el hilo de la UI).
"""

import hashlib
import os
import threading
from collections import OrderedDict
from typing import Any, Callable, Dict, Optional, Tuple

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QImage, QImageReader, QPixmap

from app.config import DATA_DIR

THUMBS_DIR = os.path.join(DATA_DIR, "thumbs")
THUMB_MAX_SIDE = 640          # px del lado mayor (se muestra a ~300 px lógicos, con margen para pantallas HiDPI)
THUMB_QUALITY = 82
DISK_LIMIT_BYTES = 300 * 1024 * 1024


class LruCache:
    """LRU simple con tope por cantidad de entradas (seguro entre hilos)."""

    def __init__(self, max_items: int = 256):
        self.max_items = max_items
        self._data: "OrderedDict[Any, Any]" = OrderedDict()
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0

    def get(self, key, default=None):
        with self._lock:
            if key in self._data:
                self._data.move_to_end(key)
                self.hits += 1
                return self._data[key]
            self.misses += 1
            return default

    def put(self, key, value) -> None:
        with self._lock:
            self._data[key] = value
            self._data.move_to_end(key)
            while len(self._data) > self.max_items:
                self._data.popitem(last=False)

    def get_or_create(self, key, factory: Callable[[], Any]):
        val = self.get(key)
        if val is None:
            val = factory()
            if val is not None:
                self.put(key, val)
        return val

    def clear(self) -> None:
        with self._lock:
            self._data.clear()

    def __len__(self) -> int:
        return len(self._data)


_dims = LruCache(2000)        # ruta de miniatura -> (ancho, alto)
_pixmaps = LruCache(400)      # (ruta, lado, ratio) -> QPixmap


def _source_key(path: str, max_side: int) -> Optional[str]:
    try:
        st = os.stat(path)
    except OSError:
        return None
    raw = f"{os.path.abspath(path)}|{st.st_mtime_ns}|{st.st_size}|{max_side}"
    return hashlib.sha1(raw.encode("utf-8", "replace")).hexdigest()


def thumb_for(path: str, max_side: int = THUMB_MAX_SIDE, thumbs_dir: str = THUMBS_DIR) -> Tuple[str, int, int]:
    """(ruta de la miniatura, ancho, alto). Si no se puede reducir (formato raro, archivo roto) devuelve la original
    con 0, 0. La primera vez decodifica ya reducido (QImageReader.setScaledSize: en JPEG usa el escalado del decodificador,
    mucho más rápido y liviano que cargar la imagen entera)."""
    key = _source_key(path, max_side)
    if key is None:
        return path, 0, 0
    thumb = os.path.join(thumbs_dir, key[:2], key + ".jpg")
    hit = _dims.get(thumb)
    if hit is not None and os.path.exists(thumb):
        return thumb, hit[0], hit[1]
    if os.path.exists(thumb):
        size = QImageReader(thumb).size()
        if size.isValid():
            _dims.put(thumb, (size.width(), size.height()))
            try:
                os.utime(thumb)    # «usada hace poco» para la poda
            except OSError:
                pass
            return thumb, size.width(), size.height()
    reader = QImageReader(path)
    reader.setAutoTransform(True)
    src = reader.size()
    if not src.isValid() or src.width() <= 0 or src.height() <= 0:
        return path, 0, 0
    if max(src.width(), src.height()) > max_side:
        scale = max_side / max(src.width(), src.height())
        reader.setScaledSize(QSize(max(1, round(src.width() * scale)), max(1, round(src.height() * scale))))
    img = reader.read()
    if img.isNull():
        return path, 0, 0
    try:
        os.makedirs(os.path.dirname(thumb), exist_ok=True)
        tmp = thumb + ".tmp"
        if not img.save(tmp, "JPG", THUMB_QUALITY):
            return path, 0, 0
        os.replace(tmp, thumb)
    except OSError:
        return path, 0, 0
    _dims.put(thumb, (img.width(), img.height()))
    return thumb, img.width(), img.height()


def fit_size(w: int, h: int, max_w: int = 300, max_h: int = 240) -> Tuple[int, int]:
    """Tamaño a mostrar (en px lógicos) respetando la proporción y sin agrandar."""
    if w <= 0 or h <= 0:
        return max_w, max_h
    scale = min(max_w / w, max_h / h, 1.0)
    return max(1, round(w * scale)), max(1, round(h * scale))


def scaled_pixmap(path: str, side_px: int, ratio: float = 1.0) -> Optional[QPixmap]:
    """Avatar cuadrado recortado al centro, en caché de memoria (sólo hilo de la UI)."""
    if not path:
        return None
    try:
        mtime = os.stat(path).st_mtime_ns
    except OSError:
        return None
    key = (path, mtime, side_px, round(ratio, 2))
    cached = _pixmaps.get(key)
    if cached is not None:
        return cached
    pix = QPixmap(path)
    if pix.isNull():
        return None
    side = round(side_px * ratio)
    scaled = pix.scaled(side, side, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
    scaled.setDevicePixelRatio(ratio)
    _pixmaps.put(key, scaled)
    return scaled


def forget_pixmap(path: str) -> None:
    """Descarta las versiones en memoria de un archivo (la clave ya incluye la fecha, esto solo libera memoria)."""
    with _pixmaps._lock:
        for k in [k for k in _pixmaps._data if k[0] == path]:
            del _pixmaps._data[k]


def prune_disk(limit_bytes: int = DISK_LIMIT_BYTES, thumbs_dir: str = THUMBS_DIR) -> int:
    """Borra las miniaturas menos usadas hasta quedar bajo `limit_bytes`. Devuelve cuántas borró."""
    files = []
    total = 0
    for root, _dirs, names in os.walk(thumbs_dir):
        for n in names:
            p = os.path.join(root, n)
            try:
                st = os.stat(p)
            except OSError:
                continue
            files.append((st.st_mtime, st.st_size, p))
            total += st.st_size
    removed = 0
    if total <= limit_bytes:
        return 0
    for _mt, size, p in sorted(files):
        try:
            os.remove(p)
        except OSError:
            continue
        _dims.clear()
        total -= size
        removed += 1
        if total <= limit_bytes * 0.8:
            break
    return removed


def disk_usage(thumbs_dir: str = THUMBS_DIR) -> int:
    total = 0
    for root, _dirs, names in os.walk(thumbs_dir):
        for n in names:
            try:
                total += os.path.getsize(os.path.join(root, n))
            except OSError:
                pass
    return total
