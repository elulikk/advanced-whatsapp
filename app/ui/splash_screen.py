import math
import random
import time

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import (QColor, QFont, QGuiApplication, QLinearGradient, QPainter, QPainterPath, QPen,
                           QRadialGradient)
from PySide6.QtWidgets import QApplication, QWidget

from app.ui.theme import C

W, H = 340, 480
SCALE = 3.5               # la llama se dibuja en un lienzo de 256 px (ver tools/generar_iconos.py) y se agranda
OX, OY = 30.0, 16.0       # esquina de la llama dentro de la ventana
FLAME_TOP, FLAME_BOTTOM = OY, OY + 114 * SCALE

FIRE_TOP = QColor("#ffd23f")
FIRE_MID = QColor("#ff7a18")
FIRE_BOTTOM = QColor("#d92b0b")


def _pt(x: float, y: float, dx: float = 0.0) -> QPointF:
    return QPointF(OX + (x - 88 + dx) * SCALE, OY + (y - 62) * SCALE)


def flame_path(t: float = 0.0) -> QPainterPath:
    """Silueta de la llama (la misma del icono); la punta y los lados se mecen un poco con `t`."""
    a, b = math.sin(t * 2.3) * 2.2, math.sin(t * 3.1 + 1.0) * 1.6
    f = QPainterPath()
    f.moveTo(_pt(128, 62, a))
    f.cubicTo(_pt(138, 88, a), _pt(168, 100, b * 0.4), _pt(168, 134))
    f.cubicTo(_pt(168, 158), _pt(150, 176), _pt(128, 176))
    f.cubicTo(_pt(106, 176), _pt(88, 158), _pt(88, 134))
    f.cubicTo(_pt(88, 116, -b * 0.4), _pt(98, 104), _pt(108, 96, -a * 0.5))
    f.cubicTo(_pt(108, 108, -a * 0.5), _pt(116, 114), _pt(120, 114, a * 0.3))
    f.cubicTo(_pt(114, 96, a), _pt(118, 78, a), _pt(128, 62, a))
    f.closeSubpath()
    return f


class SplashScreen(QWidget):
    """Pantalla de inicio con forma de llama: se llena de fuego (con el porcentaje) mientras se abre la base
    de datos y se conecta WhatsApp."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setObjectName("splash")
        self.resize(W, H)
        self._center_on_screen()
        self._target = 0.0
        self._shown = 0.0
        self._status = "Iniciando aplicación..."
        self._t0 = time.monotonic()
        self._t = 0.0
        self._sparks = []
        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._tick)

    def _center_on_screen(self):
        screen = QGuiApplication.primaryScreen()
        if screen:
            geo = screen.availableGeometry()
            self.move(geo.x() + (geo.width() - self.width()) // 2, geo.y() + (geo.height() - self.height()) // 2)

    # ── API (la usa main.py) ──

    def set_status(self, text: str, progress: int = None):
        self._status = text
        if progress is not None:
            self._target = float(max(0, min(100, progress)))
        self.update()

    def wait_until_full(self, timeout: float = 1.5):
        """Deja que la llama termine de llenarse antes de cerrar la pantalla."""
        end = time.monotonic() + timeout
        while self._shown < 99.5 and time.monotonic() < end:
            QApplication.processEvents()
            time.sleep(0.01)
        end = time.monotonic() + 0.25
        while time.monotonic() < end:
            QApplication.processEvents()
            time.sleep(0.01)

    def finish(self, target_window):
        self.close()

    # ── Animación ──

    def showEvent(self, e):
        super().showEvent(e)
        self._timer.start()

    def hideEvent(self, e):
        self._timer.stop()
        super().hideEvent(e)

    def _tick(self):
        self._t = time.monotonic() - self._t0
        diff = self._target - self._shown
        if diff > 0:
            self._shown = min(self._target, self._shown + max(diff * 0.07, 0.12))
        # Chispas que suben desde el nivel del fuego
        level_y = FLAME_BOTTOM - (FLAME_BOTTOM - FLAME_TOP) * self._shown / 100.0
        if random.random() < 0.55:
            self._sparks.append([random.uniform(OX + 60, OX + 220), min(level_y + random.uniform(0, 30), FLAME_BOTTOM - 10),
                                 random.uniform(-0.5, 0.5), random.uniform(0.8, 2.2), 1.0, random.uniform(1.2, 3.0)])
        for s in self._sparks:
            s[0] += s[2] + math.sin(self._t * 4 + s[1] * 0.05) * 0.4
            s[1] -= s[3]
            s[4] -= 0.012
        self._sparks = [s for s in self._sparks if s[4] > 0]
        self.update()

    # ── Dibujo ──

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.TextAntialiasing)
        t = self._t
        flame = flame_path(t)
        pulse = 0.75 + 0.25 * math.sin(t * 3.0)

        # Resplandor exterior
        p.setBrush(Qt.NoBrush)
        for i in range(1, 8):
            alpha = int(34 * pulse / (i * 0.8))
            p.setPen(QPen(QColor(255, 110, 20, alpha), i * 5, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            p.drawPath(flame)

        # Interior negro con un poco de calor abajo
        body = QLinearGradient(0, FLAME_TOP, 0, FLAME_BOTTOM)
        body.setColorAt(0, QColor("#120a07"))
        body.setColorAt(1, QColor("#050404"))
        p.setPen(Qt.NoPen)
        p.setBrush(body)
        p.drawPath(flame)

        # Fuego que sube según el porcentaje (dos olas con distinta fase)
        level = FLAME_BOTTOM - (FLAME_BOTTOM - FLAME_TOP) * self._shown / 100.0
        fire = QLinearGradient(0, FLAME_BOTTOM, 0, FLAME_TOP)
        fire.setColorAt(0, FIRE_BOTTOM)
        fire.setColorAt(0.5, FIRE_MID)
        fire.setColorAt(1, FIRE_TOP)
        back = QColor(FIRE_BOTTOM)
        back.setAlpha(150)
        if self._shown > 0.2:
            for phase, amp, brush in ((1.7, 7.0, back), (0.0, 5.0, fire)):
                wave = QPainterPath()
                wave.moveTo(0, H)
                for x in range(0, W + 6, 6):
                    wave.lineTo(x, level + math.sin(x * 0.045 + t * 3.2 + phase) * amp)
                wave.lineTo(W, H)
                wave.closeSubpath()
                p.setBrush(brush)
                p.drawPath(wave.intersected(flame))

        # Borde de brasa
        edge = QLinearGradient(0, FLAME_TOP, 0, FLAME_BOTTOM)
        edge.setColorAt(0, QColor(255, 190, 80, 230))
        edge.setColorAt(1, QColor(217, 43, 11, 230))
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(edge, 2.2))
        p.drawPath(flame)

        # Chispas
        p.setPen(Qt.NoPen)
        for x, y, _vx, _vy, life, size in self._sparks:
            col = QColor(255, int(120 + 100 * life), 30, int(255 * life))
            p.setBrush(col)
            p.drawEllipse(QPointF(x, y), size, size)

        cx = OX + 40 * SCALE
        # Nombre y porcentaje (con contorno oscuro para que se lea sobre el fuego)
        self._draw_text(p, "ADVANCED WHATSAPP", cx, FLAME_TOP + 0.46 * (FLAME_BOTTOM - FLAME_TOP), 13, QFont.DemiBold, 2.5)
        self._draw_text(p, f"{int(self._shown)}%", cx, FLAME_TOP + 0.64 * (FLAME_BOTTOM - FLAME_TOP), 56, QFont.Black, 0)

        # Estado debajo de la llama (con contorno para que se lea sobre cualquier fondo)
        cx = OX + 40 * SCALE
        self._draw_text(p, self._status, cx, FLAME_BOTTOM + 30, 13, QFont.DemiBold, 0.5)
        p.end()

    @staticmethod
    def _draw_text(p: QPainter, text: str, cx: float, cy: float, px: int, weight, spacing: float):
        f = QFont("Segoe UI")
        f.setPixelSize(px)
        f.setWeight(weight)
        f.setLetterSpacing(QFont.AbsoluteSpacing, spacing)
        path = QPainterPath()
        path.addText(0, 0, f, text)
        box = path.boundingRect()
        path.translate(cx - box.center().x(), cy - box.center().y())
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QColor(10, 4, 2, 200), 4, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.drawPath(path)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#ffffff"))
        p.drawPath(path)
