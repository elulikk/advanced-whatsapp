import os
from datetime import datetime
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFrame,
    QTextEdit, QScrollArea, QDialog
)
from PySide6.QtCore import Qt, Signal, QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QPixmap, QGuiApplication

from app.db import resolve_phone, get_contact_notes, set_contact_notes, get_chat_media
from app.ui.chat_list import AvatarWidget, ElidedLabel
from app.ui.theme import C, HEADER_H, R_SM
from app.ui.icons import tool_button, pixmap

_ICONS = {"image": "image", "video": "video", "document": "file", "audio": "mic"}


def format_phone(phone: str) -> str:
    """5492972432125 -> +54 9 2972 43-2125 (formato argentino; otros países quedan con +)."""
    d = "".join(ch for ch in (phone or "") if ch.isdigit())
    if not d:
        return ""
    if d.startswith("549") and len(d) == 13:
        return f"+54 9 {d[3:7]} {d[7:9]}-{d[9:]}"
    if d.startswith("549") and len(d) == 12:
        return f"+54 9 {d[3:6]} {d[6:9]}-{d[9:]}"
    return f"+{d}"


class AvatarViewer(QDialog):
    """Foto de perfil en grande. Clic o Esc para cerrar."""

    def __init__(self, name: str, avatar_path, parent=None):
        super().__init__(parent)
        self.setWindowTitle(name or "Foto de perfil")
        self.setStyleSheet(f"QDialog {{ background-color: {C['chat_bg']}; }}")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 12, 12, 12)
        pix = QPixmap(avatar_path) if avatar_path and os.path.exists(avatar_path) else QPixmap()
        if pix.isNull():
            lay.addWidget(AvatarWidget(name, size=320), alignment=Qt.AlignCenter)
        else:
            lbl = QLabel()
            lbl.setPixmap(pix.scaled(640, 640, Qt.KeepAspectRatio, Qt.SmoothTransformation))
            lay.addWidget(lbl, alignment=Qt.AlignCenter)
        cap = QLabel(name)
        cap.setAlignment(Qt.AlignCenter)
        cap.setStyleSheet("font-size: 14px; font-weight: 600; margin-top: 6px;")
        lay.addWidget(cap)

    def mousePressEvent(self, event):
        self.accept()


class ContactInfoPanel(QFrame):
    """Panel lateral 'Info. del contacto'."""
    close_requested = Signal()
    edit_requested = Signal()
    search_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.chat = None
        self.setObjectName("infoPanel")
        self.setFixedWidth(300)
        self.setStyleSheet(f"#infoPanel {{ background-color: {C['panel']}; border-left: 1px solid {C['border']}; }}")

        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(600)
        self._save_timer.timeout.connect(self._save_notes)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Cabecera
        header = QFrame()
        header.setObjectName("paneHeader")
        header.setFixedHeight(HEADER_H)
        hl = QHBoxLayout(header)
        hl.setContentsMargins(8, 0, 8, 0)
        hl.setSpacing(6)
        btn_close = tool_button("close", "Cerrar", icon_size=16)
        btn_close.clicked.connect(self.close_requested)
        hl.addWidget(btn_close)
        title = QLabel("Info. del contacto")
        title.setStyleSheet(f"font-size: 14px; font-weight: 600; color: {C['text']};")
        hl.addWidget(title, stretch=1)
        btn_edit = tool_button("edit", "Editar contacto", icon_size=16)
        btn_edit.clicked.connect(self.edit_requested)
        hl.addWidget(btn_edit)
        root.addWidget(header)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        body = QWidget()
        self.body_layout = QVBoxLayout(body)
        self.body_layout.setContentsMargins(16, 18, 16, 16)
        self.body_layout.setSpacing(8)
        scroll.setWidget(body)
        root.addWidget(scroll, stretch=1)

        self.avatar = AvatarWidget("", size=96)
        self.avatar.set_clickable("Ver foto en grande")
        self.avatar.mousePressEvent = lambda e: self._show_big_avatar()
        self.body_layout.addWidget(self.avatar, alignment=Qt.AlignHCenter)
        self.body_layout.addSpacing(4)

        self.lbl_name = QLabel("")
        self.lbl_name.setStyleSheet(f"font-size: 15px; font-weight: 600; color: {C['text']};")
        self.lbl_name.setAlignment(Qt.AlignCenter)
        self.lbl_name.setWordWrap(True)
        self.lbl_name.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.body_layout.addWidget(self.lbl_name)

        self.lbl_phone = QLabel("")
        self.lbl_phone.setStyleSheet(f"font-size: 12px; color: {C['text_2']};")
        self.lbl_phone.setAlignment(Qt.AlignCenter)
        self.lbl_phone.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.body_layout.addWidget(self.lbl_phone)

        actions = QHBoxLayout()
        actions.setSpacing(6)
        actions.addStretch()
        btn_search = tool_button("search", "Buscar en el chat  (Ctrl+F)", text="Buscar", icon_size=16)
        btn_search.clicked.connect(self.search_requested)
        actions.addWidget(btn_search)
        self.btn_copy_phone = tool_button("copy", "Copiar el número al portapapeles", text="Copiar número", icon_size=16)
        self.btn_copy_phone.clicked.connect(self._copy_phone)
        actions.addWidget(self.btn_copy_phone)
        actions.addStretch()
        self.body_layout.addLayout(actions)

        self._add_separator()

        # Notas
        lbl_notes = QLabel("NOTAS SOBRE ESTE CONTACTO")
        lbl_notes.setObjectName("sectionLabel")
        self.body_layout.addWidget(lbl_notes)
        self.txt_notes = QTextEdit()
        self.txt_notes.setAcceptRichText(False)
        self.txt_notes.setPlaceholderText("Añade notas (se guardan solas)")
        self.txt_notes.setFixedHeight(88)
        self.txt_notes.textChanged.connect(self._save_timer.start)
        self.body_layout.addWidget(self.txt_notes)

        self._add_separator()

        # Archivos, enlaces y documentos
        row = QHBoxLayout()
        lbl_files = QLabel("ARCHIVOS, ENLACES Y DOCUMENTOS")
        lbl_files.setObjectName("sectionLabel")
        row.addWidget(lbl_files, stretch=1)
        self.lbl_count = QLabel("0")
        self.lbl_count.setObjectName("hint")
        row.addWidget(self.lbl_count)
        self.body_layout.addLayout(row)

        self.files_box = QVBoxLayout()
        self.files_box.setSpacing(1)
        self.body_layout.addLayout(self.files_box)
        self.lbl_no_files = QLabel("Sin archivos ni enlaces en este chat.")
        self.lbl_no_files.setObjectName("hint")
        self.body_layout.addWidget(self.lbl_no_files)
        self.body_layout.addStretch(1)

    def _add_separator(self):
        sep = QFrame()
        sep.setObjectName("hline")
        self.body_layout.addWidget(sep)

    def _copy_phone(self):
        txt = self.lbl_phone.text()
        if txt.startswith("+"):
            QGuiApplication.clipboard().setText(txt.replace(" ", "").replace("-", ""))

    # ── API ───────────────────────────────────────────────────────────────
    def show_chat(self, chat: dict, display_name: str):
        self._flush_notes()
        self.chat = chat
        jid = chat.get("jid", "")
        self.avatar.set_avatar(display_name, chat.get("avatar_path"))
        self.lbl_name.setText(display_name)
        if chat.get("is_group"):
            self.lbl_phone.setText("Grupo de WhatsApp")
        else:
            phone = resolve_phone(chat)
            self.lbl_phone.setText(format_phone(phone) if phone else "Número no disponible")
        self.btn_copy_phone.setVisible(self.lbl_phone.text().startswith("+"))

        self.txt_notes.blockSignals(True)
        self.txt_notes.setPlainText(get_contact_notes(jid))
        self.txt_notes.blockSignals(False)
        self.refresh_files()

    def _show_big_avatar(self):
        AvatarViewer(self.lbl_name.text(), self.avatar.avatar_path, self).exec()

    def set_avatar_path(self, display_name: str, path: str):
        self.avatar.set_avatar(display_name, path)

    def refresh_files(self):
        while self.files_box.count():
            w = self.files_box.takeAt(0).widget()
            if w:
                w.deleteLater()
        if not self.chat:
            return
        media = get_chat_media(self.chat.get("jid", ""))
        files, links = media["files"], media["links"]
        self.lbl_count.setText(str(len(files) + len(links)))
        self.lbl_no_files.setVisible(not files and not links)
        for f in files[:30]:
            path = f.get("media_path")
            name = os.path.basename(path) if path else (f.get("text") or "Archivo")
            self._add_entry(_ICONS.get(f['media_type'], "file"), name, f["timestamp"],
                            QUrl.fromLocalFile(path) if path and os.path.exists(path) else None)
        for l in links[:30]:
            self._add_entry("link", l['url'], l["timestamp"], QUrl(l["url"]))

    def _add_entry(self, icon_name: str, text: str, ts: int, url):
        date = datetime.fromtimestamp(ts).strftime("%d/%m/%y") if ts else ""
        row = QPushButton()
        row.setToolTip(text)
        row.setCursor(Qt.PointingHandCursor if url else Qt.ArrowCursor)
        row.setStyleSheet(f"""
            QPushButton {{ border: none; border-radius: {R_SM}px; background: transparent; min-height: 30px; max-height: 30px; padding: 0; }}
            QPushButton:hover {{ background-color: {C['hover']}; }}
            QPushButton:pressed {{ background-color: {C['selected']}; }}
        """)
        lay = QHBoxLayout(row)
        lay.setContentsMargins(8, 0, 8, 0)
        lay.setSpacing(8)
        ico = QLabel()
        ico.setPixmap(pixmap(icon_name, C["text_2"], 16))
        name_lbl = ElidedLabel(text)   # recorta con «…» según el ancho real
        name_lbl.setToolTip("")
        name_lbl.setStyleSheet(f"color: {C['text'] if url else C['text_2']}; font-size: 12px;")
        date_lbl = QLabel(date)
        date_lbl.setObjectName("hint")
        for w in (ico, name_lbl, date_lbl):
            w.setAttribute(Qt.WA_TransparentForMouseEvents)
        lay.addWidget(ico)
        lay.addWidget(name_lbl, stretch=1)
        lay.addWidget(date_lbl)
        if url:
            row.clicked.connect(lambda: QDesktopServices.openUrl(url))
        self.files_box.addWidget(row)

    # ── Notas ─────────────────────────────────────────────────────────────
    def _save_notes(self):
        if self.chat and self.chat.get("jid"):
            set_contact_notes(self.chat["jid"], self.txt_notes.toPlainText())

    def _flush_notes(self):
        if self._save_timer.isActive():
            self._save_timer.stop()
            self._save_notes()
