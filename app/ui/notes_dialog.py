"""Nota privada de un contacto: se edita desde el chat (la misma que el panel de info; columna `chats.notes`)."""
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QLabel, QTextEdit, QVBoxLayout

from app.db import get_contact_notes, set_contact_notes
from app.ui.theme import C


class NotesDialog(QDialog):
    def __init__(self, jid: str, name: str, parent=None):
        super().__init__(parent)
        self.jid = jid
        self.setWindowTitle(f"Nota privada · {name}")
        self.setMinimumSize(380, 260)
        lay = QVBoxLayout(self)
        hint = QLabel("Solo la ves vos: no se envía a nadie.")
        hint.setStyleSheet(f"color: {C['text_3']}; font-size: 12px;")
        lay.addWidget(hint)
        self.txt = QTextEdit()
        self.txt.setAcceptRichText(False)
        self.txt.setPlaceholderText("Escribí una nota sobre este contacto…")
        self.txt.setPlainText(get_contact_notes(jid))
        lay.addWidget(self.txt)
        box = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        box.button(QDialogButtonBox.Save).setText("Guardar")
        box.button(QDialogButtonBox.Cancel).setText("Cancelar")
        box.accepted.connect(self._save)
        box.rejected.connect(self.reject)
        lay.addWidget(box)

    def _save(self):
        set_contact_notes(self.jid, self.txt.toPlainText())
        self.accept()
