"""Bloqueo de la app entera: una capa que tapa toda la ventana hasta ingresar la contraseña de los chats bloqueados."""
from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget

from app import chat_lock
from app.ui.icons import pixmap
from app.ui.theme import C


class AppLockOverlay(QWidget):
    unlocked = Signal()
    quit_requested = Signal()

    def __init__(self, window: QWidget):
        super().__init__(window)
        self._window = window
        self.active = False
        self.setObjectName("appLock")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet(f"#appLock {{ background-color: {C['panel']}; }}")
        window.installEventFilter(self)

        outer = QVBoxLayout(self)
        outer.addStretch(1)
        col = QVBoxLayout()
        col.setSpacing(12)
        col.setAlignment(Qt.AlignHCenter)
        ic = QLabel()
        ic.setPixmap(pixmap("lock", C["text_2"], 44))
        ic.setAlignment(Qt.AlignHCenter)
        col.addWidget(ic)
        title = QLabel("Advanced WhatsApp está bloqueado")
        title.setAlignment(Qt.AlignHCenter)
        title.setStyleSheet(f"font-size: 18px; font-weight: 600; color: {C['text']};")
        col.addWidget(title)
        self.lbl_msg = QLabel("Ingresá la contraseña para continuar.")
        self.lbl_msg.setAlignment(Qt.AlignHCenter)
        self.lbl_msg.setStyleSheet(f"font-size: 13px; color: {C['text_2']};")
        col.addWidget(self.lbl_msg)
        self.txt = QLineEdit()
        self.txt.setEchoMode(QLineEdit.Password)
        self.txt.setPlaceholderText("Contraseña")
        self.txt.setFixedWidth(260)
        self.txt.returnPressed.connect(self._try)
        col.addWidget(self.txt, alignment=Qt.AlignHCenter)
        row = QHBoxLayout()
        row.setAlignment(Qt.AlignHCenter)
        btn = QPushButton("Desbloquear")
        btn.clicked.connect(self._try)
        row.addWidget(btn)
        btn_quit = QPushButton("Salir")
        btn_quit.clicked.connect(self.quit_requested.emit)
        row.addWidget(btn_quit)
        col.addLayout(row)
        outer.addLayout(col)
        outer.addStretch(2)
        self.hide()

    def engage(self):
        self.active = True
        self.txt.clear()
        self.lbl_msg.setText("Ingresá la contraseña para continuar.")
        self.setGeometry(self._window.rect())
        self.raise_()
        self.show()
        self.txt.setFocus()

    def _try(self):
        if chat_lock.verify_password(self.txt.text()):
            self.active = False
            self.txt.clear()
            self.hide()
            self.unlocked.emit()
        else:
            self.txt.clear()
            self.lbl_msg.setText("Contraseña incorrecta.")
            self.lbl_msg.setStyleSheet(f"font-size: 13px; color: {C['danger_text']};")

    def eventFilter(self, obj, event):
        if obj is self._window and event.type() == QEvent.Resize and self.active:
            self.setGeometry(self._window.rect())
        return super().eventFilter(obj, event)
