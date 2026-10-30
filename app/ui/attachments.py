"""Adjuntos del compositor: zona de soltar archivos, fichas con icono/miniatura y barra de progreso de envío."""
import os

from PySide6.QtCore import Qt, Signal, QRectF, QSize
from PySide6.QtGui import QPainter, QColor, QPen, QPixmap
from PySide6.QtWidgets import (
    QWidget, QFrame, QHBoxLayout, QVBoxLayout, QLabel, QProgressBar, QScrollArea
)

from app.ui.theme import C, R_MD, R_SM
from app.ui.icons import tool_button, pixmap as icon_pixmap

IMAGE_EXT = (".png", ".jpg", ".jpeg", ".webp", ".gif")
VIDEO_EXT = (".mp4", ".mov", ".mkv", ".avi", ".webm", ".3gp")
MAX_ATTACHMENTS = 10


def kind_for(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    if ext in IMAGE_EXT:
        return "image"
    if ext in VIDEO_EXT:
        return "video"
    return "document"


def size_text(path: str) -> str:
    kb = os.path.getsize(path) / 1024
    return f"{kb / 1024:.1f} MB" if kb >= 1024 else f"{max(1, round(kb))} KB"


class DropOverlay(QWidget):
    """Velo con borde punteado que aparece sobre el chat mientras se arrastran archivos."""

    def __init__(self, parent):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self._count = 0
        self.hide()

    def show_over(self, count: int):
        self._count = count
        self.setGeometry(self.parentWidget().rect())
        self.raise_()
        self.show()
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        bg = QColor(C["chat_bg"])
        bg.setAlpha(225)
        p.fillRect(self.rect(), bg)
        frame = QRectF(self.rect()).adjusted(14, 14, -14, -14)
        pen = QPen(QColor(C["accent"]), 2, Qt.DashLine)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(frame, R_MD, R_MD)

        ico = icon_pixmap("download", C["accent"], 56)
        cx, cy = frame.center().x(), frame.center().y()
        p.drawPixmap(int(cx - 28), int(cy - 56), ico)
        p.setPen(QColor(C["text"]))
        f = p.font()
        f.setPixelSize(17)
        f.setWeight(f.Weight.DemiBold)
        p.setFont(f)
        title = "Suelta para adjuntar" if self._count <= 1 else f"Suelta para adjuntar {self._count} archivos"
        p.drawText(QRectF(frame.left(), cy + 14, frame.width(), 26), Qt.AlignHCenter | Qt.AlignTop, title)
        f.setPixelSize(12)
        f.setWeight(f.Weight.Normal)
        p.setFont(f)
        p.setPen(QColor(C["text_2"]))
        p.drawText(QRectF(frame.left(), cy + 42, frame.width(), 20), Qt.AlignHCenter | Qt.AlignTop,
                   "Imágenes, videos y documentos")
        p.end()


class _Chip(QFrame):
    removed = Signal(str)

    def __init__(self, path: str):
        super().__init__()
        self.path = path
        self.setObjectName("attChip")
        self.setStyleSheet(f"#attChip {{ background-color: {C['raised']}; border: 1px solid {C['border']}; "
                           f"border-radius: {R_SM}px; }} #attChip QLabel {{ background: transparent; border: none; }}")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(6, 5, 2, 5)
        lay.setSpacing(8)

        self.kind = kind_for(path)
        self.ico = QLabel()
        self.ico.setFixedSize(36, 36)
        self.ico.setAlignment(Qt.AlignCenter)
        thumb = None
        if self.kind == "image":
            px = QPixmap(path)
            if not px.isNull():
                thumb = px.scaled(72, 72, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
                thumb = thumb.copy((thumb.width() - 72) // 2, (thumb.height() - 72) // 2, 72, 72)
                thumb.setDevicePixelRatio(2.0)
        if thumb is not None:
            self.ico.setPixmap(thumb)
        else:
            glyph = {"image": "image", "video": "video"}.get(self.kind, "file")
            self.ico.setPixmap(icon_pixmap(glyph, C["accent"], 24))
        lay.addWidget(self.ico)

        col = QVBoxLayout()
        col.setSpacing(0)
        name = os.path.basename(path)
        self.lbl_name = QLabel(name if len(name) <= 28 else name[:18] + "…" + name[-8:])
        self.lbl_name.setToolTip(name)
        self.lbl_name.setStyleSheet(f"color: {C['text']}; font-size: 12px;")
        self.lbl_info = QLabel(size_text(path))
        self.lbl_info.setStyleSheet(f"color: {C['text_2']}; font-size: 11px;")
        col.addWidget(self.lbl_name)
        col.addWidget(self.lbl_info)
        lay.addLayout(col)

        self.btn_close = tool_button("close", "Quitar adjunto", size=24, icon_size=12)
        self.btn_close.clicked.connect(lambda: self.removed.emit(self.path))
        lay.addWidget(self.btn_close)

    def set_state(self, state: str):
        """'sending' | 'done' | 'error' | '' (en espera)."""
        self.btn_close.setVisible(state in ("", "error"))
        if state == "done":
            self.ico.setPixmap(icon_pixmap("check", C["accent"], 24))
            self.lbl_info.setText("Enviado")
        elif state == "sending":
            self.lbl_info.setText("Enviando…")
        elif state == "error":
            self.lbl_info.setText("Error al enviar")
            self.lbl_info.setStyleSheet(f"color: {C['danger']}; font-size: 11px;")
        else:
            self.lbl_info.setText(size_text(self.path))
            self.lbl_info.setStyleSheet(f"color: {C['text_2']}; font-size: 11px;")


class AttachmentBar(QFrame):
    """Fichas de los archivos por enviar + barra de progreso global durante el envío."""
    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("attBar")
        self.setStyleSheet(f"#attBar {{ background-color: {C['surface']}; border-top: 1px solid {C['border']}; }}")
        self.setVisible(False)
        self._chips: dict = {}
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 6, 10, 6)
        root.setSpacing(4)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.scroll.setFixedHeight(62)
        self.scroll.setStyleSheet("QScrollArea { background: transparent; } QScrollArea > QWidget > QWidget "
                                  "{ background: transparent; }")
        holder = QWidget()
        self._row = QHBoxLayout(holder)
        self._row.setContentsMargins(0, 0, 0, 0)
        self._row.setSpacing(8)
        self._row.addStretch(1)
        self.scroll.setWidget(holder)
        root.addWidget(self.scroll)

        self.progress_row = QWidget()
        pr = QHBoxLayout(self.progress_row)
        pr.setContentsMargins(0, 0, 0, 0)
        pr.setSpacing(8)
        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(6)
        pr.addWidget(self.progress, stretch=1)
        self.lbl_progress = QLabel("")
        self.lbl_progress.setStyleSheet(f"color: {C['text_2']}; font-size: 11px;")
        pr.addWidget(self.lbl_progress)
        self.progress_row.setVisible(False)
        root.addWidget(self.progress_row)

    def paths(self) -> list:
        return list(self._chips)

    def add(self, path: str) -> bool:
        if path in self._chips or len(self._chips) >= MAX_ATTACHMENTS or not os.path.isfile(path):
            return False
        chip = _Chip(path)
        chip.removed.connect(self.remove)
        self._chips[path] = chip
        self._row.insertWidget(self._row.count() - 1, chip)
        self.setVisible(True)
        self.changed.emit()
        return True

    def remove(self, path: str):
        chip = self._chips.pop(path, None)
        if chip is None:
            return
        chip.setParent(None)
        chip.deleteLater()
        if not self._chips:
            self.setVisible(False)
        self.changed.emit()

    def clear(self):
        for p in list(self._chips):
            chip = self._chips.pop(p)
            chip.setParent(None)
            chip.deleteLater()
        self.progress_row.setVisible(False)
        self.setVisible(False)
        self.changed.emit()

    def set_state(self, path: str, state: str):
        if path in self._chips:
            self._chips[path].set_state(state)

    def show_progress(self, visible: bool):
        self.progress_row.setVisible(visible)
        if visible:
            self.progress.setValue(0)

    def set_progress(self, fraction: float, label: str):
        self.progress.setValue(int(max(0.0, min(1.0, fraction)) * 1000))
        self.lbl_progress.setText(label)
