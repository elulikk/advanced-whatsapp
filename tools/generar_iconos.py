"""
Advanced WhatsApp - Genera los recursos gráficos de assets/
================================================
- advancedwhatsapp.ico : icono de la app en varios tamaños (16 a 256 px), para la ventana, la barra de tareas,
                los accesos directos y el protocolo advancedwhatsapp://.
- advancedwhatsapp.png : 256 px, lo usan las notificaciones de Windows y el logo dentro de la app.
- android/.../drawable-nodpi/app_logo.png : logo de la barra superior de la app de Android.
- arrow_down.svg / check.svg : flechita de los combos y tilde de las casillas (tema).

Ya vienen en el repositorio; solo hace falta correrlo si se cambia el diseño:
    python tools/generar_iconos.py
"""

import os
import struct
import sys

from PySide6.QtCore import QByteArray, QBuffer, QIODevice, QPointF, Qt
from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter, QPainterPath, QTransform

ASSETS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets")
SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)

DARK = "#0b1114"
GREEN = "#25D366"
ANDROID_RES = os.path.join(os.path.dirname(ASSETS), "android", "app", "src", "main", "res")


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


def bubble_path(s: float) -> QPainterPath:
    """Globo de chat redondo con la colita abajo a la izquierda (lienzo de 256 px escalado por s)."""
    bubble = QPainterPath()
    bubble.addEllipse(QPointF(128 * s, 122 * s), 104 * s, 104 * s)
    tail = QPainterPath()
    tail.moveTo(56 * s, 180 * s)
    tail.lineTo(22 * s, 240 * s)
    tail.lineTo(96 * s, 216 * s)
    tail.closeSubpath()
    return bubble.united(tail)


def draw_icon(size: int, background: str = None) -> QImage:
    """Globo verde estilo WhatsApp con la llama blanca adentro (opción «Minimal»). Fondo transparente salvo `background`."""
    img = QImage(size, size, QImage.Format_ARGB32)
    img.fill(QColor(background) if background else Qt.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    s = size / 256.0
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(GREEN))
    p.drawPath(bubble_path(s))
    # Llama blanca, un poco más grande que la original y centrada en el globo
    t = QTransform()
    t.translate(128 * s, 124 * s)
    t.scale(1.25, 1.25)
    t.translate(-128 * s, -119 * s)
    p.setBrush(QColor("#ffffff"))
    p.drawPath(t.map(flame_path(s)))
    p.end()
    return img


# Puntos de la llama (lienzo de 256 px) en el orden de flame_path: inicio y luego tríos de control de cada curva
FLAME_PTS = [(128, 62), [(138, 88), (168, 100), (168, 134)], [(168, 158), (150, 176), (128, 176)],
             [(106, 176), (88, 158), (88, 134)], [(88, 116), (98, 104), (108, 96)], [(108, 108), (116, 114), (120, 114)],
             [(114, 96), (118, 78), (128, 62)]]


def android_foreground_xml() -> str:
    """Capa de adelante del ícono adaptativo (vector de 108x108 con la zona segura de 66 en el centro)."""
    k, off = 72 / 256, 18

    def tr(x, y, flame=False):
        if flame:   # misma transformación que en draw_icon
            x, y = (x - 128) * 1.25 + 128, (y - 119) * 1.25 + 124
        return f"{off + x * k:.2f},{off + y * k:.2f}"

    cx, cy, r = off + 128 * k, off + 122 * k, 104 * k
    circle = f"M{cx:.2f},{cy - r:.2f}a{r:.2f},{r:.2f} 0 1,1 0,{2 * r:.2f}a{r:.2f},{r:.2f} 0 1,1 0,{-2 * r:.2f}z"
    tail = f"M{tr(56, 180)}L{tr(22, 240)}L{tr(96, 216)}z"
    start, *curves = FLAME_PTS
    flame = f"M{tr(*start, flame=True)}" + "".join("C" + " ".join(tr(*pt, flame=True) for pt in c) for c in curves) + "z"
    return f"""<?xml version="1.0" encoding="utf-8"?>
<!-- Generado por tools/generar_iconos.py: globo verde con la llama blanca -->
<vector xmlns:android="http://schemas.android.com/apk/res/android"
    android:width="108dp" android:height="108dp" android:viewportWidth="108" android:viewportHeight="108">
    <path android:fillColor="{GREEN}" android:pathData="{circle}" />
    <path android:fillColor="{GREEN}" android:pathData="{tail}" />
    <path android:fillColor="#FFFFFF" android:pathData="{flame}" />
</vector>
"""


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
    # Android: logo de la barra superior (el ícono del launcher es adaptativo, en mipmap-anydpi-v26 + drawable/ic_launcher_*.xml)
    logo_dir = os.path.join(ANDROID_RES, "drawable-nodpi")
    os.makedirs(logo_dir, exist_ok=True)
    draw_icon(192).save(os.path.join(logo_dir, "app_logo.png"), "PNG")
    with open(os.path.join(ANDROID_RES, "drawable", "ic_launcher_foreground.xml"), "w", encoding="utf-8", newline=chr(10)) as f:
        f.write(android_foreground_xml())
    with open(os.path.join(ASSETS, "arrow_down.svg"), "w", encoding="utf-8") as f:
        f.write(ARROW_DOWN_SVG)
    with open(os.path.join(ASSETS, "check.svg"), "w", encoding="utf-8") as f:
        f.write(CHECK_SVG)
    print(f"Recursos generados en {ASSETS}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
