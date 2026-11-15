"""Galería de un chat: pestañas Fotos y videos, Documentos, Links y Audios.

- Carga paginada (60 por vez, al llegar cerca del final).
- Miniaturas con caché en memoria (LRU) y decodificadas ya reducidas; solo se cargan las que están a la vista.
- La multimedia no se baja al sincronizar: las fotos a la vista se piden a `media_service` y el archivo aparece solo.
"""
import os
from collections import OrderedDict
from datetime import datetime

from PySide6.QtCore import QSize, Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QIcon, QImageReader, QPixmap
from PySide6.QtWidgets import (QDialog, QLabel, QListView, QListWidget, QListWidgetItem, QTabWidget, QVBoxLayout)

from app import media_service
from app import organization as org
from app.ui.icons import icon
from app.ui.theme import C

PAGE = 60
THUMB = 112
THUMB_CACHE_MAX = 400
_thumbs: "OrderedDict[str, QPixmap]" = OrderedDict()   # ruta -> miniatura (compartida entre galerías)

TABS = (("media", "Fotos y videos", "image"), ("docs", "Documentos", "file"),
        ("links", "Links", "link"), ("audio", "Audios", "mic"))


def thumbnail(path: str):
    """Miniatura cuadrada de la imagen (None si no se puede leer). Con caché LRU."""
    pm = _thumbs.get(path)
    if pm is not None:
        _thumbs.move_to_end(path)
        return pm
    reader = QImageReader(path)
    reader.setAutoTransform(True)
    size = reader.size()
    if size.isValid():
        reader.setScaledSize(size.scaled(THUMB * 2, THUMB * 2, Qt.KeepAspectRatioByExpanding))   # decodifica ya chico
    img = reader.read()
    if img.isNull():
        return None
    pm = QPixmap.fromImage(img).scaled(THUMB, THUMB, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
    x, y = (pm.width() - THUMB) // 2, (pm.height() - THUMB) // 2
    pm = pm.copy(x, y, THUMB, THUMB)
    _thumbs[path] = pm
    if len(_thumbs) > THUMB_CACHE_MAX:
        _thumbs.popitem(last=False)
    return pm


def _when(ts: int) -> str:
    try:
        return datetime.fromtimestamp(int(ts)).strftime("%d/%m/%Y %H:%M")
    except (OSError, ValueError, OverflowError):
        return ""


class _Tab(QListWidget):
    """Una pestaña: lista paginada de un tipo de contenido."""

    def __init__(self, chat_jid: str, kind: str, parent=None):
        super().__init__(parent)
        self.chat_jid, self.kind = chat_jid, kind
        self._offset = 0
        self._done = False
        self._items = {}            # id de mensaje -> item
        self.setVerticalScrollMode(QListWidget.ScrollPerPixel)
        self.verticalScrollBar().setSingleStep(20)
        self.setUniformItemSizes(True)
        if kind == "media":
            self.setViewMode(QListView.IconMode)
            self.setResizeMode(QListView.Adjust)
            self.setMovement(QListView.Static)
            self.setIconSize(QSize(THUMB, THUMB))
            self.setGridSize(QSize(THUMB + 12, THUMB + 12))
            self.setSpacing(2)
        self.verticalScrollBar().valueChanged.connect(self._on_scroll)
        self.itemActivated.connect(self._open)
        self._vis_timer = QTimer(self)
        self._vis_timer.setSingleShot(True)
        self._vis_timer.setInterval(120)
        self._vis_timer.timeout.connect(self._load_visible)

    def load_more(self):
        if self._done:
            return
        rows, read = org.gallery_page(self.chat_jid, self.kind, PAGE, self._offset)
        self._offset += read
        if read < PAGE:
            self._done = True
        for r in rows:
            self._add(r)
        self._vis_timer.start()

    def _add(self, r: dict):
        if self.kind == "links":
            it = QListWidgetItem(icon("link"), f"{r['url']}\n{_when(r['timestamp'])}")
            it.setData(Qt.UserRole, {"url": r["url"]})
        elif self.kind == "media":
            it = QListWidgetItem(icon("video" if r["media_type"] == "video" else "image", C["text_3"], 40), "")
            it.setToolTip(_when(r["timestamp"]) + (f"\n{r['text']}" if r.get("text") else ""))
            it.setData(Qt.UserRole, r)
            self._items[r["id"]] = it
        else:
            if self.kind == "audio":
                label = f"Nota de voz · {_when(r['timestamp'])}"
                if r.get("transcript"):
                    label += "\n" + r["transcript"].replace("\n", " ")[:90]
            else:
                label = f"{r.get('text') or 'Documento'}\n{_when(r['timestamp'])}"
            it = QListWidgetItem(icon("mic" if self.kind == "audio" else "file"), label)
            it.setData(Qt.UserRole, r)
            self._items[r["id"]] = it
        self.addItem(it)

    def _on_scroll(self, value):
        sb = self.verticalScrollBar()
        if sb.maximum() > 0 and value >= int(sb.maximum() * 0.8):
            self.load_more()
        self._vis_timer.start()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._vis_timer.start()

    def _visible_items(self):
        rect = self.viewport().rect()
        for i in range(self.count()):
            it = self.item(i)
            r = self.visualItemRect(it)
            if r.bottom() < 0:
                continue
            if r.top() > rect.bottom():
                break
            yield it

    def _load_visible(self):
        """Miniaturas de lo que se ve; lo que todavía no se bajó se pide (una vez) a media_service."""
        if self.kind != "media":
            return
        budget = 24
        for it in self._visible_items():
            r = it.data(Qt.UserRole)
            path = r.get("media_path")
            if path and os.path.exists(path):
                if r["media_type"] == "image" and not it.data(Qt.UserRole + 1):
                    pm = thumbnail(path)
                    if pm is not None:
                        it.setIcon(QIcon(pm))
                    it.setData(Qt.UserRole + 1, True)
                    budget -= 1
            elif r["media_type"] == "image" and not it.data(Qt.UserRole + 2):
                it.setData(Qt.UserRole + 2, True)
                media_service.request_media(r)
            if budget <= 0:
                self._vis_timer.start()
                break

    def media_arrived(self, msg_id: str):
        it = self._items.get(msg_id)
        if it is None:
            return
        from app.db import get_message
        fresh = get_message(msg_id)
        if fresh and fresh.get("media_path"):
            r = {**it.data(Qt.UserRole), "media_path": fresh["media_path"]}
            it.setData(Qt.UserRole, r)
            it.setData(Qt.UserRole + 1, False)
            self._vis_timer.start()

    def _open(self, it: QListWidgetItem):
        r = it.data(Qt.UserRole) or {}
        if r.get("url"):
            QDesktopServices.openUrl(QUrl(r["url"]))
            return
        path = r.get("media_path")
        if path and os.path.exists(path):
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))
        else:
            media_service.request_media(r)   # se baja ahora; al llegar queda disponible para abrir
            it.setToolTip("Descargando…")


class GalleryDialog(QDialog):
    def __init__(self, chat_jid: str, chat_name: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Galería · {chat_name}")
        self.resize(640, 560)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        self.tabs = QTabWidget()
        self._tabs = {}
        for kind, title, ic in TABS:
            tab = _Tab(chat_jid, kind)
            self._tabs[kind] = tab
            self.tabs.addTab(tab, icon(ic), title)
        self.tabs.currentChanged.connect(self._on_tab)
        lay.addWidget(self.tabs)
        hint = QLabel("Doble clic para abrir. Las fotos se descargan al verlas.")
        hint.setStyleSheet(f"color: {C['text_3']}; font-size: 11px;")
        lay.addWidget(hint)
        media_service.media_signals.media_ready.connect(self._on_media_ready)
        self._on_tab(0)

    def _on_tab(self, index: int):
        tab = self.tabs.widget(index)
        if tab.count() == 0 and not tab._done:
            tab.load_more()

    def _on_media_ready(self, msg_id: str):
        self._tabs["media"].media_arrived(msg_id)

    def done(self, r):
        try:
            media_service.media_signals.media_ready.disconnect(self._on_media_ready)
        except (RuntimeError, TypeError):
            pass
        super().done(r)
