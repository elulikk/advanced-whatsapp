"""
Advanced WhatsApp - Atajos de teclado
=========================================
Todos los atajos de la ventana principal en un solo lugar (la lista que muestra Ctrl+/ sale de la misma tabla que los
instala, así nunca quedan desincronizadas). Las acciones sobre «el chat» usan el chat abierto, o el seleccionado en la lista.
"""

import time
from typing import Callable, List, Optional, Tuple

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import QDialog, QHeaderView, QLabel, QTableWidget, QTableWidgetItem, QVBoxLayout

from app.db import db_conn, get_chat, get_chats, is_chat_muted, mark_chat_as_read, set_chat_muted
from app.gateway_manager import GatewayManager
from app.ui.theme import C

MUTE_SECONDS = 8 * 3600

# (atajo, descripción, ¿lo instala esta tabla?) — los que ya existían en main_window figuran para que se vean en la lista
LISTED: List[Tuple[str, str]] = [
    ("Ctrl+/", "Mostrar esta lista de atajos"),
    ("Ctrl+1 / Ctrl+2 / Ctrl+,", "Chats / Estados / Configuración"),
    ("Ctrl+N", "Chat nuevo"),
    ("Ctrl+K", "Buscar un chat"),
    ("Ctrl+F", "Buscar dentro del chat abierto"),
    ("Ctrl+Shift+R", "Marcar el chat como leído"),
    ("Ctrl+Shift+M", "Silenciar 8 horas / reactivar notificaciones"),
    ("Ctrl+Shift+U", "Saltar al siguiente chat sin leer"),
    ("Alt+↓ / Alt+↑", "Chat siguiente / anterior de la lista"),
    ("Ctrl+E", "Archivar el chat"),
    ("Ctrl+Shift+P", "Fijar / desfijar el chat"),
    ("Ctrl+Shift+E", "Estadísticas"),
    ("Ctrl+Q", "Salir"),
]


class ShortcutsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Atajos")
        self.resize(480, 420)
        self.setStyleSheet(f"QDialog {{ background: {C['panel']}; }}")
        lay = QVBoxLayout(self)
        title = QLabel("Atajos de teclado")
        title.setStyleSheet(f"font-size: 16px; font-weight: 600; color: {C['text']};")
        lay.addWidget(title)
        table = QTableWidget(len(LISTED), 2)
        table.setHorizontalHeaderLabels(["Atajo", "Qué hace"])
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QTableWidget.NoEditTriggers)
        table.setSelectionMode(QTableWidget.NoSelection)
        table.setShowGrid(False)
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        for i, (keys, desc) in enumerate(LISTED):
            k = QTableWidgetItem(keys)
            k.setForeground(Qt.white)
            table.setItem(i, 0, k)
            table.setItem(i, 1, QTableWidgetItem(desc))
        lay.addWidget(table)


class Shortcuts:
    """Instala los atajos nuevos en la ventana principal."""

    def __init__(self, window):
        self.w = window
        self._dialog: Optional[ShortcutsDialog] = None

        def sc(keys: str, fn: Callable):
            QShortcut(QKeySequence(keys), window, activated=fn)

        sc("Ctrl+/", self.show_help)
        sc("Ctrl+Shift+R", self.mark_read)
        sc("Ctrl+Shift+M", self.toggle_mute)
        sc("Ctrl+Shift+U", self.next_unread)
        sc("Alt+Down", lambda: self.step_chat(+1))
        sc("Alt+Up", lambda: self.step_chat(-1))
        sc("Ctrl+E", self.archive)
        sc("Ctrl+Shift+P", self.toggle_pin)
        sc("Ctrl+Shift+E", self.show_stats)

    # ── Ayudas ──
    def show_help(self):
        if self._dialog is None or not self._dialog.isVisible():
            self._dialog = ShortcutsDialog(self.w)
            self._dialog.show()
        else:
            self._dialog.close()      # el mismo atajo la cierra

    def show_stats(self):
        from app.ui.stats_dialog import StatsDialog
        StatsDialog(self.w).exec()

    # ── Chat actual ──
    def _jid(self) -> Optional[str]:
        """Chat abierto, o el seleccionado en la lista si no hay ninguno abierto."""
        if not self._chats_visible():
            return None
        cur = self.w.chat_view_panel.current_chat
        return cur.get("jid") if cur else self.w.chat_list_panel._current_jid()

    def _chats_visible(self) -> bool:
        return self.w.stacked_widget.currentWidget() is self.w.splitter

    def _reload(self):
        self.w.chat_list_panel.reload_chats(select_jid=self.w._current_jid_open())

    def mark_read(self):
        jid = self._jid()
        if not jid:
            return
        GatewayManager.get_instance().send_read_receipts(jid, [mid for mid, _ in mark_chat_as_read(jid)])
        self._reload()
        self.w.chat_list_panel.chat_marked_read.emit(jid)

    def toggle_mute(self):
        jid = self._jid()
        if not jid:
            return
        chat = get_chat(jid)
        set_chat_muted(jid, 0 if is_chat_muted(chat) else int(time.time()) + MUTE_SECONDS)
        self._reload()
        self.w.chat_list_panel.chat_marked_read.emit(jid)

    def toggle_pin(self):
        jid = self._jid()
        if not jid:
            return
        with db_conn() as conn:
            conn.execute("UPDATE chats SET is_pinned = 1 - COALESCE(is_pinned, 0) WHERE jid = ?", (jid,))
        self._reload()

    def archive(self):
        jid = self._jid()
        if not jid:
            return
        try:
            from app.organization import set_archived     # la pestaña de organización, si está en esta versión
            set_archived(jid, True)
        except ImportError:
            with db_conn() as conn:
                conn.execute("UPDATE chats SET is_archived = 1 WHERE jid = ?", (jid,))
        if self.w._current_jid_open() == jid:
            self.w.chat_view_panel.show_empty_state()
        self.w.chat_list_panel.reload_chats()

    # ── Navegación ──
    def step_chat(self, delta: int):
        if not self._chats_visible():
            return
        lw = self.w.chat_list_panel.list_widget
        n = lw.count()
        if not n:
            return
        cur = lw.currentRow()
        row = max(0, min(n - 1, (cur if cur >= 0 else (-1 if delta > 0 else n)) + delta))
        item = lw.item(row)
        if item is not None and item.data(Qt.UserRole):
            lw.setCurrentRow(row)
            self.w._on_chat_selected(item.data(Qt.UserRole))

    def next_unread(self):
        """Salta al siguiente chat con mensajes sin leer, después del actual (y vuelve al principio si no hay más abajo)."""
        if not self._chats_visible():
            return
        current = self.w._current_jid_open()
        unread = get_chats(filter_unread=True, limit=200)
        unread = [c for c in unread if c.get("jid") != current]
        if not unread:
            return
        # Orden de la lista: el siguiente según la posición del actual; si no está en la lista de no leídos, el primero
        target = unread[0]
        try:
            order = [c["jid"] for c in get_chats(limit=500)]
            pos = order.index(current) if current in order else -1
            after = [c for c in unread if c["jid"] in order and order.index(c["jid"]) > pos]
            if after:
                target = after[0]
        except Exception:
            pass
        self.w._on_chat_selected(target)
