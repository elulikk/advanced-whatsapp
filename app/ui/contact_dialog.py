from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QGridLayout,
    QPushButton, QListWidget, QListWidgetItem, QFrame, QMessageBox, QWidget, QGroupBox
)
from PySide6.QtCore import Qt, Signal, QSize

from app.db import save_or_update_contact, get_all_contacts, get_or_create_chat
from app.gateway_manager import GatewayManager
from app.ui.chat_list import AvatarWidget, ElidedLabel
from app.ui.theme import C
from app.ui.icons import icon


def _is_placeholder_name(name: str) -> bool:
    return (name or "").startswith(("Usuario (", "Contacto ("))


class ContactEditDialog(QDialog):
    """Diálogo para agregar o editar un contacto."""
    contact_saved = Signal(dict)

    def __init__(self, chat_data: dict, parent=None):
        super().__init__(parent)
        self.chat_data = chat_data or {}
        self.setWindowTitle("Guardar o editar contacto")
        self.setFixedWidth(400)
        self.setModal(True)
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(12)

        header_layout = QHBoxLayout()
        header_layout.setSpacing(12)
        raw_name = self.chat_data.get("name", "") or ""
        avatar = AvatarWidget("?" if _is_placeholder_name(raw_name) else raw_name, size=40,
                              avatar_path=self.chat_data.get("avatar_path"))
        header_layout.addWidget(avatar)
        title_vbox = QVBoxLayout()
        title_vbox.setSpacing(0)
        title_lbl = QLabel("Datos del contacto")
        title_lbl.setStyleSheet("font-size: 15px; font-weight: 600;")
        title_vbox.addWidget(title_lbl)
        sub_lbl = QLabel(self.chat_data.get("jid", ""))
        sub_lbl.setObjectName("hint")
        sub_lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
        title_vbox.addWidget(sub_lbl)
        header_layout.addLayout(title_vbox, stretch=1)
        layout.addLayout(header_layout)

        sep = QFrame()
        sep.setObjectName("hline")
        layout.addWidget(sep)

        form = QGridLayout()
        form.setHorizontalSpacing(10)
        form.setVerticalSpacing(8)
        form.addWidget(QLabel("Nombre:"), 0, 0)
        self.txt_name = QLineEdit()
        self.txt_name.setPlaceholderText("Ej: Juan Pérez / Cliente HP")
        # Un nombre generado como "Usuario (1346)" se deja en blanco para poner el real
        self.txt_name.setText("" if _is_placeholder_name(raw_name) else raw_name)
        form.addWidget(self.txt_name, 0, 1)
        form.addWidget(QLabel("Teléfono:"), 1, 0)
        current_phone = self.chat_data.get("phone", "") or ""
        self.txt_phone = QLineEdit()
        self.txt_phone.setPlaceholderText("Opcional · ej: 5492944123456")
        self.txt_phone.setText(str(current_phone))
        form.addWidget(self.txt_phone, 1, 1)
        layout.addLayout(form)

        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(8)
        btn_layout.addStretch()
        self.btn_cancel = QPushButton("Cancelar")
        self.btn_cancel.setCursor(Qt.PointingHandCursor)
        self.btn_cancel.clicked.connect(self.reject)
        btn_layout.addWidget(self.btn_cancel)
        self.btn_save = QPushButton("Guardar")
        self.btn_save.setObjectName("btnSave")
        self.btn_save.setDefault(True)
        self.btn_save.setCursor(Qt.PointingHandCursor)
        self.btn_save.clicked.connect(self._on_save)
        btn_layout.addWidget(self.btn_save)
        layout.addLayout(btn_layout)

        self.txt_name.returnPressed.connect(self._on_save)
        self.txt_phone.returnPressed.connect(self._on_save)
        self.txt_name.setFocus()

    def _on_save(self):
        name = self.txt_name.text().strip()
        phone = self.txt_phone.text().strip()

        if not name:
            QMessageBox.warning(self, "Nombre requerido", "Ingresa un nombre para el contacto.")
            self.txt_name.setFocus()
            return

        jid = self.chat_data.get("jid")
        if not jid:
            QMessageBox.critical(self, "Error", "No se encontró el identificador del chat.")
            return

        # 1. Guardar en SQLite
        save_or_update_contact(jid, name=name, phone=phone if phone else None)

        # 2. Notificar al gateway
        try:
            GatewayManager.get_instance().update_contact(jid, name=name, phone=phone if phone else None)
        except Exception:
            pass

        self.chat_data["name"] = name
        self.chat_data["custom_name"] = name   # prevalece sobre el nombre que mande WhatsApp
        if phone:
            self.chat_data["phone"] = phone

        self.contact_saved.emit(self.chat_data)
        self.accept()


class ContactListItemWidget(QWidget):
    """Fila de un contacto en el listado de Nuevo chat."""
    def __init__(self, contact: dict, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 4, 10, 4)
        layout.setSpacing(10)

        name = contact.get("name") or "Sin nombre"
        phone = contact.get("phone") or ""
        layout.addWidget(AvatarWidget(name, size=30, avatar_path=contact.get("avatar_path")))

        lbl_name = ElidedLabel(name)
        lbl_name.setStyleSheet(f"font-size: 13px; color: {C['text']};")
        layout.addWidget(lbl_name, stretch=1)
        if phone:
            lbl_phone = QLabel(f"+{phone}")
            lbl_phone.setObjectName("subtle")
            layout.addWidget(lbl_phone)


class NewChatDialog(QDialog):
    """
    Diálogo para iniciar un chat nuevo:
    1. Escribiendo un número directo (con nombre opcional).
    2. Eligiendo un contacto guardado de la agenda.
    """
    chat_ready = Signal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Nuevo chat")
        self.resize(460, 560)
        self.setModal(True)
        self.selected_chat = None
        self._all_contacts = []

        self._init_ui()
        self._load_contacts()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 14, 18, 16)
        layout.setSpacing(10)

        group = QGroupBox("Número directo")
        g = QGridLayout(group)
        g.setHorizontalSpacing(8)
        g.setVerticalSpacing(8)
        self.txt_direct_phone = QLineEdit()
        self.txt_direct_phone.setPlaceholderText("Teléfono con código de país")
        self.txt_direct_phone.setToolTip("Ej: 5491122334455 (54 = Argentina, 9 = celular)")
        self.txt_direct_phone.returnPressed.connect(self._on_direct_chat_clicked)
        g.addWidget(self.txt_direct_phone, 0, 0)
        self.txt_direct_name = QLineEdit()
        self.txt_direct_name.setPlaceholderText("Nombre (opcional, lo guarda como contacto)")
        self.txt_direct_name.returnPressed.connect(self._on_direct_chat_clicked)
        g.addWidget(self.txt_direct_name, 1, 0)
        self.btn_direct_chat = QPushButton("Chatear")
        self.btn_direct_chat.setObjectName("btnDirectChat")
        self.btn_direct_chat.setCursor(Qt.PointingHandCursor)
        self.btn_direct_chat.clicked.connect(self._on_direct_chat_clicked)
        g.addWidget(self.btn_direct_chat, 0, 1, 2, 1)
        layout.addWidget(group)

        lbl_contacts_header = QLabel("AGENDA")
        lbl_contacts_header.setObjectName("sectionLabel")
        layout.addWidget(lbl_contacts_header)

        self.txt_search_contacts = QLineEdit()
        self.txt_search_contacts.setObjectName("searchField")
        self.txt_search_contacts.setPlaceholderText("Buscar por nombre o teléfono")
        self.txt_search_contacts.addAction(icon("search", C["text_3"], 16), QLineEdit.LeadingPosition)
        self.txt_search_contacts.setClearButtonEnabled(True)
        self.txt_search_contacts.textChanged.connect(self._filter_contacts)
        self.txt_search_contacts.returnPressed.connect(self._open_first)
        layout.addWidget(self.txt_search_contacts)

        self.list_contacts = QListWidget()
        self.list_contacts.setObjectName("framedList")
        self.list_contacts.itemClicked.connect(self._on_contact_item_selected)
        self.list_contacts.itemActivated.connect(self._on_contact_item_selected)
        layout.addWidget(self.list_contacts, stretch=1)

        footer_layout = QHBoxLayout()
        self.lbl_total = QLabel("")
        self.lbl_total.setObjectName("hint")
        footer_layout.addWidget(self.lbl_total)
        footer_layout.addStretch()
        btn_close = QPushButton("Cerrar")
        btn_close.setCursor(Qt.PointingHandCursor)
        btn_close.clicked.connect(self.reject)
        footer_layout.addWidget(btn_close)
        layout.addLayout(footer_layout)
        self.txt_direct_phone.setFocus()

    def _load_contacts(self):
        self._all_contacts = get_all_contacts()
        self._render_contacts(self._all_contacts)

    def _filter_contacts(self, query: str):
        q = (query or "").strip().lower()
        if not q:
            self._render_contacts(self._all_contacts)
            return
        digits = "".join(ch for ch in q if ch.isdigit())
        filtered = [
            c for c in self._all_contacts
            if q in (c.get("name") or "").lower() or (digits and digits in (c.get("phone") or ""))
        ]
        self._render_contacts(filtered)

    def _render_contacts(self, contacts: list):
        self.list_contacts.setUpdatesEnabled(False)
        self.list_contacts.clear()
        for c in contacts:
            item = QListWidgetItem()
            item.setData(Qt.UserRole, c)
            item.setSizeHint(QSize(0, 42))
            self.list_contacts.addItem(item)
            self.list_contacts.setItemWidget(item, ContactListItemWidget(c))
        self.list_contacts.setUpdatesEnabled(True)
        total = len(self._all_contacts)
        self.lbl_total.setText(f"{len(contacts)} de {total} contactos" if len(contacts) != total else f"{total} contactos")

    def _open_first(self):
        item = self.list_contacts.item(0)
        if item:
            self._on_contact_item_selected(item)

    def _on_contact_item_selected(self, item: QListWidgetItem):
        c = item.data(Qt.UserRole)
        if not c or not c.get("jid"):
            return
        chat = get_or_create_chat(c["jid"], name=c.get("name"), phone=c.get("phone"))
        self.selected_chat = chat
        self.chat_ready.emit(chat)
        self.accept()

    def _on_direct_chat_clicked(self):
        phone = self.txt_direct_phone.text().strip()
        name = self.txt_direct_name.text().strip()

        clean_digits = "".join(ch for ch in phone if ch.isdigit())
        if len(clean_digits) < 7:
            QMessageBox.warning(self, "Número inválido",
                                "El número debe tener al menos 7 dígitos, con código de país.")
            self.txt_direct_phone.setFocus()
            return

        jid = f"{clean_digits}@s.whatsapp.net"
        chat = get_or_create_chat(jid, name=name if name else None, phone=clean_digits)
        if name:
            save_or_update_contact(jid, name=name, phone=clean_digits)
            try:
                GatewayManager.get_instance().update_contact(jid, name=name, phone=clean_digits)
            except Exception:
                pass

        self.selected_chat = chat
        self.chat_ready.emit(chat)
        self.accept()
