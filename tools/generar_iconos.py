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

from PySide6.QtCore import QByteArray, QBuffer, QIODevice, QPointF, QRectF, Qt
from PySide6.QtGui import (QColor, QGuiApplication, QImage, QLinearGradient, QPainter, QPainterPath, QPen,
                           QRadialGradient, QTransform)

ASSETS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets")
SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)
ANDROID_RES = os.path.join(os.path.dirname(ASSETS), "android", "app", "src", "main", "res")

DARK = "#0b1114"
# Geometría en un lienzo de 256 px: globo, aro blanco y disco interior comparten el centro
CX, CY, BUBBLE_R = 130, 118, 102
RING_R, RING_W = 80, 13
DISC_R = RING_R - RING_W / 2


def _path(pts) -> QPainterPath:
    """Camino cerrado: punto inicial y luego tríos de control de curvas cúbicas."""
    f = QPainterPath()
    f.moveTo(*pts[0])
    for c in pts[1:]:
        f.cubicTo(*(v for xy in c for v in xy))
    f.closeSubpath()
    return f


def bubble_path() -> QPainterPath:
    """Globo de chat redondo con la colita abajo a la izquierda, con la unión suavizada."""
    bubble = QPainterPath()
    bubble.addEllipse(QPointF(CX, CY), BUBBLE_R, BUBBLE_R)
    tail = QPainterPath()
    tail.moveTo(52, 168)
    tail.cubicTo(46, 196, 36, 216, 24, 236)          # borde izquierdo de la cola, apenas curvo
    tail.cubicTo(50, 232, 76, 222, 100, 210)         # borde de abajo, vuelve al globo
    tail.closeSubpath()
    return bubble.united(tail)


def flame_path() -> QPainterPath:
    """Llama: punta central apenas inclinada y dos lengüetas laterales con escotes redondeados."""
    return _path([(131, 56),
                  [(137, 78), (150, 92), (149, 116)],     # baja por la derecha de la punta
                  [(149, 123), (158, 114), (166, 97)],    # escote y lengüeta derecha
                  [(176, 113), (180, 130), (180, 148)],
                  [(180, 173), (157, 192), (128, 192)],   # panza
                  [(99, 192), (76, 173), (76, 148)],
                  [(76, 129), (81, 112), (91, 97)],       # lengüeta izquierda
                  [(99, 114), (108, 123), (108, 116)],    # escote izquierdo
                  [(108, 90), (121, 78), (131, 56)]])


def core_path() -> QPainterPath:
    """Corazón amarillo de la llama, apoyado en la base."""
    return _path([(129, 120),
                  [(134, 138), (153, 148), (153, 166)],
                  [(153, 180), (142, 188), (128, 188)],
                  [(114, 188), (103, 180), (103, 166)],
                  [(103, 155), (109, 147), (115, 141)],
                  [(116, 150), (120, 154), (123, 154)],
                  [(120, 143), (122, 132), (129, 120)]])


def _color(c) -> QColor:
    return QColor(c) if isinstance(c, str) else QColor(*c)


def _grad(x0, y0, x1, y1, *stops) -> QLinearGradient:
    g = QLinearGradient(x0, y0, x1, y1)
    for pos, color in stops:
        g.setColorAt(pos, _color(color))
    return g


def _radial(cx, cy, r, *stops) -> QRadialGradient:
    g = QRadialGradient(cx, cy, r)
    for pos, color in stops:
        g.setColorAt(pos, _color(color))
    return g


def _inner_stroke(p: QPainter, path: QPainterPath, color, width: float) -> None:
    """Borde por dentro de la figura (no la agranda ni la empasta como un contorno común)."""
    p.save()
    p.setClipPath(path)
    p.setBrush(Qt.NoBrush)
    p.setPen(QPen(_color(color), width * 2, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    p.drawPath(path)
    p.restore()


def _placed(path: QPainterPath, k: float, dx: float, dy: float) -> QPainterPath:
    """Escala la figura k veces desde la base de la llama (128, 192) y la corre (dx, dy)."""
    t = QTransform()
    t.translate(128 + dx, 192 + dy)
    t.scale(k, k)
    t.translate(-128, -192)
    return t.map(path)


def _paint(p: QPainter, detail: int) -> None:
    """Dibuja el ícono en coordenadas de 256 px. detail: 2 = completo (48 px o más), 1 = 24-40 px, 0 = 16-20 px."""
    bubble = bubble_path()
    if detail == 2:   # sombra del globo: capas cada vez más anchas y tenues, corridas hacia abajo
        shadow = QTransform.fromTranslate(0, 4).map(bubble)
        p.setBrush(Qt.NoBrush)
        for i in range(5, 0, -1):
            p.setPen(QPen(QColor(0, 45, 20, 6), i * 1.8, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            p.drawPath(shadow)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(0, 45, 20, 22))
        p.drawPath(shadow)
    # cuerpo del globo
    p.setPen(Qt.NoPen)
    p.setBrush(_grad(60, 20, 190, 236, (0, "#5ce793"), (0.55, "#2cc463"), (1, "#14974a")))
    p.drawPath(bubble)
    _inner_stroke(p, bubble, (8, 105, 50, 150 if detail == 2 else 200), 3.5 if detail == 2 else 7)
    if detail == 2:   # luz de borde arriba a la izquierda
        p.save()
        p.setClipPath(bubble)
        p.setPen(QPen(QColor(255, 255, 255, 120), 3, Qt.SolidLine, Qt.RoundCap))
        r = BUBBLE_R - 6
        p.drawArc(QRectF(CX - r, CY - r, 2 * r, 2 * r), 100 * 16, 62 * 16)
        p.restore()

    if detail >= 1:
        disc = QPainterPath()
        disc.addEllipse(QPointF(CX, CY), DISC_R, DISC_R)
        ring = QPainterPath()
        ring.addEllipse(QPointF(CX, CY), RING_R + RING_W / 2, RING_R + RING_W / 2)
        ring = ring.subtracted(disc)
        p.setPen(Qt.NoPen)
        if detail == 2:   # sombra del aro sobre el globo
            p.setBrush(QColor(0, 60, 25, 45))
            p.drawPath(QTransform.fromTranslate(0, 3).map(ring))
        p.setBrush(_grad(0, CY - RING_R, 0, CY + RING_R, (0, "#ffffff"), (1, "#dcefe4")))
        p.drawPath(ring)
        if detail == 2:
            _inner_stroke(p, ring, (120, 170, 140, 90), 1.2)
        # disco interior con algo de profundidad
        p.setBrush(_radial(CX - 14, CY - 30, DISC_R * 1.45, (0, "#4fe08b"), (0.6, "#28bb5f"), (1, "#178f47")))
        p.drawPath(disc)
        if detail == 2:
            p.save()   # sombra interna arriba: el disco queda hundido respecto del aro
            p.setClipPath(disc)
            hole = QPainterPath()
            hole.addRect(QRectF(0, 0, 256, 256))
            inner = QPainterPath()
            inner.addEllipse(QPointF(CX, CY + 5), DISC_R, DISC_R)
            p.setBrush(QColor(0, 50, 20, 60))
            p.drawPath(hole.subtracted(inner))
            # resplandor cálido detrás de la llama
            p.setBrush(_radial(CX - 2, CY + 26, 62, (0, (255, 214, 90, 120)), (1, (255, 214, 90, 0))))
            p.drawPath(disc)
            p.restore()

    # llama: en los tamaños chicos más grande, para que se lea
    k, dy = {2: (0.93, -4), 1: (1.04, -4), 0: (1.22, -4)}[detail]
    flame = _placed(flame_path(), k, CX - 2 - 128, dy)
    fb = flame.boundingRect()
    p.setPen(Qt.NoPen)
    p.setBrush(_grad(0, fb.top(), 0, fb.bottom(), (0, "#ffb53d"), (0.5, "#ff7a1f"), (1, "#e2400c")))
    p.drawPath(flame)
    _inner_stroke(p, flame, (190, 50, 8, 200 if detail == 2 else 235), 3 if detail == 2 else 6)
    core = _placed(core_path(), k, CX - 2 - 128, dy)
    cb = core.boundingRect()
    p.setBrush(_grad(0, cb.top(), 0, cb.bottom(), (0, "#fff7c8"), (0.45, "#ffe066"), (1, "#ffb42a")))
    p.drawPath(core)
    if detail == 2:
        _inner_stroke(p, core, (240, 140, 20, 150), 1.6)
        # reflejo en el flanco izquierdo de la llama
        hl = QPainterPath()
        hl.moveTo(fb.left() + fb.width() * 0.12, fb.top() + fb.height() * 0.5)
        hl.cubicTo(fb.left() + fb.width() * 0.1, fb.top() + fb.height() * 0.64,
                   fb.left() + fb.width() * 0.14, fb.top() + fb.height() * 0.74,
                   fb.left() + fb.width() * 0.2, fb.top() + fb.height() * 0.8)
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QColor(255, 240, 210, 130), 3.2, Qt.SolidLine, Qt.RoundCap))
        p.drawPath(hl)


def draw_icon(size: int, background: str = None) -> QImage:
    """Globo verde estilo WhatsApp con aro blanco y llama naranja con corazón amarillo (opción «Brasa»).
    Se dibuja a 4x (mínimo 512 px) y se achica con suavizado; en los tamaños chicos se simplifica el diseño."""
    detail = 2 if size >= 48 else (1 if size >= 24 else 0)
    big = max(512, size * 4)
    img = QImage(big, big, QImage.Format_ARGB32_Premultiplied)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    p.scale(big / 256, big / 256)
    _paint(p, detail)
    p.end()
    out = img.scaled(size, size, Qt.IgnoreAspectRatio, Qt.SmoothTransformation).convertToFormat(QImage.Format_ARGB32)
    if background:
        base = QImage(size, size, QImage.Format_ARGB32)
        base.fill(QColor(background))
        bp = QPainter(base)
        bp.drawImage(0, 0, out)
        bp.end()
        out = base
    return out


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
    # Android: logo de la barra superior (el ícono del launcher es adaptativo: mipmap-anydpi-v26/ic_launcher.xml)
    logo_dir = os.path.join(ANDROID_RES, "drawable-nodpi")
    os.makedirs(logo_dir, exist_ok=True)
    draw_icon(192).save(os.path.join(logo_dir, "app_logo.png"), "PNG")
    # Android: marca (logo + nombre) de la barra superior, la misma que la app de escritorio; 32 dp de alto a 4x
    sys.path.insert(0, os.path.dirname(ASSETS))
    from app.ui.brand import brand_image
    brand_image(32, 4).save(os.path.join(logo_dir, "app_brand.png"), "PNG")
    # capa de adelante del ícono adaptativo: PNG de 432 px con el globo ocupando 2/3 (zona segura de 66 sobre 108)
    fg = QImage(432, 432, QImage.Format_ARGB32)
    fg.fill(Qt.transparent)
    fp = QPainter(fg)
    fp.setRenderHint(QPainter.SmoothPixmapTransform)
    fp.drawImage(72, 72, draw_icon(288))
    fp.end()
    fg.save(os.path.join(logo_dir, "ic_launcher_foreground.png"), "PNG")
    old_xml = os.path.join(ANDROID_RES, "drawable", "ic_launcher_foreground.xml")
    if os.path.exists(old_xml):
        os.remove(old_xml)
    with open(os.path.join(ASSETS, "arrow_down.svg"), "w", encoding="utf-8") as f:
        f.write(ARROW_DOWN_SVG)
    with open(os.path.join(ASSETS, "check.svg"), "w", encoding="utf-8") as f:
        f.write(CHECK_SVG)
    print(f"Recursos generados en {ASSETS}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
