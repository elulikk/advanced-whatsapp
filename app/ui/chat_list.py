import os
import re
from datetime import datetime
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QFrame, QMenu, QMessageBox, QToolButton, QButtonGroup
)
from PySide6.QtCore import Qt, Signal, QSize, QTimer, QRectF
from PySide6.QtGui import QPainter, QBrush, QColor, QFont, QPainterPath, QPixmap

import time

from app.db import (get_chats, get_total_unread_count, clear_chat_messages, delete_chat, mark_chat_as_read,
                    set_chat_muted, is_chat_muted, db_conn, get_all_contacts, get_or_create_chat)
from app.gateway_manager import GatewayManager
from app.ui.theme import C, HEADER_H, R_LG, unread_badge_qss
from app.ui.icons import icon, tool_button, pixmap
from app.ui.effects import HoverFade, mix, overlay, menu_below

DIAS = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]
MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
         "agosto", "septiembre", "octubre", "noviembre", "diciembre"]


def resolve_display_name(chat_data: dict) -> str:
    """Resuelve un nombre amigable idéntico a WhatsApp Desktop, eliminando 'Usuario (...)'."""
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


# Códigos de área argentinos de 3 dígitos (el resto, salvo el 11, son de 4)
_AR_AREA3 = {"221", "223", "261", "264", "280", "291", "299", "341", "342", "343", "351", "362", "370",
             "376", "379", "381", "383", "387", "388"}


def search_phone_candidate(text: str) -> str:
    """Si lo buscado parece un número de teléfono, devuelve sus dígitos con código de país
    (sin '+' se asume celular argentino: 2944586958 → 5492944586958); si no, ''."""
    text = (text or "").strip()
    if not text or not re.fullmatch(r"\+?[\d\s\-().]+", text):
        return ""
    d = "".join(ch for ch in text if ch.isdigit())
    if not 10 <= len(d) <= 15:
        return ""
    if text.startswith("+"):
        return d
    d = d[2:] if d.startswith("00") else d
    d = d[1:] if d.startswith("0") and len(d) == 11 else d
    if len(d) == 10:
        return "549" + d
    if d.startswith("54") and not d.startswith("549") and len(d) == 12:
        return "549" + d[2:]
    return d


def format_phone(digits: str) -> str:
    """'5492944586958' → '+54 9 2944 58-6958' (números argentinos); otros: '+<dígitos>'."""
    if digits.startswith("549") and len(digits) == 13:
        n = digits[3:]
        area = 2 if n.startswith("11") else 3 if n[:3] in _AR_AREA3 else 4
        sub = n[area:]
        cut = len(sub) - 4
        return f"+54 9 {n[:area]} {sub[:cut]}-{sub[cut:]}"
    return f"+{digits}"


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
        self.setFixedSize(size, size)

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
        pix = QPixmap(path)
        if pix.isNull():
            return None
        ratio = self.devicePixelRatioF()
        side = round(self.size_px * ratio)
        scaled = pix.scaled(side, side, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
        scaled.setDevicePixelRatio(ratio)
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

    def __init__(self, chat_data: dict, parent=None, ai_state: str = ""):
        super().__init__(parent)
        self.chat_data = chat_data
        self.ai_state = ai_state
        self._selected = False
        self._fade = HoverFade(self)

        layout = QHBoxLayout(self)
        pad = self.INSET + 8
        layout.setContentsMargins(pad, 0, pad, 0)
        layout.setSpacing(12)

        name = resolve_display_name(chat_data)
        self.avatar = AvatarWidget(name, size=42, avatar_path=chat_data.get("avatar_path"))
        layout.addWidget(self.avatar)

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
            self.update()

    def paintEvent(self, event):
        h = self._fade.value
        if not self._selected and h <= 0.002:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(mix(C["selected"], "#33444d", h) if self._selected else overlay(0.055 * h))
        p.drawRoundedRect(QRectF(self.rect()).adjusted(self.INSET, 1, -self.INSET, -1), R_LG, R_LG)
        p.end()

    def _apply(self, chat_data: dict):
        unread = chat_data.get("unread_count", 0) or 0
        self.lbl_name.setStyleSheet(
            f"font-size: 13px; color: {C['text']}; font-weight: {'600' if unread else '400'};")
        if self.ai_state in AI_PREVIEW:
            txt, color = AI_PREVIEW[self.ai_state]
            self.lbl_preview.setText(txt)
            self.lbl_preview.setStyleSheet(f"font-size: 12px; color: {C[color]}; font-style: italic;")
        else:
            self.lbl_preview.setText(_preview_text(chat_data))
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


class UnknownNumberWidget(QWidget):
    """Fila de un número que no está en los chats: se puede escribirle directo o agregarlo como contacto."""
    add_clicked = Signal()

    def __init__(self, data: dict, parent=None):
        super().__init__(parent)
        self._fade = HoverFade(self)
        self.setCursor(Qt.PointingHandCursor)
        layout = QHBoxLayout(self)
        pad = ChatItemWidget.INSET + 8
        layout.setContentsMargins(pad, 0, pad, 0)
        layout.setSpacing(12)

        name = data.get("name") or format_phone(data["phone"])
        layout.addWidget(AvatarWidget(name, size=42, avatar_path=data.get("avatar_path")))
        lbl = ElidedLabel(name)
        lbl.setStyleSheet(f"font-size: 13px; color: {C['text']};")
        layout.addWidget(lbl, stretch=1)
        if not data.get("name"):
            btn = tool_button("user_plus", "Agregar a contactos")
            btn.clicked.connect(self.add_clicked.emit)
            layout.addWidget(btn)

    def paintEvent(self, event):
        h = self._fade.value
        if h <= 0.002:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(overlay(0.055 * h))
        p.drawRoundedRect(QRectF(self.rect()).adjusted(ChatItemWidget.INSET, 1, -ChatItemWidget.INSET, -1), R_LG, R_LG)
        p.end()


class ChatListPanel(QWidget):
    """Panel lateral con barra de búsqueda, filtros y lista de chats."""
    chat_selected = Signal(dict)
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
        self._filter_unread = False
        self._page_size = 40
        self._current_offset = 0
        self._has_more = True
        self._is_loading = False
        self._chat_items = {}  # jid -> (item, widget)
        self._ai_states = {}   # jid -> "pending" | "ready" | "error" (redactor de IA)
        self._selected_row = None  # ChatItemWidget marcado como chat abierto

        self.setObjectName("chatListPanel")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet(f"#chatListPanel {{ background-color: {C['panel']}; }}")
        self._init_ui()
        self.reload_chats()

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
        self.txt_search.installEventFilter(self)
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(220)
        self._search_timer.timeout.connect(lambda: self.reload_chats())
        self.txt_search.textChanged.connect(self._on_search_changed)
        self.txt_search.returnPressed.connect(self._open_first_result)
        search_layout.addWidget(self.txt_search)

        chips = QHBoxLayout()
        chips.setSpacing(6)
        self._filter_group = QButtonGroup(self)
        self.btn_filter_all = self._make_filter("Todos", True)
        self.btn_filter_unread = self._make_filter("No leídos", False)
        self.btn_filter_all.clicked.connect(lambda: self._set_filter_unread(False))
        self.btn_filter_unread.clicked.connect(lambda: self._set_filter_unread(True))
        chips.addWidget(self.btn_filter_all)
        chips.addWidget(self.btn_filter_unread)
        chips.addStretch()
        search_layout.addLayout(chips)
        layout.addWidget(search_box)

        # ── Lista ──
        self.list_widget = QListWidget()
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
            if event.key() == Qt.Key_Down and self._first_selectable():
                self.list_widget.setFocus()
                self.list_widget.setCurrentItem(self._first_selectable())
                return True
        return super().eventFilter(obj, event)

    def _first_selectable(self):
        for row in range(self.list_widget.count()):
            item = self.list_widget.item(row)
            if item.flags() & Qt.ItemIsSelectable:
                return item
        return None

    def _open_first_result(self):
        item = self._first_selectable()
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
            for chat in get_chats(filter_unread=True, limit=10000):
                if chat.get("jid"):
                    GatewayManager.get_instance().send_read_receipts(
                        chat["jid"], [mid for mid, _ in mark_chat_as_read(chat["jid"])])
            self.reload_chats(select_jid=self._current_jid())
            self.all_marked_read.emit()

    def _current_jid(self):
        item = self.list_widget.currentItem()
        data = item.data(Qt.UserRole) if item else None
        return data.get("jid") if isinstance(data, dict) and not data.get("_unsaved") else None

    def _set_filter_unread(self, unread: bool):
        self._filter_unread = unread
        self.btn_filter_unread.setChecked(unread)
        self.btn_filter_all.setChecked(not unread)
        self.reload_chats(select_jid=self._current_jid())

    def _on_search_changed(self, text: str):
        self._search_timer.start()

    def _add_chat_item(self, chat: dict):
        item = QListWidgetItem(self.list_widget)
        item.setSizeHint(QSize(0, self.ROW_HEIGHT))
        item.setData(Qt.UserRole, chat)
        jid = chat.get("jid")
        widget = ChatItemWidget(chat, ai_state=self._ai_states.get(jid, ""))
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
        chats = get_chats(search=search, filter_unread=self._filter_unread, limit=self._page_size, offset=0)
        self.all_chats = chats

        self.unread_total_changed.emit(get_total_unread_count())

        # Conservar la posición del scroll: al vaciar la lista se volvía arriba
        sb = self.list_widget.verticalScrollBar()
        saved_scroll = sb.value()

        self.list_widget.setUpdatesEnabled(False)
        self.list_widget.clear()
        self._chat_items.clear()

        selected_item = None
        for chat in chats:
            item = self._add_chat_item(chat)
            if select_jid and chat.get("jid") == select_jid:
                selected_item = item

        if len(chats) < self._page_size:
            self._has_more = False

        # Número buscado que no está en los chats: se ofrece escribirle o agregarlo, como WhatsApp
        extra = self._add_unknown_number(search, chats)

        if selected_item:
            self.list_widget.setCurrentItem(selected_item)

        sb.setValue(min(saved_scroll, sb.maximum()))
        self.list_widget.setUpdatesEnabled(True)

        empty = not chats and not extra
        self.list_widget.setVisible(not empty)
        self.lbl_no_results.setVisible(empty)
        if empty:
            self.lbl_no_results.setText(
                "No hay chats que coincidan." if search else
                ("No tienes chats sin leer." if self._filter_unread else "Todavía no hay chats."))

    def _add_unknown_number(self, search: str, chats: list) -> bool:
        """Agrega al final de la lista la sección «Usuarios que no están en tus contactos» cuando lo buscado
        es un número sin chat. Devuelve True si agregó algo."""
        # Con filas de alturas distintas (encabezado + fila) la lista no puede ser uniforme
        self.list_widget.setUniformItemSizes(True)
        digits = search_phone_candidate(search)
        if not digits or self._filter_unread or self._has_more:
            return False
        tail = digits[-10:]
        for c in chats:
            known = "".join(ch for ch in (c.get("phone") or "") if ch.isdigit()) or (c.get("jid") or "").split("@")[0]
            if known.endswith(tail):
                return False

        jid = f"{digits}@s.whatsapp.net"
        data = {"jid": jid, "phone": digits, "_unsaved": True}
        saved = next((c for c in get_all_contacts(search=tail)
                      if "".join(ch for ch in (c.get("phone") or "") if ch.isdigit()).endswith(tail)), None)
        if saved:
            data.update(name=saved.get("name"), avatar_path=saved.get("avatar_path"))

        self.list_widget.setUniformItemSizes(False)
        head = QListWidgetItem(self.list_widget)
        head.setFlags(Qt.NoItemFlags)
        head.setSizeHint(QSize(0, 36))
        lbl = QLabel("Contactos" if saved else "Usuarios que no están en tus contactos")
        lbl.setStyleSheet(f"font-size: 12px; color: {C['text_3']}; padding: 10px 0 0 {ChatItemWidget.INSET + 8}px;")
        self.list_widget.setItemWidget(head, lbl)

        item = QListWidgetItem(self.list_widget)
        item.setSizeHint(QSize(0, self.ROW_HEIGHT))
        item.setData(Qt.UserRole, data)
        widget = UnknownNumberWidget(data)
        widget.add_clicked.connect(lambda d=data: self._add_unknown_contact(d))
        self.list_widget.setItemWidget(item, widget)
        return True

    def _open_unknown_number(self, data: dict):
        self.chat_selected.emit(get_or_create_chat(data["jid"], name=data.get("name"), phone=data["phone"]))

    def _add_unknown_contact(self, data: dict):
        from app.ui.contact_dialog import ContactEditDialog
        dlg = ContactEditDialog({"jid": data["jid"], "phone": data["phone"], "name": ""}, parent=self)
        dlg.contact_saved.connect(lambda updated: self._open_unknown_number({**data, "name": updated.get("name")}))
        dlg.exec()

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
        more_chats = get_chats(
            search=search,
            filter_unread=self._filter_unread,
            limit=self._page_size,
            offset=next_offset
        )

        if not more_chats:
            self._has_more = False
            self._is_loading = False
            return

        self._current_offset = next_offset
        for chat in more_chats:
            jid = chat.get("jid")
            if jid and jid in self._chat_items:
                continue
            self._add_chat_item(chat)

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
        if chat_data and chat_data.get("_unsaved"):
            self._open_unknown_number(chat_data)
        elif chat_data:
            self.chat_selected.emit(chat_data)

    def _show_chat_context_menu(self, pos):
        item = self.list_widget.itemAt(pos)
        if not item:
            return
        chat_data = item.data(Qt.UserRole)
        if not chat_data or chat_data.get("_unsaved"):
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
        menu.addSeparator()
        act_clear = menu.addAction(icon("eraser", C["danger"], active_color=C["danger"]), "Vaciar chat…")
        act_delete = menu.addAction(icon("trash", C["danger"], active_color=C["danger"]), "Eliminar chat…")

        act = menu.exec(self.list_widget.mapToGlobal(pos))
        if act is None:
            return
        jid = chat_data.get("jid")
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
        elif act == act_edit:
            self._open_edit_contact_dialog(chat_data)
        elif act == act_clear:
            self._confirm_clear_chat(chat_data)
        elif act == act_delete:
            self._confirm_delete_chat(chat_data)

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
