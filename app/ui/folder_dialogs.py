"""Carpetas inteligentes: administrador (crear, editar, ordenar, borrar) y editor de reglas."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QLabel,
                               QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPushButton, QVBoxLayout)

from app import organization as org
from app import tags as tagdb
from app.db import get_chats
from app.ui.icons import icon
from app.ui.theme import C


def _chat_label(chat: dict) -> str:
    from app.ui.chat_list import resolve_display_name
    return resolve_display_name(chat)


class FolderEditDialog(QDialog):
    """Nombre y reglas de una carpeta."""

    def __init__(self, folder: dict = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Editar carpeta" if folder else "Nueva carpeta")
        self.setMinimumWidth(420)
        rules = org.normalize_rules((folder or {}).get("rules"))
        lay = QVBoxLayout(self)
        form = QFormLayout()
        self.txt_name = QLineEdit((folder or {}).get("name", ""))
        self.txt_name.setPlaceholderText("Ej.: Clientes sin responder")
        form.addRow("Nombre", self.txt_name)

        self.cmb_kind = QComboBox()
        for key, label in org.KIND_LABELS.items():
            self.cmb_kind.addItem(label, key)
        self.cmb_kind.setCurrentIndex(max(0, self.cmb_kind.findData(rules["kind"])))
        form.addRow("Tipo de chat", self.cmb_kind)

        self.cmb_muted = QComboBox()
        for key, label in org.MUTED_LABELS.items():
            self.cmb_muted.addItem(label, key)
        self.cmb_muted.setCurrentIndex(max(0, self.cmb_muted.findData(rules["muted"])))
        form.addRow("Notificaciones", self.cmb_muted)

        self.chk_unread = QCheckBox("Solo chats con mensajes sin leer")
        self.chk_unread.setChecked(rules["unread"])
        form.addRow("", self.chk_unread)
        lay.addLayout(form)

        lay.addWidget(QLabel("Tags (alcanza con que tenga uno de los tildados):"))
        self.lst_tags = QListWidget()
        self.lst_tags.setMaximumHeight(110)
        for t in tagdb.list_tags():
            it = QListWidgetItem(t["name"])
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Checked if t["id"] in rules["tags"] else Qt.Unchecked)
            it.setData(Qt.UserRole, t["id"])
            self.lst_tags.addItem(it)
        lay.addWidget(self.lst_tags)

        lay.addWidget(QLabel("Contactos elegidos (entran siempre, aunque no cumplan lo demás):"))
        self.txt_find = QLineEdit()
        self.txt_find.setPlaceholderText("Buscar contacto…")
        self.txt_find.textChanged.connect(self._filter_contacts)
        lay.addWidget(self.txt_find)
        self.lst_chats = QListWidget()
        self.lst_chats.setMinimumHeight(150)
        picked = set(rules["jids"])
        seen = set()
        for chat in get_chats(limit=600, include_hidden=False) + get_chats(limit=300, groups_only=True):
            jid = chat["jid"]
            if jid in seen:
                continue
            seen.add(jid)
            it = QListWidgetItem(_chat_label(chat))
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Checked if jid in picked else Qt.Unchecked)
            it.setData(Qt.UserRole, jid)
            self.lst_chats.addItem(it)
        lay.addWidget(self.lst_chats)

        box = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        box.button(QDialogButtonBox.Save).setText("Guardar")
        box.button(QDialogButtonBox.Cancel).setText("Cancelar")
        box.accepted.connect(self._accept)
        box.rejected.connect(self.reject)
        lay.addWidget(box)

    def _filter_contacts(self, text: str):
        term = text.strip().lower()
        for i in range(self.lst_chats.count()):
            it = self.lst_chats.item(i)
            it.setHidden(bool(term) and term not in it.text().lower() and it.checkState() != Qt.Checked)

    def rules(self) -> dict:
        def checked(lst):
            return [lst.item(i).data(Qt.UserRole) for i in range(lst.count()) if lst.item(i).checkState() == Qt.Checked]
        return org.normalize_rules({
            "tags": checked(self.lst_tags), "kind": self.cmb_kind.currentData(), "muted": self.cmb_muted.currentData(),
            "unread": self.chk_unread.isChecked(), "jids": checked(self.lst_chats)})

    def name(self) -> str:
        return self.txt_name.text().strip()

    def _accept(self):
        if not self.name():
            QMessageBox.warning(self, "Carpeta", "Ponele un nombre a la carpeta.")
            return
        if org.rules_are_empty(self.rules()):
            QMessageBox.warning(self, "Carpeta", "Elegí al menos una regla (tag, tipo, sin leer, silenciados o contactos).")
            return
        self.accept()


class FolderManagerDialog(QDialog):
    """Lista de carpetas con alta, edición, borrado y orden."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Carpetas inteligentes")
        self.setMinimumSize(460, 380)
        lay = QVBoxLayout(self)
        hint = QLabel("Las carpetas se arman solas con reglas y aparecen como pestañas en la lista de chats.")
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color: {C['text_2']}; font-size: 12px;")
        lay.addWidget(hint)

        body = QHBoxLayout()
        self.lst = QListWidget()
        self.lst.itemDoubleClicked.connect(lambda _it: self._edit())
        body.addWidget(self.lst, stretch=1)
        col = QVBoxLayout()
        for text, ic, fn in (("Nueva", "plus", self._new), ("Editar", "edit", self._edit), ("Borrar", "trash", self._delete),
                             ("Subir", "up", lambda: self._move(-1)), ("Bajar", "down", lambda: self._move(1))):
            b = QPushButton(icon(ic), " " + text)
            b.clicked.connect(fn)
            col.addWidget(b)
        col.addStretch(1)
        body.addLayout(col)
        lay.addLayout(body)
        close = QPushButton("Cerrar")
        close.clicked.connect(self.accept)
        lay.addWidget(close, alignment=Qt.AlignRight)
        self._reload()

    def _reload(self, select_id: int = 0):
        names = {t["id"]: t["name"] for t in tagdb.list_tags()}
        self.lst.clear()
        for f in org.list_folders():
            it = QListWidgetItem(f"{f['name']}\n{org.describe_rules(f['rules'], names)}")
            it.setData(Qt.UserRole, f["id"])
            self.lst.addItem(it)
            if f["id"] == select_id:
                self.lst.setCurrentItem(it)

    def _current_id(self) -> int:
        it = self.lst.currentItem()
        return it.data(Qt.UserRole) if it else 0

    def _new(self):
        dlg = FolderEditDialog(parent=self)
        if dlg.exec():
            self._reload(org.create_folder(dlg.name(), dlg.rules()))

    def _edit(self):
        fid = self._current_id()
        folder = org.get_folder(fid) if fid else None
        if not folder:
            return
        dlg = FolderEditDialog(folder, parent=self)
        if dlg.exec():
            org.update_folder(fid, name=dlg.name(), rules=dlg.rules())
            self._reload(fid)

    def _delete(self):
        fid = self._current_id()
        folder = org.get_folder(fid) if fid else None
        if folder and QMessageBox.question(self, "Borrar carpeta", f"¿Borrar la carpeta «{folder['name']}»?\n"
                                           "Los chats no se tocan.") == QMessageBox.Yes:
            org.delete_folder(fid)
            self._reload()

    def _move(self, delta: int):
        fid = self._current_id()
        ids = [f["id"] for f in org.list_folders()]
        if fid not in ids:
            return
        i = ids.index(fid)
        j = i + delta
        if 0 <= j < len(ids):
            ids[i], ids[j] = ids[j], ids[i]
            org.reorder_folders(ids)
            self._reload(fid)
