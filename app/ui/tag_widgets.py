"""Tags visuales: dibujo de las formas, paleta arrastrable junto a la lista de chats y panel de configuración."""
import math

from PySide6.QtCore import Qt, Signal, QRectF, QPointF, QSize, QMimeData, QTimer, QPoint, QRect
from PySide6.QtGui import (QPainter, QPainterPath, QColor, QFont, QPen, QPixmap, QDrag, QIcon, QFontMetricsF,
                           QPolygonF, QLinearGradient)
from PySide6.QtWidgets import (QWidget, QHBoxLayout, QVBoxLayout, QLabel, QToolButton, QScrollArea, QFrame,
                               QDialog, QListWidget, QListWidgetItem, QLineEdit, QComboBox, QSpinBox, QCheckBox,
                               QSlider, QPushButton, QFormLayout, QMenu, QColorDialog, QMessageBox, QApplication,
                               QAbstractItemView, QWidgetAction, QGridLayout, QSizePolicy, QLayout, QButtonGroup)

from app import tags as tagdb
from app.config import get_pref, set_pref, get_tag_palette_view, set_tag_palette_view
from app.ui.theme import C, R_LG
from app.ui.icons import icon, tool_button
from app.ui.effects import HoverFade, mix, overlay

TAG_MIME = "application/x-advancedwhatsapp-tag"

PRESET_COLORS = ["#2f7bf5", "#53bdeb", "#1fa37f", "#7bc96f", "#f5d76e", "#d9a33a", "#f08a4b", "#e5534b",
                 "#d6457a", "#a36be0", "#8a979e", "#ffffff", "#0b1114"]


# ── Dibujo ──

def _poly(points) -> QPainterPath:
    path = QPainterPath()
    path.addPolygon(QPolygonF([QPointF(x, y) for x, y in points]))
    path.closeSubpath()
    return path


def shape_path(shape: str, r: QRectF) -> QPainterPath:
    """Contorno de la forma dentro de `r`."""
    cx, cy, w, h = r.center().x(), r.center().y(), r.width(), r.height()
    path = QPainterPath()
    if shape == "square":
        path.addRoundedRect(r, w * 0.22, h * 0.22)
    elif shape == "diamond":
        path = _poly([(cx, r.top()), (r.right(), cy), (cx, r.bottom()), (r.left(), cy)])
    elif shape in ("triangle", "alert"):
        top, bottom = r.top() + h * 0.04, r.bottom() - h * 0.06
        pts = [(cx, top), (r.right(), bottom), (r.left(), bottom)]
        if shape == "alert":   # esquinas redondeadas tipo señal de advertencia
            path = QPainterPath()
            rad = w * 0.12
            for i in range(3):
                p0, p1, p2 = pts[i - 1], pts[i], pts[(i + 1) % 3]
                a = _toward(p1, p0, rad)
                b = _toward(p1, p2, rad)
                if i == 0:
                    path.moveTo(*a)
                else:
                    path.lineTo(*a)
                path.quadTo(QPointF(*p1), QPointF(*b))
            path.closeSubpath()
        else:
            path = _poly(pts)
    elif shape == "octagon":
        # Lados planos tocando el cuadrado: radio = apotema / cos(22,5°)
        rad = min(w, h) / 2 / math.cos(math.radians(22.5))
        path = _poly([(cx + rad * math.cos(math.radians(45 * i + 22.5)), cy + rad * math.sin(math.radians(45 * i + 22.5)))
                      for i in range(8)])
    elif shape == "house":
        x, y = r.left(), r.top()
        eave = y + h * 0.46
        path = _poly([(cx, y + h * 0.02), (r.right(), eave), (r.right() - w * 0.13, eave),
                      (r.right() - w * 0.13, r.bottom()), (x + w * 0.13, r.bottom()), (x + w * 0.13, eave),
                      (x, eave)])
    elif shape == "hexagon":
        rad = min(w, h) / 2
        path = _poly([(cx + rad * math.cos(math.radians(60 * i - 90)), cy + rad * math.sin(math.radians(60 * i - 90)))
                      for i in range(6)])
    elif shape == "star":
        ro, ri = min(w, h) / 2, min(w, h) / 2 * 0.48
        pts = []
        for i in range(10):
            rr = ro if i % 2 == 0 else ri
            ang = math.radians(36 * i - 90)
            pts.append((cx + rr * math.cos(ang), cy + 0.06 * h + rr * math.sin(ang)))
        path = _poly(pts)
    elif shape == "heart":
        x, y = r.left(), r.top()
        path.moveTo(cx, y + h * 0.95)
        path.cubicTo(x - w * 0.05, y + h * 0.55, x + w * 0.02, y + h * 0.05, cx - w * 0.02, y + h * 0.26)
        path.lineTo(cx + w * 0.02, y + h * 0.26)
        path.cubicTo(r.right() - w * 0.02, y + h * 0.05, r.right() + w * 0.05, y + h * 0.55, cx, y + h * 0.95)
        path.closeSubpath()
    elif shape == "exclaim":
        # Signo de admiración solo, sin fondo
        bw = w * 0.36
        path.addRoundedRect(QRectF(cx - bw / 2, r.top(), bw, h * 0.62), bw * 0.45, bw * 0.45)
        d = w * 0.36
        path.addEllipse(QRectF(cx - d / 2, r.bottom() - d, d, d))
    else:   # circle
        path.addEllipse(r)
    return path


def _toward(p, q, dist):
    dx, dy = q[0] - p[0], q[1] - p[1]
    n = math.hypot(dx, dy) or 1
    return p[0] + dx / n * dist, p[1] + dy / n * dist


def paint_tag(p: QPainter, rect: QRectF, tag: dict, ring: str = None):
    """Dibuja el tag en `rect` (cuadrado). `ring`: color del anillo que lo separa de lo que tiene debajo."""
    p.save()
    p.setRenderHint(QPainter.Antialiasing)
    size = min(rect.width(), rect.height())
    ring_w = max(1.5, size * 0.11) if ring else 0
    r = QRectF(rect.center().x() - size / 2, rect.center().y() - size / 2, size, size)
    r = r.adjusted(ring_w / 2, ring_w / 2, -ring_w / 2, -ring_w / 2)
    shape = tag.get("shape") or "circle"
    color = QColor(tag.get("color") or "#2f7bf5")
    path = shape_path(shape, r)
    if ring:
        p.setPen(QPen(QColor(ring), ring_w, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    else:
        p.setPen(Qt.NoPen)
    p.setBrush(color)
    p.drawPath(path)

    text = (tag.get("text") or "").strip()
    if shape == "alert" and not text:
        text = "!"
    if text and shape != "exclaim":
        f = QFont("Segoe UI")
        f.setBold(True)
        px = size * (0.62 if len(text) == 1 else 0.46 if len(text) == 2 else 0.36)
        if shape in ("triangle", "alert", "star", "heart", "diamond", "house"):
            px *= 0.82
        f.setPixelSize(max(6, round(px)))
        p.setFont(f)
        fm = QFontMetricsF(f)
        while fm.horizontalAdvance(text) > r.width() * 0.84 and f.pixelSize() > 6:
            f.setPixelSize(f.pixelSize() - 1)
            p.setFont(f)
            fm = QFontMetricsF(f)
        tr = QRectF(r)
        if shape in ("triangle", "alert"):
            tr.adjust(0, r.height() * 0.22, 0, 0)
        elif shape in ("star", "heart"):
            tr.adjust(0, r.height() * 0.04, 0, -r.height() * 0.08)
        elif shape == "house":
            tr.adjust(0, r.height() * 0.3, 0, 0)
        p.setPen(QColor(tag.get("text_color") or "#ffffff"))
        p.drawText(tr, Qt.AlignCenter, text)
    p.restore()


def tag_pixmap(tag: dict, size: int = 20, ring: str = None, ratio: float = None) -> QPixmap:
    ratio = ratio or (QApplication.instance().devicePixelRatio() if QApplication.instance() else 1.0)
    pm = QPixmap(round(size * ratio), round(size * ratio))
    pm.setDevicePixelRatio(ratio)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    paint_tag(p, QRectF(0, 0, size, size), tag, ring)
    p.end()
    return pm


def paint_avatar_tags(p: QPainter, size: float, tags: list, ring: str):
    """Tags sobre un avatar circular de `size` px: abajo a la derecha, arriba a la derecha, abajo a la
    izquierda y arriba a la izquierda (como en la app oficial con el estado). Más de 4: '+n'."""
    if not tags:
        return
    b = max(12.0, round(size * 0.42))
    o = -1.0   # un pelito afuera del círculo
    spots = [(size - b - o, size - b - o), (size - b - o, o), (o, size - b - o), (o, o)]
    shown = tags[:4] if len(tags) <= 4 else tags[:3]
    for t, (x, y) in zip(shown, spots):
        paint_tag(p, QRectF(x, y, b, b), t, ring)
    if len(tags) > 4:
        x, y = spots[3]
        more = {"shape": "circle", "color": C["raised"], "text": f"+{len(tags) - 3}", "text_color": C["text"]}
        paint_tag(p, QRectF(x, y, b, b), more, ring)


def paint_row_effect(p: QPainter, card: QRectF, color: str, radius: float):
    """Efecto «teñir la fila» de la lista de chats: un velo del color que se desvanece hacia la derecha y una
    franja fina a la izquierda. Sobrio a propósito: se lee de un vistazo sin tapar el texto."""
    if not color:
        return
    p.save()
    p.setRenderHint(QPainter.Antialiasing)
    clip = QPainterPath()
    clip.addRoundedRect(card, radius, radius)
    p.setClipPath(clip)
    c0, c1 = QColor(color), QColor(color)
    c0.setAlphaF(0.20)
    c1.setAlphaF(0.03)
    grad = QLinearGradient(card.topLeft(), card.topRight())
    grad.setColorAt(0.0, c0)
    grad.setColorAt(1.0, c1)
    p.fillRect(card, grad)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(color))
    inset = max(6.0, card.height() * 0.2)
    p.drawRoundedRect(QRectF(card.left() + 3, card.top() + inset, 3, card.height() - 2 * inset), 1.5, 1.5)
    p.restore()


def tags_tooltip(tags: list, last_message_time: int = 0) -> str:
    if not tags:
        return ""
    lines = [f"• {t['name']} ({tagdb.describe_expiry(t, t.get('assigned_at'), last_message_time)})" for t in tags]
    return "Tags:\n" + "\n".join(lines) + "\n\nClic derecho en el chat para quitarlos."


# ── Paleta arrastrable ──

class TagChip(QWidget):
    """Un tag de la paleta: se arrastra sobre un chat de la lista (o sobre el chat abierto) para ponérselo.
    En vista grilla es solo el icono; en vista lista, icono + nombre + vencimiento."""
    edit_requested = Signal(int)
    clicked = Signal(int)

    SIZE = 24        # icono
    GRID = 32        # celda de la grilla
    ROW_H = 30       # fila de la lista

    def __init__(self, tag: dict, mode: str = "grid", parent=None):
        super().__init__(parent)
        self.tag = tag
        self.mode = mode
        self._press = None
        self._fade = HoverFade(self)
        self._expiry = tagdb.short_expiry(tag)
        if mode == "list":
            self.setFixedHeight(self.ROW_H)
            self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        else:
            self.setFixedSize(self.GRID, self.GRID)
        self.setCursor(Qt.OpenHandCursor)
        self.setToolTip(f"{tag['name']}  ·  {tagdb.describe_expiry(tag)}\n"
                        "Arrastralo sobre un chat para ponérselo. Clic derecho: editar.")
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(lambda _: self.edit_requested.emit(tag["id"]))

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        if self._fade.value > 0.002:
            p.setPen(Qt.NoPen)
            p.setBrush(overlay(0.07 * self._fade.value))
            p.drawRoundedRect(QRectF(self.rect()), 8, 8)
        top = (self.height() - self.SIZE) / 2
        if self.mode == "list":
            paint_tag(p, QRectF(6, top, self.SIZE, self.SIZE), self.tag, ring=C["surface"])
            f = QFont("Segoe UI")
            f.setPixelSize(12)
            p.setFont(f)
            fm = QFontMetricsF(f)
            x = 6 + self.SIZE + 10
            small = QFont(f)
            small.setPixelSize(11)
            exp_w = QFontMetricsF(small).horizontalAdvance(self._expiry)
            avail = self.width() - x - 8
            show_exp = avail - exp_w - 12 > 60
            name_w = avail - (exp_w + 12 if show_exp else 0)
            name = fm.elidedText(self.tag["name"], Qt.ElideRight, name_w)
            p.setPen(QColor(C["text"]))
            p.drawText(QRectF(x, 0, name_w, self.height()), Qt.AlignVCenter | Qt.AlignLeft, name)
            if show_exp:
                p.setFont(small)
                p.setPen(QColor(C["text_3"]))
                p.drawText(QRectF(self.width() - 8 - exp_w, 0, exp_w, self.height()),
                           Qt.AlignVCenter | Qt.AlignRight, self._expiry)
        else:
            left = (self.width() - self.SIZE) / 2
            paint_tag(p, QRectF(left, top, self.SIZE, self.SIZE), self.tag, ring=C["surface"])
        p.end()

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._press = e.position().toPoint()
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self._press is None or not (e.buttons() & Qt.LeftButton):
            return
        if (e.position().toPoint() - self._press).manhattanLength() < QApplication.startDragDistance():
            return
        self._press = None
        drag = QDrag(self)
        mime = QMimeData()
        mime.setData(TAG_MIME, str(self.tag["id"]).encode())   # sin texto: el campo de mensaje no lo pega
        drag.setMimeData(mime)
        drag.setPixmap(tag_pixmap(self.tag, 30, ring=C["panel"]))
        drag.setHotSpot(QPoint(15, 15))
        drag.exec(Qt.CopyAction)

    def mouseReleaseEvent(self, e):
        if self._press is not None and e.button() == Qt.LeftButton:
            self.clicked.emit(self.tag["id"])
        self._press = None
        super().mouseReleaseEvent(e)


def tag_id_from_mime(mime) -> int:
    if mime is None or not mime.hasFormat(TAG_MIME):
        return 0
    try:
        return int(bytes(mime.data(TAG_MIME)).decode())
    except ValueError:
        return 0


class FlowLayout(QLayout):
    """Acomoda los widgets en filas que bajan de renglón cuando no entran (vista grilla)."""

    def __init__(self, parent=None, spacing: int = 2):
        super().__init__(parent)
        self._items = []
        self._spacing = spacing
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item):
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, i):
        return self._items[i] if 0 <= i < len(self._items) else None

    def takeAt(self, i):
        return self._items.pop(i) if 0 <= i < len(self._items) else None

    def expandingDirections(self):
        return Qt.Orientations(0)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._do_layout(QRect(0, 0, width, 0), test=True)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._do_layout(rect, test=False)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        size = QSize()
        for it in self._items:
            size = size.expandedTo(it.minimumSize())
        return size

    def _do_layout(self, rect, test):
        x, y, line_h = rect.x(), rect.y(), 0
        for it in self._items:
            hint = it.sizeHint()
            if x + hint.width() > rect.right() + 1 and line_h > 0:
                x = rect.x()
                y += line_h + self._spacing
                line_h = 0
            if not test:
                it.setGeometry(QRect(QPoint(x, y), hint))
            x += hint.width() + self._spacing
            line_h = max(line_h, hint.height())
        return y + line_h - rect.y()


class TagPalettePanel(QFrame):
    """Panel plegable de tags arrastrables (debajo de los filtros de la lista de chats), en grilla o lista.
    Se abre y se cierra con el botón de tags de la cabecera; recuerda si quedó abierto y la vista elegida."""
    manage_requested = Signal(int)   # id del tag a editar (0 = abrir el panel sin elegir)
    chip_clicked = Signal(int)
    open_changed = Signal(bool)

    GRID_MAX_ROWS = 3
    LIST_MAX_ROWS = 7

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("tagPanel")
        self.setStyleSheet(f"#tagPanel {{ background-color: {C['surface']}; border: 1px solid {C['border']};"
                           f" border-radius: {R_LG}px; }}")
        self._view = get_tag_palette_view()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 4, 4, 6)
        lay.setSpacing(2)

        head = QHBoxLayout()
        head.setSpacing(0)
        title = QLabel("Tags")
        title.setStyleSheet(f"color: {C['accent']}; font-weight: 600; font-size: 12px; background: transparent;")
        head.addWidget(title)
        self.lbl_hint = QLabel("  arrastralos a un chat")
        self.lbl_hint.setStyleSheet(f"color: {C['text_3']}; font-size: 11px; background: transparent;")
        head.addWidget(self.lbl_hint)
        head.addStretch(1)
        self.btn_grid = tool_button("grid", "Vista en grilla (solo iconos)", size=26, icon_size=15)
        self.btn_list = tool_button("list", "Vista en lista (con nombres)", size=26, icon_size=15)
        group = QButtonGroup(self)
        for b, view in ((self.btn_grid, "grid"), (self.btn_list, "list")):
            b.setCheckable(True)
            b.setChecked(view == self._view)
            group.addButton(b)
            b.clicked.connect(lambda _=False, v=view: self.set_view(v))
            head.addWidget(b)
        self.btn_manage = tool_button("settings", "Configurar tags (formas, colores, vencimiento y efectos)",
                                      size=26, icon_size=15)
        self.btn_manage.clicked.connect(lambda: self.manage_requested.emit(0))
        head.addWidget(self.btn_manage)
        btn_close = tool_button("close", "Cerrar el panel de tags", size=26, icon_size=13)
        btn_close.clicked.connect(lambda: self.set_open(False))
        head.addWidget(btn_close)
        lay.addLayout(head)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setStyleSheet("QScrollArea, QScrollArea > QWidget > QWidget { background: transparent; }")
        lay.addWidget(self.scroll)

        self.lbl_empty = QLabel("No hay tags. Creá uno con el engranaje.")
        self.lbl_empty.setObjectName("hint")
        self.lbl_empty.setContentsMargins(0, 4, 0, 4)
        lay.addWidget(self.lbl_empty)

        tagdb.tag_signals.changed.connect(self.rebuild)
        self.rebuild()
        self.setVisible(get_pref("tags_palette_open"))

    def is_open(self) -> bool:
        return not self.isHidden()

    def set_open(self, on: bool):
        if on == self.is_open():
            return
        self.setVisible(on)
        set_pref("tags_palette_open", on)
        if on:
            QTimer.singleShot(0, self._fit_height)
        self.open_changed.emit(on)

    def set_view(self, view: str):
        if view == self._view:
            return
        self._view = view
        set_tag_palette_view(view)
        self.rebuild()

    def rebuild(self):
        old = self.scroll.takeWidget()
        if old is not None:
            old.deleteLater()
        tags = tagdb.list_tags()
        inner = QWidget()
        if self._view == "list":
            box = QVBoxLayout(inner)
            box.setContentsMargins(0, 0, 4, 0)
            box.setSpacing(0)
        else:
            box = FlowLayout(inner, spacing=2)
        for t in tags:
            chip = TagChip(t, self._view)
            chip.edit_requested.connect(self.manage_requested.emit)
            chip.clicked.connect(self.chip_clicked.emit)
            box.addWidget(chip)
        if self._view == "list":
            box.addStretch(1)
        self.scroll.setWidget(inner)
        self._inner = inner
        self.scroll.setVisible(bool(tags))
        self.lbl_empty.setVisible(not tags)
        self.lbl_hint.setVisible(bool(tags))
        self._fit_height()

    def _fit_height(self):
        """El panel crece con los tags hasta un máximo de filas; de ahí en más se desplaza."""
        n = len(tagdb.list_tags())
        if not n:
            return
        if self._view == "list":
            content = n * TagChip.ROW_H
            cap = self.LIST_MAX_ROWS * TagChip.ROW_H
        else:
            width = max(TagChip.GRID, self.scroll.viewport().width() or (self.width() - 14))
            content = self._inner.layout().heightForWidth(width)
            cap = self.GRID_MAX_ROWS * TagChip.GRID + (self.GRID_MAX_ROWS - 1) * 2
        self.scroll.setFixedHeight(max(TagChip.GRID, min(content, cap)))

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self._view == "grid":
            self._fit_height()


class HeaderTagList(QWidget):
    """Tags del chat abierto en la cabecera: forma chica + nombre."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.tags = []
        self.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
        self.setFixedHeight(18)
        self.setVisible(False)

    def set_tags(self, tags: list, last_message_time: int = 0):
        self.tags = list(tags or [])
        self.setToolTip(tags_tooltip(self.tags, last_message_time))
        self.setVisible(bool(self.tags))
        self.updateGeometry()
        self.update()

    def _font(self):
        f = QFont("Segoe UI")
        f.setPixelSize(11)
        f.setBold(True)
        return f

    def sizeHint(self):
        fm = QFontMetricsF(self._font())
        w = sum(14 + 4 + fm.horizontalAdvance(t["name"]) + 10 for t in self.tags)
        return QSize(int(w) + 2, 18)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setFont(self._font())
        fm = QFontMetricsF(self._font())
        x = 0.0
        for t in self.tags:
            paint_tag(p, QRectF(x, 2, 14, 14), t)
            x += 18
            p.setPen(QColor(mix(t.get("color") or C["text"], C["text"], 0.35)))
            tw = fm.horizontalAdvance(t["name"])
            p.drawText(QRectF(x, 0, tw + 2, 18), Qt.AlignVCenter | Qt.AlignLeft, t["name"])
            x += tw + 10
        p.end()


# ── Panel de configuración ──

class ColorButton(QToolButton):
    """Muestra un color; al tocarlo, colores rápidos u «Otro color…». Con allow_none se puede desactivar."""
    color_changed = Signal(str)

    def __init__(self, color: str = "#2f7bf5", parent=None):
        super().__init__(parent)
        self._color = color
        self.setFixedSize(46, 28)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("Elegir color")
        self.clicked.connect(self._pick)

    def color(self) -> str:
        return self._color

    def set_color(self, color: str):
        self._color = color or "#2f7bf5"
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        p.setPen(QPen(QColor(C["border_hi"] if self.underMouse() else C["border"]), 1))
        p.setBrush(QColor(C["raised"]))
        p.drawRoundedRect(r, 7, 7)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(self._color) if self.isEnabled() else QColor(C["text_3"]))
        p.drawRoundedRect(r.adjusted(5, 5, -5, -5), 4, 4)
        p.end()

    def _pick(self):
        menu = QMenu(self)
        grid_host = QWidget()
        grid = QGridLayout(grid_host)
        grid.setContentsMargins(8, 8, 8, 4)
        grid.setSpacing(5)
        for i, col in enumerate(PRESET_COLORS):
            b = QToolButton()
            b.setFixedSize(24, 24)
            b.setCursor(Qt.PointingHandCursor)
            b.setToolTip(col)
            b.setStyleSheet(f"QToolButton {{ background-color: {col}; border: 2px solid "
                            f"{C['text'] if col.lower() == self._color.lower() else C['border']}; border-radius: 12px; }}"
                            f"QToolButton:hover {{ border-color: {C['text']}; }}")
            b.clicked.connect(lambda _=False, c=col: (menu.close(), self._set(c)))
            grid.addWidget(b, i // 7, i % 7)
        wa = QWidgetAction(menu)
        wa.setDefaultWidget(grid_host)
        menu.addAction(wa)
        menu.addSeparator()
        act_other = menu.addAction("Otro color…")
        act = menu.exec(self.mapToGlobal(QPoint(0, self.height() + 2)))
        if act == act_other:
            c = QColorDialog.getColor(QColor(self._color), self.window(), "Elegir color")
            if c.isValid():
                self._set(c.name())

    def _set(self, c: str):
        self.set_color(c)
        self.color_changed.emit(c)


class _TagPreview(QWidget):
    """Vista previa: la fila del chat en la lista (avatar con el tag) y un mini chat con los efectos."""
    ROW_W = 214

    def __init__(self, parent=None):
        super().__init__(parent)
        self.tag = None
        self.setFixedHeight(120)

    def set_tag(self, tag):
        self.tag = tag
        self.update()

    def paintEvent(self, _):
        if not self.tag:
            return
        t = self.tag
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        # Fila de la lista de chats, con su efecto
        lst = QRectF(0, 4, self.ROW_W, self.height() - 8)
        p.setPen(QPen(QColor(C["border"]), 1))
        p.setBrush(QColor(C["panel"]))
        p.drawRoundedRect(lst, 10, 10)
        card = QRectF(6, (self.height() - 58) / 2, self.ROW_W - 12, 58)
        paint_row_effect(p, card, t.get("fx_row"), R_LG)
        av = 40
        p.save()
        p.translate(card.left() + 10, card.center().y() - av / 2)
        path = QPainterPath()
        path.addEllipse(QRectF(0, 0, av, av))
        p.fillPath(path, QColor("#6f5596"))
        f = QFont("Segoe UI")
        f.setPixelSize(15)
        f.setBold(True)
        p.setFont(f)
        p.setPen(QColor("#f2f5f6"))
        p.drawText(QRectF(0, 0, av, av), Qt.AlignCenter, "CL")
        paint_avatar_tags(p, av, [t], C["panel"])
        p.restore()
        tx = card.left() + 10 + av + 12
        f.setPixelSize(13)
        f.setBold(False)
        p.setFont(f)
        p.setPen(QColor(t.get("fx_name") or C["text"]))
        p.drawText(QRectF(tx, card.top() + 9, card.right() - tx, 20), Qt.AlignLeft | Qt.AlignVCenter, "Cliente")
        f.setPixelSize(12)
        p.setFont(f)
        p.setPen(QColor(C["text_2"]))
        p.drawText(QRectF(tx, card.top() + 29, card.right() - tx - 6, 20), Qt.AlignLeft | Qt.AlignVCenter,
                   QFontMetricsF(f).elidedText("Hola, ¿cómo va la reparación?", Qt.ElideRight, card.right() - tx - 6))

        # Mini ventana de chat con los efectos
        f.setPixelSize(11)
        f.setBold(True)
        box = QRectF(self.ROW_W + 10, 4, self.width() - self.ROW_W - 12, self.height() - 8)
        clip = QPainterPath()
        clip.addRoundedRect(box, 10, 10)
        p.save()
        p.setClipPath(clip)
        bg = mix(C["chat_bg"], t["fx_bg"], (t.get("fx_bg_strength") or 18) / 100) if t.get("fx_bg") else QColor(C["chat_bg"])
        p.fillRect(box, bg)
        head = QRectF(box.left(), box.top(), box.width(), 26)
        p.fillRect(head, mix(C["surface"], t["fx_header"], 0.28) if t.get("fx_header") else QColor(C["surface"]))
        if t.get("fx_header"):
            p.fillRect(QRectF(head.left(), head.bottom() - 2, head.width(), 2), QColor(t["fx_header"]))
        paint_tag(p, QRectF(head.left() + 8, head.top() + 6, 14, 14), t)
        f.setPixelSize(11)
        p.setFont(f)
        p.setPen(QColor(C["text"]))
        p.drawText(QRectF(head.left() + 28, head.top(), 200, 26), Qt.AlignVCenter, "Cliente")
        f.setBold(False)
        f.setPixelSize(12)
        p.setFont(f)
        txt = QColor(t["fx_text"]) if t.get("fx_text") else QColor(C["text"])
        for i, (msg, mine) in enumerate((("Hola, ¿cómo va la reparación?", False), ("Ya casi está lista 👍", True))):
            fm = QFontMetricsF(f)
            w = fm.horizontalAdvance(msg) + 20
            y = head.bottom() + 10 + i * 34
            x = box.right() - w - 10 if mine else box.left() + 10
            b = QRectF(x, y, w, 26)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(C["bubble_out"] if mine else C["bubble_in"]))
            p.drawRoundedRect(b, 8, 8)
            p.setPen(txt)
            p.drawText(b, Qt.AlignCenter, msg)
        p.restore()
        p.setPen(QPen(QColor(C["border"]), 1))
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(box, 10, 10)
        p.end()


class TagManagerDialog(QDialog):
    """Crear, editar, ordenar y borrar tags. Los cambios se guardan solos."""

    def __init__(self, select_id: int = 0, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Tags de chats")
        self.resize(860, 640)
        self._loading = False
        self._current_id = 0
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(300)
        self._save_timer.timeout.connect(self._save)

        root = QHBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(16)

        # ── Lista ──
        left = QVBoxLayout()
        left.setSpacing(8)
        lbl = QLabel("TAGS (el orden es la prioridad)")
        lbl.setObjectName("sectionLabel")
        left.addWidget(lbl)
        self.list = QListWidget()
        self.list.setIconSize(QSize(20, 20))
        self.list.setDragDropMode(QAbstractItemView.InternalMove)
        self.list.setDefaultDropAction(Qt.MoveAction)
        self.list.setFixedWidth(230)
        self.list.currentItemChanged.connect(self._on_select)
        self.list.model().rowsMoved.connect(lambda *_: self._save_order())
        left.addWidget(self.list, stretch=1)

        btns = QHBoxLayout()
        btns.setSpacing(4)
        self.btn_new = tool_button("plus", "Nuevo tag", text="Nuevo", size=30, icon_size=16)
        self.btn_new.clicked.connect(self._new)
        btns.addWidget(self.btn_new)
        btns.addStretch()
        self.btn_up = tool_button("up", "Subir (más prioridad)", size=30, icon_size=16)
        self.btn_up.clicked.connect(lambda: self._move(-1))
        btns.addWidget(self.btn_up)
        self.btn_down = tool_button("down", "Bajar (menos prioridad)", size=30, icon_size=16)
        self.btn_down.clicked.connect(lambda: self._move(1))
        btns.addWidget(self.btn_down)
        self.btn_del = tool_button("trash", "Borrar tag", size=30, icon_size=16, color=C["danger"])
        self.btn_del.clicked.connect(self._delete)
        btns.addWidget(self.btn_del)
        left.addLayout(btns)
        hint = QLabel("Si un chat tiene varios tags con efectos, se aplica el del tag más arriba. "
                      "Arrastrá para reordenar.")
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        hint.setFixedWidth(230)
        left.addWidget(hint)
        root.addLayout(left)

        # ── Editor ──
        right_host = QWidget()
        right = QVBoxLayout(right_host)
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(10)

        self.preview = _TagPreview()
        right.addWidget(self.preview)

        form = QFormLayout()
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(8)
        form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)

        self.ed_name = QLineEdit()
        self.ed_name.setMaxLength(40)
        self.ed_name.setPlaceholderText("Ej.: Servicio técnico")
        self.ed_name.textChanged.connect(self._changed)
        form.addRow("Nombre", self.ed_name)

        row = QHBoxLayout()
        self.cb_shape = QComboBox()
        self.cb_shape.setIconSize(QSize(18, 18))
        for key, label in tagdb.SHAPES.items():
            self.cb_shape.addItem(label, key)
        self.cb_shape.currentIndexChanged.connect(self._changed)
        row.addWidget(self.cb_shape, stretch=1)
        self.btn_color = ColorButton()
        self.btn_color.setToolTip("Color del tag")
        self.btn_color.color_changed.connect(self._changed)
        row.addWidget(self.btn_color)
        form.addRow("Forma y color", row)

        row = QHBoxLayout()
        self.ed_text = QLineEdit()
        self.ed_text.setMaxLength(3)
        self.ed_text.setPlaceholderText("Hasta 3 letras (opcional)")
        self.ed_text.textChanged.connect(self._changed)
        row.addWidget(self.ed_text, stretch=1)
        self.btn_text_color = ColorButton("#ffffff")
        self.btn_text_color.setToolTip("Color del texto del tag")
        self.btn_text_color.color_changed.connect(self._changed)
        row.addWidget(self.btn_text_color)
        form.addRow("Texto adentro", row)

        row = QHBoxLayout()
        self.cb_expire = QComboBox()
        for key, label in tagdb.EXPIRE_MODES.items():
            self.cb_expire.addItem(label, key)
        self.cb_expire.currentIndexChanged.connect(self._on_expire_mode)
        row.addWidget(self.cb_expire, stretch=1)
        self.sp_expire = QSpinBox()
        self.sp_expire.setRange(1, 9999)
        self.sp_expire.setFixedWidth(96)
        self.sp_expire.valueChanged.connect(self._changed)
        row.addWidget(self.sp_expire)
        form.addRow("Vencimiento", row)
        self.chk_business = QCheckBox("Contar solo días hábiles (lunes a viernes)")
        self.chk_business.setToolTip("Los sábados y domingos no cuentan. Los feriados sí cuentan.")
        self.chk_business.toggled.connect(self._changed)
        form.addRow("", self.chk_business)
        right.addLayout(form)

        row_lbl = QLabel("EFECTOS EN LA LISTA DE CHATS")
        row_lbl.setObjectName("sectionLabel")
        right.addWidget(row_lbl)
        rowfx = QGridLayout()
        rowfx.setHorizontalSpacing(10)
        rowfx.setVerticalSpacing(8)
        self.chk_row = QCheckBox("Teñir la fila del chat")
        self.btn_row = ColorButton("#e5534b")
        self.chk_name = QCheckBox("Color del nombre")
        self.btn_name = ColorButton("#f5d76e")
        rowfx.addWidget(self.chk_row, 0, 0)
        rowfx.addWidget(self.btn_row, 0, 1)
        rowfx.addWidget(self.chk_name, 1, 0)
        rowfx.addWidget(self.btn_name, 1, 1)
        rowfx.setColumnStretch(2, 1)
        right.addLayout(rowfx)

        fx_lbl = QLabel("EFECTOS EN LA VENTANA DEL CHAT")
        fx_lbl.setObjectName("sectionLabel")
        right.addWidget(fx_lbl)
        fx = QGridLayout()
        fx.setHorizontalSpacing(10)
        fx.setVerticalSpacing(8)
        self.chk_bg = QCheckBox("Teñir el fondo")
        self.btn_bg = ColorButton("#e5534b")
        self.sl_bg = QSlider(Qt.Horizontal)
        self.sl_bg.setRange(5, 60)
        self.sl_bg.setToolTip("Intensidad del tinte")
        self.lbl_bg = QLabel()
        self.lbl_bg.setObjectName("subtle")
        self.lbl_bg.setFixedWidth(36)
        fx.addWidget(self.chk_bg, 0, 0)
        fx.addWidget(self.btn_bg, 0, 1)
        fx.addWidget(self.sl_bg, 0, 2)
        fx.addWidget(self.lbl_bg, 0, 3)
        self.chk_text = QCheckBox("Color de letra de los mensajes")
        self.btn_fx_text = ColorButton("#f5d76e")
        fx.addWidget(self.chk_text, 1, 0)
        fx.addWidget(self.btn_fx_text, 1, 1)
        self.chk_header = QCheckBox("Resaltar la cabecera")
        self.btn_header = ColorButton("#e5534b")
        fx.addWidget(self.chk_header, 2, 0)
        fx.addWidget(self.btn_header, 2, 1)
        fx.setColumnStretch(2, 1)
        for w in (self.chk_bg, self.chk_text, self.chk_header, self.chk_row, self.chk_name):
            w.toggled.connect(self._changed)
        for w in (self.btn_bg, self.btn_fx_text, self.btn_header, self.btn_row, self.btn_name):
            w.color_changed.connect(self._changed)
        self.sl_bg.valueChanged.connect(self._changed)
        right.addLayout(fx)
        right.addStretch(1)

        bottom = QHBoxLayout()
        self.lbl_usage = QLabel()
        self.lbl_usage.setObjectName("hint")
        bottom.addWidget(self.lbl_usage, stretch=1)
        btn_close = QPushButton("Listo")
        btn_close.setDefault(True)
        btn_close.clicked.connect(self.accept)
        bottom.addWidget(btn_close)
        right.addLayout(bottom)
        self.editor = right_host
        root.addWidget(right_host, stretch=1)

        self._reload_list(select_id)

    # ── Lista ──

    def _reload_list(self, select_id: int = 0):
        self._loading = True
        self.list.clear()
        sel = None
        for t in tagdb.list_tags():
            it = QListWidgetItem(QIcon(tag_pixmap(t, 20)), t["name"] or "(sin nombre)")
            it.setData(Qt.UserRole, t["id"])
            self.list.addItem(it)
            if t["id"] == select_id:
                sel = it
        self._loading = False
        if sel is None and self.list.count():
            sel = self.list.item(0)
        if sel:
            self.list.setCurrentItem(sel)
        else:
            self._current_id = 0
            self._load_editor(None)

    def _on_select(self, cur, _prev):
        if self._loading:
            return
        if self._save_timer.isActive():
            self._save_timer.stop()
            self._save()
        tid = cur.data(Qt.UserRole) if cur else 0
        self._current_id = tid
        self._load_editor(tagdb.get_tag(tid) if tid else None)

    def _new(self):
        if self._save_timer.isActive():
            self._save_timer.stop()
            self._save()
        n = len(tagdb.list_tags())
        tid = tagdb.create_tag({"name": f"Tag {n + 1}", "color": PRESET_COLORS[n % 10]})
        self._reload_list(tid)
        self.ed_name.setFocus()
        self.ed_name.selectAll()

    def _delete(self):
        t = tagdb.get_tag(self._current_id)
        if not t:
            return
        n = tagdb.count_chats_with_tag(t["id"])
        extra = f"\nSe va a quitar de {n} chat(s)." if n else ""
        if QMessageBox.question(self, "Borrar tag", f"¿Borrar el tag «{t['name']}»?{extra}",
                                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        self._save_timer.stop()
        row = self.list.currentRow()
        tagdb.delete_tag(t["id"])
        nxt = self.list.item(row + 1) or self.list.item(row - 1)
        self._reload_list(nxt.data(Qt.UserRole) if nxt else 0)

    def _move(self, delta: int):
        row = self.list.currentRow()
        new = row + delta
        if row < 0 or not (0 <= new < self.list.count()):
            return
        self._loading = True
        it = self.list.takeItem(row)
        self.list.insertItem(new, it)
        self.list.setCurrentItem(it)
        self._loading = False
        self._save_order()

    def _save_order(self):
        ids = [self.list.item(i).data(Qt.UserRole) for i in range(self.list.count())]
        tagdb.reorder_tags(ids)

    # ── Editor ──

    def _load_editor(self, t):
        self.editor.setEnabled(t is not None)
        self.btn_del.setEnabled(t is not None)
        self.btn_up.setEnabled(t is not None)
        self.btn_down.setEnabled(t is not None)
        self.preview.set_tag(t)
        if t is None:
            self.lbl_usage.setText("Todavía no hay tags. Tocá «Nuevo» para crear uno.")
            return
        self._loading = True
        self.ed_name.setText(t["name"])
        self.cb_shape.setCurrentIndex(max(0, self.cb_shape.findData(t["shape"])))
        self.btn_color.set_color(t["color"])
        self.ed_text.setText(t.get("text") or "")
        self.btn_text_color.set_color(t.get("text_color") or "#ffffff")
        self.cb_expire.setCurrentIndex(max(0, self.cb_expire.findData(t["expire_mode"])))
        self.sp_expire.setValue(int(t.get("expire_value") or (30 if t["expire_mode"] == "inactive" else 24)))
        self.chk_bg.setChecked(bool(t.get("fx_bg")))
        self.btn_bg.set_color(t.get("fx_bg") or "#e5534b")
        self.sl_bg.setValue(int(t.get("fx_bg_strength") or 18))
        self.chk_text.setChecked(bool(t.get("fx_text")))
        self.btn_fx_text.set_color(t.get("fx_text") or "#f5d76e")
        self.chk_header.setChecked(bool(t.get("fx_header")))
        self.btn_header.set_color(t.get("fx_header") or t["color"])
        self.chk_business.setChecked(bool(t.get("expire_business")))
        self.chk_row.setChecked(bool(t.get("fx_row")))
        self.btn_row.set_color(t.get("fx_row") or t["color"])
        self.chk_name.setChecked(bool(t.get("fx_name")))
        self.btn_name.set_color(t.get("fx_name") or t["color"])
        self._loading = False
        self._sync_controls()
        self._refresh_shape_icons(t["color"])
        n = tagdb.count_chats_with_tag(t["id"])
        self.lbl_usage.setText(f"Puesto en {n} chat(s)." if n else "Todavía no está puesto en ningún chat.")

    def _on_expire_mode(self):
        if not self._loading:
            mode = self.cb_expire.currentData()
            self._loading = True
            self.sp_expire.setValue({"inactive": 30, "days": 10}.get(mode, 24))
            self._loading = False
        self._changed()

    def _sync_controls(self):
        mode = self.cb_expire.currentData()
        self.sp_expire.setVisible(mode != "none")
        self.sp_expire.setSuffix(" h" if mode == "hours" else " días")
        self.chk_business.setVisible(mode in tagdb.DAY_MODES)
        self.btn_bg.setEnabled(self.chk_bg.isChecked())
        self.sl_bg.setEnabled(self.chk_bg.isChecked())
        self.lbl_bg.setText(f"{self.sl_bg.value()} %")
        self.btn_fx_text.setEnabled(self.chk_text.isChecked())
        self.btn_header.setEnabled(self.chk_header.isChecked())
        self.btn_row.setEnabled(self.chk_row.isChecked())
        self.btn_name.setEnabled(self.chk_name.isChecked())
        self.ed_text.setEnabled(self.cb_shape.currentData() != "exclaim")
        self.btn_text_color.setEnabled(self.cb_shape.currentData() != "exclaim")

    def _refresh_shape_icons(self, color: str):
        for i in range(self.cb_shape.count()):
            self.cb_shape.setItemIcon(i, QIcon(tag_pixmap({"shape": self.cb_shape.itemData(i), "color": color}, 18)))

    def _form_data(self) -> dict:
        mode = self.cb_expire.currentData()
        return {
            "name": self.ed_name.text().strip() or "Sin nombre",
            "shape": self.cb_shape.currentData(),
            "color": self.btn_color.color(),
            "text": self.ed_text.text().strip(),
            "text_color": self.btn_text_color.color(),
            "expire_mode": mode,
            "expire_value": self.sp_expire.value() if mode != "none" else 0,
            "fx_bg": self.btn_bg.color() if self.chk_bg.isChecked() else "",
            "fx_bg_strength": self.sl_bg.value(),
            "fx_text": self.btn_fx_text.color() if self.chk_text.isChecked() else "",
            "fx_header": self.btn_header.color() if self.chk_header.isChecked() else "",
            "expire_business": 1 if (self.chk_business.isChecked() and mode in tagdb.DAY_MODES) else 0,
            "fx_row": self.btn_row.color() if self.chk_row.isChecked() else "",
            "fx_name": self.btn_name.color() if self.chk_name.isChecked() else "",
        }

    def _changed(self, *_):
        if self._loading or not self._current_id:
            return
        self._sync_controls()
        data = self._form_data()
        self.preview.set_tag({**data, "id": self._current_id})
        self._refresh_shape_icons(data["color"])
        it = self.list.currentItem()
        if it:
            it.setText(data["name"])
            it.setIcon(QIcon(tag_pixmap(data, 20)))
        self._save_timer.start()

    def _save(self):
        if self._current_id and tagdb.get_tag(self._current_id):
            tagdb.update_tag(self._current_id, self._form_data())

    def done(self, r):
        if self._save_timer.isActive():
            self._save_timer.stop()
            self._save()
        super().done(r)
