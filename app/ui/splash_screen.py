from PySide6.QtWidgets import QWidget, QVBoxLayout, QLabel, QProgressBar
from PySide6.QtCore import Qt

from app.ui.theme import C, WIN11
from app.ui.icons import pixmap
from app.ui.effects import round_corners


class SplashScreen(QWidget):
    """Pantalla de inicio mientras se abre la base de datos y se conecta WhatsApp."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setObjectName("splash")
        self.setAttribute(Qt.WA_StyledBackground, True)
        # En Windows 11 el sistema pone esquinas redondeadas, sombra y borde (ver showEvent)
        border = "none" if WIN11 else f"1px solid {C['border']}"
        self.setStyleSheet(f"#splash {{ background-color: {C['panel']}; border: {border}; }}")
        self.resize(360, 200)
        self._center_on_screen()
        self._init_ui()

    def _center_on_screen(self):
        from PySide6.QtGui import QGuiApplication
        screen = QGuiApplication.primaryScreen()
        if screen:
            geo = screen.availableGeometry()
            self.move(geo.x() + (geo.width() - self.width()) // 2, geo.y() + (geo.height() - self.height()) // 2)

    def _init_ui(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 24, 28, 20)
        lay.setSpacing(8)
        lay.setAlignment(Qt.AlignCenter)

        icon_lbl = QLabel()
        icon_lbl.setPixmap(pixmap("chats", C["accent"], 40))
        lay.addWidget(icon_lbl, alignment=Qt.AlignCenter)

        title_lbl = QLabel("Advanced WhatsApp")
        title_lbl.setStyleSheet(f"font-family: 'Segoe UI'; font-size: 18px; font-weight: 600; "
                                f"letter-spacing: 1px; color: {C['text']};")
        lay.addWidget(title_lbl, alignment=Qt.AlignCenter)
        lay.addSpacing(8)

        self.progress_bar = QProgressBar()
        self.progress_bar.setFixedHeight(4)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(10)
        self.progress_bar.setStyleSheet(f"""
            QProgressBar {{ background-color: {C['raised']}; border: none; border-radius: 2px; }}
            QProgressBar::chunk {{ background-color: {C['accent']}; border-radius: 2px; }}
        """)
        lay.addWidget(self.progress_bar)

        self.lbl_status = QLabel("Iniciando aplicación...")
        self.lbl_status.setAlignment(Qt.AlignCenter)
        self.lbl_status.setStyleSheet(f"font-family: 'Segoe UI'; font-size: 12px; color: {C['text_2']};")
        lay.addWidget(self.lbl_status)

    def showEvent(self, e):
        super().showEvent(e)
        round_corners(self)

    def set_status(self, text: str, progress: int = None):
        self.lbl_status.setText(text)
        if progress is not None:
            self.progress_bar.setValue(progress)

    def finish(self, target_window):
        self.close()
