import base64
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFrame, QMessageBox
)
from PySide6.QtCore import Qt, QTimer, QByteArray
from PySide6.QtGui import QPixmap, QImage

from app.gateway_manager import GatewayManager
from app.ui.theme import C, R_LG


class QRDialog(QDialog):
    """Diálogo para vincular WhatsApp escaneando el código QR en vivo."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Vincular WhatsApp")
        self.setFixedSize(440, 500)
        self.gateway = GatewayManager.get_instance()
        self._last_qr = None

        self._init_ui()

        # Consultar estado y refrescar el QR cada 2 segundos
        self.timer = QTimer(self)
        self.timer.setInterval(2000)
        self.timer.timeout.connect(self._check_status)
        self.timer.start()

        self._check_status()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(10)

        self.lbl_title = QLabel("Vincular WhatsApp")
        self.lbl_title.setStyleSheet("font-size: 16px; font-weight: 600;")
        layout.addWidget(self.lbl_title)

        self.lbl_instructions = QLabel(
            "1. Abre WhatsApp en tu teléfono.<br>"
            "2. Ve a <b>Ajustes → Dispositivos vinculados</b>.<br>"
            "3. Toca <b>Vincular un dispositivo</b> y apunta la cámara a este código."
        )
        self.lbl_instructions.setObjectName("subtle")
        self.lbl_instructions.setWordWrap(True)
        layout.addWidget(self.lbl_instructions)

        # Marco del código QR (fondo blanco para que el teléfono lo lea bien)
        self.qr_frame = QFrame()
        self.qr_frame.setObjectName("qrFrame")
        self.qr_frame.setFixedSize(284, 284)
        self.qr_frame.setStyleSheet(f"#qrFrame {{ background-color: #ffffff; border: 1px solid {C['border']}; "
                                    f"border-radius: {R_LG}px; }}")
        qr_layout = QVBoxLayout(self.qr_frame)
        qr_layout.setContentsMargins(10, 10, 10, 10)
        self.lbl_qr_image = QLabel("Generando código QR…")
        self.lbl_qr_image.setStyleSheet("color: #1b2329; font-size: 12px;")
        self.lbl_qr_image.setAlignment(Qt.AlignCenter)
        self.lbl_qr_image.setWordWrap(True)
        qr_layout.addWidget(self.lbl_qr_image)
        layout.addWidget(self.qr_frame, alignment=Qt.AlignCenter)

        self.lbl_status = QLabel("Consultando estado…")
        self.lbl_status.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.lbl_status)
        layout.addStretch()

        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(8)
        self.btn_resync = QPushButton("Re-escanear…")
        self.btn_resync.setToolTip("Genera un QR nuevo con sincronización completa del historial")
        self.btn_resync.setCursor(Qt.PointingHandCursor)
        self.btn_resync.setVisible(False)
        self.btn_resync.clicked.connect(self._on_resync_clicked)
        btn_layout.addWidget(self.btn_resync)

        self.btn_disconnect = QPushButton("Cerrar sesión…")
        self.btn_disconnect.setObjectName("btnDanger")
        self.btn_disconnect.setCursor(Qt.PointingHandCursor)
        self.btn_disconnect.setVisible(False)
        self.btn_disconnect.clicked.connect(self._on_disconnect_clicked)
        btn_layout.addWidget(self.btn_disconnect)
        btn_layout.addStretch()

        self.btn_close = QPushButton("Cerrar")
        self.btn_close.setObjectName("btnPrimary")
        self.btn_close.setCursor(Qt.PointingHandCursor)
        self.btn_close.setDefault(True)
        self.btn_close.clicked.connect(self.accept)
        btn_layout.addWidget(self.btn_close)
        layout.addLayout(btn_layout)

    def _set_status(self, text: str, color: str):
        self.lbl_status.setText(text)
        self.lbl_status.setStyleSheet(f"font-size: 12px; font-weight: 600; color: {color};")

    def _set_frame(self, dark: bool):
        """Fondo blanco solo cuando hay un QR que escanear; si no, el recuadro sigue el tema oscuro."""
        bg, fg = (C["surface"], C["text"]) if dark else ("#ffffff", "#1b2329")
        self.qr_frame.setStyleSheet(f"#qrFrame {{ background-color: {bg}; border: 1px solid {C['border']}; "
                                    f"border-radius: {R_LG}px; }}")
        self.lbl_qr_image.setStyleSheet(f"color: {fg}; font-size: 12px;")

    def _set_qr_text(self, text: str):
        self._last_qr = None
        self.lbl_qr_image.setText(text)

    def _check_status(self):
        st = self.gateway.get_status()
        status = st.get("status", "disconnected")
        connected = status == "connected"
        self.btn_disconnect.setVisible(connected)
        self.btn_resync.setVisible(connected)

        if connected:
            phone = st.get("phone") or "desconocido"
            name = st.get("push_name") or ""
            self._set_status(f"● Conectado como +{phone}" + (f" ({name})" if name else ""), C["accent"])
            self._set_qr_text("Sesión vinculada.\nPuedes enviar y recibir mensajes.\n\n"
                              "No hace falta volver a escanear.")
            self._set_frame(dark=True)
        elif status == "qr_ready" or st.get("has_qr"):
            self._set_status("Escanea el código con tu teléfono", C["link"])
            self._set_frame(dark=False)
            qr_url = self.gateway.get_qr().get("qr_data_url")
            if qr_url and "," in qr_url and qr_url != self._last_qr:
                try:
                    img_data = base64.b64decode(qr_url.split(",")[1])
                    qimg = QImage()
                    qimg.loadFromData(QByteArray(img_data))
                    pix = QPixmap.fromImage(qimg).scaled(262, 262, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                    self.lbl_qr_image.setPixmap(pix)
                    self._last_qr = qr_url
                except Exception as e:
                    self._set_qr_text(f"Error mostrando el QR: {e}")
        elif status == "connecting":
            self._set_status("Conectando con los servidores de WhatsApp…", C["warn"])
            self._set_frame(dark=True)
            self._set_qr_text("Iniciando el servicio de WhatsApp…")
        else:
            self._set_status("Desconectado · iniciando el gateway…", "#e58a84")
            self._set_frame(dark=True)
            self._set_qr_text("Esperando conexión…")

    def _on_resync_clicked(self):
        r = QMessageBox.question(
            self,
            "Sincronizar historial completo",
            "Para que WhatsApp transfiera todo el historial antiguo de mensajes y contactos de tu teléfono,\n"
            "se generará un nuevo código QR con sincronización completa.\n\n"
            "¿Generar el código QR ahora?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        )
        if r == QMessageBox.Yes:
            self.gateway.disconnect()
            self._check_status()

    def _on_disconnect_clicked(self):
        r = QMessageBox.question(
            self,
            "Cerrar sesión de WhatsApp",
            "¿Cerrar la sesión activa y desvincular este dispositivo?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        )
        if r == QMessageBox.Yes:
            self.gateway.disconnect()
            self._check_status()

    def closeEvent(self, event):
        self.timer.stop()
        super().closeEvent(event)

    def done(self, r):
        self.timer.stop()
        super().done(r)
