from PySide6.QtWidgets import QWidget, QVBoxLayout, QAbstractButton
from PySide6.QtCore import Qt, Signal, QRectF, QPointF
from PySide6.QtGui import QPainter, QColor, QFont, QPen

from app.ui.theme import C, HEADER_H
from app.ui.icons import pixmap
from app.ui.effects import HoverFade, overlay

RAIL_W = 52
BTN_H = 40


class NavButton(QAbstractButton):
    """Botón del rail lateral: icono de línea, fondo redondeado con fundido, marca de pestaña activa
    en el borde del rail y contador opcional. Es un QAbstractButton: así el QSS global de QPushButton
    (alto mínimo, padding) no le cambia el tamaño."""
    def __init__(self, icon_name: str, tooltip: str, parent=None):
        super().__init__(parent)
        self.icon_name = icon_name
        self.badge_text = ""
        self.dot = False          # punto sin número (p. ej. estados sin ver)
        self.setToolTip(tooltip)
        self.setFixedSize(RAIL_W, BTN_H)
        self.setCursor(Qt.PointingHandCursor)
        self.is_active = False
        self._fade = HoverFade(self)

    def set_active(self, active: bool):
        self.is_active = active
        self.update()

    def set_badge(self, text: str):
        self.badge_text = text
        self.update()

    def set_dot(self, on: bool):
        if on != self.dot:
            self.dot = on
            self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        h = self._fade.value
        tile = QRectF((RAIL_W - BTN_H) / 2, 0, BTN_H, BTN_H)

        p.setPen(Qt.NoPen)
        if self.is_active:
            p.setBrush(QColor(C["selected"]))
            p.drawRoundedRect(tile, 10, 10)
            # Marca de pestaña activa pegada al borde del rail
            p.setBrush(QColor(C["ember"]))
            p.drawRoundedRect(QRectF(-2, (BTN_H - 18) / 2, 5, 18), 2.5, 2.5)
        elif h > 0.002:
            p.setBrush(overlay(0.07 * h))
            p.drawRoundedRect(tile, 10, 10)
        if self.isDown():
            p.setBrush(overlay(0.06))
            p.drawRoundedRect(tile, 10, 10)

        ratio = self.devicePixelRatioF()
        x = round(tile.center().x() * ratio - 10 * ratio) / ratio
        y = round(tile.center().y() * ratio - 10 * ratio) / ratio
        lit = 1.0 if self.is_active else h
        if lit < 0.999:
            p.setOpacity(1.0 - lit)
            p.drawPixmap(QPointF(x, y), pixmap(self.icon_name, C["text_2"], 20, ratio=ratio))
        if lit > 0.001:
            p.setOpacity(lit)
            p.drawPixmap(QPointF(x, y), pixmap(self.icon_name, C["text"], 20, ratio=ratio))
        p.setOpacity(1.0)

        if self.badge_text:
            f = QFont("Segoe UI")
            f.setPixelSize(10)
            f.setBold(True)
            p.setFont(f)
            w = max(18, p.fontMetrics().horizontalAdvance(self.badge_text) + 10)
            badge = QRectF(tile.right() - w + 4, 1, w, 18)
            # Anillo del color del rail: separa el contador del icono
            p.setPen(QPen(QColor(C["rail"]), 2))
            p.setBrush(QColor(C["accent"]))
            p.drawRoundedRect(badge, 9, 9)
            p.setPen(QColor(C["on_accent"]))
            p.drawText(badge, Qt.AlignCenter, self.badge_text)
        elif self.dot:
            p.setPen(QPen(QColor(C["rail"]), 2))
            p.setBrush(QColor(C["accent"]))
            p.drawEllipse(QRectF(tile.right() - 11, 4, 10, 10))
        p.end()


class NavRailWidget(QWidget):
    """Barra vertical de navegación: Chats, Estados, Análisis interno y Configuración."""
    tab_changed = Signal(str)
    settings_requested = Signal()
    qr_requested = Signal()
    self_analysis_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedWidth(RAIL_W)
        # Un QWidget propio no pinta el fondo del QSS salvo que se pida
        self.setObjectName("navRail")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet(f"#navRail {{ background-color: {C['rail']}; border-right: 1px solid {C['border']}; }}")
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        # El primer botón queda centrado con las cabeceras de los paneles
        top = (HEADER_H - BTN_H) // 2
        layout.setContentsMargins(0, top, 0, top)
        layout.setSpacing(4)

        self.btn_chats = NavButton("chats", "Chats  (Ctrl+1)")
        self.btn_chats.set_active(True)
        self.btn_chats.clicked.connect(lambda: self._select_tab("chats"))
        layout.addWidget(self.btn_chats)

        self.btn_status = NavButton("status", "Estados  (Ctrl+2)")
        self.btn_status.clicked.connect(lambda: self._select_tab("status"))
        layout.addWidget(self.btn_status)

        layout.addStretch()

        # Análisis psicológico propio a partir de mis mensajes en todos los chats
        self.btn_self_analysis = NavButton("analysis", "Análisis interno (yo)")
        self.btn_self_analysis.clicked.connect(self.self_analysis_requested.emit)
        layout.addWidget(self.btn_self_analysis)

        self.btn_settings = NavButton("settings", "Configuración  (Ctrl+,)")
        self.btn_settings.clicked.connect(lambda: self._select_tab("settings"))
        layout.addWidget(self.btn_settings)

    def _select_tab(self, tab_name: str):
        self.btn_chats.set_active(tab_name == "chats")
        self.btn_status.set_active(tab_name == "status")
        self.btn_settings.set_active(tab_name == "settings")
        if tab_name == "settings":
            self.settings_requested.emit()
        self.tab_changed.emit(tab_name)

    def set_tab(self, tab_name: str):
        self._select_tab(tab_name)

    def set_status_badge(self, unseen_contacts: int):
        """Punto verde en Estados si hay contactos con estados sin ver."""
        self.btn_status.set_dot(unseen_contacts > 0)
        self.btn_status.setToolTip(f"Estados  (Ctrl+2) · {unseen_contacts} sin ver" if unseen_contacts
                                   else "Estados  (Ctrl+2)")

    def set_unread_badge(self, count: int):
        self.btn_chats.set_badge("99+" if count > 99 else (str(count) if count > 0 else ""))
