"""Selector de emojis y stickers del compositor: un popup con dos pestañas (Emojis | Stickers).

- Emojis: recientes + categorías + buscador. La lista (app/emoji_data.py) se parsea al abrirlo por primera vez.
- Stickers: favoritos, recientes, todos y paquetes propios (app/stickers.py); importación desde archivos,
  carpetas y el historial de chats (en un hilo, el resultado vuelve por señal).
"""
import os
import threading
from typing import List, Optional

from PySide6.QtCore import Qt, Signal, QSize, QTimer, QPoint
from PySide6.QtGui import QIcon, QPixmap, QImage, QFont, QGuiApplication
from PySide6.QtWidgets import (
    QFrame, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QToolButton, QPushButton,
    QComboBox, QMenu, QFileDialog, QInputDialog, QMessageBox, QAbstractItemView
)

from app import emojis, stickers
from app.ui.theme import C, R_MD, R_SM
from app.ui.icons import tool_button, icon

VIEW_RECENTS, VIEW_FAVS, VIEW_ALL = "__recientes__", "__favoritos__", "__todos__"
THUMB = 76
_icon_cache: dict = {}


def thumb_icon(path: str) -> QIcon:
    """Miniatura de un sticker (primer cuadro). Qt lee webp; si falta el plugin se decodifica con Pillow."""
    ic = _icon_cache.get(path)
    if ic is not None:
        return ic
    pm = QPixmap(path)
    if pm.isNull():
        try:
            from PIL import Image
            with Image.open(path) as im:
                im = im.convert("RGBA")
                im.thumbnail((THUMB * 2, THUMB * 2))
                img = QImage(im.tobytes("raw", "RGBA"), im.width, im.height, QImage.Format_RGBA8888).copy()
                pm = QPixmap.fromImage(img)
        except Exception:
            pm = QPixmap()
    if not pm.isNull():
        pm = pm.scaled(THUMB * 2, THUMB * 2, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    ic = QIcon(pm)
    if len(_icon_cache) > 600:
        _icon_cache.clear()
    _icon_cache[path] = ic
    return ic


def _grid(cell: int) -> QListWidget:
    g = QListWidget()
    g.setViewMode(QListWidget.IconMode)
    g.setResizeMode(QListWidget.Adjust)
    g.setMovement(QListWidget.Static)
    g.setWrapping(True)
    g.setUniformItemSizes(True)
    g.setSelectionMode(QAbstractItemView.NoSelection)
    g.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    g.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
    g.setSpacing(2)
    g.setGridSize(QSize(cell, cell))
    g.setFrameShape(QFrame.NoFrame)
    g.setCursor(Qt.PointingHandCursor)
    g.setStyleSheet(f"""
        QListWidget {{ background: transparent; outline: none; border: none; }}
        QListWidget::item {{ border-radius: {R_MD}px; color: {C['text']}; }}
        QListWidget::item:hover {{ background: {C['hover']}; }}
    """)
    return g


class EmojiStickerPanel(QFrame):
    emoji_chosen = Signal(str)
    sticker_chosen = Signal(str, str)        # ruta del webp, id
    _progress = Signal(int, int)
    _import_done = Signal(dict)

    W, H = 380, 400

    def __init__(self, parent=None):
        super().__init__(parent, Qt.Popup | Qt.FramelessWindowHint)
        self.setObjectName("emojiPanel")
        self.setFixedSize(self.W, self.H)
        self.setStyleSheet(f"""
            #emojiPanel {{ background-color: {C['surface']}; border: 1px solid {C['border']}; border-radius: {R_MD}px; }}
            QLineEdit, QComboBox {{ background: {C['raised']}; border: 1px solid {C['border']};
                border-radius: {R_SM}px; color: {C['text']}; padding: 4px 8px; min-height: 22px; }}
            QLineEdit:focus {{ border-color: {C['accent']}; }}
            QLabel#hint {{ color: {C['text_2']}; font-size: 12px; }}
        """)
        self._anchor: Optional[QToolButton] = None
        self._busy = False
        self._gen = 0                       # invalida el llenado de miniaturas al cambiar de vista
        self._pending_thumbs: List = []
        self._cat_index = 0
        self._view = VIEW_RECENTS
        self._built_emojis = False
        self._built_stickers = False

        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        tabs = QHBoxLayout()
        tabs.setSpacing(4)
        self.btn_tab_emoji = self._tab_button("Emojis")
        self.btn_tab_sticker = self._tab_button("Stickers")
        self.btn_tab_emoji.clicked.connect(lambda: self.set_tab(0))
        self.btn_tab_sticker.clicked.connect(lambda: self.set_tab(1))
        tabs.addWidget(self.btn_tab_emoji)
        tabs.addWidget(self.btn_tab_sticker)
        tabs.addStretch()
        root.addLayout(tabs)

        # ── Página de emojis ──
        self.page_emoji = QFrame()
        pe = QVBoxLayout(self.page_emoji)
        pe.setContentsMargins(0, 0, 0, 0)
        pe.setSpacing(6)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Buscar emoji…  (ej: corazón, risa, pizza)")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._on_search)
        pe.addWidget(self.search)
        self.cat_row = QHBoxLayout()
        self.cat_row.setSpacing(0)
        pe.addLayout(self.cat_row)
        self.emoji_grid = _grid(38)
        f = QFont(self.emoji_grid.font())
        f.setPixelSize(22)
        self.emoji_grid.setFont(f)
        self.emoji_grid.itemClicked.connect(self._on_emoji_clicked)
        pe.addWidget(self.emoji_grid, stretch=1)
        self.lbl_emoji_title = QLabel("")
        self.lbl_emoji_title.setObjectName("hint")
        pe.insertWidget(2, self.lbl_emoji_title)
        root.addWidget(self.page_emoji, stretch=1)

        # ── Página de stickers ──
        self.page_sticker = QFrame()
        ps = QVBoxLayout(self.page_sticker)
        ps.setContentsMargins(0, 0, 0, 0)
        ps.setSpacing(6)
        top = QHBoxLayout()
        top.setSpacing(6)
        self.cmb_view = QComboBox()
        self.cmb_view.activated.connect(self._on_view_chosen)
        top.addWidget(self.cmb_view, stretch=1)
        self.btn_more = tool_button("plus", "Importar stickers, crear paquetes…", size=30, icon_size=18)
        self.btn_more.clicked.connect(self._show_more_menu)
        top.addWidget(self.btn_more)
        ps.addLayout(top)
        self.sticker_grid = _grid(THUMB + 12)
        self.sticker_grid.setIconSize(QSize(THUMB, THUMB))
        self.sticker_grid.itemClicked.connect(self._on_sticker_clicked)
        self.sticker_grid.setContextMenuPolicy(Qt.CustomContextMenu)
        self.sticker_grid.customContextMenuRequested.connect(self._sticker_menu)
        ps.addWidget(self.sticker_grid, stretch=1)
        self.lbl_status = QLabel("")
        self.lbl_status.setObjectName("hint")
        self.lbl_status.setWordWrap(True)
        ps.addWidget(self.lbl_status)
        root.addWidget(self.page_sticker, stretch=1)

        self._thumb_timer = QTimer(self)
        self._thumb_timer.setInterval(0)
        self._thumb_timer.timeout.connect(self._fill_thumbs)
        self._progress.connect(self._on_progress)
        self._import_done.connect(self._on_import_done)
        self.set_tab(0)

    # ── Estructura ──
    def _tab_button(self, text: str) -> QPushButton:
        b = QPushButton(text)
        b.setCheckable(True)
        b.setCursor(Qt.PointingHandCursor)
        b.setStyleSheet(f"""
            QPushButton {{ background: transparent; color: {C['text_2']}; border: none; border-radius: {R_SM}px;
                padding: 5px 14px; font-size: 13px; font-weight: 600; }}
            QPushButton:hover {{ background: {C['hover']}; }}
            QPushButton:checked {{ background: {C['selected']}; color: {C['text']}; }}
        """)
        return b

    def set_tab(self, i: int):
        self.btn_tab_emoji.setChecked(i == 0)
        self.btn_tab_sticker.setChecked(i == 1)
        self.page_emoji.setVisible(i == 0)
        self.page_sticker.setVisible(i == 1)
        if i == 0:
            if not self._built_emojis:
                self._build_emojis()
            self.search.setFocus()
        else:
            self._refresh_views()
            self._load_view()

    def show_above(self, anchor: QToolButton, tab: Optional[int] = None):
        """Abre el popup encima del botón (el compositor está abajo de todo)."""
        self._anchor = anchor
        if tab is not None:
            self.set_tab(tab)
        else:
            self._refresh_recents_tab()
        top_left = anchor.mapToGlobal(QPoint(0, 0))
        x, y = top_left.x(), top_left.y() - self.H - 6
        screen = QGuiApplication.screenAt(top_left) or QGuiApplication.primaryScreen()
        geo = screen.availableGeometry()
        x = max(geo.left() + 4, min(x, geo.right() - self.W - 4))
        y = max(geo.top() + 4, y)
        self.move(x, y)
        self.show()
        self.raise_()
        if self.page_emoji.isVisible():
            self.search.setFocus()

    # ── Emojis ──
    def _build_emojis(self):
        self._built_emojis = True
        self._cats = emojis.categories()
        self._cat_buttons: List[QToolButton] = []
        self._recents = stickers.recent_emojis()
        icons = ["🕘"] + [c[1] for c in self._cats]
        for i, ch in enumerate(icons):
            b = QToolButton()
            b.setText(ch)
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.setToolTip("Recientes" if i == 0 else self._cats[i - 1][0])
            b.setFixedHeight(30)
            b.setStyleSheet(f"QToolButton {{ border: none; border-radius: {R_SM}px; font-size: 16px; padding: 0 5px; "
                            f"background: transparent; }} QToolButton:hover {{ background: {C['hover']}; }} "
                            f"QToolButton:checked {{ background: {C['selected']}; }}")
            b.clicked.connect(lambda _=False, k=i: self._show_category(k))
            self.cat_row.addWidget(b)
            self._cat_buttons.append(b)
        self._show_category(0 if self._recents else 1)

    def _refresh_recents_tab(self):
        if self._built_emojis:
            self._recents = stickers.recent_emojis()
            if self._cat_index == 0 and not self.search.text().strip():
                self._show_category(0 if self._recents else 1)

    def _show_category(self, k: int):
        self.search.blockSignals(True)
        self.search.clear()
        self.search.blockSignals(False)
        self._cat_index = k
        for i, b in enumerate(self._cat_buttons):
            b.setChecked(i == k)
        if k == 0:
            self.lbl_emoji_title.setText("Recientes" if self._recents else "Todavía no usaste emojis: elegí una categoría")
            self._fill_emojis(self._recents)
        else:
            self.lbl_emoji_title.setText(self._cats[k - 1][0])
            self._fill_emojis([e for e, _keys in self._cats[k - 1][2]])

    def _on_search(self, text: str):
        text = text.strip()
        if not text:
            self._show_category(self._cat_index or (1 if not self._recents else 0))
            return
        for b in self._cat_buttons:
            b.setChecked(False)
        found = emojis.search(text)
        self.lbl_emoji_title.setText(f"{len(found)} resultado(s)" if found else "Sin resultados")
        self._fill_emojis(found)

    def _fill_emojis(self, items: List[str]):
        g = self.emoji_grid
        g.setUpdatesEnabled(False)
        g.clear()
        for e in items:
            it = QListWidgetItem(e)
            it.setTextAlignment(Qt.AlignCenter)
            it.setSizeHint(QSize(38, 38))
            g.addItem(it)
        g.setUpdatesEnabled(True)
        g.verticalScrollBar().setValue(0)

    def _on_emoji_clicked(self, item: QListWidgetItem):
        e = item.text()
        self._recents = stickers.add_recent_emoji(e)
        self.emoji_chosen.emit(e)

    # ── Stickers ──
    def _refresh_views(self):
        """Rearma el combo (recientes, favoritos, todos y paquetes) conservando la vista elegida."""
        cur = self._view
        self.cmb_view.blockSignals(True)
        self.cmb_view.clear()
        self.cmb_view.addItem("🕘 Recientes", VIEW_RECENTS)
        self.cmb_view.addItem("⭐ Favoritos", VIEW_FAVS)
        self.cmb_view.addItem("Todos", VIEW_ALL)
        for p in stickers.packs():
            self.cmb_view.addItem(f"{p['name']}  ({p['count']})", p["name"])
        i = self.cmb_view.findData(cur)
        if i < 0:
            i, self._view = 0, VIEW_RECENTS
        self.cmb_view.setCurrentIndex(i)
        self.cmb_view.blockSignals(False)

    def _on_view_chosen(self, i: int):
        self._view = self.cmb_view.itemData(i)
        self._load_view()

    def _view_records(self) -> List[dict]:
        if self._view == VIEW_RECENTS:
            return stickers.recents()
        if self._view == VIEW_FAVS:
            return stickers.favorites()
        if self._view == VIEW_ALL:
            return stickers.list_stickers()
        return stickers.list_stickers(self._view)

    def _load_view(self):
        recs = self._view_records()
        self._gen += 1
        self._thumb_timer.stop()
        g = self.sticker_grid
        g.setUpdatesEnabled(False)
        g.clear()
        pending = []
        for r in recs:
            it = QListWidgetItem()
            it.setData(Qt.UserRole, r["id"])
            it.setData(Qt.UserRole + 1, stickers.sticker_path(r))
            it.setSizeHint(QSize(THUMB + 10, THUMB + 10))
            g.addItem(it)
            pending.append(it)
        g.setUpdatesEnabled(True)
        g.verticalScrollBar().setValue(0)
        self._pending_thumbs = pending
        if pending:
            self._thumb_timer.start()
        if not recs and not self._busy:
            if stickers.count() == 0:
                self._set_status("Todavía no tenés stickers. Con el botón + podés importar los de tus chats o archivos.")
            else:
                self._set_status({VIEW_RECENTS: "Los stickers que envíes aparecen acá.",
                                  VIEW_FAVS: "Marcá stickers como favoritos con clic derecho."}.get(self._view, "Este paquete está vacío."))
        elif not self._busy:
            self._set_status("")

    def _fill_thumbs(self):
        """Pone las miniaturas de a tandas para que abrir el panel con cientos de stickers no congele la UI."""
        gen = self._gen
        for _ in range(16):
            if not self._pending_thumbs or gen != self._gen:
                break
            it = self._pending_thumbs.pop(0)
            try:
                it.setIcon(thumb_icon(it.data(Qt.UserRole + 1)))
            except RuntimeError:
                break           # el item ya fue destruido por un clear()
        if not self._pending_thumbs or gen != self._gen:
            self._thumb_timer.stop()

    def _set_status(self, text: str):
        self.lbl_status.setText(text)
        self.lbl_status.setVisible(bool(text))

    def _on_sticker_clicked(self, item: QListWidgetItem):
        self.sticker_chosen.emit(item.data(Qt.UserRole + 1), item.data(Qt.UserRole))
        self.hide()

    def _sticker_menu(self, pos):
        item = self.sticker_grid.itemAt(pos)
        if not item:
            return
        sid = item.data(Qt.UserRole)
        rec = next((r for r in stickers.list_stickers() if r["id"] == sid), None)
        if not rec:
            return
        menu = QMenu(self)
        act_fav = menu.addAction(icon("star"),
                                 "Quitar de favoritos" if rec.get("fav") else "Agregar a favoritos")
        move = menu.addMenu("Mover a paquete")
        for p in stickers.packs():
            if p["name"] != rec.get("pack"):
                move.addAction(p["name"]).setData(p["name"])
        move.addSeparator()
        new_pack = move.addAction("Nuevo paquete…")
        menu.addSeparator()
        act_del = menu.addAction(icon("trash", C["danger"]), "Eliminar de mis stickers")
        chosen = menu.exec(self.sticker_grid.viewport().mapToGlobal(pos))
        if chosen is None:
            return
        if chosen is act_fav:
            stickers.toggle_fav(sid)
        elif chosen is act_del:
            stickers.delete_sticker(sid)
        elif chosen is new_pack:
            name, ok = QInputDialog.getText(self, "Nuevo paquete", "Nombre del paquete:")
            if ok and name.strip():
                stickers.create_pack(name)
                stickers.move_to_pack(sid, name.strip())
        elif chosen.data():
            stickers.move_to_pack(sid, chosen.data())
        self._refresh_views()
        self._load_view()

    # ── Importar ──
    def _show_more_menu(self):
        menu = QMenu(self)
        a_files = menu.addAction(icon("image"), "Importar archivos…  (.webp .png .jpg .wastickers)")
        a_dir = menu.addAction(icon("folder"), "Importar una carpeta de imágenes…")
        a_chats = menu.addAction(icon("download"), "Importar stickers de mis chats")
        menu.addSeparator()
        a_new = menu.addAction(icon("plus"), "Nuevo paquete…")
        a_del = None
        if self._view not in (VIEW_RECENTS, VIEW_FAVS, VIEW_ALL):
            a_del = menu.addAction(icon("trash", C["danger"]), "Eliminar este paquete")
        chosen = menu.exec(self.btn_more.mapToGlobal(QPoint(0, self.btn_more.height())))
        if chosen is None:
            return
        if chosen is a_files:
            self._import_from_dialog(folder=False)
        elif chosen is a_dir:
            self._import_from_dialog(folder=True)
        elif chosen is a_chats:
            self._start_import(lambda cb: stickers.import_from_chats(progress=cb), "Buscando stickers en tus chats…")
        elif chosen is a_new:
            name, ok = QInputDialog.getText(self, "Nuevo paquete", "Nombre del paquete:")
            if ok and name.strip():
                if stickers.create_pack(name):
                    self._view = name.strip()[:60]
                self._refresh_views()
                self._load_view()
        elif a_del is not None and chosen is a_del:
            ans = QMessageBox.question(self, "Eliminar paquete",
                                       f"¿Eliminar «{self._view}» y todos sus stickers? No se puede deshacer.")
            if ans == QMessageBox.Yes:
                stickers.delete_pack(self._view)
                self._view = VIEW_RECENTS
                self._refresh_views()
                self._load_view()

    def _import_from_dialog(self, folder: bool):
        parent = self.parentWidget() or None
        self.hide()      # el diálogo es modal: cerramos el popup y lo reabrimos para ver el avance
        if folder:
            d = QFileDialog.getExistingDirectory(parent, "Carpeta con stickers o imágenes")
            paths = [d] if d else []
        else:
            paths, _ = QFileDialog.getOpenFileNames(
                parent, "Importar stickers", "",
                "Stickers e imágenes (*.webp *.png *.jpg *.jpeg *.gif *.wastickers);;Todos los archivos (*.*)")
        if self._anchor is not None:
            self.show_above(self._anchor, tab=1)
        if paths:
            self._start_import(lambda cb: stickers.add_files(paths, progress=cb), "Importando…")

    def _start_import(self, job, message: str):
        if self._busy:
            return
        self._busy = True
        self.btn_more.setEnabled(False)
        self._set_status(message)

        def work():
            try:
                res = job(lambda done, total: self._progress.emit(done, total))
            except Exception as e:
                res = {"error": str(e)}
            self._import_done.emit(res)

        threading.Thread(target=work, daemon=True, name="advancedwhatsapp-stickers").start()

    def _on_progress(self, done: int, total: int):
        if self._busy:
            self._set_status(f"Importando… {done} de {total}")

    def _on_import_done(self, res: dict):
        self._busy = False
        self.btn_more.setEnabled(True)
        self._refresh_views()
        self._load_view()
        if res.get("error"):
            self._set_status(f"No se pudo importar: {res['error']}")
            return
        parts = [f"{res.get('added', 0)} agregado(s)"]
        if res.get("duplicates"):
            parts.append(f"{res['duplicates']} repetido(s)")
        if res.get("unavailable"):
            parts.append(f"{res['unavailable']} ya no disponible(s) en WhatsApp")
        if res.get("failed"):
            parts.append(f"{res['failed']} con error")
        self._set_status("Listo: " + ", ".join(parts) + ".")
