"""
MAGSAPP - Genera los recursos gráficos de assets/
================================================
- magsapp.ico : icono de la app en varios tamaños (16 a 256 px), para la ventana, la barra de tareas,
                los accesos directos y el protocolo magsapp://.
- magsapp.png : 256 px, lo usan las notificaciones de Windows.
- arrow_down.svg / check.svg : flechita de los combos y tilde de las casillas (tema).

Ya vienen en el repositorio; solo hace falta correrlo si se cambia el diseño:
    python tools/generar_iconos.py
"""

import os
import struct
import sys

from PySide6.QtCore import QByteArray, QBuffer, QIODevice, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QGuiApplication, QImage, QLinearGradient, QPainter, QPainterPath

ASSETS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets")
SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)

ACCENT_TOP = "#25b88f"
ACCENT_BOTTOM = "#14826a"
DARK = "#0b1114"


def draw_icon(size: int) -> QImage:
    """Cuadrado redondeado verde con un globo de chat blanco y una "M"."""
    img = QImage(size, size, QImage.Format_ARGB32)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    s = size / 256.0

    # Fondo
    margin = 8 * s if size >= 32 else 0.5
    rect = QRectF(margin, margin, size - 2 * margin, size - 2 * margin)
    grad = QLinearGradient(rect.topLeft(), rect.bottomLeft())
    grad.setColorAt(0, QColor(ACCENT_TOP))
    grad.setColorAt(1, QColor(ACCENT_BOTTOM))
    p.setPen(Qt.NoPen)
    p.setBrush(grad)
    p.drawRoundedRect(rect, 56 * s, 56 * s)

    # Globo de chat (círculo + colita abajo a la izquierda)
    bubble = QPainterPath()
    cx, cy, r = 128 * s, 120 * s, 78 * s
    bubble.addEllipse(QPointF(cx, cy), r, r)
    tail = QPainterPath()
    tail.moveTo(70 * s, 160 * s)
    tail.lineTo(56 * s, 206 * s)
    tail.lineTo(108 * s, 190 * s)
    tail.closeSubpath()
    bubble = bubble.united(tail)
    p.setBrush(QColor("#ffffff"))
    p.drawPath(bubble)

    # "M" (en tamaños chicos no se lee: queda solo el globo)
    if size >= 24:
        f = QFont("Segoe UI")
        f.setWeight(QFont.Black)
        f.setPixelSize(max(1, round(96 * s)))
        p.setFont(f)
        p.setPen(QColor(ACCENT_BOTTOM))
        p.drawText(QRectF(cx - r, cy - r, 2 * r, 2 * r), Qt.AlignCenter, "M")
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
    _app = QGuiApplication(sys.argv[:1])   # QFont necesita una aplicación Qt
    os.makedirs(ASSETS, exist_ok=True)
    images = {size: draw_icon(size) for size in SIZES}
    write_ico(os.path.join(ASSETS, "magsapp.ico"), images)
    images[256].save(os.path.join(ASSETS, "magsapp.png"), "PNG")
    with open(os.path.join(ASSETS, "arrow_down.svg"), "w", encoding="utf-8") as f:
        f.write(ARROW_DOWN_SVG)
    with open(os.path.join(ASSETS, "check.svg"), "w", encoding="utf-8") as f:
        f.write(CHECK_SVG)
    print(f"Recursos generados en {ASSETS}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
