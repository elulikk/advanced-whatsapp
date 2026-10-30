from typing import List, Optional, Tuple
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFrame, QDialog,
    QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QGraphicsDropShadowEffect
)
from PySide6.QtCore import Qt, Signal, QPoint, QRectF, QSize
from PySide6.QtGui import QGuiApplication, QCursor, QPainter, QColor

from app.db import get_chats
from app.ui.chat_list import resolve_display_name
from app.ui.theme import C, R_SM, R_LG
from app.ui.icons import pixmap, icon
from app.ui.effects import HoverFade, overlay

QUICK_REACTIONS = ["👍", "❤️", "😂", "😮", "😢", "🙏"]
MORE_REACTIONS = ["🔥", "🎉", "👏", "😍", "🤔", "😡", "👎", "😅", "🙌", "💯", "✅", "😭"]

MAX_FORWARD_TARGETS = 5   # WhatsApp limita a 5 chats por reenvío
SHADOW = 14               # margen transparente alrededor de los popups para que entre la sombra


def add_shadow(widget: QWidget):
    fx = QGraphicsDropShadowEffect(widget)
    fx.setBlurRadius(24)
    fx.setOffset(0, 4)
    fx.setColor(QColor(0, 0, 0, 150))
    widget.setGraphicsEffect(fx)


class ReactionBar(QFrame):
    """Fila de reacciones en forma de píldora. Emite el emoji elegido ('' = quitar la propia)."""
    emoji_chosen = Signal(str)

    def __init__(self, current: str = "", parent=None):
        super().__init__(parent)
        self._current = current
        self.setObjectName("reactionBar")
        self.setStyleSheet(f"""
            QFrame#reactionBar {{ background-color: {C['surface']}; border: 1px solid {C['border']}; border-radius: 21px; }}
            QPushButton {{ border: none; border-radius: 16px; font-size: 18px; padding: 0; background: transparent;
                          min-height: 32px; max-height: 32px; }}
            QPushButton:hover {{ background-color: {C['hover']}; }}
            QPushButton:pressed {{ background-color: {C['selected']}; }}
            QPushButton[mine="true"] {{ background-color: {C['accent_dim']}; }}
        """)
        self._row = QHBoxLayout(self)
        self._row.setContentsMargins(5, 5, 5, 5)
        self._row.setSpacing(2)
        for e in QUICK_REACTIONS:
            self._row.addWidget(self._button(e))
        more = QPushButton()
        more.setIcon(icon("plus", C["text_2"], active_color=C["text"]))
        more.setIconSize(QSize(18, 18))
        more.setFixedSize(32, 32)
        more.setToolTip("Más reacciones")
        more.setCursor(Qt.PointingHandCursor)
        more.clicked.connect(self._expand)
        self._row.addWidget(more)
        self._more_btn = more

    def _button(self, emoji: str) -> QPushButton:
        b = QPushButton(emoji)
        b.setFixedSize(32, 32)
        b.setCursor(Qt.PointingHandCursor)
        b.setProperty("mine", "true" if emoji == self._current else "false")
        b.clicked.connect(lambda: self.emoji_chosen.emit("" if emoji == self._current else emoji))
        return b

    def _expand(self):
        self._more_btn.hide()
        for e in MORE_REACTIONS:
            self._row.addWidget(self._button(e))
        self.updateGeometry()
        win = self.window()
        if win is not self:
            win.adjustSize()


def _place_popup(popup: QWidget, anchor: QPoint):
    """Coloca un popup en `anchor` sin que se salga de la pantalla (descontando el margen de la sombra)."""
    popup.adjustSize()
    anchor = anchor - QPoint(SHADOW, SHADOW // 2)
    screen = QGuiApplication.screenAt(anchor) or QGuiApplication.primaryScreen()
    area = screen.availableGeometry()
    x = min(max(anchor.x(), area.left() + 4), area.right() - popup.width() - 4)
    y = anchor.y()
    if y + popup.height() > area.bottom() - 4:
        y = max(area.top() + 4, anchor.y() - popup.height())
    popup.move(x, y)


class _MenuRow(QPushButton):
    """Fila del menú de mensaje: icono de línea + texto, con resaltado redondeado que aparece con un fundido."""

    def __init__(self, icon_name: str, label: str, enabled: bool = True, danger: bool = False, parent=None):
        super().__init__(parent)
        self.setEnabled(enabled)
        self.setCursor(Qt.PointingHandCursor if enabled else Qt.ArrowCursor)
        self._fade = HoverFade(self)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 0, 24, 0)
        lay.setSpacing(10)
        if not enabled:
            color, icon_color = C["text_3"], C["text_3"]
        elif danger:
            color, icon_color = "#f0a39e", C["danger"]
        else:
            color, icon_color = C["text"], C["text_2"]
        ico = QLabel()
        ico.setFixedSize(18, 18)
        ico.setAlignment(Qt.AlignCenter)
        if icon_name:
            ico.setPixmap(pixmap(icon_name, icon_color, 16))
        txt = QLabel(label)
        txt.setStyleSheet(f"color: {color}; font-size: 13px; background: transparent;")
        for w in (ico, txt):
            w.setAttribute(Qt.WA_TransparentForMouseEvents)
        lay.addWidget(ico)
        lay.addWidget(txt, stretch=1)
        self.setStyleSheet("QPushButton { background: transparent; border: none; text-align: left; "
                           "min-height: 32px; max-height: 32px; padding: 0; }")

    def paintEvent(self, e):
        h = self._fade.value if self.isEnabled() else 0.0
        if h > 0.002 or self.isDown():
            p = QPainter(self)
            p.setRenderHint(QPainter.Antialiasing)
            p.setPen(Qt.NoPen)
            p.setBrush(overlay(0.11 if self.isDown() else 0.075 * h))
            p.drawRoundedRect(QRectF(self.rect()), R_SM, R_SM)
            p.end()


class MessageContextMenu(QFrame):
    """
    Menú contextual de un mensaje, como el de WhatsApp: barra de reacciones y lista de acciones.
    `actions` = [(clave, icono, texto, habilitada)]; una clave None dibuja un separador.
    El icono es un nombre de icons.py; «delete» se muestra en rojo.
    """
    action_chosen = Signal(str)
    emoji_chosen = Signal(str)

    def __init__(self, current_emoji: str, actions: List[Tuple[Optional[str], str, str, bool]], parent=None):
        super().__init__(parent, Qt.Popup | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(SHADOW, SHADOW // 2, SHADOW, SHADOW + 4)
        outer.setSpacing(6)

        bar = ReactionBar(current_emoji)
        bar.emoji_chosen.connect(self._on_emoji)
        add_shadow(bar)
        outer.addWidget(bar, alignment=Qt.AlignLeft)

        card = QFrame()
        card.setObjectName("menuCard")
        card.setStyleSheet(f"QFrame#menuCard {{ background-color: {C['surface']}; border: 1px solid {C['border']}; "
                           f"border-radius: {R_LG}px; }}")
        card.setMinimumWidth(200)
        add_shadow(card)
        lay = QVBoxLayout(card)
        lay.setContentsMargins(5, 5, 5, 5)
        lay.setSpacing(0)
        for key, icon_name, label, enabled in actions:
            if key is None:
                sep = QFrame()
                sep.setFixedHeight(11)
                sep.setStyleSheet(f"background: transparent; border: none; border-top: 1px solid {C['border']}; "
                                  f"margin: 5px 6px 0 6px;")
                lay.addWidget(sep)
                continue
            row = _MenuRow(icon_name, label, enabled, danger=(key == "delete"))
            row.clicked.connect(lambda _=False, k=key: self._on_action(k))
            lay.addWidget(row)
        outer.addWidget(card)

    def _on_action(self, key: str):
        self.close()
        self.action_chosen.emit(key)

    def _on_emoji(self, emoji: str):
        self.close()
        self.emoji_chosen.emit(emoji)

    def popup_at_cursor(self):
        _place_popup(self, QCursor.pos())
        self.show()


class ForwardDialog(QDialog):
    """Elige a qué chats reenviar los mensajes seleccionados."""

    def __init__(self, count: int, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Reenviar mensaje" if count == 1 else f"Reenviar {count} mensajes")
        self.resize(400, 500)
        self.setModal(True)
        self._chats: List[dict] = []
        self._checked: dict = {}     # jid -> chat, conserva la selección al filtrar
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(10)
        title = QLabel("Reenviar a… (hasta 5 chats)")
        title.setStyleSheet("font-size: 15px; font-weight: 600;")
        lay.addWidget(title)

        self.txt_search = QLineEdit()
        self.txt_search.setObjectName("searchField")
        self.txt_search.setPlaceholderText("Buscar chat")
        self.txt_search.addAction(icon("search", C["text_3"]), QLineEdit.LeadingPosition)
        self.txt_search.setClearButtonEnabled(True)
        self.txt_search.textChanged.connect(self._render)
        lay.addWidget(self.txt_search)

        self.list = QListWidget()
        self.list.setObjectName("framedList")
        self.list.itemDoubleClicked.connect(
            lambda it: it.setCheckState(Qt.Unchecked if it.checkState() == Qt.Checked else Qt.Checked))
        self.list.itemChanged.connect(self._on_item_changed)
        lay.addWidget(self.list, stretch=1)

        row = QHBoxLayout()
        row.setSpacing(8)
        self.lbl_count = QLabel("")
        self.lbl_count.setObjectName("subtle")
        row.addWidget(self.lbl_count)
        row.addStretch()
        btn_cancel = QPushButton("Cancelar")
        btn_cancel.clicked.connect(self.reject)
        row.addWidget(btn_cancel)
        self.btn_send = QPushButton("Reenviar")
        self.btn_send.setObjectName("btnSend")
        self.btn_send.setIcon(icon("forward", C["on_accent"], active_color=C["on_accent"]))
        self.btn_send.clicked.connect(self.accept)
        row.addWidget(self.btn_send)
        for b in (btn_cancel, self.btn_send):
            b.setCursor(Qt.PointingHandCursor)
        lay.addLayout(row)

        self._render()

    def _render(self, *_):
        term = self.txt_search.text().strip()
        self.list.blockSignals(True)
        self.list.clear()
        for chat in get_chats(search=term or None, limit=300):
            jid = chat.get("jid")
            if not jid:
                continue
            item = QListWidgetItem(resolve_display_name(chat))
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked if jid in self._checked else Qt.Unchecked)
            item.setData(Qt.UserRole, chat)
            self.list.addItem(item)
        self.list.blockSignals(False)
        self._update_state()

    def _on_item_changed(self, item: QListWidgetItem):
        chat = item.data(Qt.UserRole)
        jid = chat.get("jid")
        if item.checkState() == Qt.Checked:
            if len(self._checked) >= MAX_FORWARD_TARGETS:
                self.list.blockSignals(True)
                item.setCheckState(Qt.Unchecked)
                self.list.blockSignals(False)
                QMessageBox.information(self, "Reenviar", f"Puedes reenviar a un máximo de {MAX_FORWARD_TARGETS} chats a la vez.")
                return
            self._checked[jid] = chat
        else:
            self._checked.pop(jid, None)
        self._update_state()

    def _update_state(self):
        n = len(self._checked)
        self.lbl_count.setText(f"{n} chat{'s' if n != 1 else ''} seleccionado{'s' if n != 1 else ''}")
        self.btn_send.setEnabled(n > 0)

    def selected_chats(self) -> List[dict]:
        return list(self._checked.values())
