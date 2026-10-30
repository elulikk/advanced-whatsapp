"""
MAGSAPP - Panel de Estados
==========================
Columna izquierda: "Mi estado" y los estados de los contactos (últimas 24 h), con el anillo verde de los que
no viste; al tocar uno se abre el visor. A la derecha: mis estados activos y el programador (textos, fotos o
videos con fecha y hora que el gateway publica solo mientras MAGSAPP siga abierto o en la bandeja).
"""

import base64
import os
import time
from datetime import datetime, timedelta

from PySide6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QLabel, QFrame, QDialog, QPlainTextEdit, QPushButton,
    QDateTimeEdit, QSpinBox, QFileDialog, QListWidget, QListWidgetItem, QMessageBox,
    QScrollArea, QToolButton, QButtonGroup, QApplication
)
from PySide6.QtCore import Qt, QTimer, QDateTime, QSize, Signal
from PySide6.QtGui import QPixmap, QPainter, QColor, QPainterPath, QFont

from app.gateway_manager import GatewayManager
from app.ui.theme import C, HEADER_H, R_MD
from app.ui.icons import pixmap, tool_button, icon
from app.ui.chat_list import AvatarWidget, ElidedLabel
from app.ui.status_viewer import StatusViewerDialog, StatusRingAvatar, when_label
from app.ui.effects import HoverFade, overlay

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp"}
VIDEO_EXT = {".mp4", ".mov", ".m4v", ".3gp"}
MAX_TEXT = 700

# Fondos de los estados de texto (tonos de la app oficial)
BACKGROUNDS = ["#005c4b", "#1e6fd9", "#7e3fbf", "#c2185b", "#e0582a", "#6d4c41", "#4f6a78", "#a87b12"]

STATE_INFO = {
    "pending":  ("Programado", C["accent_hi"]),
    "sending":  ("Publicando…", C["warn"]),
    "sent":     ("Publicado", C["text_2"]),
    "failed":   ("Falló", C["danger_text"]),
    "expired":  ("Vencido", C["warn"]),
    "canceled": ("Cancelado", C["text_3"]),
}

DAYS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]


def _media_kind(path: str):
    ext = os.path.splitext(path)[1].lower()
    if ext in IMAGE_EXT:
        return "image"
    if ext in VIDEO_EXT:
        return "video"
    return None


def _day_label(ts: int) -> str:
    d = datetime.fromtimestamp(ts).date()
    today = datetime.now().date()
    if d == today:
        return "Hoy"
    if d == today + timedelta(days=1):
        return "Mañana"
    if d == today - timedelta(days=1):
        return "Ayer"
    return f"{DAYS[d.weekday()].capitalize()} {d.strftime('%d/%m')}"


def _default_start() -> QDateTime:
    """Próxima hora en punto, con al menos 10 minutos de margen."""
    t = datetime.now() + timedelta(minutes=10)
    t = (t + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
    return QDateTime.fromSecsSinceEpoch(int(t.timestamp()))


def _datetime_edit(value: QDateTime) -> QDateTimeEdit:
    dt = QDateTimeEdit(value)
    dt.setCalendarPopup(True)
    dt.setDisplayFormat("dddd dd/MM/yyyy  HH:mm")
    dt.setMinimumDateTime(QDateTime.currentDateTime().addSecs(-60))
    return dt


def _rounded(pix: QPixmap, size: int, radius: int = R_MD) -> QPixmap:
    """Recorta la imagen al centro en un cuadrado redondeado."""
    ratio = 2.0
    side = int(size * ratio)
    src = pix.scaled(side, side, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
    out = QPixmap(side, side)
    out.fill(Qt.transparent)
    p = QPainter(out)
    p.setRenderHint(QPainter.Antialiasing)
    path = QPainterPath()
    path.addRoundedRect(0, 0, side, side, radius * ratio, radius * ratio)
    p.setClipPath(path)
    p.drawPixmap((side - src.width()) // 2, (side - src.height()) // 2, src)
    p.end()
    out.setDevicePixelRatio(ratio)
    return out


class StatusThumb(QWidget):
    """Miniatura del estado: foto recortada, icono de video o el texto sobre su color de fondo."""
    _cache = {}

    def __init__(self, item: dict, size: int = 56, parent=None):
        super().__init__(parent)
        self.item = item
        self.size_px = size
        self.setFixedSize(size, size)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        s = self.size_px
        kind = self.item.get("type")
        path = self.item.get("media_path") or ""
        thumb = self.item.get("thumb")   # miniatura JPEG en base64 de un estado ya publicado
        if (kind == "image" and os.path.exists(path)) or thumb:
            key = (("thumb", self.item.get("id")) if thumb else path, s)
            if key not in self._cache:
                src = QPixmap()
                if thumb:
                    src.loadFromData(base64.b64decode(thumb))
                else:
                    src.load(path)
                self._cache[key] = _rounded(src, s)
            p.drawPixmap(0, 0, self._cache[key])
            if kind == "video":
                p.drawPixmap(s - 20, s - 20, pixmap("play", "#ffffff", 16))
            return
        bg = self.item.get("background") or C["raised"]
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(bg if kind == "text" else C["raised"]))
        p.drawRoundedRect(0, 0, s, s, R_MD, R_MD)
        if kind == "video":
            p.drawPixmap((s - 24) // 2, (s - 24) // 2, pixmap("video", C["text_2"], 24))
            return
        p.setPen(QColor("#ffffff"))
        f = QFont(self.font())
        f.setPointSizeF(7.5)
        f.setBold(True)
        p.setFont(f)
        p.drawText(self.rect().adjusted(5, 5, -5, -5), Qt.AlignCenter | Qt.TextWordWrap,
                   (self.item.get("text") or "")[:40])


# ── Diálogo para programar ──────────────────────────────────────────────────

class ScheduleStatusDialog(QDialog):
    """Carga uno o varios estados. Con varias fotos/videos se reparten desde la hora de inicio con un
    intervalo fijo: así se deja un fin de semana entero programado de una sola vez."""
    scheduled = Signal()

    def __init__(self, parent=None, mode: str = "text"):
        super().__init__(parent)
        self.gateway = GatewayManager.get_instance()
        self.files = []
        self.background = BACKGROUNDS[0]
        self.setWindowTitle("Programar estado")
        self.setMinimumWidth(480)
        self.setModal(True)
        self.setAcceptDrops(True)
        self._init_ui()
        self._set_mode(mode)

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(12)

        title = QLabel("Nuevo estado")
        title.setStyleSheet("font-size: 15px; font-weight: 600;")
        layout.addWidget(title)

        chips = QHBoxLayout()
        chips.setSpacing(6)
        self.chip_group = QButtonGroup(self)
        self.chip_text = self._chip("Texto", "edit")
        self.chip_media = self._chip("Fotos o videos", "image")
        self.chip_text.clicked.connect(lambda: self._set_mode("text"))
        self.chip_media.clicked.connect(lambda: self._set_mode("media"))
        chips.addWidget(self.chip_text)
        chips.addWidget(self.chip_media)
        chips.addStretch()
        layout.addLayout(chips)

        # Una sola página visible: la oculta no ocupa lugar y el diálogo se ajusta a la que se ve
        self.text_page = self._text_page()
        self.media_page = self._media_page()
        layout.addWidget(self.text_page)
        layout.addWidget(self.media_page)

        sep = QFrame()
        sep.setObjectName("hline")
        layout.addWidget(sep)

        when = QHBoxLayout()
        when.setSpacing(8)
        self.lbl_when = QLabel("Publicar el:")
        when.addWidget(self.lbl_when)
        self.dt_start = _datetime_edit(_default_start())
        when.addWidget(self.dt_start, stretch=1)
        layout.addLayout(when)

        self.interval_row = QWidget()
        il = QHBoxLayout(self.interval_row)
        il.setContentsMargins(0, 0, 0, 0)
        il.setSpacing(8)
        il.addWidget(QLabel("Y después, uno cada"))
        self.spin_interval = QSpinBox()
        self.spin_interval.setRange(5, 24 * 60)
        self.spin_interval.setSingleStep(15)
        self.spin_interval.setValue(180)
        self.spin_interval.setSuffix(" min")
        self.spin_interval.valueChanged.connect(self._update_summary)
        il.addWidget(self.spin_interval)
        il.addStretch()
        layout.addWidget(self.interval_row)

        self.lbl_summary = QLabel()
        self.lbl_summary.setObjectName("hint")
        self.lbl_summary.setWordWrap(True)
        layout.addWidget(self.lbl_summary)
        self.dt_start.dateTimeChanged.connect(self._update_summary)

        btns = QHBoxLayout()
        btns.setSpacing(8)
        btn_cancel = QPushButton("Cancelar")
        btn_cancel.setCursor(Qt.PointingHandCursor)
        btn_cancel.clicked.connect(self.reject)
        btns.addWidget(btn_cancel)
        btns.addStretch()
        self.btn_now = QPushButton("Publicar ahora")
        self.btn_now.setCursor(Qt.PointingHandCursor)
        self.btn_now.clicked.connect(lambda: self._submit(now=True))
        btns.addWidget(self.btn_now)
        self.btn_schedule = QPushButton("Programar")
        self.btn_schedule.setObjectName("btnPrimary")
        self.btn_schedule.setDefault(True)
        self.btn_schedule.setCursor(Qt.PointingHandCursor)
        self.btn_schedule.clicked.connect(lambda: self._submit(now=False))
        btns.addWidget(self.btn_schedule)
        layout.addLayout(btns)

    def _chip(self, text: str, icon_name: str) -> QToolButton:
        b = QToolButton()
        b.setObjectName("chip")
        b.setText(text)
        b.setIcon(icon(icon_name))
        b.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        b.setCheckable(True)
        b.setCursor(Qt.PointingHandCursor)
        self.chip_group.addButton(b)
        return b

    def _text_page(self) -> QWidget:
        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(8)
        self.txt_status = QPlainTextEdit()
        self.txt_status.setPlaceholderText("Escribí el estado…")
        self.txt_status.setFixedHeight(130)
        self.txt_status.textChanged.connect(self._on_text_changed)
        v.addWidget(self.txt_status)

        row = QHBoxLayout()
        row.setSpacing(6)
        row.addWidget(QLabel("Fondo:"))
        self.swatches = QButtonGroup(self)
        for color in BACKGROUNDS:
            sw = QToolButton()
            sw.setCheckable(True)
            sw.setFixedSize(22, 22)
            sw.setCursor(Qt.PointingHandCursor)
            sw.setStyleSheet(
                f"QToolButton {{ background-color: {color}; border-radius: 11px; border: 2px solid transparent; }}"
                f"QToolButton:checked {{ border-color: {C['text']}; }}"
            )
            sw.clicked.connect(lambda _=False, c=color: self._set_background(c))
            self.swatches.addButton(sw)
            row.addWidget(sw)
            if color == self.background:
                sw.setChecked(True)
        row.addStretch()
        self.lbl_count = QLabel(f"0/{MAX_TEXT}")
        self.lbl_count.setObjectName("hint")
        row.addWidget(self.lbl_count)
        v.addLayout(row)
        self._set_background(self.background)
        return page

    def _media_page(self) -> QWidget:
        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(8)
        top = QHBoxLayout()
        btn_pick = QPushButton("Elegir archivos…")
        btn_pick.setIcon(icon("attach"))
        btn_pick.setCursor(Qt.PointingHandCursor)
        btn_pick.clicked.connect(self._pick_files)
        top.addWidget(btn_pick)
        self.btn_clear = QPushButton("Quitar todos")
        self.btn_clear.setCursor(Qt.PointingHandCursor)
        self.btn_clear.clicked.connect(lambda: self._set_files([]))
        top.addWidget(self.btn_clear)
        top.addStretch()
        v.addLayout(top)

        self.list_files = QListWidget()
        self.list_files.setObjectName("framedList")
        self.list_files.setIconSize(QSize(40, 40))
        self.list_files.setFixedHeight(150)
        self.list_files.setDragDropMode(QListWidget.InternalMove)
        self.list_files.model().rowsMoved.connect(self._sync_order)
        v.addWidget(self.list_files)

        hint = QLabel("Fotos (JPG, PNG, WEBP) o videos MP4 de hasta 60 s. Arrastrá para cambiar el orden.")
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        v.addWidget(hint)

        self.txt_caption = QPlainTextEdit()
        self.txt_caption.setPlaceholderText("Texto opcional (se agrega a todos)")
        self.txt_caption.setFixedHeight(56)
        v.addWidget(self.txt_caption)
        return page

    # ── Estado del formulario ──

    def _set_mode(self, mode: str):
        self.mode = mode
        (self.chip_text if mode == "text" else self.chip_media).setChecked(True)
        self.text_page.setVisible(mode == "text")
        self.media_page.setVisible(mode != "text")
        self._update_summary()
        self.layout().activate()
        self.resize(self.width(), self.sizeHint().height())

    def _set_background(self, color: str):
        self.background = color
        self.txt_status.setStyleSheet(
            f"QPlainTextEdit {{ background-color: {color}; color: #ffffff; font-size: 15px; border-color: {color}; }}"
        )

    def _on_text_changed(self):
        n = len(self.txt_status.toPlainText())
        self.lbl_count.setText(f"{n}/{MAX_TEXT}")
        self.lbl_count.setStyleSheet(f"color: {C['danger_text']};" if n > MAX_TEXT else "")

    def _pick_files(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Elegir fotos o videos", os.path.expanduser("~"),
            "Fotos y videos (*.jpg *.jpeg *.png *.webp *.mp4 *.mov *.m4v *.3gp)"
        )
        if paths:
            self._set_files(self.files + paths)

    def _set_files(self, paths):
        seen, files, skipped = set(), [], []
        for p in paths:
            if p in seen:
                continue
            seen.add(p)
            (files if _media_kind(p) else skipped).append(p)
        self.files = files
        self.list_files.clear()
        for p in files:
            it = QListWidgetItem(os.path.basename(p))
            it.setData(Qt.UserRole, p)
            if _media_kind(p) == "image":
                it.setIcon(QPixmap(p).scaled(80, 80, Qt.KeepAspectRatio, Qt.SmoothTransformation))
            else:
                it.setIcon(icon("video"))
            self.list_files.addItem(it)
        self.btn_clear.setEnabled(bool(files))
        if skipped:
            QMessageBox.warning(self, "Archivos no admitidos",
                                "Se ignoraron:\n" + "\n".join(os.path.basename(p) for p in skipped))
        self._update_summary()

    def _sync_order(self, *_):
        self.files = [self.list_files.item(i).data(Qt.UserRole) for i in range(self.list_files.count())]
        self._update_summary()

    def _update_summary(self, *_):
        many = self.mode == "media" and len(self.files) > 1
        self.interval_row.setVisible(many)
        self.lbl_when.setText("Primero el:" if many else "Publicar el:")
        self.btn_now.setVisible(not many)
        if many:
            last = self.dt_start.dateTime().toSecsSinceEpoch() + (len(self.files) - 1) * self.spin_interval.value() * 60
            self.lbl_summary.setText(
                f"{len(self.files)} estados · el último sale el "
                f"{_day_label(last).lower()} a las {datetime.fromtimestamp(last).strftime('%H:%M')}."
            )
        else:
            self.lbl_summary.setText("")
        self.lbl_summary.setVisible(many)

    # ── Arrastrar archivos ──

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        paths = [u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()]
        if paths:
            self._set_mode("media")
            self._set_files(self.files + paths)

    # ── Envío al gateway ──

    def _submit(self, now: bool):
        start = 0 if now else self.dt_start.dateTime().toSecsSinceEpoch()
        if not now and start < time.time() - 60:
            QMessageBox.warning(self, "Fecha pasada", "Elegí una fecha y hora futuras.")
            return

        jobs = []
        if self.mode == "text":
            text = self.txt_status.toPlainText().strip()
            if not text:
                QMessageBox.warning(self, "Estado vacío", "Escribí el texto del estado.")
                return
            if len(text) > MAX_TEXT:
                QMessageBox.warning(self, "Texto largo", f"El estado admite hasta {MAX_TEXT} caracteres.")
                return
            jobs.append(dict(kind="text", at=start, text=text, background=self.background))
        else:
            if not self.files:
                QMessageBox.warning(self, "Sin archivos", "Elegí al menos una foto o un video.")
                return
            caption = self.txt_caption.toPlainText().strip()
            step = self.spin_interval.value() * 60
            for i, p in enumerate(self.files):
                jobs.append(dict(kind=_media_kind(p), at=(start + i * step) if start else 0,
                                 text=caption, media_path=p))

        self.setEnabled(False)
        errors = []
        for job in jobs:
            res = self.gateway.schedule_status(**job)
            if not res.get("success"):
                name = os.path.basename(job.get("media_path") or "") or "estado de texto"
                errors.append(f"{name}: {res.get('error')}")
        self.setEnabled(True)
        self.scheduled.emit()

        if errors:
            QMessageBox.warning(self, "No se pudo programar",
                                f"Se programaron {len(jobs) - len(errors)} de {len(jobs)}.\n\n" + "\n".join(errors))
            return
        self.accept()


class RescheduleDialog(QDialog):
    """Cambia la fecha/hora (y el texto) de un estado que todavía no salió."""

    def __init__(self, item: dict, parent=None):
        super().__init__(parent)
        self.item = item
        self.setWindowTitle("Reprogramar estado")
        self.setMinimumWidth(400)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(12)

        at = int(item.get("at") or 0)
        start = QDateTime.fromSecsSinceEpoch(at) if at > time.time() else _default_start()
        row = QHBoxLayout()
        row.addWidget(QLabel("Publicar el:"))
        self.dt = _datetime_edit(start)
        row.addWidget(self.dt, stretch=1)
        layout.addLayout(row)

        self.txt = QPlainTextEdit(item.get("text") or "")
        self.txt.setPlaceholderText("Texto opcional" if item.get("type") != "text" else "Texto del estado")
        self.txt.setFixedHeight(90)
        layout.addWidget(self.txt)

        btns = QHBoxLayout()
        btns.addStretch()
        b_cancel = QPushButton("Cancelar")
        b_cancel.clicked.connect(self.reject)
        btns.addWidget(b_cancel)
        b_ok = QPushButton("Guardar")
        b_ok.setObjectName("btnSave")
        b_ok.setDefault(True)
        b_ok.clicked.connect(self.accept)
        btns.addWidget(b_ok)
        layout.addLayout(btns)

    def values(self):
        return self.dt.dateTime().toSecsSinceEpoch(), self.txt.toPlainText().strip()


# ── Tablero de estados programados ──────────────────────────────────────────

class StatusCard(QFrame):
    action = Signal(str, dict)   # (acción, item)

    def __init__(self, item: dict, parent=None):
        super().__init__(parent)
        self.item = item
        self.setObjectName("card")
        h = QHBoxLayout(self)
        h.setContentsMargins(10, 10, 10, 10)
        h.setSpacing(12)
        h.addWidget(StatusThumb(item))

        info = QVBoxLayout()
        info.setSpacing(3)
        top = QHBoxLayout()
        top.setSpacing(8)
        when = datetime.fromtimestamp(int(item.get("at") or 0))
        lbl_time = QLabel(when.strftime("%H:%M"))
        lbl_time.setStyleSheet("font-weight: 600; font-size: 14px;")
        top.addWidget(lbl_time)
        state = item.get("state", "pending")
        label, color = STATE_INFO.get(state, (state, C["text_2"]))
        if state == "sent" and item.get("sent_at"):
            label += f" a las {datetime.fromtimestamp(item['sent_at']).strftime('%H:%M')}"
            if item.get("recipients"):
                label += f" · {item['recipients'] - 1} contactos"
        lbl_state = QLabel(label)
        lbl_state.setStyleSheet(f"color: {color}; font-size: 12px;")
        top.addWidget(lbl_state)
        top.addStretch()
        info.addLayout(top)

        kind_txt = {"text": "Texto", "image": "Foto", "video": "Video"}.get(item.get("type"), "")
        body = (item.get("text") or "").replace("\n", " ")
        if item.get("type") != "text":
            body = f"{item.get('source_name') or kind_txt}" + (f" · {body}" if body else "")
        lbl_body = ElidedLabel(body or kind_txt)
        lbl_body.setObjectName("subtle")
        info.addWidget(lbl_body)
        if item.get("error") and state in ("failed", "expired", "pending"):
            lbl_err = ElidedLabel(item["error"])
            lbl_err.setObjectName("hint")
            lbl_err.setToolTip(item["error"])
            info.addWidget(lbl_err)
        h.addLayout(info, stretch=1)

        def btn(name, tip, act):
            b = tool_button(name, tip)
            b.clicked.connect(lambda: self.action.emit(act, self.item))
            h.addWidget(b)

        if state == "pending":
            btn("send", "Publicar ahora", "now")
            btn("edit", "Cambiar fecha, hora o texto", "edit")
            btn("close", "Cancelar", "cancel")
        elif state in ("failed", "expired", "canceled"):
            btn("refresh", "Reprogramar", "edit")
            btn("trash", "Quitar de la lista", "remove")
        elif state == "sent":
            btn("trash", "Quitar del historial (el estado sigue publicado)", "remove")


def _ago(ts: int) -> str:
    mins = max(0, int(time.time() - ts) // 60)
    if mins < 1:
        return "Recién"
    if mins < 60:
        return f"Hace {mins} min"
    return f"Hace {mins // 60} h"


class ViewersDialog(QDialog):
    """Lista de contactos que vieron un estado, del más reciente al más antiguo."""

    def __init__(self, item: dict, parent=None):
        super().__init__(parent)
        viewers = item.get("viewers") or []
        self.setWindowTitle("Visto por")
        self.setMinimumSize(340, 420)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(10)
        title = QLabel(f"Visto por {len(viewers)}")
        title.setStyleSheet("font-size: 15px; font-weight: 600;")
        layout.addWidget(title)
        lst = QListWidget()
        lst.setObjectName("framedList")
        for v in viewers:
            when = datetime.fromtimestamp(int(v.get("ts") or 0))
            lst.addItem(f"{v.get('name') or 'Contacto'}   ·   {_day_label(v['ts']).lower()} {when.strftime('%H:%M')}")
        if not viewers:
            lst.addItem("Todavía nadie lo vio.")
        layout.addWidget(lst, stretch=1)
        hint = QLabel("Solo se cuentan las vistas recibidas mientras MAGSAPP estaba conectado.")
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        btn = QPushButton("Cerrar")
        btn.clicked.connect(self.accept)
        layout.addWidget(btn, alignment=Qt.AlignRight)


class ActiveStatusCard(QFrame):
    """Estado propio publicado (últimas 24 h): vistas, ver archivo y eliminar para todos."""
    action = Signal(str, dict)

    def __init__(self, item: dict, parent=None):
        super().__init__(parent)
        self.item = item
        self.setObjectName("card")
        h = QHBoxLayout(self)
        h.setContentsMargins(10, 10, 10, 10)
        h.setSpacing(12)
        h.addWidget(StatusThumb(item))

        info = QVBoxLayout()
        info.setSpacing(3)
        top = QHBoxLayout()
        top.setSpacing(8)
        lbl_time = QLabel(datetime.fromtimestamp(int(item["timestamp"])).strftime("%H:%M"))
        lbl_time.setStyleSheet("font-weight: 600; font-size: 14px;")
        top.addWidget(lbl_time)
        lbl_ago = QLabel(f"{_ago(item['timestamp'])} · vence en {max(0, 24 - int(time.time() - item['timestamp']) // 3600)} h")
        lbl_ago.setObjectName("subtle")
        top.addWidget(lbl_ago)
        top.addStretch()
        info.addLayout(top)
        kind_txt = {"text": "Texto", "image": "Foto", "video": "Video"}.get(item.get("type"), "")
        lbl_body = ElidedLabel((item.get("text") or "").replace("\n", " ") or kind_txt)
        lbl_body.setObjectName("subtle")
        info.addWidget(lbl_body)
        h.addLayout(info, stretch=1)

        btn_views = tool_button("eye", "Ver quién lo vio", text=f" {item.get('views', 0)}")
        btn_views.clicked.connect(lambda: self.action.emit("viewers", self.item))
        h.addWidget(btn_views)
        if item.get("has_media"):
            b_open = tool_button("download", "Abrir la foto o el video")
            b_open.clicked.connect(lambda: self.action.emit("open", self.item))
            h.addWidget(b_open)
        b_del = tool_button("trash", "Eliminar para todos")
        b_del.clicked.connect(lambda: self.action.emit("delete", self.item))
        h.addWidget(b_del)


class ContactStatusRow(QWidget):
    """Fila de un contacto con estados: anillo, nombre y hora del último."""
    clicked = Signal()

    def __init__(self, contact: dict, parent=None):
        super().__init__(parent)
        self.contact = contact
        self._fade = HoverFade(self)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(64)
        h = QHBoxLayout(self)
        h.setContentsMargins(14, 6, 14, 6)
        h.setSpacing(12)
        items = contact.get("items") or []
        h.addWidget(StatusRingAvatar(contact.get("name") or "?", contact.get("avatar_path"),
                                     total=len(items), unseen=int(contact.get("unseen") or 0)))
        v = QVBoxLayout()
        v.setSpacing(1)
        name = ElidedLabel(contact.get("name") or "Contacto")
        name.setStyleSheet("font-size: 13px; font-weight: 600;" if contact.get("unseen") else "font-size: 13px;")
        v.addStretch()
        v.addWidget(name)
        sub = QLabel(when_label(contact.get("last_ts")) + (f"  ·  {len(items)} estados" if len(items) > 1 else ""))
        sub.setObjectName("subtle")
        v.addWidget(sub)
        v.addStretch()
        h.addLayout(v, stretch=1)

    def paintEvent(self, e):
        if self._fade.value > 0.002:
            p = QPainter(self)
            p.setRenderHint(QPainter.Antialiasing)
            p.setPen(Qt.NoPen)
            p.setBrush(overlay(0.055 * self._fade.value))
            p.drawRoundedRect(self.rect().adjusted(6, 1, -6, -1), 10, 10)
            p.end()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton and self.rect().contains(e.position().toPoint()):
            self.clicked.emit()
        super().mouseReleaseEvent(e)


class StatusPanel(QWidget):
    """Panel de Estados: "Mi estado" y los estados de los contactos a la izquierda; mis estados a la derecha."""
    unseen_changed = Signal(int)        # contactos con estados sin ver (punto verde en el menú lateral)
    open_chat_requested = Signal(str)   # jid: "Responder" desde el visor

    def __init__(self, parent=None):
        super().__init__(parent)
        self.gateway = GatewayManager.get_instance()
        self._items = []
        self._active = []
        self._contacts = []
        self._contacts_sig = None
        self._init_ui()
        self._timer = QTimer(self)
        self._timer.setInterval(15000)
        self._timer.timeout.connect(self.refresh)
        # Estados nuevos de contactos: se agrupan los avisos para no recargar por cada uno
        self._contacts_timer = QTimer(self)
        self._contacts_timer.setSingleShot(True)
        self._contacts_timer.setInterval(1200)
        self._contacts_timer.timeout.connect(self.refresh_contacts)
        # El punto del menú lateral se mantiene al día aunque la pestaña no esté abierta
        self._badge_timer = QTimer(self)
        self._badge_timer.setInterval(120000)
        self._badge_timer.timeout.connect(self.refresh_contacts_badge)
        self._badge_timer.start()

    def _init_ui(self):
        root_layout = QHBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        # ── Columna izquierda ──
        left = QFrame()
        left.setObjectName("statusList")
        left.setFixedWidth(320)
        left.setStyleSheet(f"#statusList {{ background-color: {C['panel']}; border-right: 1px solid {C['border']}; }}")
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(0)

        header = QFrame()
        header.setObjectName("paneHeader")
        header.setFixedHeight(HEADER_H)
        hl = QHBoxLayout(header)
        hl.setContentsMargins(16, 0, 8, 0)
        lbl_title = QLabel("Estados")
        lbl_title.setObjectName("paneTitle")
        hl.addWidget(lbl_title)
        hl.addStretch()
        btn_add = tool_button("plus", "Programar estado")
        btn_add.clicked.connect(lambda: self.open_scheduler("text"))
        hl.addWidget(btn_add)
        left_layout.addWidget(header)

        # Mi estado
        mine = QWidget()
        ml = QHBoxLayout(mine)
        ml.setContentsMargins(16, 12, 16, 12)
        ml.setSpacing(12)
        ml.addWidget(AvatarWidget("Yo", size=42))
        info = QVBoxLayout()
        info.setSpacing(2)
        lbl_my_name = QLabel("Mi estado")
        lbl_my_name.setStyleSheet("font-weight: 600;")
        self.lbl_my_sub = QLabel("Sin estados programados")
        self.lbl_my_sub.setObjectName("subtle")
        info.addWidget(lbl_my_name)
        info.addWidget(self.lbl_my_sub)
        ml.addLayout(info, stretch=1)
        left_layout.addWidget(mine)

        actions = QVBoxLayout()
        actions.setContentsMargins(16, 0, 16, 12)
        actions.setSpacing(6)
        b_text = QPushButton("  Programar estado de texto")
        b_text.setIcon(icon("edit"))
        b_text.setCursor(Qt.PointingHandCursor)
        b_text.clicked.connect(lambda: self.open_scheduler("text"))
        actions.addWidget(b_text)
        b_media = QPushButton("  Programar fotos o videos")
        b_media.setIcon(icon("image"))
        b_media.setCursor(Qt.PointingHandCursor)
        b_media.clicked.connect(lambda: self.open_scheduler("media"))
        actions.addWidget(b_media)
        left_layout.addLayout(actions)

        sep = QFrame()
        sep.setObjectName("hline")
        left_layout.addWidget(sep)

        # Estados de los contactos (se arma en refresh_contacts)
        contacts_scroll = QScrollArea()
        contacts_scroll.setWidgetResizable(True)
        contacts_scroll.setFrameShape(QFrame.NoFrame)
        contacts_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        contacts_scroll.setStyleSheet("QScrollArea { background: transparent; }")
        holder = QWidget()
        holder.setObjectName("contactsHolder")
        holder.setStyleSheet("#contactsHolder { background: transparent; }")
        self.contacts_layout = QVBoxLayout(holder)
        self.contacts_layout.setContentsMargins(0, 4, 0, 8)
        self.contacts_layout.setSpacing(0)
        contacts_scroll.setWidget(holder)
        left_layout.addWidget(contacts_scroll, stretch=1)
        self._render_contacts()

        lbl_encrypted = QLabel("Tus estados están cifrados de extremo a extremo")
        lbl_encrypted.setObjectName("hint")
        lbl_encrypted.setAlignment(Qt.AlignCenter)
        lbl_encrypted.setContentsMargins(0, 8, 0, 12)
        left_layout.addWidget(lbl_encrypted)
        root_layout.addWidget(left)

        # ── Columna derecha: tablero ──
        right = QFrame()
        right.setObjectName("statusViewer")
        right.setStyleSheet(f"#statusViewer {{ background-color: {C['chat_bg']}; }}")
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(0)

        board_header = QFrame()
        board_header.setObjectName("paneHeader")
        board_header.setFixedHeight(HEADER_H)
        bh = QHBoxLayout(board_header)
        bh.setContentsMargins(20, 0, 12, 0)
        bh_title = QLabel("Mis estados")
        bh_title.setObjectName("paneTitle")
        bh.addWidget(bh_title)
        self.lbl_summary = QLabel()
        self.lbl_summary.setObjectName("subtle")
        bh.addWidget(self.lbl_summary)
        bh.addStretch()
        btn_sync = tool_button("download", "Buscar en el celular los estados ya publicados")
        btn_sync.clicked.connect(self._sync_from_phone)
        bh.addWidget(btn_sync)
        btn_refresh = tool_button("refresh", "Actualizar")
        btn_refresh.clicked.connect(self.refresh)
        bh.addWidget(btn_refresh)
        rl.addWidget(board_header)

        self.lbl_notice = QLabel(
            "Los estados se publican desde esta PC: tiene que estar encendida, con internet y MAGSAPP abierto "
            "o en la bandeja. Mientras haya estados pendientes, MAGSAPP evita que Windows se suspenda."
        )
        self.lbl_notice.setObjectName("hint")
        self.lbl_notice.setWordWrap(True)
        self.lbl_notice.setContentsMargins(20, 10, 20, 4)
        rl.addWidget(self.lbl_notice)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.board = QWidget()
        self.board_layout = QVBoxLayout(self.board)
        self.board_layout.setContentsMargins(20, 8, 20, 20)
        self.board_layout.setSpacing(8)
        self.board.setMaximumWidth(760)
        wrap = QWidget()
        wl = QHBoxLayout(wrap)
        wl.setContentsMargins(0, 0, 0, 0)
        wl.addWidget(self.board)
        scroll.setWidget(wrap)
        rl.addWidget(scroll, stretch=1)
        root_layout.addWidget(right, stretch=1)

    # ── Datos ──

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh()
        self.refresh_contacts()
        self._timer.start()

    # ── Estados de los contactos ──

    def on_contact_status(self, data: dict):
        self._contacts_timer.start()

    def refresh_contacts_badge(self):
        res = self.gateway.list_contact_statuses()
        if res.get("success"):
            self._contacts = res.get("contacts", [])
            self.unseen_changed.emit(sum(1 for c in self._contacts if c.get("unseen")))

    def refresh_contacts(self):
        res = self.gateway.list_contact_statuses()
        if not res.get("success"):
            return
        self._contacts = res.get("contacts", [])
        self.unseen_changed.emit(sum(1 for c in self._contacts if c.get("unseen")))
        if self.isVisible():
            # Solo se redibuja si cambió algo (evita parpadeos con el refresco periódico)
            sig = tuple((c.get("sender"), len(c.get("items") or []), c.get("unseen"), c.get("avatar_path"))
                        for c in self._contacts)
            if sig != self._contacts_sig:
                self._contacts_sig = sig
                self._render_contacts()

    def _render_contacts(self):
        lay = self.contacts_layout
        while lay.count():
            it = lay.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        unseen = [c for c in self._contacts if c.get("unseen")]
        seen = [c for c in self._contacts if not c.get("unseen")]

        def section(title):
            lbl = QLabel(title)
            lbl.setObjectName("sectionLabel")
            lbl.setContentsMargins(16, 12, 16, 6)
            lay.addWidget(lbl)

        if not self._contacts:
            section("RECIENTES")
            lbl = QLabel("Cuando tus contactos publiquen estados, aparecen acá.\n"
                         "Se ven los que llegan mientras MAGSAPP está abierto.")
            lbl.setObjectName("subtle")
            lbl.setWordWrap(True)
            lbl.setContentsMargins(16, 2, 16, 2)
            lay.addWidget(lbl)
        ordered = unseen + seen
        for title, group in (("RECIENTES", unseen), ("VISTOS", seen)):
            if not group:
                continue
            section(title)
            for c in group:
                row = ContactStatusRow(c)
                idx = ordered.index(c)
                row.clicked.connect(lambda i=idx, o=ordered: self._open_viewer(o, i))
                lay.addWidget(row)
        lay.addStretch()

    def _open_viewer(self, contacts: list, index: int):
        dlg = StatusViewerDialog(contacts, index, self.window())
        dlg.open_chat.connect(self.open_chat_requested.emit)
        dlg.exec()
        self._contacts_sig = None
        self.refresh_contacts()

    def hideEvent(self, event):
        super().hideEvent(event)
        self._timer.stop()

    def refresh(self):
        res = self.gateway.list_scheduled_statuses()
        if not res.get("success"):
            self._render_message("No se pudo leer la agenda de estados", res.get("error") or "")
            self.lbl_summary.setText("")
            return
        self._items = res.get("items", [])
        self._active = self.gateway.list_my_statuses().get("items", [])
        pending = [s for s in self._items if s.get("state") in ("pending", "sending")]
        audience = max(0, int(res.get("audience") or 0) - 1)
        self.lbl_summary.setText(f"·  {len(self._active)} activos  ·  {len(pending)} programados  ·  "
                                 f"se envían a {audience} contactos")
        parts = []
        if self._active:
            views = sum(s.get("views", 0) for s in self._active)
            parts.append(f"{len(self._active)} activos · {views} vistas")
        if pending:
            nxt = min(pending, key=lambda s: s["at"])
            parts.append(f"próximo {_day_label(nxt['at']).lower()} {datetime.fromtimestamp(nxt['at']).strftime('%H:%M')}")
        self.lbl_my_sub.setText(" · ".join(parts) or "Sin estados activos ni programados")
        self._render()

    def _clear_board(self):
        while self.board_layout.count():
            it = self.board_layout.takeAt(0)
            if it.widget():
                it.widget().deleteLater()

    def _render_message(self, title: str, sub: str = ""):
        self._clear_board()
        self.board_layout.addSpacing(60)
        ic = QLabel()
        ic.setPixmap(pixmap("status", C["text_3"], 56))
        self.board_layout.addWidget(ic, alignment=Qt.AlignCenter)
        t = QLabel(title)
        t.setStyleSheet(f"font-size: 15px; color: {C['text']};")
        self.board_layout.addWidget(t, alignment=Qt.AlignCenter)
        if sub:
            s = QLabel(sub)
            s.setObjectName("subtle")
            s.setWordWrap(True)
            s.setAlignment(Qt.AlignCenter)
            self.board_layout.addWidget(s, alignment=Qt.AlignCenter)
        self.board_layout.addStretch()

    def _section(self, title: str):
        lbl = QLabel(title.upper())
        lbl.setObjectName("sectionLabel")
        lbl.setContentsMargins(2, 10, 0, 2)
        self.board_layout.addWidget(lbl)

    def _render(self):
        if not self._items and not self._active:
            self._render_message("No tenés estados activos ni programados",
                                 "Cargá textos, fotos o videos con día y hora, y se publican solos.")
            return
        self._clear_board()
        if self._active:
            self._section("Publicados ahora (24 h)")
            for s in self._active:
                card = ActiveStatusCard(s)
                card.action.connect(self._on_active_action)
                self.board_layout.addWidget(card)

        upcoming = sorted((s for s in self._items if s.get("state") in ("pending", "sending")), key=lambda s: s["at"])
        history = sorted((s for s in self._items if s.get("state") not in ("pending", "sending")),
                         key=lambda s: s.get("sent_at") or s["at"], reverse=True)
        last_day = None
        for s in upcoming:
            day = _day_label(s["at"])
            if day != last_day:
                self._section(f"Programados · {day}")
                last_day = day
            self._add_card(s)
        if history:
            self._section("Historial de programados")
            for s in history[:40]:
                self._add_card(s)
        self.board_layout.addStretch()

    def _sync_from_phone(self):
        res = self.gateway.sync_my_statuses()
        if not res.get("success"):
            QMessageBox.warning(self, "Estados", res.get("error") or "No se pudo pedir los estados al celular.")
            return
        QMessageBox.information(self, "Estados", "Se le pidieron al celular los estados ya publicados. "
                                "Si responde, aparecen en unos segundos (el celular tiene que estar con internet).")
        QTimer.singleShot(5000, self.refresh)
        QTimer.singleShot(15000, self.refresh)

    def _on_active_action(self, act: str, item: dict):
        if act == "viewers":
            ViewersDialog(item, self).exec()
            return
        if act == "open":
            QApplication.setOverrideCursor(Qt.WaitCursor)
            try:
                res = self.gateway.my_status_media(item["id"])
            finally:
                QApplication.restoreOverrideCursor()
            if res.get("success"):
                os.startfile(res["media_path"])
            else:
                QMessageBox.warning(self, "Estados", res.get("error") or "No se pudo abrir el archivo.")
            return
        if act == "delete":
            if QMessageBox.question(self, "Eliminar estado",
                                    "¿Eliminar este estado para todos tus contactos?") != QMessageBox.Yes:
                return
            res = self.gateway.delete_my_status(item["id"])
            if not res.get("success"):
                QMessageBox.warning(self, "Estados", res.get("error") or "No se pudo eliminar el estado.")
            self.refresh()

    def _add_card(self, item: dict):
        card = StatusCard(item)
        card.action.connect(self._on_card_action)
        self.board_layout.addWidget(card)

    def _on_card_action(self, act: str, item: dict):
        res = None
        if act == "now":
            res = self.gateway.update_scheduled_status(item["id"], at=int(time.time()))
        elif act == "edit":
            dlg = RescheduleDialog(item, self)
            if dlg.exec() != QDialog.Accepted:
                return
            at, text = dlg.values()
            if at < time.time() - 60:
                QMessageBox.warning(self, "Fecha pasada", "Elegí una fecha y hora futuras.")
                return
            res = self.gateway.update_scheduled_status(item["id"], at=at, text=text)
        elif act == "cancel":
            if QMessageBox.question(self, "Cancelar estado", "¿Cancelar este estado programado?") != QMessageBox.Yes:
                return
            res = self.gateway.cancel_scheduled_status(item["id"])
        elif act == "remove":
            res = self.gateway.remove_scheduled_status(item["id"])
        if res is not None and not res.get("success"):
            QMessageBox.warning(self, "Estados", res.get("error") or "No se pudo completar la acción.")
        self.refresh()
        if act == "now":
            QTimer.singleShot(4000, self.refresh)

    def open_scheduler(self, mode: str = "text"):
        dlg = ScheduleStatusDialog(self, mode)
        dlg.scheduled.connect(self.refresh)
        dlg.exec()
