"""
Advanced WhatsApp - Marca (logo + nombre como una sola pieza)
=============================================================
El globo con la llama y el nombre se dibujan juntos: «ADVANCED» chico, espaciado y en degradé de brasa, y «WhatsApp»
grande debajo. Las dos líneas tienen el mismo ancho (se ajusta el espaciado de la de arriba), así el bloque de texto
queda cerrado y alineado con el logo en vez de parecer un label suelto.
La misma imagen se usa en Android (tools/generar_iconos.py la guarda como drawable-nodpi/app_brand.png).
"""

import os
from functools import lru_cache

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QImage, QLinearGradient, QPainter, QPainterPath, QPixmap

from app.ui.theme import C

LOGO_PNG = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                        "assets", "advancedwhatsapp.png")


def _family(*names: str) -> str:
    have = set(QFontDatabase.families())
    return next((n for n in names if n in have), "Segoe UI")


def _text_path(text: str, font: QFont, x: float, baseline: float) -> QPainterPath:
    path = QPainterPath()
    path.addText(QPointF(x, baseline), font, text)
    return path


def brand_image(logo: int, ratio: float = 1.0) -> QImage:
    """Logo de `logo` px lógicos con el nombre a la derecha, dibujado a `ratio` píxeles por px lógico."""
    family = _family("Segoe UI Variable Display", "Segoe UI")
    # «WhatsApp»: peso fuerte y letras apenas apretadas, como un logotipo
    big = QFont(family)
    big.setPixelSize(round(logo * 0.5))
    big.setWeight(QFont.Bold)
    big.setLetterSpacing(QFont.AbsoluteSpacing, -logo * 0.012)
    # «ADVANCED»: mayúsculas chicas, separadas hasta medir lo mismo que «WhatsApp»
    small = QFont(family)
    small.setPixelSize(round(logo * 0.205))
    small.setWeight(QFont.DemiBold)
    word, top = "WhatsApp", "ADVANCED"
    big_w = _text_path(word, big, 0, 0).boundingRect().width()
    top_w0 = _text_path(top, small, 0, 0).boundingRect().width()
    small.setLetterSpacing(QFont.AbsoluteSpacing, max(0.0, (big_w - top_w0) / (len(top) - 1)))

    # alturas visuales (de las mayúsculas reales, no de la caja de la fuente) para centrar el bloque con el logo
    cap_small = -_text_path("H", small, 0, 0).boundingRect().top()
    cap_big = -_text_path("H", big, 0, 0).boundingRect().top()
    space = logo * 0.1
    x_text = logo * 1.17
    base_small = (logo - (cap_small + space + cap_big)) / 2 + cap_small
    base_big = base_small + space + cap_big
    p_top = _text_path(top, small, x_text, base_small)
    p_big = _text_path(word, big, x_text, base_big)
    # el lienzo tiene el alto del logo más lo que baja la «p»
    width = max(p_top.boundingRect().right(), p_big.boundingRect().right()) + logo * 0.04
    height = max(float(logo), p_big.boundingRect().bottom() + 1)

    img = QImage(round(width * ratio), round(height * ratio), QImage.Format_ARGB32_Premultiplied)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    p.setRenderHint(QPainter.SmoothPixmapTransform)
    p.scale(ratio, ratio)
    src = QImage(LOGO_PNG)
    if not src.isNull():
        src = src.scaled(round(logo * ratio), round(logo * ratio), Qt.KeepAspectRatio, Qt.SmoothTransformation)
        p.drawImage(QRectF(0, 0, logo, logo), src)
    p.setPen(Qt.NoPen)
    tb = p_top.boundingRect()
    grad = QLinearGradient(tb.left(), 0, tb.right(), 0)
    grad.setColorAt(0, QColor(C["ember_hi"]))
    grad.setColorAt(1, QColor(C["ember"]))
    p.setBrush(grad)
    p.drawPath(p_top)
    p.setBrush(QColor(C["text"]))
    p.drawPath(p_big)
    p.end()
    return img


@lru_cache(maxsize=8)
def brand_pixmap(logo: int = 64) -> QPixmap:
    """La marca como pixmap a la escala de la pantalla (nítida en HiDPI)."""
    from app.ui.icons import _screen_ratio
    ratio = _screen_ratio()
    pm = QPixmap.fromImage(brand_image(logo, ratio))
    pm.setDevicePixelRatio(ratio)
    return pm
