"""
Advanced WhatsApp - Genera los recursos gráficos de assets/
================================================
- advancedwhatsapp.ico : icono de la app en varios tamaños (16 a 256 px), para la ventana, la barra de tareas,
                los accesos directos y el protocolo advancedwhatsapp://.
- advancedwhatsapp.png : 256 px, lo usan las notificaciones de Windows.
- arrow_down.svg / check.svg : flechita de los combos y tilde de las casillas (tema).

Ya vienen en el repositorio; solo hace falta correrlo si se cambia el diseño:
    python tools/generar_iconos.py
"""

import os
import struct
import sys

from PySide6.QtCore import QByteArray, QBuffer, QIODevice, QPointF, QRectF, Qt
from PySide6.QtGui import (QColor, QGuiApplication, QImage, QLinearGradient, QPainter, QPainterPath, QPen,
                           QRadialGradient, QTransform)

ASSETS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets")
SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)

BG_TOP = "#1c0e09"
BG_BOTTOM = "#050505"
FIRE_TOP = "#ffd23f"
FIRE_MID = "#ff7a18"
FIRE_BOTTOM = "#d92b0b"
DARK = "#0b1114"


def flame_path(s: float) -> QPainterPath:
    """Llama (coordenadas de un lienzo de 256 px, escaladas por s)."""
    f = QPainterPath()
    f.moveTo(128 * s, 62 * s)
    f.cubicTo(138 * s, 88 * s, 168 * s, 100 * s, 168 * s, 134 * s)
    f.cubicTo(168 * s, 158 * s, 150 * s, 176 * s, 128 * s, 176 * s)
    f.cubicTo(106 * s, 176 * s, 88 * s, 158 * s, 88 * s, 134 * s)
    f.cubicTo(88 * s, 116 * s, 98 * s, 104 * s, 108 * s, 96 * s)
    f.cubicTo(108 * s, 108 * s, 116 * s, 114 * s, 120 * s, 114 * s)
    f.cubicTo(114 * s, 96 * s, 118 * s, 78 * s, 128 * s, 62 * s)
    f.closeSubpath()
    return f


def draw_icon(size: int) -> QImage:
    """Cuadrado redondeado negro con brasas, globo de chat de fuego y una llama negra adentro."""
    img = QImage(size, size, QImage.Format_ARGB32)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    s = size / 256.0

    # Fondo negro con resplandor de brasas abajo
    margin = 8 * s if size >= 32 else 0.5
    rect = QRectF(margin, margin, size - 2 * margin, size - 2 * margin)
    bg = QPainterPath()
    bg.addRoundedRect(rect, 56 * s, 56 * s)
    grad = QLinearGradient(rect.topLeft(), rect.bottomLeft())
    grad.setColorAt(0, QColor(BG_TOP))
    grad.setColorAt(1, QColor(BG_BOTTOM))
    p.setPen(Qt.NoPen)
    p.setBrush(grad)
    p.drawPath(bg)
    p.save()
    p.setClipPath(bg)
    glow = QRadialGradient(QPointF(128 * s, 256 * s), 190 * s)
    glow.setColorAt(0, QColor(255, 90, 10, 150))
    glow.setColorAt(0.55, QColor(200, 40, 5, 50))
    glow.setColorAt(1, QColor(0, 0, 0, 0))
    p.setBrush(glow)
    p.drawRect(rect)
    p.restore()
    if size >= 32:
        p.setPen(QPen(QColor(255, 110, 20, 90), max(1.0, 2 * s)))
        p.setBrush(Qt.NoBrush)
        p.drawPath(bg)
        p.setPen(Qt.NoPen)

    # Globo de chat (círculo + colita abajo a la izquierda) con degradado de fuego
    bubble = QPainterPath()
    cx, cy, r = 128 * s, 120 * s, 78 * s
    bubble.addEllipse(QPointF(cx, cy), r, r)
    tail = QPainterPath()
    tail.moveTo(70 * s, 160 * s)
    tail.lineTo(56 * s, 206 * s)
    tail.lineTo(108 * s, 190 * s)
    tail.closeSubpath()
    bubble = bubble.united(tail)
    fire = QLinearGradient(QPointF(0, 40 * s), QPointF(0, 208 * s))
    fire.setColorAt(0, QColor(FIRE_TOP))
    fire.setColorAt(0.45, QColor(FIRE_MID))
    fire.setColorAt(1, QColor(FIRE_BOTTOM))
    p.setBrush(fire)
    p.drawPath(bubble)

    # Llama negra (espacio negativo) con una llamita naranja en el centro
    flame = flame_path(s)
    p.setBrush(QColor("#0a0605"))
    p.drawPath(flame)
    if size >= 24:
        inner = QTransform()
        inner.translate(128 * s, 174 * s)
        inner.scale(0.5, 0.5)
        inner.translate(-128 * s, -174 * s)
        ig = QLinearGradient(QPointF(0, 120 * s), QPointF(0, 176 * s))
        ig.setColorAt(0, QColor(FIRE_TOP))
        ig.setColorAt(1, QColor(FIRE_MID))
        p.setBrush(ig)
        p.drawPath(inner.map(flame))
    p.end()
    return img


def png_bytes(img: QImage) -> bytes:
    data = QByteArray()
    buf = QBuffer(data)
    buf.open(QIODevice.WriteOnly)
    img.save(buf, "PNG")
    buf.close()
    return bytes(data)


def write_ico(path: str, images: dict) -> None:
    """ICO con una entrada PNG por tamaño (formato aceptado por Windows Vista en adelante)."""
    entries = [(size, png_bytes(img)) for size, img in sorted(images.items())]
    header = struct.pack("<HHH", 0, 1, len(entries))
    offset = 6 + 16 * len(entries)
    directory, blobs = b"", b""
    for size, blob in entries:
        dim = 0 if size >= 256 else size
        directory += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(blob), offset)
        offset += len(blob)
        blobs += blob
    with open(path, "wb") as f:
        f.write(header + directory + blobs)


ARROW_DOWN_SVG = """<svg xmlns="http://www.w3.org/2000/svg" width="10" height="6" viewBox="0 0 10 6">
  <path d="M1 1l4 4 4-4" fill="none" stroke="#8a979e" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/>
</svg>
"""

CHECK_SVG = f"""<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12">
  <path d="M2.5 6.2l2.4 2.4 4.6-5" fill="none" stroke="{DARK}" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/>
</svg>
"""


def main() -> int:
    _app = QGuiApplication(sys.argv[:1])   # QImage/QPainter necesitan una aplicación Qt
    os.makedirs(ASSETS, exist_ok=True)
    images = {size: draw_icon(size) for size in SIZES}
    write_ico(os.path.join(ASSETS, "advancedwhatsapp.ico"), images)
    images[256].save(os.path.join(ASSETS, "advancedwhatsapp.png"), "PNG")
    with open(os.path.join(ASSETS, "arrow_down.svg"), "w", encoding="utf-8") as f:
        f.write(ARROW_DOWN_SVG)
    with open(os.path.join(ASSETS, "check.svg"), "w", encoding="utf-8") as f:
        f.write(CHECK_SVG)
    print(f"Recursos generados en {ASSETS}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
