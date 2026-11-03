"""Efectos visuales baratos: hover con fundido, mezcla de colores y ventanas con esquinas nativas.

- HoverFade: un valor 0..1 que sigue al mouse con un fundido corto; el widget lo usa al pintarse.
- WindowPolisher: filtro global que, en Windows 11, redondea y sombrea menús, desplegables y tooltips
  con el compositor del sistema (DWM) y oscurece la barra de título de las ventanas.
"""
import sys

from PySide6.QtCore import QObject, QEvent, QVariantAnimation, QEasingCurve, Qt, QPoint
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QWidget, QMenu, QApplication

from app.ui.theme import C, WIN11


def mix(a, b, t: float) -> QColor:
    """Color intermedio entre a y b (t = 0 → a, t = 1 → b), alfa incluido."""
    a, b = QColor(a), QColor(b)
    t = max(0.0, min(1.0, t))
    return QColor.fromRgbF(a.redF() + (b.redF() - a.redF()) * t, a.greenF() + (b.greenF() - a.greenF()) * t,
                           a.blueF() + (b.blueF() - a.blueF()) * t, a.alphaF() + (b.alphaF() - a.alphaF()) * t)


def overlay(alpha: float) -> QColor:
    """Blanco translúcido: sirve de hover sobre cualquier fondo oscuro."""
    return QColor(255, 255, 255, max(0, min(255, round(255 * alpha))))


def menu_below(menu: QMenu, anchor: QWidget, gap: int = 4) -> QPoint:
    """Punto global para abrir `menu` debajo de `anchor`, con su borde derecho alineado al del botón."""
    menu.ensurePolished()
    r = anchor.rect()
    return anchor.mapToGlobal(QPoint(r.right() + 1 - menu.sizeHint().width(), r.bottom() + 1 + gap))


class HoverFade(QObject):
    """Sigue Enter/Leave del widget y anima `value` entre 0 y 1; en cada paso llama a widget.update()."""

    def __init__(self, widget: QWidget, duration: int = 140):
        super().__init__(widget)
        self.value = 0.0
        self._widget = widget
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(duration)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self._anim.valueChanged.connect(self._step)
        widget.setAttribute(Qt.WA_Hover, True)
        widget.installEventFilter(self)

    def _step(self, v):
        self.value = float(v)
        self._widget.update()

    def go(self, target: float):
        target = float(target)
        if self._anim.state() == QVariantAnimation.Running and self._anim.endValue() == target:
            return
        self._anim.stop()
        if abs(self.value - target) < 0.001:
            return
        self._anim.setStartValue(self.value)
        self._anim.setEndValue(target)
        self._anim.start()

    def reset(self):
        """Sin animación (al ocultarse el widget el Leave puede no llegar nunca)."""
        self._anim.stop()
        self.value = 0.0

    def eventFilter(self, obj, event):
        t = event.type()
        if t == QEvent.Enter:
            if obj.isEnabled():
                self.go(1.0)
        elif t == QEvent.Leave:
            self.go(0.0)
        elif t == QEvent.Hide:
            self.reset()
        elif t == QEvent.EnabledChange and not obj.isEnabled():
            self.reset()
            obj.update()
        return False


# ── Windows: DWM ──────────────────────────────────────────────────────────────
_DWMWA_USE_IMMERSIVE_DARK_MODE = 20
_DWMWA_WINDOW_CORNER_PREFERENCE = 33
_DWMWA_BORDER_COLOR = 34
_DWMWA_CAPTION_COLOR = 35
_DWMWCP_ROUND, _DWMWCP_ROUNDSMALL = 2, 3


def _colorref(color: str) -> int:
    c = QColor(color)
    return c.red() | (c.green() << 8) | (c.blue() << 16)


def _dwm_set(widget: QWidget, attr: int, value: int) -> bool:
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        from ctypes import wintypes
        hwnd = wintypes.HWND(int(widget.winId()))
        val = ctypes.c_int(value)
        res = ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, wintypes.DWORD(attr), ctypes.byref(val),
                                                        ctypes.sizeof(val))
        return res == 0
    except Exception:
        return False


def round_corners(widget: QWidget, small: bool = False) -> bool:
    """Esquinas redondeadas, sombra y borde del tema dibujados por Windows 11 (sin efecto en otros sistemas)."""
    if not WIN11:
        return False
    ok = _dwm_set(widget, _DWMWA_WINDOW_CORNER_PREFERENCE, _DWMWCP_ROUNDSMALL if small else _DWMWCP_ROUND)
    _dwm_set(widget, _DWMWA_BORDER_COLOR, _colorref(C["border"]))
    return ok


def dark_title_bar(widget: QWidget):
    """Barra de título oscura (Windows 10/11) y del color del rail en Windows 11."""
    if sys.platform != "win32":
        return
    _dwm_set(widget, _DWMWA_USE_IMMERSIVE_DARK_MODE, 1)
    if WIN11:
        _dwm_set(widget, _DWMWA_CAPTION_COLOR, _colorref(C["rail"]))


class WindowPolisher(QObject):
    """Filtro de la aplicación: aplica los efectos de DWM a cada ventana la primera vez que se muestra."""
    _instance = None

    @classmethod
    def install(cls, app: QApplication):
        if cls._instance is None:
            cls._instance = cls(app)
            app.installEventFilter(cls._instance)
        return cls._instance

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Show and isinstance(obj, QWidget) and obj.isWindow():
            if not obj.property("_advancedwhatsapp_polished"):
                obj.setProperty("_advancedwhatsapp_polished", True)
                self._polish(obj)
        return False

    @staticmethod
    def _polish(w: QWidget):
        if w.testAttribute(Qt.WA_TranslucentBackground):
            return   # popups propios (menú de mensaje, reacciones): ya se pintan redondeados
        if isinstance(w, QMenu) or w.inherits("QComboBoxPrivateContainer"):
            round_corners(w)
        elif w.inherits("QTipLabel"):
            round_corners(w, small=True)
        elif w.windowType() in (Qt.Window, Qt.Dialog):
            dark_title_bar(w)
