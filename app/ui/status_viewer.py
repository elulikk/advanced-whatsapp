"""
Advanced WhatsApp - Visor de estados de los contactos
===========================================
Como en la app oficial: barras de progreso arriba (una por estado), avanza solo, clic a la derecha/izquierda
(o flechas) para pasar, barra espaciadora o mantener apretado para pausar, Esc para cerrar. Al terminar los
estados de un contacto sigue con el siguiente. Las fotos y videos se bajan recién al verlos.
"""

import base64
import os
import threading
import time
from datetime import datetime

from PySide6.QtCore import Qt, QTimer, Signal, QObject, QRectF, QUrl, QSize
from PySide6.QtGui import QPainter, QColor, QPixmap, QFont, QPainterPath, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QDialog, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QStackedWidget, QPushButton, QSizePolicy
)

from app.config import get_pref
from app.gateway_manager import GatewayManager
from app.ui.chat_list import AvatarWidget
from app.ui.icons import tool_button, pixmap, icon
from app.ui.theme import C

IMAGE_SECONDS = 6.0
FONTS = {0: "Segoe UI", 1: "Georgia", 2: "Segoe Script", 3: "Segoe Print", 4: "Impact", 5: "Impact"}


def when_label(ts: int) -> str:
    d = datetime.fromtimestamp(int(ts or 0))
    today = datetime.now().date()
    hour = d.strftime("%H:%M")
    if d.date() == today:
        return f"Hoy {hour}"
    if (today - d.date()).days == 1:
        return f"Ayer {hour}"
    return d.strftime("%d/%m %H:%M")


class _Fetcher(QObject):
    """Baja el archivo de un estado en segundo plano (puede tardar si hay que pedirlo al celular)."""
    done = Signal(str, dict)   # id, resultado

    def fetch(self, item_id: str):
        def run():
            res = GatewayManager.get_instance().contact_status_media(item_id)
            self.done.emit(item_id, res)
        threading.Thread(target=run, daemon=True, name="advancedwhatsapp-status-media").start()


class ProgressBars(QWidget):
    """Segmentos de progreso: los vistos llenos, el actual parcial, los siguientes vacíos."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.count = 1
        self.index = 0
        self.progress = 0.0
        self.setFixedHeight(4)

    def set_state(self, count: int, index: int, progress: float):
        self.count, self.index, self.progress = max(1, count), index, max(0.0, min(1.0, progress))
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        gap = 4
        w = (self.width() - gap * (self.count - 1)) / self.count
        for i in range(self.count):
            x = i * (w + gap)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(255, 255, 255, 70))
            p.drawRoundedRect(QRectF(x, 0, w, 4), 2, 2)
            fill = 1.0 if i < self.index else (self.progress if i == self.index else 0.0)
            if fill > 0:
                p.setBrush(QColor(255, 255, 255, 235))
                p.drawRoundedRect(QRectF(x, 0, w * fill, 4), 2, 2)
        p.end()


class _ScaledImage(QLabel):
    """Imagen que se adapta al tamaño disponible manteniendo la proporción."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._src = QPixmap()
        self.setAlignment(Qt.AlignCenter)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)

    def set_source(self, pm: QPixmap):
        self._src = pm
        self._rescale()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._rescale()

    def _rescale(self):
        if self._src.isNull():
            self.clear()
            return
        ratio = self.devicePixelRatioF()
        scaled = self._src.scaled(QSize(int(self.width() * ratio), int(self.height() * ratio)),
                                  Qt.KeepAspectRatio, Qt.SmoothTransformation)
        scaled.setDevicePixelRatio(ratio)
        self.setPixmap(scaled)


class StatusViewerDialog(QDialog):
    open_chat = Signal(str)      # jid del contacto (botón "Responder")
    seen_changed = Signal()

    def __init__(self, contacts: list, start_index: int = 0, parent=None):
        super().__init__(parent)
        self.contacts = [c for c in contacts if c.get("items")]
        self.ci = max(0, min(start_index, len(self.contacts) - 1))
        self.ii = 0
        self.elapsed = 0.0
        self.duration = IMAGE_SECONDS
        self.paused = False
        self.loading = False
        self._last_tick = time.monotonic()
        self._fetcher = _Fetcher()
        self._fetcher.done.connect(self._on_media)
        self._marked = set()
        self.gateway = GatewayManager.get_instance()

        self.setWindowTitle("Estados")
        self.setModal(True)
        self.setMinimumSize(420, 640)
        self.resize(560, 880)
        self.setObjectName("statusViewer")
        self.setStyleSheet("#statusViewer { background-color: #0b0b0b; }"
                           "QLabel { color: #ffffff; }")
        self._player = None
        self._audio = None
        self._video_widget = None
        self._build()

        self._timer = QTimer(self)
        self._timer.setInterval(40)
        self._timer.timeout.connect(self._tick)
        self._timer.start()

        QShortcut(QKeySequence(Qt.Key_Right), self, activated=self.next)
        QShortcut(QKeySequence(Qt.Key_Left), self, activated=self.prev)
        QShortcut(QKeySequence(Qt.Key_Space), self, activated=self.toggle_pause)

        # Al abrir: el primer estado sin ver de ese contacto
        if self.contacts:
            items = self.contacts[self.ci]["items"]
            self.ii = next((i for i, it in enumerate(items) if not it.get("seen")), 0)
            QTimer.singleShot(0, self._show_current)

    # ── Construcción ──

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 14)
        root.setSpacing(10)

        self.bars = ProgressBars()
        root.addWidget(self.bars)

        head = QHBoxLayout()
        head.setSpacing(10)
        self.avatar = AvatarWidget("?", size=38)
        head.addWidget(self.avatar)
        names = QVBoxLayout()
        names.setSpacing(0)
        self.lbl_name = QLabel()
        self.lbl_name.setStyleSheet("font-size: 14px; font-weight: 600;")
        self.lbl_when = QLabel()
        self.lbl_when.setStyleSheet("font-size: 12px; color: rgba(255,255,255,0.7);")
        names.addWidget(self.lbl_name)
        names.addWidget(self.lbl_when)
        head.addLayout(names, stretch=1)
        self.btn_pause = tool_button("pause", "Pausar  (barra espaciadora)", color="#ffffff")
        self.btn_pause.clicked.connect(self.toggle_pause)
        head.addWidget(self.btn_pause)
        btn_close = tool_button("close", "Cerrar  (Esc)", color="#ffffff")
        btn_close.clicked.connect(self.reject)
        head.addWidget(btn_close)
        root.addLayout(head)

        # Contenido con zonas de clic: izquierda = anterior, derecha = siguiente
        body = QHBoxLayout()
        body.setSpacing(6)
        self.btn_prev = tool_button("prev", "Anterior  (←)", size=40, icon_size=24, color="#ffffff")
        self.btn_prev.clicked.connect(self.prev)
        body.addWidget(self.btn_prev, alignment=Qt.AlignVCenter)

        self.stack = QStackedWidget()
        self.page_text = QLabel()
        self.page_text.setAlignment(Qt.AlignCenter)
        self.page_text.setWordWrap(True)
        self.page_text.setMargin(28)
        self.page_image = _ScaledImage()
        self.page_msg = QLabel()
        self.page_msg.setAlignment(Qt.AlignCenter)
        self.page_msg.setWordWrap(True)
        self.page_msg.setStyleSheet("font-size: 14px; color: rgba(255,255,255,0.75);")
        self.page_video = QWidget()
        self.page_video.setLayout(QVBoxLayout())
        self.page_video.layout().setContentsMargins(0, 0, 0, 0)
        for w in (self.page_text, self.page_image, self.page_msg, self.page_video):
            self.stack.addWidget(w)
        body.addWidget(self.stack, stretch=1)

        self.btn_next = tool_button("next", "Siguiente  (→)", size=40, icon_size=24, color="#ffffff")
        self.btn_next.clicked.connect(self.next)
        body.addWidget(self.btn_next, alignment=Qt.AlignVCenter)
        root.addLayout(body, stretch=1)

        self.lbl_caption = QLabel()
        self.lbl_caption.setWordWrap(True)
        self.lbl_caption.setAlignment(Qt.AlignCenter)
        self.lbl_caption.setStyleSheet("font-size: 14px; padding: 6px 10px; background-color: rgba(0,0,0,0.45);"
                                       " border-radius: 8px;")
        root.addWidget(self.lbl_caption)

        foot = QHBoxLayout()
        foot.addStretch()
        self.btn_reply = QPushButton("  Responder en el chat")
        self.btn_reply.setIcon(icon("reply"))
        self.btn_reply.setCursor(Qt.PointingHandCursor)
        self.btn_reply.clicked.connect(self._reply)
        foot.addWidget(self.btn_reply)
        foot.addStretch()
        root.addLayout(foot)

    # ── Navegación ──

    def _current(self):
        if not self.contacts:
            return None, None
        c = self.contacts[self.ci]
        return c, c["items"][self.ii]

    def next(self):
        if not self.contacts:
            return
        if self.ii + 1 < len(self.contacts[self.ci]["items"]):
            self.ii += 1
        elif self.ci + 1 < len(self.contacts):
            self.ci += 1
            self.ii = 0
        else:
            self.accept()
            return
        self._show_current()

    def prev(self):
        if not self.contacts:
            return
        if self.elapsed > 1.5 and not self.loading:
            self._show_current()     # como la app: primero reinicia el actual
            return
        if self.ii > 0:
            self.ii -= 1
        elif self.ci > 0:
            self.ci -= 1
            self.ii = len(self.contacts[self.ci]["items"]) - 1
        self._show_current()

    def toggle_pause(self):
        self.paused = not self.paused
        self.btn_pause.setIcon(icon("play" if self.paused else "pause", "#ffffff", active_color="#ffffff"))
        if self._player is not None:
            (self._player.pause if self.paused else self._player.play)()

    def mousePressEvent(self, e):
        # Mantener apretado pausa (como en el celular); un clic corto en los costados pasa
        self._press_at = time.monotonic()
        self._was_paused = self.paused
        if not self.paused:
            self.toggle_pause()
        super().mousePressEvent(e)

    def mouseReleaseEvent(self, e):
        held = time.monotonic() - getattr(self, "_press_at", time.monotonic())
        if not getattr(self, "_was_paused", False) and self.paused:
            self.toggle_pause()
        if held < 0.25:
            x = e.position().x() if hasattr(e, "position") else e.x()
            if x > self.width() * 0.6:
                self.next()
            elif x < self.width() * 0.4:
                self.prev()
        super().mouseReleaseEvent(e)

    # ── Mostrar ──

    def _stop_media(self):
        if self._player is not None:
            try:
                self._player.stop()
                self._player.setSource(QUrl())
            except Exception:
                pass

    def _show_current(self):
        contact, item = self._current()
        if not item:
            return
        self._stop_media()
        self.elapsed = 0.0
        self.loading = False
        self.duration = IMAGE_SECONDS
        self.lbl_name.setText(contact.get("name") or "Contacto")
        self.lbl_when.setText(when_label(item.get("timestamp")))
        self.avatar.set_avatar(contact.get("name") or "?", contact.get("avatar_path"))
        self.btn_prev.setEnabled(self.ci > 0 or self.ii > 0)
        caption = (item.get("text") or "").strip() if item.get("type") != "text" else ""
        self.lbl_caption.setText(caption)
        self.lbl_caption.setVisible(bool(caption))

        kind = item.get("type")
        if kind == "text":
            text = item.get("text") or ""
            bg = item.get("background") or "#005c4b"
            fg = item.get("text_color") or "#ffffff"
            size = 30 if len(text) < 60 else 24 if len(text) < 160 else 18
            family = FONTS.get(item.get("font") or 0, "Segoe UI")
            self.page_text.setStyleSheet(f"background-color: {bg}; color: {fg}; border-radius: 12px;"
                                         f" font-family: '{family}'; font-size: {size}px;")
            self.page_text.setText(text)
            self.duration = min(14.0, 4.0 + len(text) / 18)
            self.stack.setCurrentWidget(self.page_text)
        else:
            # Miniatura borrosa mientras baja el archivo
            thumb = item.get("thumb")
            if thumb:
                pm = QPixmap()
                try:
                    pm.loadFromData(base64.b64decode(thumb))
                except (ValueError, TypeError):   # miniatura corrupta: queda el cuadro vacío
                    pass
                self.page_image.set_source(pm)
                self.stack.setCurrentWidget(self.page_image)
            else:
                self.page_msg.setText("Cargando…")
                self.stack.setCurrentWidget(self.page_msg)
            if item.get("has_media"):
                self.loading = True
                self._fetcher.fetch(item["id"])
            elif not thumb:
                self.page_msg.setText("Este estado no se puede mostrar.")
        self._update_bars()
        self._mark_seen(item)

    def _on_media(self, item_id: str, res: dict):
        contact, item = self._current()
        if not item or item.get("id") != item_id:
            return
        self.loading = False
        if not res.get("success"):
            self.page_msg.setText("No se pudo cargar este estado.\n\n" + (res.get("error") or ""))
            self.stack.setCurrentWidget(self.page_msg)
            self.duration = 4.0
            return
        path = res.get("media_path") or ""
        kind = item.get("type")
        if kind == "image":
            pm = QPixmap(path)
            if pm.isNull():
                self.page_msg.setText("No se pudo abrir la imagen.")
                self.stack.setCurrentWidget(self.page_msg)
                return
            self.page_image.set_source(pm)
            self.stack.setCurrentWidget(self.page_image)
            self.elapsed = 0.0
            self.duration = IMAGE_SECONDS
        elif kind in ("video", "audio"):
            if not self._ensure_player():
                try:
                    os.startfile(path)
                except (OSError, AttributeError) as e:   # sin archivo o sin visor asociado
                    self.page_msg.setText(f"No se pudo abrir el archivo.\n\n{e}")
                    self.stack.setCurrentWidget(self.page_msg)
                self.duration = 3.0
                return
            if kind == "video":
                self.stack.setCurrentWidget(self.page_video)
            else:
                self.page_msg.setText("🎵 Estado de audio")
                self.stack.setCurrentWidget(self.page_msg)
            self._player.setSource(QUrl.fromLocalFile(path))
            self.elapsed = 0.0
            self.duration = float(item.get("seconds") or 30)
            if not self.paused:
                self._player.play()

    def _ensure_player(self) -> bool:
        if self._player is not None:
            return True
        try:
            from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput
            from PySide6.QtMultimediaWidgets import QVideoWidget
        except Exception:
            return False
        self._player = QMediaPlayer(self)
        self._audio = QAudioOutput(self)
        self._player.setAudioOutput(self._audio)
        self._video_widget = QVideoWidget()
        self._video_widget.setStyleSheet("background-color: #000;")
        self.page_video.layout().addWidget(self._video_widget)
        self._player.setVideoOutput(self._video_widget)
        self._player.durationChanged.connect(lambda ms: setattr(self, "duration", max(1.0, ms / 1000)))
        self._player.mediaStatusChanged.connect(self._on_media_status)
        self._player.errorOccurred.connect(self._on_player_error)
        return True

    def _on_player_error(self, *_):
        msg = self._player.errorString() if self._player is not None else ""
        self._stop_media()
        self.page_msg.setText("No se pudo reproducir este estado." + (f"\n\n{msg}" if msg else ""))
        self.stack.setCurrentWidget(self.page_msg)
        self.elapsed = 0.0
        self.duration = 4.0

    def _on_media_status(self, status):
        from PySide6.QtMultimedia import QMediaPlayer
        if status == QMediaPlayer.EndOfMedia:
            self.next()

    def _mark_seen(self, item: dict):
        if item.get("seen") or item["id"] in self._marked:
            return
        self._marked.add(item["id"])
        item["seen"] = True
        receipt = get_pref("status_receipts")
        threading.Thread(target=self.gateway.mark_contact_statuses_seen, args=([item["id"]], receipt),
                         daemon=True).start()
        self.seen_changed.emit()

    def _update_bars(self):
        contact, _ = self._current()
        if contact:
            self.bars.set_state(len(contact["items"]), self.ii, self.elapsed / max(0.1, self.duration))

    def _tick(self):
        now = time.monotonic()
        dt = now - self._last_tick
        self._last_tick = now
        if self.paused or self.loading or not self.contacts:
            return
        if self._player is not None and self.stack.currentWidget() in (self.page_video, self.page_msg) \
                and self._player.source().isValid():
            self.elapsed = self._player.position() / 1000
        else:
            self.elapsed += dt
            if self.elapsed >= self.duration:
                self.next()
                return
        self._update_bars()

    def _reply(self):
        contact, _ = self._current()
        if contact and contact.get("sender_jid"):
            self.open_chat.emit(contact["sender_jid"])
            self.accept()

    def done(self, r):
        self._timer.stop()
        self._stop_media()
        super().done(r)


class StatusRingAvatar(QWidget):
    """Avatar con el anillo de estados: un segmento por estado, verde los no vistos y gris los vistos."""

    def __init__(self, name: str, avatar_path: str = None, total: int = 1, unseen: int = 0, size: int = 46,
                 parent=None):
        super().__init__(parent)
        self.total, self.unseen = max(1, total), unseen
        self.setFixedSize(size, size)
        inner = size - 8
        self.avatar = AvatarWidget(name, size=inner, avatar_path=avatar_path, parent=self)
        self.avatar.move(4, 4)
        self.avatar.setAttribute(Qt.WA_TransparentForMouseEvents)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(1.5, 1.5, self.width() - 3, self.height() - 3)
        n = self.total
        gap = 10 if n > 1 else 0          # grados entre segmentos
        span = (360 - gap * n) / n
        seen = n - self.unseen
        from PySide6.QtGui import QPen
        for i in range(n):
            color = QColor(C["text_3"]) if i < seen else QColor(C["accent"])
            pen = QPen(color, 2.4)
            pen.setCapStyle(Qt.RoundCap)
            p.setPen(pen)
            start = 90 - gap / 2 - i * (span + gap)
            p.drawArc(rect, int(start * 16), int(-span * 16))
        p.end()
