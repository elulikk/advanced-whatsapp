import os
from datetime import datetime
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QFrame, QMenu, QMessageBox, QToolButton, QButtonGroup, QScrollArea
)
from PySide6.QtCore import Qt, Signal, QSize, QTimer, QRectF
from PySide6.QtGui import QPainter, QBrush, QColor, QFont, QPainterPath, QPixmap, QPen, QIcon

import time

from app.db import (get_chats, get_total_unread_count, clear_chat_messages, delete_chat, mark_chat_as_read,
                    mark_all_chats_as_read, set_chat_muted, is_chat_muted, db_conn, get_chat)
from app.config import get_pref
from app.ai import smart_features
from app.gateway_manager import GatewayManager
from app.ui.theme import C, HEADER_H, R_LG, unread_badge_qss
from app.ui.icons import icon, tool_button, pixmap
from app.ui.effects import HoverFade, mix, overlay, menu_below
from app import tags as tagdb
from app import chat_lock, thumb_cache
from app import organization as org
from app.ui import lock_dialogs
from app.ui.tag_widgets import (TagPalettePanel, TagManagerDialog, paint_avatar_tags, tags_tooltip, tag_pixmap,
                                tag_id_from_mime, paint_row_effect)

DIAS = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]
MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
         "agosto", "septiembre", "octubre", "noviembre", "diciembre"]


def resolve_display_name(chat_data: dict) -> str:
    """Resuelve un nombre amigable idéntico a WhatsApp Desktop, eliminando 'Usuario (...)'.
    El nombre que puso el usuario (custom_name) prevalece sobre el de WhatsApp."""
    custom = (chat_data.get("custom_name") or "").strip()
    if custom:
        return custom
    raw_name = (chat_data.get("name") or "").strip()
    phone = (chat_data.get("phone") or "").strip()
    jid = chat_data.get("jid") or ""

    if "(Tú)" in raw_name or jid.startswith("784305") or phone == "5492944940567":
        return "+54 9 294 494-0567 (Tú)"

    # Reemplazar prefijos antiguos 'Usuario (' por número o 'Contacto ('
    if raw_name.startswith("Usuario ("):
        if phone:
            return f"+{phone}"
        return raw_name.replace("Usuario (", "Contacto (")

    if not raw_name or raw_name.isdigit():
        if phone:
            return f"+{phone}"
        user_part = jid.split('@')[0] if '@' in jid else jid
        if user_part.isdigit() and len(user_part) >= 10:
            return f"+{user_part}"
        return f"Contacto ({user_part[-4:] if len(user_part) >= 4 else user_part})"

    return raw_name


def format_hour(ts: int) -> str:
    """Hora del mensaje al estilo WhatsApp: '2:15 p. m.'."""
    if not ts:
        return ""
    try:
        h = datetime.fromtimestamp(ts).strftime("%I:%M %p").lower()
    except (OSError, ValueError, OverflowError):
        return ""
    h = h.replace("am", "a. m.").replace("pm", "p. m.")
    return h[1:] if h.startswith("0") else h


def format_day_label(dt: datetime) -> str:
    """Separador de fecha del chat: HOY, AYER, el día de la semana o '3 de octubre de 2026' (en español)."""
    days = (datetime.now().date() - dt.date()).days
    if days == 0:
        return "HOY"
    if days == 1:
        return "AYER"
    if 1 < days < 7:
        return DIAS[dt.weekday()].upper()
    return f"{dt.day} de {MESES[dt.month - 1]} de {dt.year}"


def format_whatsapp_timestamp(ts: int) -> str:
    """Fecha corta de la lista de chats: hora si es de hoy, 'Ayer', el día de la semana o dd/mm/aa."""
    if not ts:
        return ""
    try:
        msg_date = datetime.fromtimestamp(ts)
    except (OSError, ValueError, OverflowError):
        return ""
    days = (datetime.now().date() - msg_date.date()).days
    if days == 0:
        return format_hour(ts)
    if days == 1:
        return "Ayer"
    if 1 < days < 7:
        return DIAS[msg_date.weekday()]
    return msg_date.strftime("%d/%m/%y")


def get_avatar_color(name: str) -> str:
    """Genera un color consistente (y sobrio) basado en el nombre para avatares sin foto."""
    if "(Tú)" in name or name == "Yo":
        return C["accent"]
    palette = [
        "#3b6e99", "#6f5596", "#94506c", "#9a5a46",
        "#8a7340", "#4f7d5a", "#3f7f86", "#5a5f99", "#7a6a5a"
    ]
    if not name:
        return palette[0]
    h = sum(ord(c) for c in name)
    return palette[h % len(palette)]


class AvatarWidget(QWidget):
    """Avatar circular que muestra la foto de perfil real si existe, o iniciales."""
    def __init__(self, name: str, size: int = 40, avatar_path: str = None, parent=None):
        super().__init__(parent)
        self.name = name or "?"
        self.size_px = size
        self.avatar_path = avatar_path
        self._pix_cache = (None, None)   # (ruta, pixmap escalado)
        self._fade = None
        self.locked = False              # chat bloqueado: se oscurece y lleva un candado encima
        self.tags = []                   # tags del chat que se dibujan encima (en orden de prioridad)
        self.tag_ring = C["panel"]       # color del anillo de los tags (el fondo sobre el que está el avatar)
        self.setFixedSize(size, size)

    def set_tags(self, tags: list):
        tags = list(tags or [])
        if tags != self.tags:
            self.tags = tags
            self.update()

    def set_locked(self, locked: bool):
        if locked != self.locked:
            self.locked = locked
            self.update()

    def set_clickable(self, tooltip: str = ""):
        """Cursor de mano y un leve brillo con fundido al pasar el mouse."""
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip(tooltip)
        if self._fade is None:
            self._fade = HoverFade(self)

    def set_avatar(self, name: str, avatar_path: str = None):
        self.name = name or "?"
        self.avatar_path = avatar_path
        self.update()

    def _scaled_pixmap(self):
        path = self.avatar_path
        if not path or not os.path.exists(path):
            return None
        if self._pix_cache[0] == path:
            return self._pix_cache[1]
        # Caché compartida (LRU en memoria): al recargar la lista no se vuelve a decodificar cada avatar
        scaled = thumb_cache.scaled_pixmap(path, self.size_px, self.devicePixelRatioF())
        if scaled is None:
            return None
        self._pix_cache = (path, scaled)
        return scaled

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)

        path = QPainterPath()
        path.addEllipse(0, 0, self.size_px, self.size_px)
        painter.setClipPath(path)

        pix = self._scaled_pixmap()
        if pix is not None:
            painter.drawPixmap(0, 0, pix)
        else:
            # Sin foto: color de fondo con iniciales
            painter.fillPath(path, QBrush(QColor(get_avatar_color(self.name))))
            painter.setPen(QColor("#f2f5f6"))
            font = QFont("Segoe UI")
            font.setPixelSize(max(9, round(self.size_px * 0.38)))
            font.setWeight(QFont.DemiBold)
            painter.setFont(font)

            if "(Tú)" in self.name or self.name == "Yo":
                initials = "YO"
            else:
                parts = [p for p in self.name.strip().split() if p[:1].isalnum()]
                if len(parts) >= 2:
                    initials = (parts[0][0] + parts[1][0]).upper()
                elif parts:
                    initials = parts[0][:2].upper()
                else:
                    initials = "?"
            painter.drawText(self.rect(), Qt.AlignCenter, initials)

        if self._fade is not None and self._fade.value > 0.002:
            painter.fillPath(path, overlay(0.14 * self._fade.value))

        if self.locked:
            # "Todavía no desbloqueado": avatar apagado con un candado en el centro
            painter.fillPath(path, QColor(0, 0, 0, 165))
            side = max(14, round(self.size_px * 0.46))
            painter.setClipping(False)
            painter.drawPixmap((self.size_px - side) // 2, (self.size_px - side) // 2,
                               pixmap("lock", "#e9edef", side))

        if self.tags:
            painter.setClipping(False)
            paint_avatar_tags(painter, self.size_px, self.tags, self.tag_ring)


class ElidedLabel(QLabel):
    """Etiqueta de una línea que recorta con '…' en lugar de estirar el panel."""
    def __init__(self, text: str = "", parent=None):
        super().__init__(parent)
        self._full = ""
        self.setMinimumWidth(10)
        self.setText(text)

    def setText(self, text: str):
        self._full = (text or "").replace("\n", " ")
        self.setToolTip(self._full if len(self._full) > 40 else "")
        self._elide()

    def text(self) -> str:
        return self._full

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._elide()

    def _elide(self):
        w = max(10, self.width())
        super().setText(self.fontMetrics().elidedText(self._full, Qt.ElideRight, w))


def _preview_text(chat_data: dict) -> str:
    if chat_lock.is_locked(chat_data.get("jid")):
        return chat_lock.LOCKED_PREVIEW
    last_msg = (chat_data.get("last_message_text") or "").strip()
    if last_msg:
        return last_msg
    return "Mensaje reciente" if chat_data.get("last_message_time", 0) > 0 else "Sin mensajes"


# Marca del redactor de IA en la vista previa: texto y color
AI_PREVIEW = {
    "pending": ("✨ Generando respuesta…", "text_2"),
    "ready":   ("✨ Respuesta de la IA lista", "accent_hi"),
    "error":   ("✨ La IA no pudo responder", "danger"),
}


class ChatItemWidget(QWidget):
    """Fila de una conversación en la lista. Pinta su propio fondo: tarjeta redondeada con margen a los
    lados, que aparece con un fundido al pasar el mouse y queda marcada si es el chat abierto."""
    INSET = 8     # margen de la tarjeta respecto del borde de la lista (igual que el buscador)

    def __init__(self, chat_data: dict, parent=None, ai_state: str = "", tags: list = None):
        super().__init__(parent)
        self.chat_data = chat_data
        self.ai_state = ai_state
        self._selected = False
        self._drop_hover = False
        self._row_fx = {}          # efectos de los tags sobre esta fila: "row" (tinte) y "name" (color del nombre)
        self._fade = HoverFade(self)

        layout = QHBoxLayout(self)
        pad = self.INSET + 8
        layout.setContentsMargins(pad, 0, pad, 0)
        layout.setSpacing(12)

        name = resolve_display_name(chat_data)
        self.avatar = AvatarWidget(name, size=42, avatar_path=chat_data.get("avatar_path"))
        layout.addWidget(self.avatar)
        self.set_tags(tags or [])

        grid = QVBoxLayout()
        grid.setSpacing(2)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.addStretch(1)

        top = QHBoxLayout()
        top.setSpacing(8)
        self.lbl_name = ElidedLabel(name)
        top.addWidget(self.lbl_name, stretch=1)
        self.lbl_time = QLabel()
        top.addWidget(self.lbl_time)
        grid.addLayout(top)

        bottom = QHBoxLayout()
        bottom.setSpacing(8)
        self.lbl_preview = ElidedLabel()
        bottom.addWidget(self.lbl_preview, stretch=1)
        self.lbl_muted = QLabel()
        self.lbl_muted.setPixmap(pixmap("bell_off", C["text_3"], 15))
        self.lbl_muted.setToolTip("Notificaciones silenciadas")
        bottom.addWidget(self.lbl_muted)
        self.lbl_pinned = QLabel()
        self.lbl_pinned.setPixmap(pixmap("pin", C["text_3"], 15))
        self.lbl_pinned.setToolTip("Chat fijado")
        bottom.addWidget(self.lbl_pinned)
        self.lbl_badge = QLabel()
        self.lbl_badge.setAlignment(Qt.AlignCenter)
        self.lbl_badge.setFixedHeight(18)
        self.lbl_badge.setMinimumWidth(18)
        self.lbl_badge.setStyleSheet(unread_badge_qss())
        bottom.addWidget(self.lbl_badge)
        grid.addLayout(bottom)
        grid.addStretch(1)

        layout.addLayout(grid, stretch=1)
        self._apply(chat_data)

    def set_selected(self, selected: bool):
        if selected != self._selected:
            self._selected = selected
            # El anillo de los tags toma el color de la tarjeta, así se ven "recortados" igual
            self.avatar.tag_ring = C["selected"] if selected else C["panel"]
            self.avatar.update()
            self.update()

    def set_tags(self, tags: list):
        self.avatar.tag_ring = C["selected"] if self._selected else C["panel"]
        self.avatar.set_tags(tags)
        self.avatar.setToolTip(tags_tooltip(tags, self.chat_data.get("last_message_time", 0)))
        fx = tagdb.chat_effects(tags or [])
        row_fx = {"row": fx["row"], "name": fx["name"]}
        if row_fx != self._row_fx:
            self._row_fx = row_fx
            if hasattr(self, "lbl_preview"):
                self._apply(self.chat_data)
            self.update()

    def set_drop_hover(self, on: bool):
        if on != self._drop_hover:
            self._drop_hover = on
            self.update()

    def paintEvent(self, event):
        h = self._fade.value
        tint = self._row_fx.get("row")
        if not self._selected and h <= 0.002 and not self._drop_hover and not tint:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(mix(C["selected"], "#33444d", h) if self._selected else overlay(0.055 * h))
        card = QRectF(self.rect()).adjusted(self.INSET, 1, -self.INSET, -1)
        p.drawRoundedRect(card, R_LG, R_LG)
        paint_row_effect(p, card, tint, R_LG)
        if self._drop_hover:
            # Destino del tag que se está arrastrando
            p.setBrush(overlay(0.06))
            p.setPen(QPen(QColor(C["accent"]), 1.5, Qt.DashLine))
            p.drawRoundedRect(card.adjusted(0.75, 0.75, -0.75, -0.75), R_LG, R_LG)
        p.end()

    def _apply(self, chat_data: dict):
        unread = chat_data.get("unread_count", 0) or 0
        locked = chat_lock.is_locked(chat_data.get("jid"))
        self.avatar.set_locked(locked)
        name_color = C["text_3"] if locked else (self._row_fx.get("name") or C["text"])
        self.lbl_name.setStyleSheet(
            f"font-size: 13px; color: {name_color}; font-weight: {'600' if unread and not locked else '400'};")
        if locked:
            self.lbl_preview.setText(chat_lock.LOCKED_PREVIEW)
            self.lbl_preview.setStyleSheet(f"font-size: 12px; color: {C['text_3']}; font-style: italic;")
        elif self.ai_state not in AI_PREVIEW and chat_data.get("needs_reply"):
            self.lbl_preview.setText("↩ " + _preview_text(chat_data))
            self.lbl_preview.setToolTip("Te hicieron una pregunta o pedido y todavía no respondiste")
            self.lbl_preview.setStyleSheet(f"font-size: 12px; color: {C['accent_hi']};")
        elif self.ai_state in AI_PREVIEW:
            txt, color = AI_PREVIEW[self.ai_state]
            self.lbl_preview.setText(txt)
            self.lbl_preview.setStyleSheet(f"font-size: 12px; color: {C[color]}; font-style: italic;")
        else:
            self.lbl_preview.setText(_preview_text(chat_data))
            self.lbl_preview.setToolTip("")
            self.lbl_preview.setStyleSheet(f"font-size: 12px; color: {C['text'] if unread else C['text_2']};")
        self.lbl_time.setText(format_whatsapp_timestamp(chat_data.get("last_message_time", 0)))
        self.lbl_time.setStyleSheet(f"font-size: 11px; color: {C['accent'] if unread else C['text_3']};")
        self.lbl_badge.setText("99+" if unread > 99 else str(unread))
        self.lbl_badge.setVisible(unread > 0)
        muted = is_chat_muted(chat_data)
        self.lbl_muted.setVisible(muted)
        self.lbl_pinned.setVisible(bool(chat_data.get("is_pinned")))
        if muted and unread:
            # Chat silenciado: contador gris, como en la app oficial
            self.lbl_badge.setStyleSheet(unread_badge_qss().replace(C["accent"], C["text_3"]))
        else:
            self.lbl_badge.setStyleSheet(unread_badge_qss())

    def update_avatar(self, avatar_path: str):
        self.chat_data["avatar_path"] = avatar_path
        self.avatar.set_avatar(self.lbl_name.text(), avatar_path=avatar_path)

    def set_ai_state(self, state: str):
        self.ai_state = state
        self._apply(self.chat_data)

    def update_info(self, chat_data: dict):
        self.chat_data.update(chat_data)
        name = resolve_display_name(self.chat_data)
        self.lbl_name.setText(name)
        self.avatar.set_avatar(name, avatar_path=self.chat_data.get("avatar_path"))
        self._apply(self.chat_data)


class ChatListWidget(QListWidget):
    """Lista de chats que acepta tags arrastrados desde la paleta: el tag cae en el chat bajo el mouse."""
    tag_dropped = Signal(int, str)    # id del tag, jid del chat

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.viewport().setAcceptDrops(True)
        self.setDropIndicatorShown(False)
        self._hover_widget = None

    def _set_hover(self, item):
        w = self.itemWidget(item) if item is not None else None
        if w is self._hover_widget:
            return
        if self._hover_widget is not None:
            try:
                self._hover_widget.set_drop_hover(False)
            except RuntimeError:   # la fila se destruyó al recargar la lista
                pass
        self._hover_widget = w
        if w is not None:
            w.set_drop_hover(True)

    def dragEnterEvent(self, e):
        if tag_id_from_mime(e.mimeData()):
            e.setDropAction(Qt.CopyAction)
            e.accept()
        else:
            super().dragEnterEvent(e)

    def dragMoveEvent(self, e):
        if not tag_id_from_mime(e.mimeData()):
            return super().dragMoveEvent(e)
        pos = e.position().toPoint()
        item = self.itemAt(pos)
        self._set_hover(item)
        # Cerca de los bordes la lista se desplaza sola, para llegar a otros chats
        sb = self.verticalScrollBar()
        if pos.y() < 28:
            sb.setValue(sb.value() - 12)
        elif pos.y() > self.viewport().height() - 28:
            sb.setValue(sb.value() + 12)
        if item is not None:
            e.setDropAction(Qt.CopyAction)
            e.accept()
        else:
            e.ignore()

    def dragLeaveEvent(self, e):
        self._set_hover(None)
        super().dragLeaveEvent(e)

    def dropEvent(self, e):
        tag_id = tag_id_from_mime(e.mimeData())
        if not tag_id:
            return super().dropEvent(e)
        item = self.itemAt(e.position().toPoint())
        self._set_hover(None)
        data = item.data(Qt.UserRole) if item else None
        if isinstance(data, dict) and data.get("jid"):
            e.setDropAction(Qt.CopyAction)
            e.accept()
            self.tag_dropped.emit(tag_id, data["jid"])
        else:
            e.ignore()


class ChipScrollArea(QScrollArea):
    """Fila de píldoras que se desplaza en horizontal (rueda del mouse) cuando no entran todas."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.NoFrame)
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setFixedHeight(34)
        self.setStyleSheet("QScrollArea { background: transparent; } QScrollArea > QWidget > QWidget { background: transparent; }")

    def wheelEvent(self, e):
        sb = self.horizontalScrollBar()
        sb.setValue(sb.value() - (e.angleDelta().y() or e.angleDelta().x()) // 2)
        e.accept()


class ChatListPanel(QWidget):
    """Panel lateral con barra de búsqueda, filtros y lista de chats."""
    chat_selected = Signal(dict)
    chat_lock_changed = Signal(str)   # se bloqueó o desbloqueó un chat (jid)
    open_qr_requested = Signal()
    new_chat_requested = Signal()
    refresh_requested = Signal()
    unread_total_changed = Signal(int)
    chat_deleted = Signal(str)        # jid
    chat_cleared = Signal(str)        # jid
    chat_marked_read = Signal(str)    # jid (para borrar su notificación)
    all_marked_read = Signal()

    ROW_HEIGHT = 64

    def __init__(self, parent=None):
        super().__init__(parent)
        self.all_chats = []
        self._filter = "all"       # "all", "unread", "groups", "archived" o "folder:<id>"
        self._secret_miss = ""     # último texto del buscador que no era el código secreto (evita recalcular el hash)
        self._page_size = 40
        self._current_offset = 0
        self._has_more = True
        self._is_loading = False
        self._chat_items = {}  # jid -> (item, widget)
        self._ai_states = {}   # jid -> "pending" | "ready" | "error" (redactor de IA)
        self._selected_row = None  # ChatItemWidget marcado como chat abierto
        self._last_purge = 0.0     # último barrido de tags vencidos

        self.setObjectName("chatListPanel")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet(f"#chatListPanel {{ background-color: {C['panel']}; }}")
        self._init_ui()
        self.reload_chats()

        # Tags: repintar al cambiar y barrer los vencidos cada minuto
        tagdb.tag_signals.changed.connect(self._refresh_tags)
        self._tag_timer = QTimer(self)
        self._tag_timer.setInterval(60000)
        self._tag_timer.timeout.connect(self._purge_expired_tags)
        self._tag_timer.start()

        # Archivado automático: al abrir y cada media hora (apagado por defecto)
        self._archive_timer = QTimer(self)
        self._archive_timer.setInterval(30 * 60 * 1000)
        self._archive_timer.timeout.connect(self.run_auto_archive)
        self._archive_timer.start()
        QTimer.singleShot(4000, self.run_auto_archive)

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # ── Cabecera ──
        header = QFrame()
        header.setObjectName("paneHeader")
        header.setFixedHeight(HEADER_H)
        header_layout = QHBoxLayout(header)
        # Título alineado con los avatares de la lista (16 px); botones a 8 px del borde, como el buscador
        header_layout.setContentsMargins(16, 0, 8, 0)
        header_layout.setSpacing(2)

        self.lbl_title = QLabel("Chats")
        self.lbl_title.setObjectName("paneTitle")
        header_layout.addWidget(self.lbl_title)
        header_layout.addStretch()

        # Panel de tags: se abre y se cierra como el redactor (y queda como lo dejaste)
        self.btn_tags = tool_button("tag", "Tags: mostrar u ocultar el panel para marcar chats")
        self.btn_tags.setCheckable(True)
        self.btn_tags.clicked.connect(lambda: self.tag_panel.set_open(not self.tag_panel.is_open()))
        header_layout.addWidget(self.btn_tags)

        self.btn_new = tool_button("new_chat", "Nuevo chat  (Ctrl+N)")
        self.btn_new.clicked.connect(self.new_chat_requested.emit)
        header_layout.addWidget(self.btn_new)

        self.btn_menu = tool_button("more", "Más opciones")
        self.btn_menu.clicked.connect(self._show_options_menu)
        header_layout.addWidget(self.btn_menu)
        layout.addWidget(header)

        # ── Aviso de conexión (como "Computadora no conectada" de la app oficial) ──
        self.conn_banner = QFrame()
        self.conn_banner.setObjectName("connBanner")
        self.conn_banner.setStyleSheet(
            f"#connBanner {{ background-color: {C['danger_dim']}; border-bottom: 1px solid {C['border']}; }}")
        cb = QHBoxLayout(self.conn_banner)
        cb.setContentsMargins(16, 8, 12, 8)
        cb.setSpacing(10)
        ic = QLabel()
        ic.setPixmap(pixmap("wifi_off", C["danger_text"], 18))
        cb.addWidget(ic, alignment=Qt.AlignTop)
        self.lbl_conn = QLabel("Sin conexión con WhatsApp")
        self.lbl_conn.setWordWrap(True)
        self.lbl_conn.setStyleSheet(f"color: {C['text']}; font-size: 12px;")
        cb.addWidget(self.lbl_conn, stretch=1)
        self.btn_conn = QToolButton()
        self.btn_conn.setText("Vincular")
        self.btn_conn.setObjectName("chip")
        self.btn_conn.setCursor(Qt.PointingHandCursor)
        self.btn_conn.clicked.connect(self.open_qr_requested.emit)
        cb.addWidget(self.btn_conn, alignment=Qt.AlignVCenter)
        self.conn_banner.setVisible(False)
        layout.addWidget(self.conn_banner)
        # Un corte breve (reconexión de rutina) no muestra el aviso; si dura más de 8 s, sí
        self._conn_state = "connected"
        self._conn_timer = QTimer(self)
        self._conn_timer.setSingleShot(True)
        self._conn_timer.setInterval(8000)
        self._conn_timer.timeout.connect(self._show_conn_banner)

        # ── Búsqueda y filtros ──
        search_box = QWidget()
        search_layout = QVBoxLayout(search_box)
        search_layout.setContentsMargins(ChatItemWidget.INSET, 10, ChatItemWidget.INSET, 6)
        search_layout.setSpacing(8)

        self.txt_search = QLineEdit()
        self.txt_search.setObjectName("searchField")
        self.txt_search.setPlaceholderText("Buscar chat o número  (Ctrl+K)")
        self.txt_search.addAction(icon("search", C["text_3"], 16), QLineEdit.LeadingPosition)
        self.txt_search.setClearButtonEnabled(True)
        act_ai = self.txt_search.addAction(icon("sparkle", C["accent"], 16), QLineEdit.TrailingPosition)
        act_ai.setToolTip("Buscar con IA: por significado, también en las transcripciones de audios")
        act_ai.triggered.connect(self._open_ai_search)
        self.txt_search.installEventFilter(self)
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(220)
        self._search_timer.timeout.connect(self._on_search_timeout)
        self.txt_search.textChanged.connect(self._on_search_changed)
        self.txt_search.returnPressed.connect(self._open_first_result)
        search_layout.addWidget(self.txt_search)

        chips = QHBoxLayout()
        chips.setSpacing(6)
        self._filter_group = QButtonGroup(self)
        self.btn_filter_all = self._make_filter("Todos", True)
        self.btn_filter_unread = self._make_filter("No leídos", False)
        self.btn_filter_groups = self._make_filter("Grupos", False)
        self.btn_filter_pending = self._make_filter("Sin responder", False)
        self.btn_filter_all.clicked.connect(lambda: self._set_filter("all"))
        self.btn_filter_unread.clicked.connect(lambda: self._set_filter("unread"))
        self.btn_filter_groups.clicked.connect(lambda: self._set_filter("groups"))
        self.btn_filter_pending.clicked.connect(lambda: self._set_filter("pending"))
        chips.addWidget(self.btn_filter_all)
        chips.addWidget(self.btn_filter_unread)
        chips.addWidget(self.btn_filter_groups)
        chips.addWidget(self.btn_filter_pending)
        chips.addStretch()
        # Red de seguridad: si algún chat quedó "sin leer" por error, se limpia todo de un clic
        self.btn_read_all = tool_button("check_all", "Marcar todos los chats como leídos",
                                        size=28, icon_size=16, text="Marcar leídos")
        self.btn_read_all.clicked.connect(self.mark_all_read)
        self.btn_read_all.hide()
        chips.addWidget(self.btn_read_all)
        self.btn_folders = tool_button("folder", "Carpetas inteligentes: crear, editar y ordenar", size=28, icon_size=16)
        self.btn_folders.clicked.connect(self.open_folder_manager)
        chips.addWidget(self.btn_folders)
        search_layout.addLayout(chips)

        # Carpetas inteligentes y Archivados: una fila de píldoras aparte (se oculta si no hay nada)
        self.folder_bar = ChipScrollArea()
        self._folder_holder = QWidget()
        self._folder_row = QHBoxLayout(self._folder_holder)
        self._folder_row.setContentsMargins(0, 0, 0, 0)
        self._folder_row.setSpacing(6)
        self.folder_bar.setWidget(self._folder_holder)
        self.folder_bar.setVisible(False)
        search_layout.addWidget(self.folder_bar)
        self._folder_buttons = {}

        # Panel de tags: se arrastran sobre un chat para marcarlo
        self.tag_panel = TagPalettePanel()
        self.tag_panel.manage_requested.connect(self.open_tag_manager)
        self.tag_panel.chip_clicked.connect(self._on_tag_chip_clicked)
        self.tag_panel.open_changed.connect(self.btn_tags.setChecked)
        self.btn_tags.setChecked(self.tag_panel.is_open())
        search_layout.addWidget(self.tag_panel)
        layout.addWidget(search_box)

        # ── Lista ──
        self.list_widget = ChatListWidget()
        self.list_widget.tag_dropped.connect(self._on_tag_dropped)
        self.list_widget.setObjectName("chatList")
        self.list_widget.setVerticalScrollMode(QListWidget.ScrollPerPixel)
        self.list_widget.verticalScrollBar().setSingleStep(18)
        self.list_widget.setUniformItemSizes(True)
        self.list_widget.itemClicked.connect(self._on_item_clicked)
        self.list_widget.itemActivated.connect(self._on_item_clicked)
        self.list_widget.currentItemChanged.connect(self._on_current_changed)
        self.list_widget.verticalScrollBar().valueChanged.connect(self._on_scroll)
        self.list_widget.setContextMenuPolicy(Qt.CustomContextMenu)
        self.list_widget.customContextMenuRequested.connect(self._show_chat_context_menu)
        layout.addWidget(self.list_widget, stretch=1)

        self.lbl_no_results = QLabel("No hay chats que coincidan.")
        self.lbl_no_results.setObjectName("subtle")
        self.lbl_no_results.setAlignment(Qt.AlignHCenter | Qt.AlignTop)
        self.lbl_no_results.setContentsMargins(0, 18, 0, 0)
        self.lbl_no_results.setVisible(False)
        layout.addWidget(self.lbl_no_results, stretch=1)

    def set_connection_state(self, status: str):
        self._conn_state = status or "disconnected"
        if self._conn_state == "connected":
            self._conn_timer.stop()
            self.conn_banner.setVisible(False)
        elif self._conn_state in ("qr_ready", "logged_out"):
            self._show_conn_banner()
        elif not self._conn_timer.isActive() and not self.conn_banner.isVisible():
            self._conn_timer.start()
        elif self.conn_banner.isVisible():
            self._show_conn_banner()

    def _show_conn_banner(self):
        st = self._conn_state
        if st == "connected":
            return
        if st in ("qr_ready", "logged_out"):
            self.lbl_conn.setText("WhatsApp no está vinculado en esta PC. Escaneá el código QR con tu celular.")
            self.btn_conn.setVisible(True)
        else:
            self.lbl_conn.setText("Sin conexión con WhatsApp. Reconectando… Revisá la conexión a internet de la PC.")
            self.btn_conn.setVisible(False)
        self.conn_banner.setVisible(True)

    def _make_filter(self, text: str, checked: bool) -> QToolButton:
        """Filtro en forma de píldora (estilo #chip del tema)."""
        b = QToolButton()
        b.setObjectName("chip")
        b.setText(text)
        b.setCheckable(True)
        b.setChecked(checked)
        b.setCursor(Qt.PointingHandCursor)
        self._filter_group.addButton(b)
        return b

    def _on_current_changed(self, current, _previous):
        """Marca la fila del chat abierto (el ítem anterior puede estar borrándose: se usa el widget guardado)."""
        w = self.list_widget.itemWidget(current) if current is not None else None
        w = w if isinstance(w, ChatItemWidget) else None
        old, self._selected_row = self._selected_row, w
        if old is not None and old is not w:
            try:
                old.set_selected(False)
            except RuntimeError:   # la fila ya se destruyó al recargar la lista
                pass
        if w is not None:
            w.set_selected(True)

    def focus_search(self):
        self.txt_search.setFocus()
        self.txt_search.selectAll()

    def eventFilter(self, obj, event):
        if obj is self.txt_search and event.type() == event.Type.KeyPress:
            if event.key() == Qt.Key_Escape and self.txt_search.text():
                self.txt_search.clear()
                return True
            if event.key() == Qt.Key_Down and self.list_widget.count():
                self.list_widget.setFocus()
                self.list_widget.setCurrentRow(0)
                return True
        return super().eventFilter(obj, event)

    def _open_first_result(self):
        item = self.list_widget.item(0)
        if item:
            self.list_widget.setCurrentItem(item)
            self._on_item_clicked(item)

    def _show_options_menu(self):
        menu = QMenu(self)
        act_refresh = menu.addAction(icon("refresh"), "Refrescar sincronización")
        act_qr = menu.addAction(icon("qr"), "Vincular / dispositivos")
        act_new = menu.addAction(icon("new_chat"), "Nuevo chat directo")
        menu.addSeparator()
        act_read_all = menu.addAction(icon("check_all"), "Marcar todos como leídos")

        act = menu.exec(menu_below(menu, self.btn_menu))
        if act == act_refresh:
            self.reload_chats()
            self.refresh_requested.emit()
        elif act == act_qr:
            self.open_qr_requested.emit()
        elif act == act_new:
            self.new_chat_requested.emit()
        elif act == act_read_all:
            self.mark_all_read()

    def mark_all_read(self):
        """Deja todos los chats en 0 y avisa a WhatsApp (tildes azules) de lo que no estaba leído."""
        total = get_total_unread_count()
        if total:
            res = QMessageBox.question(
                self, "Marcar todos como leídos",
                f"¿Marcar todos los chats como leídos? ({total} mensajes sin leer)",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes
            )
            if res != QMessageBox.Yes:
                return
        gw = GatewayManager.get_instance()
        for jid, ids in mark_all_chats_as_read().items():
            gw.send_read_receipts(jid, ids)
        self.reload_chats(select_jid=self._current_jid())
        self.all_marked_read.emit()

    def _current_jid(self):
        item = self.list_widget.currentItem()
        data = item.data(Qt.UserRole) if item else None
        return data.get("jid") if isinstance(data, dict) else None

    def _set_filter(self, kind: str):
        self._filter = kind
        self.reload_chats(select_jid=self._current_jid())

    def _fetch_chats(self, search: str, offset: int) -> list:
        if self._filter == "pending":
            chats = smart_features.unanswered_chats(limit=self._page_size + 200 if search else self._page_size,
                                                    offset=0 if search else offset)
            if search:
                chats = [c for c in chats if search.lower() in resolve_display_name(c).lower()][offset:offset + self._page_size]
            return chats
        extra = None
        archived = "only" if self._filter == "archived" else "hide"
        if self._filter.startswith("folder:"):
            folder = org.get_folder(int(self._filter[7:] or 0))
            extra = org.rules_where(folder["rules"]) if folder else org.rules_where(None)
        chats = get_chats(search=search, filter_unread=self._filter == "unread", groups_only=self._filter == "groups",
                          limit=self._page_size, offset=offset, extra_where=extra, archived=archived,
                          show_secret=chat_lock.is_revealed())
        pending = smart_features.needs_reply_jids([c.get("jid") for c in chats])
        for c in chats:
            c["needs_reply"] = c.get("jid") in pending
        return chats

    def _open_ai_search(self):
        from app.ui.ai_extras import AiSearchDialog
        dlg = AiSearchDialog(self.txt_search.text().strip(), self)
        dlg.chat_requested.connect(self._open_chat_by_jid)
        dlg.exec()
        dlg.deleteLater()

    def _open_chat_by_jid(self, jid: str):
        chat = get_chat(jid)
        if chat:
            self.chat_selected.emit(chat)

    def _on_search_changed(self, text: str):
        self._search_timer.start()

    def _on_search_timeout(self):
        """Pasó el debounce del buscador: si lo escrito es el código secreto, muestra u oculta los chats ocultos."""
        text = self.txt_search.text().strip()
        if text and text != self._secret_miss:
            res = chat_lock.try_secret(text)
            if res:
                self.txt_search.clear()   # no deja rastro del código en pantalla
                self._secret_miss = ""
                self.lbl_title.setText("Chats (ocultos visibles)" if res == "revealed" else "Chats")
                self.reload_chats()
                return
            self._secret_miss = text
        self.reload_chats()

    # ── Carpetas inteligentes y Archivados ──

    def _rebuild_folder_bar(self):
        """Vuelve a armar las píldoras de las carpetas y de Archivados según lo que haya."""
        while self._folder_row.count():
            item = self._folder_row.takeAt(0)
            w = item.widget()
            if w is not None:
                if isinstance(w, QToolButton):
                    self._filter_group.removeButton(w)
                w.deleteLater()
        self._folder_buttons = {}
        folders = org.list_folders()
        for f in folders:
            n = org.folder_unread_count(f["id"])
            self._add_folder_chip(f"folder:{f['id']}", f["name"] + (f"  {n}" if n else ""), f["name"])
        arch = org.archived_count()
        if arch or self._filter == "archived":
            self._add_folder_chip("archived", f"Archivados  {arch}" if arch else "Archivados", "Chats archivados")
        self._folder_row.addStretch(1)
        self.folder_bar.setVisible(bool(folders) or bool(arch) or self._filter == "archived")
        # Si la carpeta activa se borró, se vuelve a Todos
        if self._filter.startswith("folder:") and self._filter not in self._folder_buttons:
            self._filter = "all"
            self.btn_filter_all.setChecked(True)

    def _add_folder_chip(self, key: str, text: str, tooltip: str):
        b = self._make_filter(text, self._filter == key)
        b.setToolTip(tooltip)
        b.clicked.connect(lambda _=False, k=key: self._set_filter(k))
        self._folder_row.addWidget(b)
        self._folder_buttons[key] = b

    def open_folder_manager(self):
        from app.ui.folder_dialogs import FolderManagerDialog
        FolderManagerDialog(parent=self.window()).exec()
        self.reload_chats(select_jid=self._current_jid())

    def run_auto_archive(self):
        """Archiva los chats sin actividad (si el ajuste está prendido) y repinta si cambió algo."""
        try:
            if org.auto_archive():
                self.reload_chats(select_jid=self._current_jid())
        except Exception as e:
            print(f"[Advanced WhatsApp] archivado automático: {e}")

    def _add_chat_item(self, chat: dict, tags: list = None):
        item = QListWidgetItem(self.list_widget)
        item.setSizeHint(QSize(0, self.ROW_HEIGHT))
        item.setData(Qt.UserRole, chat)
        jid = chat.get("jid")
        widget = ChatItemWidget(chat, ai_state=self._ai_states.get(jid, ""), tags=tags)
        self.list_widget.setItemWidget(item, widget)
        if jid:
            self._chat_items[jid] = (item, widget)
        return item

    def set_ai_state(self, jid: str, state: str):
        """Marca un chat como «generando» / «respuesta lista» / «error» del redactor de IA ('' la quita)."""
        if not jid or self._ai_states.get(jid, "") == state:
            return
        if state:
            self._ai_states[jid] = state
        else:
            self._ai_states.pop(jid, None)
        entry = self._chat_items.get(jid)
        if entry:
            entry[1].set_ai_state(state)

    def reload_chats(self, select_jid: str = None):
        """Carga los primeros 40 chats desde SQLite."""
        self._current_offset = 0
        self._has_more = True
        self._is_loading = False

        search = self.txt_search.text().strip()
        chats = self._fetch_chats(search, 0)
        self.all_chats = chats
        if time.time() - self._last_purge > 30:
            self._last_purge = time.time()
            tagdb.purge_expired()
        tag_map = tagdb.tags_for_chats([c.get("jid") for c in chats])
        self._rebuild_folder_bar()

        self.btn_read_all.setVisible(get_total_unread_count() > 0)
        # Ícono, título y bandeja: los chats silenciados no suman (igual que WhatsApp), y los grupos solo si se avisan
        self.unread_total_changed.emit(get_total_unread_count(include_muted=False,
                                                              include_groups=get_pref("notify_groups")))

        # Conservar la posición del scroll: al vaciar la lista se volvía arriba
        sb = self.list_widget.verticalScrollBar()
        saved_scroll = sb.value()

        self.list_widget.setUpdatesEnabled(False)
        self.list_widget.clear()
        self._chat_items.clear()

        selected_item = None
        for chat in chats:
            item = self._add_chat_item(chat, tag_map.get(chat.get("jid")))
            if select_jid and chat.get("jid") == select_jid:
                selected_item = item

        if len(chats) < self._page_size:
            self._has_more = False

        if selected_item:
            self.list_widget.setCurrentItem(selected_item)

        sb.setValue(min(saved_scroll, sb.maximum()))
        self.list_widget.setUpdatesEnabled(True)

        empty = not chats
        self.list_widget.setVisible(not empty)
        self.lbl_no_results.setVisible(empty)
        if empty:
            self.lbl_no_results.setText(
                "No hay chats que coincidan." if search else
                {"unread": "No tienes chats sin leer.", "groups": "Todavía no hay grupos.",
                 "pending": "No tenés nada sin responder. 🎉",
                 "archived": "No hay chats archivados."}.get(
                    self._filter, "No hay chats en esta carpeta." if self._filter.startswith("folder:")
                    else "Todavía no hay chats."))

    def _on_scroll(self, value):
        sb = self.list_widget.verticalScrollBar()
        if sb.maximum() > 0 and value >= int(sb.maximum() * 0.82):
            self._load_more_chats()

    def _load_more_chats(self):
        """Carga la siguiente página de conversaciones al hacer scroll."""
        if self._is_loading or not self._has_more:
            return
        self._is_loading = True

        search = self.txt_search.text().strip()
        next_offset = self._current_offset + self._page_size
        more_chats = self._fetch_chats(search, next_offset)

        if not more_chats:
            self._has_more = False
            self._is_loading = False
            return

        self._current_offset = next_offset
        tag_map = tagdb.tags_for_chats([c.get("jid") for c in more_chats])
        for chat in more_chats:
            jid = chat.get("jid")
            if jid and jid in self._chat_items:
                continue
            self._add_chat_item(chat, tag_map.get(jid))

        if len(more_chats) < self._page_size:
            self._has_more = False

        self._is_loading = False

    def update_avatar_in_place(self, jid: str, avatar_path: str):
        """Actualiza la foto de perfil en el widget existente sin recargar la lista."""
        if not jid:
            return
        entry = self._chat_items.get(jid)
        if not entry:
            clean = jid.split('@')[0]
            for k, v in self._chat_items.items():
                if k.startswith(clean):
                    entry = v
                    break
        if entry:
            item, widget = entry
            widget.update_avatar(avatar_path)
            chat_data = item.data(Qt.UserRole)
            if isinstance(chat_data, dict):
                chat_data["avatar_path"] = avatar_path
                item.setData(Qt.UserRole, chat_data)

    def update_chat_in_place(self, chat_data: dict):
        """Actualiza vista previa y badge de un chat existente."""
        jid = chat_data.get("jid")
        if not jid:
            return
        entry = self._chat_items.get(jid)
        if entry:
            item, widget = entry
            chat_data["needs_reply"] = jid in smart_features.needs_reply_jids([jid])
            widget.update_info(chat_data)
            item.setData(Qt.UserRole, chat_data)
            row = self.list_widget.row(item)
            if row > 0:
                taken = self.list_widget.takeItem(row)
                self.list_widget.insertItem(0, taken)
                self.list_widget.setItemWidget(taken, widget)
        else:
            self.reload_chats()

    def _on_item_clicked(self, item: QListWidgetItem):
        chat_data = item.data(Qt.UserRole)
        if chat_data:
            self.chat_selected.emit(chat_data)

    def _show_chat_context_menu(self, pos):
        item = self.list_widget.itemAt(pos)
        if not item:
            return
        chat_data = item.data(Qt.UserRole)
        if not chat_data:
            return

        menu = QMenu(self)
        act_read = None
        if chat_data.get("unread_count"):
            act_read = menu.addAction(icon("check"), "Marcar como leído")
        pinned = bool(chat_data.get("is_pinned"))
        act_pin = menu.addAction(icon("pin"), "Desfijar chat" if pinned else "Fijar chat")
        mute_actions = {}
        if is_chat_muted(chat_data):
            act_unmute = menu.addAction(icon("bell"), "Activar notificaciones")
        else:
            act_unmute = None
            mute_menu = menu.addMenu(icon("bell_off"), "Silenciar notificaciones")
            for label, secs in (("8 horas", 8 * 3600), ("1 semana", 7 * 86400), ("Siempre", -1)):
                mute_actions[mute_menu.addAction(label)] = secs
        act_edit = menu.addAction(icon("user"), "Guardar / editar contacto")
        is_locked = chat_lock.is_locked(chat_data.get("jid"))
        act_lock = menu.addAction(icon("lock"), "Desbloquear chat" if is_locked else "Bloquear chat")
        act_hide = None
        if is_locked:
            hidden = chat_lock.is_hidden(chat_data.get("jid"))
            act_hide = menu.addAction(icon("eye"), "Dejar de ocultar chat" if hidden else "Ocultar chat (se busca con el código)")
        archived = bool(chat_data.get("is_archived"))
        act_archive = menu.addAction(icon("folder"), "Sacar de Archivados" if archived else "Archivar chat")
        silent = org.is_silent_read(chat_data.get("jid"))
        act_silent = menu.addAction(icon("eye"), "Lectura invisible: activada (tocá para apagar)" if silent
                                    else "Lectura invisible (sin tildes azules)")
        tag_actions, act_untag_all, act_tags_cfg = self._add_tag_menu(menu, chat_data.get("jid"))
        menu.addSeparator()
        act_clear = menu.addAction(icon("eraser", C["danger"], active_color=C["danger"]), "Vaciar chat…")
        act_delete = menu.addAction(icon("trash", C["danger"], active_color=C["danger"]), "Eliminar chat…")

        act = menu.exec(self.list_widget.mapToGlobal(pos))
        if act is None:
            return
        jid = chat_data.get("jid")
        if act in tag_actions:
            tag_id, has = tag_actions[act]
            if has:
                tagdb.remove_tag(jid, tag_id)
            else:
                tagdb.assign_tag(jid, tag_id)
            return
        if act is act_untag_all:
            tagdb.remove_tag(jid)
            return
        if act is act_tags_cfg:
            self.open_tag_manager()
            return
        if act == act_read:
            GatewayManager.get_instance().send_read_receipts(
                jid, [mid for mid, _ in mark_chat_as_read(jid)])
            self.reload_chats(select_jid=self._current_jid())
            self.chat_marked_read.emit(jid)
        elif act == act_pin:
            with db_conn() as conn:
                conn.execute("UPDATE chats SET is_pinned = ? WHERE jid = ?", (0 if pinned else 1, jid))
            self.reload_chats(select_jid=self._current_jid())
        elif act == act_unmute:
            set_chat_muted(jid, 0)
            self.reload_chats(select_jid=self._current_jid())
        elif act in mute_actions:
            secs = mute_actions[act]
            set_chat_muted(jid, -1 if secs < 0 else int(time.time()) + secs)
            self.reload_chats(select_jid=self._current_jid())
            self.chat_marked_read.emit(jid)
        elif act == act_lock:
            self._toggle_chat_lock(chat_data, is_locked)
        elif act_hide is not None and act == act_hide:
            self._toggle_chat_hidden(chat_data)
        elif act == act_archive:
            org.set_archived(jid, not archived)
            self.reload_chats(select_jid=self._current_jid())
        elif act == act_silent:
            org.set_silent_read(jid, not silent)
        elif act == act_edit:
            self._open_edit_contact_dialog(chat_data)
        elif act == act_clear:
            self._confirm_clear_chat(chat_data)
        elif act == act_delete:
            self._confirm_delete_chat(chat_data)

    def _toggle_chat_lock(self, chat_data: dict, is_locked: bool):
        jid = chat_data.get("jid")
        if is_locked:
            # Para sacar el bloqueo hay que demostrar que sos vos
            if not (chat_lock.is_open(jid) or lock_dialogs.ask_current_password(self, "Ingresá la contraseña para desbloquear el chat:")):
                return
            chat_lock.unlock_chat(jid)
        else:
            if not lock_dialogs.ensure_password(self) or not chat_lock.lock_chat(jid):
                return
        self.chat_lock_changed.emit(jid)
        self.reload_chats(select_jid=self._current_jid())

    def _toggle_chat_hidden(self, chat_data: dict):
        jid = chat_data.get("jid")
        if chat_lock.is_hidden(jid):
            chat_lock.set_hidden(jid, False)
        else:
            if not lock_dialogs.ensure_secret_code(self) or not chat_lock.set_hidden(jid, True):
                return
            QMessageBox.information(
                self, "Chat oculto",
                "El chat ya no aparece en la lista ni en la búsqueda, y no muestra notificaciones.\n\n"
                "Para verlo, escribí tu código secreto en el buscador (o mostralo desde Configuración).")
        self.chat_lock_changed.emit(jid)
        self.reload_chats(select_jid=self._current_jid())

    # ── Tags ──

    def _add_tag_menu(self, menu: QMenu, jid: str):
        """Submenú «Tags»: los que tiene el chat aparecen tildados (tocarlos los quita)."""
        current = {t["id"] for t in tagdb.tags_for_chat(jid)}
        sub = menu.addMenu(icon("tag"), "Tags")
        actions = {}
        for t in tagdb.list_tags():
            has = t["id"] in current
            a = sub.addAction(QIcon(tag_pixmap(t, 16)), ("Quitar «%s»" if has else "Poner «%s»") % t["name"])
            a.setCheckable(True)
            a.setChecked(has)
            actions[a] = (t["id"], has)
        if actions:
            sub.addSeparator()
        act_all = sub.addAction(icon("eraser"), "Quitar todos los tags") if current else None
        act_cfg = sub.addAction(icon("settings"), "Configurar tags…")
        return actions, act_all, act_cfg

    def open_tag_manager(self, tag_id: int = 0):
        TagManagerDialog(select_id=tag_id or 0, parent=self.window()).exec()

    def _on_tag_dropped(self, tag_id: int, jid: str):
        tagdb.assign_tag(jid, tag_id)

    def _on_tag_chip_clicked(self, tag_id: int):
        """Un clic sin arrastrar en la paleta solo explica cómo se usa (así no se ponen tags por accidente)."""
        from PySide6.QtWidgets import QToolTip
        from PySide6.QtGui import QCursor
        t = tagdb.get_tag(tag_id)
        if t:
            QToolTip.showText(QCursor.pos(), f"Arrastrá «{t['name']}» sobre un chat para ponérselo.", self)

    def _refresh_tags(self):
        """Actualiza los tags de las filas cargadas sin recargar la lista."""
        if not self._chat_items:
            return
        tag_map = tagdb.tags_for_chats(list(self._chat_items.keys()))
        for jid, (_item, widget) in list(self._chat_items.items()):
            try:
                widget.set_tags(tag_map.get(jid, []))
            except RuntimeError:
                pass

    def _purge_expired_tags(self):
        self._last_purge = time.time()
        tagdb.purge_expired()   # si borra algo, avisa por tag_signals y la lista se repinta

    def _open_edit_contact_dialog(self, chat_data: dict):
        from app.ui.contact_dialog import ContactEditDialog
        dlg = ContactEditDialog(chat_data, parent=self)
        dlg.contact_saved.connect(lambda updated: self.reload_chats(select_jid=updated.get("jid")))
        dlg.exec()

    def _confirm_clear_chat(self, chat_data: dict):
        jid = chat_data.get("jid")
        name = resolve_display_name(chat_data)
        res = QMessageBox.question(
            self, "Vaciar chat",
            f"¿Vaciar todos los mensajes de «{name}»?\nLa conversación queda en la lista.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        )
        if res == QMessageBox.Yes and jid:
            clear_chat_messages(jid)
            self.reload_chats(select_jid=jid)
            self.chat_cleared.emit(jid)

    def _confirm_delete_chat(self, chat_data: dict):
        jid = chat_data.get("jid")
        name = resolve_display_name(chat_data)
        res = QMessageBox.question(
            self, "Eliminar chat",
            f"¿Eliminar la conversación con «{name}»?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        )
        if res == QMessageBox.Yes and jid:
            delete_chat(jid)
            self.reload_chats()
            self.chat_deleted.emit(jid)
