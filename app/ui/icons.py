"""Iconos vectoriales de línea (monocromos) para toda la interfaz, dibujados desde SVG en memoria.

Los QIcon se renderizan al tamaño y la escala de pantalla exactos que pide Qt (menús a 16 px, botones a 18 px,
pantallas al 125 %…), así nunca se ven reescalados ni borrosos.
"""
from functools import lru_cache

from PySide6.QtCore import Qt, QByteArray, QRectF, QPointF, QSize
from PySide6.QtGui import QIcon, QIconEngine, QPixmap, QImage, QPainter, QGuiApplication, QPen, QColor
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QToolButton

from app.ui.theme import C
from app.ui.effects import HoverFade, mix, overlay

# Trazos en una grilla de 24x24 (estilo línea, 1.8 px)
_PATHS = {
    "chats": '<path d="M21 11.5a8.4 8.4 0 0 1-9 8.4 9 9 0 0 1-3.9-.9L3 20.5l1.6-4.6A8 8 0 0 1 3.5 11.5 8.5 8.5 0 0 1 12 3a8.4 8.4 0 0 1 9 8.5z"/>',
    "status": '<circle cx="12" cy="12" r="4"/><path d="M12 2.5a9.5 9.5 0 0 1 9.5 9.5"/><path d="M21.5 12a9.5 9.5 0 0 1-9.5 9.5" stroke-dasharray="3 2.6"/><path d="M12 21.5A9.5 9.5 0 0 1 2.5 12" stroke-dasharray="3 2.6"/><path d="M2.5 12A9.5 9.5 0 0 1 12 2.5" stroke-dasharray="3 2.6"/>',
    "analysis": '<path d="M2.5 12.5h4l2.5-6 5 12 2.5-6h5"/>',
    "settings": '<circle cx="12" cy="12" r="3.2"/><path d="M12 2.8v2.6M12 18.6v2.6M2.8 12h2.6M18.6 12h2.6M5.5 5.5l1.8 1.8M16.7 16.7l1.8 1.8M5.5 18.5l1.8-1.8M16.7 7.3l1.8-1.8"/><circle cx="12" cy="12" r="6.6"/>',
    "search": '<circle cx="11" cy="11" r="6.5"/><path d="M16 16l4.5 4.5"/>',
    "more": '<circle cx="12" cy="5.5" r="1.4" fill="currentColor" stroke="none"/><circle cx="12" cy="12" r="1.4" fill="currentColor" stroke="none"/><circle cx="12" cy="18.5" r="1.4" fill="currentColor" stroke="none"/>',
    "new_chat": '<path d="M20 12.5V19a1.5 1.5 0 0 1-1.5 1.5h-13A1.5 1.5 0 0 1 4 19V6a1.5 1.5 0 0 1 1.5-1.5H12"/><path d="M17.5 3.5l3 3L12 15H9v-3z"/>',
    "attach": '<path d="M21 11l-8.6 8.6a5.5 5.5 0 0 1-7.8-7.8l8.6-8.6a3.7 3.7 0 0 1 5.2 5.2l-8.6 8.6a1.8 1.8 0 0 1-2.6-2.6l7.9-7.9"/>',
    "send": '<path d="M21 3L10.5 13.5"/><path d="M21 3l-6.5 18-4-7.5-7.5-4z"/>',
    "close": '<path d="M6.5 6.5l11 11M17.5 6.5l-11 11"/>',
    "sparkle": '<path d="M12 3l1.9 5.1L19 10l-5.1 1.9L12 17l-1.9-5.1L5 10l5.1-1.9z"/><path d="M19 15.5l.8 2 2 .8-2 .8-.8 2-.8-2-2-.8 2-.8z"/>',
    "info": '<circle cx="12" cy="12" r="8.5"/><path d="M12 11v5.5"/><circle cx="12" cy="7.8" r="1" fill="currentColor" stroke="none"/>',
    "user": '<circle cx="12" cy="8" r="4"/><path d="M4.5 20.5c0-3.9 3.4-6.3 7.5-6.3s7.5 2.4 7.5 6.3"/>',
    "user_plus": '<circle cx="10" cy="8" r="4"/><path d="M2.5 20.5c0-3.9 3.4-6.3 7.5-6.3s7.5 2.4 7.5 6.3"/><path d="M19 8v6M16 11h6"/>',
    "edit": '<path d="M16.5 3.5l4 4L8 20H4v-4z"/>',
    "down": '<path d="M6 9l6 6 6-6"/>',
    "up": '<path d="M6 15l6-6 6 6"/>',
    "refresh": '<path d="M20 11a8 8 0 0 0-14.6-4.5L4 8"/><path d="M4 3.5V8h4.5"/><path d="M4 13a8 8 0 0 0 14.6 4.5L20 16"/><path d="M20 20.5V16h-4.5"/>',
    "qr": '<rect x="3.5" y="3.5" width="6.5" height="6.5" rx="1"/><rect x="14" y="3.5" width="6.5" height="6.5" rx="1"/><rect x="3.5" y="14" width="6.5" height="6.5" rx="1"/><path d="M14 14h3v3h-3zM20.5 14v2M17 20.5h3.5V18M14 20.5h.01"/>',
    "pin": '<path d="M9 3.5h6l-1 6 3.5 3.5h-11L10 9.5z"/><path d="M12 13v7.5"/>',
    "lock": '<rect x="5" y="10.5" width="14" height="10" rx="1.5"/><path d="M8 10.5V7.5a4 4 0 0 1 8 0v3"/>',
    "trash": '<path d="M4 6.5h16M9.5 6.5V4h5v2.5M6 6.5l1 14h10l1-14"/>',
    "eraser": '<path d="M7.5 20.5l-4-4a1.8 1.8 0 0 1 0-2.6l9.4-9.4a1.8 1.8 0 0 1 2.6 0l4.5 4.5a1.8 1.8 0 0 1 0 2.6L11.5 20.5"/><path d="M7.5 20.5h13"/><path d="M6.5 11l7 7"/>',
    "folder": '<path d="M3.5 6.5a1.5 1.5 0 0 1 1.5-1.5h4.5l2 2.5H19a1.5 1.5 0 0 1 1.5 1.5v9a1.5 1.5 0 0 1-1.5 1.5H5A1.5 1.5 0 0 1 3.5 18z"/>',
    "eye": '<path d="M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12z"/><circle cx="12" cy="12" r="3"/>',
    "mic": '<rect x="9" y="2.5" width="6" height="11.5" rx="3"/><path d="M5.5 11a6.5 6.5 0 0 0 13 0"/><path d="M12 17.5V21M8.5 21h7"/>',
    "pause": '<path d="M8.5 5v14M15.5 5v14"/>',
    "play": '<path d="M7.5 4.5v15l12-7.5z"/>',
    "reply": '<path d="M9.5 6.5L4 12l5.5 5.5"/><path d="M4 12h9.5a6.5 6.5 0 0 1 6.5 6.5v1"/>',
    "forward": '<path d="M14.5 6.5L20 12l-5.5 5.5"/><path d="M20 12h-9.5A6.5 6.5 0 0 0 4 18.5v1"/>',
    "copy": '<rect x="8.5" y="8.5" width="12" height="12" rx="2"/><path d="M15.5 8.5V5.5a2 2 0 0 0-2-2h-8a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h3"/>',
    "download": '<path d="M12 3.5v12"/><path d="M7 10.5l5 5 5-5"/><path d="M4.5 20.5h15"/>',
    "select": '<rect x="3.5" y="3.5" width="17" height="17" rx="3.5"/><path d="M8 12.2l2.8 2.8L16 9.5"/>',
    "box": '<rect x="3.5" y="3.5" width="17" height="17" rx="3.5"/>',
    "check": '<path d="M5 12.5l4.5 4.5L19 7.5"/>',
    "check_all": '<path d="M2.5 12.5l4.5 4.5 9.5-9.5"/><path d="M12 16.5l.5.5 9.5-9.5"/>',
    "plus": '<path d="M12 5v14M5 12h14"/>',
    "smile": '<circle cx="12" cy="12" r="8.5"/><path d="M8.5 14.2a4.6 4.6 0 0 0 7 0"/><circle cx="9.2" cy="9.8" r="1" fill="currentColor" stroke="none"/><circle cx="14.8" cy="9.8" r="1" fill="currentColor" stroke="none"/>',
    "image": '<rect x="3.5" y="4.5" width="17" height="15" rx="2"/><circle cx="9" cy="10" r="1.6"/><path d="M20.5 16l-4.5-4.5-9 8"/>',
    "video": '<rect x="2.5" y="6" width="13" height="12" rx="2"/><path d="M15.5 10.5l6-3.5v10l-6-3.5z"/>',
    "file": '<path d="M14 3.5H7A1.5 1.5 0 0 0 5.5 5v14A1.5 1.5 0 0 0 7 20.5h10a1.5 1.5 0 0 0 1.5-1.5V8z"/><path d="M14 3.5V8h4.5"/>',
    "link": '<path d="M10 13.5a4.5 4.5 0 0 0 6.8.5l2.8-2.8a4.5 4.5 0 0 0-6.4-6.4l-1.6 1.6"/><path d="M14 10.5a4.5 4.5 0 0 0-6.8-.5l-2.8 2.8a4.5 4.5 0 0 0 6.4 6.4l1.6-1.6"/>',
    "bell": '<path d="M6 16.5V11a6 6 0 0 1 12 0v5.5l1.5 2h-15z"/><path d="M10 20.5a2.2 2.2 0 0 0 4 0"/>',
    "bell_off": '<path d="M8.6 5.9A6 6 0 0 1 18 11v4"/><path d="M6 11v5.5l-1.5 2H17"/><path d="M10 20.5a2.2 2.2 0 0 0 4 0"/><path d="M3.5 3.5l17 17"/>',
    "wifi_off": '<path d="M3.5 3.5l17 17"/><path d="M8.5 16a5 5 0 0 1 7 0"/><path d="M5 12.5a10 10 0 0 1 4.2-2.4M14.8 10.1A10 10 0 0 1 19 12.5"/><path d="M2 9a14.5 14.5 0 0 1 4.3-2.6M11 5.6a14.5 14.5 0 0 1 11 3.4"/><circle cx="12" cy="19.5" r="1" fill="currentColor" stroke="none"/>',
    "prev": '<path d="M15 5l-7 7 7 7"/>',
    "next": '<path d="M9 5l7 7-7 7"/>',
}


def _svg(name: str, color: str, stroke: float) -> bytes:
    body = _PATHS[name].replace("currentColor", color)
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" '
            f'stroke-width="{stroke}" stroke-linecap="round" stroke-linejoin="round">{body}</svg>').encode()


def _stroke_for(logical_size: float) -> float:
    """Trazo un poco más grueso en iconos chicos para que no se pierdan (16 px → ~1.33 px de línea)."""
    return 2.0 if logical_size <= 16 else 1.8


@lru_cache(maxsize=1024)
def _render(name: str, color: str, w: int, h: int, stroke: float) -> QImage:
    """Imagen de w×h píxeles físicos (sin escala)."""
    img = QImage(max(1, w), max(1, h), QImage.Format_ARGB32_Premultiplied)
    img.fill(Qt.transparent)
    renderer = QSvgRenderer(QByteArray(_svg(name, color, stroke)))
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    renderer.render(p, QRectF(0, 0, w, h))
    p.end()
    return img


def _screen_ratio() -> float:
    app = QGuiApplication.instance()
    if app is not None and app.primaryScreen() is not None:
        return max(1.0, app.primaryScreen().devicePixelRatio())
    return 2.0


@lru_cache(maxsize=512)
def pixmap(name: str, color: str = None, size: int = 18, stroke: float = None, ratio: float = None) -> QPixmap:
    """Pixmap del icono a `size` px lógicos y a la escala de pantalla (o `ratio`)."""
    color = color or C["text_2"]
    ratio = ratio or _screen_ratio()
    phys = round(size * ratio)
    px = QPixmap.fromImage(_render(name, color, phys, phys, stroke or _stroke_for(size)))
    px.setDevicePixelRatio(phys / size)
    return px


def image(name: str, color: str, size: int, ratio: float = 2.0) -> QImage:
    """QImage con escala (para recursos de documentos HTML como el chat)."""
    phys = round(size * ratio)
    img = QImage(_render(name, color, phys, phys, _stroke_for(size)))
    img.setDevicePixelRatio(phys / size)
    return img


_ENGINES = []   # referencias vivas: Qt puede llamar a los motores mucho después de crearlos


class _LineIconEngine(QIconEngine):
    """Motor de QIcon que dibuja el SVG al tamaño exacto pedido, con un color por modo/estado."""

    def __init__(self, name: str, colors: dict):
        super().__init__()
        self._name = name
        self._colors = colors
        _ENGINES.append(self)

    def _color(self, mode, state) -> str:
        if mode == QIcon.Disabled:
            return self._colors["disabled"]
        if state == QIcon.On:
            return self._colors["on"]
        if mode in (QIcon.Active, QIcon.Selected):
            return self._colors["active"]
        return self._colors["normal"]

    def pixmap(self, size, mode, state):
        return self.scaledPixmap(size, mode, state, 1.0)

    def scaledPixmap(self, size, mode, state, scale):
        w, h = round(size.width() * scale), round(size.height() * scale)
        px = QPixmap.fromImage(_render(self._name, self._color(mode, state), w, h,
                                       _stroke_for(size.width())))
        px.setDevicePixelRatio(scale)
        return px

    def paint(self, painter, rect, mode, state):
        ratio = painter.device().devicePixelRatioF() if painter.device() else 1.0
        img = _render(self._name, self._color(mode, state), round(rect.width() * ratio),
                      round(rect.height() * ratio), _stroke_for(rect.width()))
        painter.drawImage(QRectF(rect), img)

    def actualSize(self, size, mode, state):
        return size

    def clone(self):
        return _LineIconEngine(self._name, dict(self._colors))

    def key(self):
        return "magsapp-line"

    def isNull(self):
        return False


@lru_cache(maxsize=512)
def icon(name: str, color: str = None, size: int = 18, active_color: str = None) -> QIcon:
    """Icono con color normal y, opcional, un color distinto al pasar el mouse o estar activo.
    `size` queda por compatibilidad: el icono se dibuja al tamaño que pida cada widget."""
    colors = {
        "normal": color or C["text_2"],
        "active": active_color or C["text"],
        "on": active_color or C["accent"],
        "disabled": C["text_3"],
    }
    return QIcon(_LineIconEngine(name, colors))


class IconButton(QToolButton):
    """Botón plano de icono (y texto opcional a la derecha). Se pinta solo: fondo circular o en píldora que
    aparece con un fundido al pasar el mouse, icono que se aclara y anillo de foco al llegar con el teclado."""
    PAD = 10      # margen lateral con texto
    GAP = 6       # entre icono y texto

    def __init__(self, parent=None):
        super().__init__(parent)
        self._fade = HoverFade(self)
        self._kbd_focus = False
        self.setAutoRaise(True)
        self.setCursor(Qt.PointingHandCursor)

    def sizeHint(self):
        if not self.text():
            return super().sizeHint()
        iw = self.iconSize().width() if not self.icon().isNull() else 0
        tw = self.fontMetrics().horizontalAdvance(self.text())
        return QSize(self.PAD * 2 + iw + (self.GAP if iw else 0) + tw, max(self.height(), 28))

    def minimumSizeHint(self):
        return self.sizeHint()

    def focusInEvent(self, e):
        self._kbd_focus = e.reason() in (Qt.TabFocusReason, Qt.BacktabFocusReason, Qt.ShortcutFocusReason)
        super().focusInEvent(e)

    def focusOutEvent(self, e):
        self._kbd_focus = False
        super().focusOutEvent(e)

    @staticmethod
    def _snap(v: float, ratio: float) -> float:
        """Alinea a píxel físico: evita iconos borrosos a escalas como 125 %."""
        return round(v * ratio) / ratio

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        r = QRectF(self.rect())
        radius = r.height() / 2
        enabled = self.isEnabled()
        hover = self._fade.value if enabled else 0.0
        checked = self.isCheckable() and self.isChecked()

        alpha = 0.07 * hover
        if checked:
            alpha = max(alpha, 0.09)
        if self.isDown():
            alpha = 0.13
        if alpha > 0.002:
            p.setPen(Qt.NoPen)
            p.setBrush(overlay(alpha))
            p.drawRoundedRect(r, radius, radius)
        if self._kbd_focus and self.hasFocus():
            p.setPen(QPen(QColor(C["accent"]), 1.2))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(r.adjusted(0.6, 0.6, -0.6, -0.6), radius - 0.6, radius - 0.6)

        ratio = self.devicePixelRatioF()
        ic = self.icon()
        isz = self.iconSize() if not ic.isNull() else QSize(0, 0)
        text = self.text()
        tw = self.fontMetrics().horizontalAdvance(text) if text else 0
        content_w = isz.width() + (self.GAP if text and isz.width() else 0) + tw
        x = self._snap((r.width() - content_w) / 2, ratio)
        y = self._snap((r.height() - isz.height()) / 2, ratio)

        if not ic.isNull():
            state = QIcon.On if checked else QIcon.Off
            if not enabled:
                p.drawPixmap(QPointF(x, y), ic.pixmap(isz, ratio, QIcon.Disabled, state))
            elif checked or hover <= 0.001:
                p.drawPixmap(QPointF(x, y), ic.pixmap(isz, ratio, QIcon.Normal, state))
            else:
                # Fundido entre el color normal y el activo
                p.setOpacity(1.0 - hover)
                p.drawPixmap(QPointF(x, y), ic.pixmap(isz, ratio, QIcon.Normal, state))
                p.setOpacity(hover)
                p.drawPixmap(QPointF(x, y), ic.pixmap(isz, ratio, QIcon.Active, state))
                p.setOpacity(1.0)

        if text:
            tx = x + isz.width() + (self.GAP if isz.width() else 0)
            color = C["text_3"] if not enabled else mix(C["text_2"], C["text"], max(hover, 1.0 if checked else 0.0))
            p.setPen(QColor(color))
            p.drawText(QRectF(tx, 0, tw + 2, r.height()), Qt.AlignVCenter | Qt.AlignLeft, text)
        p.end()


def tool_button(name: str, tooltip: str = "", size: int = 32, icon_size: int = 18,
                text: str = "", color: str = None) -> QToolButton:
    """Botón plano con icono (y texto opcional a la derecha): circular, o en píldora si lleva texto."""
    b = IconButton()
    b.setIcon(icon(name, color))
    b.setIconSize(QSize(icon_size, icon_size))
    b.setToolTip(tooltip)
    if text:
        b.setText(text)
        b.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        b.setFixedHeight(size)
    else:
        b.setFixedSize(size, size)
    return b
