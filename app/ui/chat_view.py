import os
import re
import html
import json
import shutil
import tempfile
from datetime import datetime
import threading
import time
from PySide6.QtWidgets import (QCheckBox, 
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QTextBrowser,
    QTextEdit, QPushButton, QFrame, QFileDialog, QMessageBox, QMenu, QLineEdit, QDialog, QComboBox,
    QToolButton, QToolTip
)
from PySide6.QtCore import Qt, Signal, QTimer, QSize, QPoint
from PySide6.QtGui import QKeyEvent, QImage, QGuiApplication, QTextCursor

from PySide6.QtGui import (
    QDesktopServices, QAction, QCursor, QTextDocument, QTextTable, QTextFormat, QTextFrameFormat, QPainter,
    QPainterPath, QColor, QFontMetrics
)
from PySide6.QtCore import QUrl, QEvent, QPointF, QRectF

from app.ui.contact_info_panel import format_phone
from app import polls, quick_replies
from app.db import (
    resolve_phone, get_messages, mark_chat_as_read, clear_chat_messages, get_message, get_reactions, set_reaction,
    get_pinned_messages, set_message_pinned, delete_messages, revoke_message_local, save_message, _preview_for,
    MAX_PINNED, count_messages_since, get_or_create_chat, save_or_update_contact, set_message_starred, parse_poll,
    get_chat
)
from app.ui.message_menu import (
    ReactionBar, MessageContextMenu, ForwardDialog, SHADOW, add_shadow
)
from app.gateway_manager import GatewayManager
from app import stickers as stickerlib
from app.config import (
    get_chat_font_scale, set_chat_font_scale, clamp_font_scale, get_polish_ai, set_polish_ai, load_app_config,
    get_polish_mode, set_polish_mode, get_polish_profile, set_polish_profile, MEDIA_DIR
)
from app.ai import psych_analysis as pa
from app.ai import reply_polisher
from app.ai import message_summary
from app.ui.ai_extras import AiExtras
from app.ai.ai_service import AIService
from app.ai.transcription import transcribe_audio, pcm_to_wav, merge_dictation
from app.ai.reply_polisher import CONTEXT_MESSAGES, MODES, DEFAULT_MODE
from app.media_service import (
    media_signals, request_media, request_transcript, is_pending, get_error, unavailable_reason
)
from app import link_preview, audio_queue, thumb_cache
from app.ui.chat_list import AvatarWidget, format_hour, format_day_label, resolve_display_name
from app.ui.theme import C, HEADER_H, R_MD, R_LG, R_SM
from app.ui.icons import icon, tool_button, image as icon_image, pixmap as icon_pixmap, flame_pixmap
from app.ui.effects import HoverFade, overlay, menu_below, mix
from app import tags as tagdb
from app.ui.tag_widgets import HeaderTagList, tag_id_from_mime
from app.ui.attachments import AttachmentBar, DropOverlay, kind_for, MAX_ATTACHMENTS
from app.ui.voice_player import (
    voice_player, render_voice_image, resource_url, format_clock, IMG_W, IMG_H, WAVE_X0, WAVE_X1,
    CHIP_X0, CHIP_X1
)
from app.ui.voice_recorder import (
    VoiceRecorder, LevelMeter, encode_ogg_opus, whatsapp_waveform, peak_level,
    MIN_SECONDS, MAX_SECONDS, VOICE_DIR, MIC_PRIVACY_HINT
)


FORWARDABLE_TYPES = ("text", "image", "video", "audio", "document", "sticker")
REVOKE_WINDOW_SECONDS = 2 * 24 * 3600   # WhatsApp permite eliminar para todos durante ~2 días


def _is_emoji_only(text: str) -> bool:
    """True si el mensaje son solo emojis (hasta 3), como 👍 o ❤️🔥."""
    t = (text or "").replace("\ufe0f", "").replace("\u200d", "").strip()
    if not t or len(t) > 12:
        return False
    count = 0
    for ch in t:
        if ch.isspace():
            continue
        o = ord(ch)
        is_emoji = o >= 0x2190 or 0x1F000 <= o <= 0x1FAFF or 0xE0020 <= o <= 0xE007F
        if not is_emoji:
            return False
        count += 1
    return 1 <= count <= 3


class ReactionPicker(QFrame):
    """Selector emergente de reacciones. Emite el emoji elegido ('' = quitar la propia)."""
    emoji_chosen = Signal(str)

    def __init__(self, current: str = "", parent=None):
        super().__init__(parent, Qt.Popup | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(SHADOW, SHADOW, SHADOW, SHADOW)
        bar = ReactionBar(current)
        add_shadow(bar)
        bar.emoji_chosen.connect(self.emoji_chosen)
        bar.emoji_chosen.connect(lambda _: self.close())
        lay.addWidget(bar)


class ChatBrowser(QTextBrowser):
    """Visor del chat con burbujas redondeadas.

    El HTML de Qt no redondea tablas: al cargar el chat se anotan las tablas con fondo (burbujas, citas, fechas,
    reacciones), se les quita el fondo y se pintan debajo del texto con esquinas redondeadas. Una tabla con
    `border-style: solid` y borde 0 lleva además una franja de acento a la izquierda del color del borde.
    Los iconos de línea del chat se piden como imágenes `icon:<nombre>`."""
    ICONS = {"react": ("smile", "text_3", 15), "reply": ("reply", "text_3", 15),
             "forward": ("forward", "text_3", 15), "pin": ("pin", "text_2", 12),
             "summary": ("sparkle", "text_3", 14), "summary_on": ("sparkle", "accent_hi", 14),
             "sel_on": ("select", "accent_hi", 20), "sel_off": ("box", "text_2", 20)}

    def __init__(self, parent=None):
        super().__init__(parent)
        self._shapes = []   # [(tabla, color, acento | None, anidada)]

    def setHtml(self, text: str):
        self._shapes = []    # las tablas anteriores dejan de existir con el documento viejo
        super().setHtml(text)
        self._collect_shapes()

    @staticmethod
    def _first_cell_offset(table: QTextTable) -> QPointF:
        """Qt suma a frameBoundingRect() de una tabla la posición de su primera celda: se descuenta."""
        cf = table.cellAt(0, 0).format().toTableCellFormat()
        tf = table.format()
        pad = tf.cellPadding()
        left = cf.leftPadding() if cf.hasProperty(QTextFormat.Property.TableCellLeftPadding) else pad
        top = cf.topPadding() if cf.hasProperty(QTextFormat.Property.TableCellTopPadding) else pad
        edge = tf.border() + tf.cellSpacing()
        return QPointF(edge + left, edge + top)

    def _collect_shapes(self):
        shapes = []

        def walk(frame, nested):
            for child in frame.childFrames():
                fmt = child.frameFormat()
                has_bg = isinstance(child, QTextTable) and fmt.background().style() != Qt.NoBrush
                if has_bg:
                    accent = None
                    if fmt.border() == 0 and fmt.borderStyle() == QTextFrameFormat.BorderStyle.BorderStyle_Solid:
                        accent = QColor(fmt.borderBrush().color())
                    shapes.append((child, QColor(fmt.background().color()), accent, nested,
                                   self._first_cell_offset(child)))
                    fmt.clearBackground()
                    child.setFrameFormat(fmt)
                walk(child, nested or has_bg)

        walk(self.document().rootFrame(), False)
        self._shapes = shapes

    def loadResource(self, rtype, name):
        if rtype == QTextDocument.ImageResource and name.scheme() == "icon" and name.path() in self.ICONS:
            glyph, color, size = self.ICONS[name.path()]
            return icon_image(glyph, C[color], size)
        return super().loadResource(rtype, name)

    def paintEvent(self, e):
        if self._shapes:
            dx, dy = self.horizontalScrollBar().value(), self.verticalScrollBar().value()
            visible = QRectF(e.rect()).translated(dx, dy)
            layout = self.document().documentLayout()
            p = QPainter(self.viewport())
            p.setRenderHint(QPainter.Antialiasing)
            p.translate(-dx, -dy)
            for frame, color, accent, nested, offset in self._shapes:
                r = layout.frameBoundingRect(frame).translated(-offset)
                if not r.intersects(visible):
                    continue
                radius = R_SM if nested else (r.height() / 2 if r.height() <= 30 else R_MD)
                path = QPainterPath()
                path.addRoundedRect(r, radius, radius)
                p.fillPath(path, color)
                if accent is not None:
                    p.save()
                    p.setClipPath(path)
                    p.fillRect(QRectF(r.left(), r.top(), 4, r.height()), accent)
                    p.restore()
            p.end()
        super().paintEvent(e)


class ContactChip(QWidget):
    """Avatar + nombre de la cabecera del chat: se resalta con un fundido al pasar el mouse y abre la info."""
    clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._fade = HoverFade(self)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("Ver la info del contacto")

    def mousePressEvent(self, e):
        e.accept()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton and self.rect().contains(e.position().toPoint()):
            self.clicked.emit()

    def paintEvent(self, _):
        h = self._fade.value
        if h <= 0.002:
            return
        # Resalta solo hasta el final del texto, no todo el ancho de la cabecera
        right = max((w.geometry().x() + w.sizeHint().width() for w in self.findChildren(QLabel)), default=self.width())
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(overlay(0.06 * h))
        r = QRectF(0, 3, min(self.width(), right + 12), self.height() - 6)
        p.drawRoundedRect(r, R_LG, R_LG)
        p.end()


class MessageInputEdit(QTextEdit):
    """QTextEdit que envía con Enter y agrega salto de línea con Shift+Enter.
    Con accept_media=True, pegar (Ctrl+V o «Pegar» del menú) una imagen o archivos copiados los emite como adjuntos.
    Con `line_height`, una sola línea ocupa exactamente ese alto (alineada con los botones de al lado)."""
    send_requested = Signal()
    has_text_changed = Signal(bool)
    image_pasted = Signal(QImage)
    files_pasted = Signal(list)

    def __init__(self, parent=None, placeholder: str = "Escribe un mensaje", min_lines: int = 1, max_lines: int = 6,
                 accept_media: bool = False, radius: int = R_MD, line_height: int = 0):
        super().__init__(parent)
        self._min_lines = min_lines
        self._max_lines = max_lines
        self._accept_media = accept_media
        self._radius = radius
        self._line_height = line_height
        self._chrome = 12
        self.setPlaceholderText(placeholder)
        self.setAcceptRichText(False)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.document().setDocumentMargin(2)
        self.set_font_scale(100)
        self.textChanged.connect(self._on_text_changed)

    def set_font_scale(self, pct: int):
        px = round(13 * pct / 100)
        f = self.font()
        f.setPixelSize(px)
        line = QFontMetrics(f).lineSpacing()
        # Padding vertical que centra una línea dentro de line_height (borde 2 + márgenes del documento 4)
        free = self._line_height - (line + 6) if self._line_height else 12
        top = max(5, free // 2)
        bottom = max(5, free - free // 2)
        side = max(10, self._radius - 4)
        self._chrome = 2 + top + bottom
        self.setStyleSheet(f"""
            QTextEdit {{
                background-color: {C['raised']};
                border: 1px solid {C['border']};
                border-radius: {self._radius}px;
                padding: {top}px {side}px {bottom}px {side}px;
                color: {C['text']};
                font-size: {px}px;
            }}
            QTextEdit:hover {{ border-color: {C['border_hi']}; }}
            QTextEdit:focus {{ border-color: {C['accent']}; }}
            QTextEdit:disabled {{ color: {C['text_3']}; }}
        """)
        self.document().setDefaultFont(f)
        QTimer.singleShot(0, self._fit_height)

    def _fit_height(self):
        """Crece con el texto entre min_lines y max_lines; a partir de ahí aparece la barra."""
        line = self.fontMetrics().lineSpacing()
        doc_h = self.document().size().height()
        chrome = self._chrome  # padding + borde
        lo = line * self._min_lines + 4 + chrome
        hi = line * self._max_lines + 4 + chrome
        h = int(max(lo, min(hi, doc_h + chrome)))
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded if doc_h + chrome > hi else Qt.ScrollBarAlwaysOff)
        if self.height() != h:
            self.setFixedHeight(h)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        QTimer.singleShot(0, self._fit_height)

    def _on_text_changed(self):
        self._fit_height()
        has_txt = bool(self.toPlainText().strip())
        self.has_text_changed.emit(has_txt)

    def _expand_shortcut(self, partial: bool = False) -> bool:
        """Si lo escrito termina en /atajo, lo reemplaza por la plantilla (partial: completa con el primer atajo que coincida)."""
        cur = self.textCursor()
        if cur.hasSelection():
            return False
        before = self.toPlainText()[:cur.position()]
        new = quick_replies.expand_trailing(before)
        if new is None and partial:
            sc = quick_replies.trailing_shortcut(before)
            sug = quick_replies.suggestions(sc) if sc else []
            if sug:
                new = before[: len(before) - len(sc) - 1] + sug[0]["text"]
        if new is None:
            return False
        cur.setPosition(0)
        cur.setPosition(len(before), cur.MoveMode.KeepAnchor)
        cur.insertText(new)
        self.setTextCursor(cur)
        return True

    def keyPressEvent(self, event: QKeyEvent):
        if event.key() in (Qt.Key_Space, Qt.Key_Tab) and not (event.modifiers() & (Qt.ControlModifier | Qt.AltModifier)):
            if self._expand_shortcut(partial=event.key() == Qt.Key_Tab):
                if event.key() == Qt.Key_Space:
                    self.insertPlainText(" ")
                event.accept()
                return
        if event.key() in (Qt.Key_Return, Qt.Key_Enter) and not (event.modifiers() & Qt.ShiftModifier):
            if self._expand_shortcut():   # Enter sobre un /atajo lo expande sin enviar: se revisa antes de mandar
                event.accept()
                return
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            if event.modifiers() & Qt.ShiftModifier:
                super().keyPressEvent(event)
            else:
                event.accept()
                self.send_requested.emit()
                return
        else:
            super().keyPressEvent(event)

    def _pasted_media(self, source):
        """('files', [rutas]) | ('image', QImage) | None. Si el portapapeles trae texto, se pega el texto."""
        if not self._accept_media or source is None:
            return None
        if source.hasUrls():
            files = [u.toLocalFile() for u in source.urls() if u.isLocalFile() and os.path.isfile(u.toLocalFile())]
            if files:
                return ("files", files)
        if source.hasImage() and not (source.hasText() and source.text().strip()):
            img = source.imageData()
            if isinstance(img, QImage) and not img.isNull():
                return ("image", img)
        return None

    def canInsertFromMimeData(self, source) -> bool:
        return bool(self._pasted_media(source)) or super().canInsertFromMimeData(source)

    def insertFromMimeData(self, source):
        media = self._pasted_media(source)
        if media is None:
            super().insertFromMimeData(source)
        elif media[0] == "files":
            self.files_pasted.emit(media[1])
        else:
            self.image_pasted.emit(media[1])


class ChatViewPanel(QWidget):
    """Panel central/derecho con visualización de mensajes y compositor oficial."""
    COMPOSER_H = 36                            # alto de los botones y del campo de una línea del compositor
    message_sent = Signal(dict)
    contact_updated = Signal(dict)
    chat_open_requested = Signal(dict)
    font_scale_changed = Signal(int)
    ai_state_changed = Signal(str, str)        # jid, "pending" | "ready" | "error" | "" (para la lista de chats)
    _polish_done = Signal(int, str, str)       # id de petición, jid, texto (respuesta o error)
    _polish_models_ready = Signal(str, list)   # proveedor, modelos
    _voice_done = Signal(dict)                 # nota de voz convertida y enviada (o con error)
    _dictation_done = Signal(str, str, str)    # jid, texto dictado, aviso (error o IA no disponible)
    _scheduled_counts_ready = Signal(dict)     # jid -> mensajes programados pendientes
    scheduled_total_changed = Signal(int)
    _att_result = Signal(int, dict)            # índice del adjunto, respuesta del gateway
    _att_finished = Signal()
    _sticker_result = Signal(str, dict, str, int, str, str)   # jid, respuesta, ruta enviada, hora programada, cita, autor de la cita

    def __init__(self, parent=None):
        super().__init__(parent)
        self._font_scale = get_chat_font_scale()
        self._polish_open = False
        self._polish_req = 0               # contador de peticiones (id único de cada una)
        self._polish_jobs: dict = {}       # jid -> {"req", "kind", "status"}: peticiones en curso, una por chat
        self._polish_saved: dict = {}      # jid -> {"draft", "extra", "status"}: el redactor de cada chat
        self._polish_busy = False
        self._polish_models_loaded = False
        self.current_chat = None
        self._att_sending = False        # envío de adjuntos en curso (hilo aparte)
        self._att_state = None           # estado del envío: paths, índice, progreso simulado
        self._drafts: dict = {}          # jid -> texto sin enviar
        self._messages_limit = 40
        self.gateway = GatewayManager.get_instance()

        # Menú de mensaje: responder / seleccionar / fijados
        self._msg_frames: list = []      # [(QTextFrame, msg_id)] del último repintado
        self._reply_to = None            # mensaje al que se está respondiendo
        self._select_mode = False
        self._selected: set = set()
        self._pin_index = 0              # cuál de los fijados muestra la barra

        # Refresco agrupado: al terminar varias descargas se repinta una sola vez
        self._media_refresh_timer = QTimer(self)
        self._media_refresh_timer.setSingleShot(True)
        self._media_refresh_timer.setInterval(350)
        self._media_refresh_timer.timeout.connect(self._refresh_keep_scroll)
        media_signals.media_ready.connect(self._on_media_event)
        media_signals.transcript_ready.connect(self._on_media_event)
        media_signals.job_failed.connect(self._on_media_event)
        message_summary.summary_signals.summary_ready.connect(self._on_media_event)
        self._summary_hidden: set = set()   # mensajes cuyo resumen se ocultó (solo en esta sesión)
        self._expanded_folds: set = set()   # transcripciones («t/<id>») y síntesis («s/<id>») desplegadas; por defecto, plegadas

        # Notas de voz: reproductor interno; la imagen de cada nota se redibuja al sonar
        self._voice_msgs: dict = {}      # msg_id -> (path, from_me)
        self._voice_click_frac = None
        self._voice_click_chip = False   # el clic cayó en el botón de velocidad
        player = voice_player()
        player.speed_changed.connect(self._on_voice_speed_changed)
        player.state_changed.connect(self._on_voice_changed)
        player.progress_changed.connect(self._on_voice_changed)
        player.peaks_ready.connect(self._on_voice_changed)
        player.finished.connect(self._on_voice_finished)

        # Vista previa de links: al llegar un preview se redibuja el chat (junta varios en un solo repintado)
        self._link_timer = QTimer(self)
        self._link_timer.setSingleShot(True)
        self._link_timer.setInterval(250)
        self._link_timer.timeout.connect(lambda: self.refresh_messages(scroll_to_bottom=False))
        link_preview.signals.preview_ready.connect(self._on_link_preview_ready)

        # Grabación de notas de voz: el micrófono graba a memoria y al enviar se convierte a OGG/Opus
        self._recorder = VoiceRecorder(self)
        self._voice_chat_jid = None      # chat en el que empezó la grabación en curso
        self._voice_sending: set = set() # chats con una nota convirtiéndose / subiendo (o un dictado transcribiéndose)
        self._dictation = None           # None | 'plain' | 'polish': la grabación en curso es un dictado a texto
        self._dictating: set = set()     # chats con un dictado transcribiéndose
        self._voice_blink = False

        # Programar envío: con hora elegida, lo próximo que se mande en este chat queda en la cola del gateway
        self._schedule_at = None
        self._scheduled_counts: dict = {}   # copia local: abrir un chat no consulta al gateway
        self._scheduled_loaded = False

        self._init_ui()

        self._scheduled_counts_ready.connect(self._on_scheduled_counts)
        self._sticker_result.connect(self._on_sticker_result)

        self._recorder.level.connect(self.voice_meter.push)
        self._recorder.failed.connect(self._on_recorder_failed)
        self._voice_done.connect(self._on_voice_done)
        self._voice_clock = QTimer(self)
        self._voice_clock.setInterval(500)
        self._voice_clock.timeout.connect(self._on_voice_tick)
        self.input_text.set_font_scale(self._font_scale)
        self.polish_input.set_font_scale(self._font_scale)
        # Tags del chat abierto: sus efectos (fondo, letra, cabecera) se aplican al abrirlo y cuando cambian
        self._tag_fx = tagdb.chat_effects([])
        tagdb.tag_signals.changed.connect(self._apply_chat_tags)
        self.show_empty_state()

    def _init_ui(self):
        self.layout_root = QVBoxLayout(self)
        self.layout_root.setContentsMargins(0, 0, 0, 0)
        self.layout_root.setSpacing(0)

        # ─── 1. Estado vacío (sin chat seleccionado) ──────────────────────────
        self.empty_container = QWidget()
        self.empty_container.setObjectName("emptyState")
        self.empty_container.setAttribute(Qt.WA_StyledBackground, True)
        self.empty_container.setStyleSheet(f"#emptyState {{ background-color: {C['chat_bg']}; }}")
        empty_layout = QVBoxLayout(self.empty_container)
        empty_layout.setAlignment(Qt.AlignCenter)
        empty_layout.setSpacing(6)

        icon_lbl = QLabel()
        icon_lbl.setPixmap(flame_pixmap(72))
        empty_layout.addWidget(icon_lbl, alignment=Qt.AlignCenter)

        title_lbl = QLabel("Advanced WhatsApp")
        title_lbl.setStyleSheet(f"font-size: 20px; font-weight: 300; color: {C['text']}; letter-spacing: 1px;")
        empty_layout.addWidget(title_lbl, alignment=Qt.AlignCenter)

        desc_lbl = QLabel("Elige un chat de la lista para empezar.\n"
                          "Ctrl+N nuevo chat  ·  Ctrl+K buscar  ·  Ctrl+F buscar en el chat")
        desc_lbl.setObjectName("subtle")
        desc_lbl.setAlignment(Qt.AlignCenter)
        empty_layout.addWidget(desc_lbl, alignment=Qt.AlignCenter)

        lock_row = QLabel("Cifrado de extremo a extremo")
        lock_row.setObjectName("hint")
        lock_row.setContentsMargins(0, 14, 0, 0)
        empty_layout.addWidget(lock_row, alignment=Qt.AlignCenter)

        self.layout_root.addWidget(self.empty_container)

        # ─── 2. Contenedor de chat activo ──────────────────────────────────────
        self.chat_container = QWidget()
        self.chat_layout = QVBoxLayout(self.chat_container)
        self.chat_layout.setContentsMargins(0, 0, 0, 0)
        self.chat_layout.setSpacing(0)

        # Cabecera del chat: avatar y nombre forman un único botón (abre la info del contacto)
        self.header_frame = QFrame()
        self.header_frame.setObjectName("paneHeader")
        self.header_frame.setFixedHeight(HEADER_H)
        header_layout = QHBoxLayout(self.header_frame)
        header_layout.setContentsMargins(8, 0, 8, 0)
        header_layout.setSpacing(2)

        self.contact_chip = ContactChip()
        self.contact_chip.clicked.connect(self.toggle_info_panel)
        chip_layout = QHBoxLayout(self.contact_chip)
        chip_layout.setContentsMargins(6, 0, 0, 0)
        chip_layout.setSpacing(10)

        self.header_avatar = AvatarWidget("", size=36)
        chip_layout.addWidget(self.header_avatar)

        title_layout = QVBoxLayout()
        title_layout.setSpacing(0)
        title_layout.setContentsMargins(0, 0, 0, 0)
        title_layout.addStretch(1)

        self.lbl_contact_name = QLabel("Contacto")
        self.lbl_contact_name.setStyleSheet(f"font-size: 14px; font-weight: 600; color: {C['text']};")
        name_row = QHBoxLayout()
        name_row.setSpacing(10)
        name_row.setContentsMargins(0, 0, 0, 0)
        name_row.addWidget(self.lbl_contact_name)
        # Tags del chat abierto, al lado del nombre
        self.header_tags = HeaderTagList()
        name_row.addWidget(self.header_tags)
        name_row.addStretch(1)
        title_layout.addLayout(name_row)

        self.lbl_contact_status = QLabel("")
        self.lbl_contact_status.setStyleSheet(f"font-size: 12px; color: {C['text_2']};")
        self.lbl_contact_status.setTextInteractionFlags(Qt.TextSelectableByMouse)
        title_layout.addWidget(self.lbl_contact_status)
        title_layout.addStretch(1)

        chip_layout.addLayout(title_layout)
        chip_layout.addStretch(1)
        header_layout.addWidget(self.contact_chip, stretch=1)

        # Análisis psicológico del contacto con la IA configurada
        self.btn_psych = tool_button("analysis", "Análisis psicológico del contacto (por tipo de relación y con preguntas "
                                     "puntuales) con la IA", text="Análisis")
        self.btn_psych.clicked.connect(self._open_psych_analysis)
        header_layout.addWidget(self.btn_psych)
        header_layout.addSpacing(4)

        # Nota privada del contacto: se resalta (y muestra el texto al pasar el mouse) cuando hay una
        self.btn_notes = tool_button("edit", "Nota privada del contacto")
        self.btn_notes.clicked.connect(self._open_notes_dialog)
        header_layout.addWidget(self.btn_notes)

        self.btn_edit_contact = tool_button("info", "Info del contacto")
        self.btn_edit_contact.setCheckable(True)
        self.btn_edit_contact.clicked.connect(self.toggle_info_panel)
        header_layout.addWidget(self.btn_edit_contact)

        self.btn_search = tool_button("search", "Buscar en el chat  (Ctrl+F)")
        self.btn_search.clicked.connect(self._search_btn_clicked)
        header_layout.addWidget(self.btn_search)

        self.btn_options = tool_button("more", "Opciones del chat")
        self.btn_options.clicked.connect(self._show_chat_options)
        header_layout.addWidget(self.btn_options)

        self.chat_layout.addWidget(self.header_frame)

        # Barra de búsqueda dentro del chat (oculta hasta usar la lupa o Ctrl+F)
        self.search_row = QFrame()
        self.search_row.setObjectName("searchRow")
        self.search_row.setStyleSheet(
            f"#searchRow {{ background-color: {C['surface']}; border-bottom: 1px solid {C['border']}; }}")
        self.search_row.setVisible(False)
        sr_lay = QHBoxLayout(self.search_row)
        # Barras bajo la cabecera: 14 px a la izquierda (borde del avatar) y botones centrados con los de arriba
        # (centro a 24 px del borde: botón de 32 con margen 8, o de 28 con margen 10)
        sr_lay.setContentsMargins(14, 6, 8, 6)
        sr_lay.setSpacing(4)
        self.search_bar = QLineEdit()
        self.search_bar.setObjectName("searchField")
        self.search_bar.setPlaceholderText("Buscar en el chat")
        self.search_bar.addAction(icon("search", C["text_3"], 16), QLineEdit.LeadingPosition)
        self.search_bar.returnPressed.connect(self._on_search_submit)
        self.search_bar.textChanged.connect(lambda _: self.lbl_search_info.setText(""))
        self.search_bar.installEventFilter(self)
        sr_lay.addWidget(self.search_bar, stretch=1)
        self.lbl_search_info = QLabel("")
        self.lbl_search_info.setObjectName("hint")
        sr_lay.addWidget(self.lbl_search_info)
        sr_lay.addSpacing(4)
        btn_prev = tool_button("up", "Anterior  (Shift+Enter)", icon_size=16)
        btn_prev.clicked.connect(lambda: self._on_search_submit(backward=True))
        sr_lay.addWidget(btn_prev)
        btn_next = tool_button("down", "Siguiente  (Enter)", icon_size=16)
        btn_next.clicked.connect(self._on_search_submit)
        sr_lay.addWidget(btn_next)
        btn_close_search = tool_button("close", "Cerrar  (Esc)", icon_size=16)
        btn_close_search.clicked.connect(self._close_search)
        sr_lay.addWidget(btn_close_search)
        self.chat_layout.addWidget(self.search_row)

        # Barra de mensajes fijados: clic = ir al mensaje / siguiente fijado
        self.pin_bar = QFrame()
        self.pin_bar.setObjectName("pinBar")
        self.pin_bar.setVisible(False)
        self.pin_bar.setStyleSheet(
            f"#pinBar {{ background-color: {C['surface']}; border-bottom: 1px solid {C['border']}; }}")
        pin_lay = QHBoxLayout(self.pin_bar)
        pin_lay.setContentsMargins(14, 3, 10, 3)
        pin_lay.setSpacing(8)
        pin_ico = QLabel()
        pin_ico.setPixmap(icon_pixmap("pin", C["accent"], 16))
        pin_lay.addWidget(pin_ico)
        self.lbl_pin = QLabel()
        self.lbl_pin.setCursor(Qt.PointingHandCursor)
        self.lbl_pin.setToolTip("Ir al mensaje fijado")
        self.lbl_pin.setStyleSheet(f"color: {C['text']}; font-size: 12px;")
        self.lbl_pin.mousePressEvent = lambda e: self._on_pin_bar_clicked()
        pin_lay.addWidget(self.lbl_pin, stretch=1)
        btn_unpin = tool_button("close", "Desfijar", size=28, icon_size=14)
        btn_unpin.clicked.connect(self._unpin_shown)
        pin_lay.addWidget(btn_unpin)
        self.chat_layout.addWidget(self.pin_bar)
        # IA extra: banner «Lo que te perdiste», chips de respuestas rápidas, traducción y recordatorios
        self.ai_extras = AiExtras(self)
        self.chat_layout.addWidget(self.ai_extras.banner)

        # Visor de mensajes
        self.txt_messages = ChatBrowser()
        self.txt_messages.setOpenExternalLinks(False)
        self.txt_messages.setOpenLinks(False)
        self.txt_messages.anchorClicked.connect(self._on_anchor_clicked)
        self.txt_messages.setContextMenuPolicy(Qt.CustomContextMenu)
        self.txt_messages.customContextMenuRequested.connect(self._show_message_context_menu)
        self.txt_messages.setFrameShape(QFrame.NoFrame)
        self.txt_messages.document().setDocumentMargin(12)
        self._msg_bg = None
        self._set_messages_bg(C["chat_bg"])
        self.chat_layout.addWidget(self.txt_messages, stretch=1)

        # Estado de scroll: seguir el final del chat o conservar la posición al repintar
        self._follow_bottom = True
        self._pending_restore = None
        self._rebuilding = False
        self._clear_pending_timer = QTimer(self)
        self._clear_pending_timer.setSingleShot(True)
        self._clear_pending_timer.setInterval(800)
        self._clear_pending_timer.timeout.connect(lambda: setattr(self, "_pending_restore", None))
        sb = self.txt_messages.verticalScrollBar()
        sb.rangeChanged.connect(self._on_range_changed)
        sb.valueChanged.connect(self._on_value_changed)

        # Botón flotante para ir al final del chat
        self.btn_bottom = QToolButton(self.txt_messages)
        self.btn_bottom.setIcon(icon("down", C["text_2"], active_color=C["text"]))
        self.btn_bottom.setIconSize(QSize(20, 20))
        self.btn_bottom.setToolTip("Ir a los mensajes más recientes")
        self.btn_bottom.setFixedSize(38, 38)
        self.btn_bottom.setCursor(Qt.PointingHandCursor)
        self.btn_bottom.setStyleSheet(f"""
            QToolButton {{ background-color: {C['surface']}; border: 1px solid {C['border']}; border-radius: 19px; }}
            QToolButton:hover {{ background-color: {C['hover']}; border-color: {C['border_hi']}; }}
            QToolButton:pressed {{ background-color: {C['selected']}; }}
        """)
        self.btn_bottom.setVisible(False)
        self.btn_bottom.clicked.connect(self._jump_to_bottom)
        self.txt_messages.installEventFilter(self)
        self.txt_messages.viewport().installEventFilter(self)
        self.txt_messages.viewport().setAcceptDrops(True)
        # La cabecera también recibe tags arrastrados desde la paleta
        self.header_frame.setAcceptDrops(True)
        self.header_frame.installEventFilter(self)

        bar_qss = (f"background-color: {C['surface']}; border-top: 1px solid {C['border']};")

        # Barra de archivo adjunto en preparación
        self.attachment_bar = AttachmentBar()
        self.attachment_bar.changed.connect(
            lambda: self._on_composer_text_changed(bool(self.input_text.toPlainText().strip())))
        self.chat_layout.addWidget(self.attachment_bar)
        self._att_timer = QTimer(self)
        self._att_timer.setInterval(100)
        self._att_timer.timeout.connect(self._on_att_tick)
        self._att_result.connect(self._on_att_result)
        self._att_finished.connect(self._on_att_finished)
        self.drop_overlay = DropOverlay(self.chat_container)

        # Barra de respuesta (cita del mensaje al que se responde)
        self.reply_bar = QFrame()
        self.reply_bar.setObjectName("replyBar")
        self.reply_bar.setVisible(False)
        self.reply_bar.setStyleSheet(f"#replyBar {{ {bar_qss} }}")
        reply_lay = QHBoxLayout(self.reply_bar)
        reply_lay.setContentsMargins(14, 6, 10, 6)
        reply_lay.setSpacing(8)
        self.lbl_reply = QLabel()
        self.lbl_reply.setTextFormat(Qt.RichText)
        self.lbl_reply.setStyleSheet(f"background-color: {C['raised']}; border-left: 4px solid {C['accent']}; "
                                     f"border-radius: {R_SM}px; padding: 5px 10px; color: {C['text_2']}; "
                                     f"font-size: 12px;")
        reply_lay.addWidget(self.lbl_reply, stretch=1)
        btn_cancel_reply = tool_button("close", "Cancelar respuesta  (Esc)", size=28, icon_size=14)
        btn_cancel_reply.clicked.connect(self._cancel_reply)
        reply_lay.addWidget(btn_cancel_reply)
        self.chat_layout.addWidget(self.reply_bar)

        # Barra del modo selección (reemplaza al compositor mientras hay mensajes seleccionándose)
        self.select_bar = QFrame()
        self.select_bar.setObjectName("selectBar")
        self.select_bar.setVisible(False)
        self.select_bar.setStyleSheet(f"#selectBar {{ {bar_qss} }}")
        sel_lay = QHBoxLayout(self.select_bar)
        sel_lay.setContentsMargins(8, 8, 8, 8)
        sel_lay.setSpacing(6)
        btn_exit_sel = tool_button("close", "Salir de la selección  (Esc)", icon_size=16)
        btn_exit_sel.clicked.connect(self._exit_select_mode)
        sel_lay.addWidget(btn_exit_sel)
        self.lbl_selected = QLabel("0 seleccionados")
        sel_lay.addWidget(self.lbl_selected, stretch=1)
        self.btn_sel_copy = QPushButton("Copiar")
        self.btn_sel_copy.setIcon(icon("copy"))
        self.btn_sel_copy.clicked.connect(self._copy_selected)
        sel_lay.addWidget(self.btn_sel_copy)
        self.btn_sel_forward = QPushButton("Reenviar…")
        self.btn_sel_forward.setIcon(icon("forward"))
        self.btn_sel_forward.clicked.connect(lambda: self._forward_messages(self._selected_messages()))
        sel_lay.addWidget(self.btn_sel_forward)
        self.btn_sel_delete = QPushButton("Eliminar…")
        self.btn_sel_delete.setObjectName("btnDanger")
        self.btn_sel_delete.setIcon(icon("trash", "#f0a39e", active_color="#ffffff"))
        self.btn_sel_delete.clicked.connect(lambda: self._delete_messages(self._selected_messages()))
        sel_lay.addWidget(self.btn_sel_delete)
        for b in (self.btn_sel_copy, self.btn_sel_forward, self.btn_sel_delete):
            b.setCursor(Qt.PointingHandCursor)
        self.chat_layout.addWidget(self.select_bar)

        # Redactor de respuestas: borrador → IA → campo de mensaje, listo para enviar
        self.polish_bar = QFrame()
        self.polish_bar.setObjectName("polishBar")
        self.polish_bar.setVisible(False)
        self.polish_bar.setStyleSheet(
            f"#polishBar {{ {bar_qss} }}"
            f"#polishBar QLabel {{ color: {C['text_2']}; font-size: 12px; }}")
        pol_lay = QVBoxLayout(self.polish_bar)
        pol_lay.setContentsMargins(14, 8, 10, 8)
        pol_lay.setSpacing(6)

        ia_row = QHBoxLayout()
        ia_row.setSpacing(8)
        title = QLabel("Redactor")
        title.setStyleSheet(f"color: {C['accent']}; font-weight: 600; font-size: 12px;")
        ia_row.addWidget(title)
        lbl_ia = QLabel("IA:")
        ia_row.addWidget(lbl_ia)
        self.cmb_polish_provider = QComboBox()
        self.cmb_polish_provider.addItems(pa.PROVIDERS)
        ia_row.addWidget(self.cmb_polish_provider)
        self.cmb_polish_model = QComboBox()
        self.cmb_polish_model.setMinimumWidth(120)
        self.cmb_polish_model.setToolTip("Modelos disponibles del proveedor (se cargan automáticamente)")
        ia_row.addWidget(self.cmb_polish_model, stretch=1)
        btn_polish_close = tool_button("close", "Cerrar el redactor", size=28, icon_size=14)
        btn_polish_close.clicked.connect(self._toggle_polish)
        ia_row.addWidget(btn_polish_close)
        pol_lay.addLayout(ia_row)

        goal_row = QHBoxLayout()
        goal_row.setSpacing(8)
        lbl_goal = QLabel("Qué hacer:")
        goal_row.addWidget(lbl_goal)
        # Mismo ancho de etiqueta en ambas filas: los combos empiezan en la misma columna
        lbl_goal.setFixedWidth(max(title.sizeHint().width() + 8 + lbl_ia.sizeHint().width(),
                                   lbl_goal.sizeHint().width()))
        self.cmb_polish_mode = QComboBox()
        for key, (label, _) in MODES.items():
            self.cmb_polish_mode.addItem(label, key)
        saved_mode = get_polish_mode()
        self.cmb_polish_mode.setCurrentIndex(max(0, self.cmb_polish_mode.findData(
            saved_mode if saved_mode in MODES else DEFAULT_MODE)))
        self.cmb_polish_mode.setToolTip("Cómo quieres que la IA transforme tu borrador")
        self.cmb_polish_mode.setSizeAdjustPolicy(QComboBox.AdjustToContents)
        self.cmb_polish_mode.setMinimumWidth(self.cmb_polish_mode.sizeHint().width())
        self.cmb_polish_mode.activated.connect(lambda _: set_polish_mode(self.cmb_polish_mode.currentData()))
        goal_row.addWidget(self.cmb_polish_mode)
        self.polish_extra = QLineEdit()
        self.polish_extra.setPlaceholderText("Indicación extra (opcional): ej. «menciona que el viernes no hay stock»")
        self.polish_extra.returnPressed.connect(self._run_polish)
        goal_row.addWidget(self.polish_extra, stretch=1)
        goal_row.addSpacing(28 + 8)   # termina donde el combo de arriba (antes del botón de cerrar)
        pol_lay.addLayout(goal_row)

        self.polish_input = MessageInputEdit(
            placeholder="Borrador: escribe tu respuesta como te salga y pulsa Enter para mejorarla",
            min_lines=2, max_lines=8)
        self.polish_input.send_requested.connect(self._run_polish)
        self.polish_input.installEventFilter(self)
        polish_input_row = QHBoxLayout()
        polish_input_row.setSpacing(0)
        polish_input_row.addWidget(self.polish_input, stretch=1)
        polish_input_row.addSpacing(28 + 8)
        pol_lay.addLayout(polish_input_row)

        run_row = QHBoxLayout()
        run_row.setSpacing(8)
        self.lbl_polish_status = QLabel("")
        self.lbl_polish_status.setWordWrap(True)
        run_row.addWidget(self.lbl_polish_status, stretch=1)
        self.chk_polish_profile = QCheckBox("Usar análisis y notas")
        self.chk_polish_profile.setToolTip(
            "La IA también lee tus notas sobre este contacto y su último análisis psicológico para ajustar el "
            "tono (nunca los menciona en el mensaje). El historial de análisis anteriores no se envía.")
        self.chk_polish_profile.setChecked(get_polish_profile())
        self.chk_polish_profile.toggled.connect(set_polish_profile)
        run_row.addWidget(self.chk_polish_profile)
        self.btn_polish_suggest = QPushButton("Sugerir respuesta")
        self.btn_polish_suggest.setToolTip(
            f"¿No sabes qué responder? La IA propone una respuesta leyendo los últimos {CONTEXT_MESSAGES} "
            "mensajes del chat. «Qué hacer» aporta el tono (suave, firme, sarcástico, hiriente…) y la indicación extra, "
            "lo que quieres decir. Si escribiste algo en el borrador, lo toma como ideas a incluir.")
        self.btn_polish_suggest.setCursor(Qt.PointingHandCursor)
        self.btn_polish_suggest.clicked.connect(self._run_suggest)
        run_row.addWidget(self.btn_polish_suggest)
        self.btn_polish_run = QPushButton("Mejorar")
        self.btn_polish_run.setObjectName("btnPrimary")
        self.btn_polish_run.setIcon(icon("sparkle", C["on_accent"], active_color=C["on_accent"]))
        self.btn_polish_run.setToolTip(
            f"Transforma tu borrador con la IA según «Qué hacer», usando los últimos {CONTEXT_MESSAGES} "
            "mensajes del chat como contexto, y lo deja en el campo de mensaje para que lo envíes cuando quieras")
        self.btn_polish_run.setCursor(Qt.PointingHandCursor)
        self.btn_polish_run.clicked.connect(self._run_polish)
        run_row.addWidget(self.btn_polish_run)
        run_row.addSpacing(28 + 8)
        pol_lay.addLayout(run_row)
        self.chat_layout.addWidget(self.polish_bar)

        self._polish_done.connect(self._on_polish_done)
        self._dictation_done.connect(self._on_dictation_done)
        self._polish_models_ready.connect(self._fill_polish_models)
        prov, model = get_polish_ai()
        if prov not in pa.PROVIDERS:
            prov, model = pa.default_provider_model()
        self.cmb_polish_provider.setCurrentText(prov)
        self._set_polish_model(model or pa.config_model(prov))
        self.cmb_polish_provider.currentTextChanged.connect(self._on_polish_provider_changed)
        self.cmb_polish_model.activated.connect(lambda _: self._save_polish_choice())

        # Compositor: botones circulares y un campo en píldora del mismo alto cuando tiene una línea
        composer_frame = QFrame()
        composer_frame.setObjectName("composer")
        self.composer_frame = composer_frame
        composer_frame.setStyleSheet(f"#composer {{ {bar_qss} }}")
        composer_layout = QHBoxLayout(composer_frame)
        composer_layout.setContentsMargins(8, 8, 8, 8)
        composer_layout.setSpacing(4)
        btn = self.COMPOSER_H

        self.btn_attach = tool_button("attach", "Adjuntar archivo (también puedes arrastrarlo al chat o pegarlo con Ctrl+V)",
                                      size=btn, icon_size=20)
        self.btn_attach.clicked.connect(self._choose_attachment)
        composer_layout.addWidget(self.btn_attach, alignment=Qt.AlignBottom)

        self.btn_polish = tool_button("sparkle", "Redactor: escribe un borrador y la IA lo deja listo para enviar",
                                      size=btn, icon_size=20)
        self.btn_polish.setCheckable(True)
        self.btn_polish.clicked.connect(self._toggle_polish)
        composer_layout.addWidget(self.btn_polish, alignment=Qt.AlignBottom)

        self.btn_translate = tool_button("translate", "Traducir antes de enviar: escribí el mensaje y elegí el idioma",
                                         size=btn, icon_size=20)
        self.btn_translate.clicked.connect(lambda: self.ai_extras.show_draft_menu(self.btn_translate))
        composer_layout.addWidget(self.btn_translate, alignment=Qt.AlignBottom)

        self.btn_schedule = tool_button("clock", "Programar envío: elegí día y hora, y lo próximo que mandes sale solo",
                                        size=btn, icon_size=20)
        self.btn_schedule.clicked.connect(self._choose_schedule_time)
        composer_layout.addWidget(self.btn_schedule, alignment=Qt.AlignBottom)
        self.btn_emoji = tool_button("smile", "Emojis y stickers", size=btn, icon_size=20)
        self.btn_emoji.clicked.connect(self._toggle_emoji_panel)
        composer_layout.addWidget(self.btn_emoji, alignment=Qt.AlignBottom)
        composer_layout.addSpacing(4)

        self.input_text = MessageInputEdit(placeholder="Escribe un mensaje  (Enter envía, Shift+Enter salto de línea)",
                                           accept_media=True, radius=btn // 2, line_height=btn)
        self.input_text.send_requested.connect(self._send_message)
        self.input_text.has_text_changed.connect(self._on_composer_text_changed)
        self.input_text.image_pasted.connect(self._attach_pasted_image)
        self.input_text.files_pasted.connect(self._attach_pasted_files)
        self.input_text.installEventFilter(self)
        self.input_text.viewport().installEventFilter(self)
        composer_layout.addWidget(self.input_text, stretch=1)

        # Grabación de nota de voz: ocupa el lugar del campo de texto mientras se graba o se envía
        self.voice_panel = QWidget()
        self.voice_panel.setFocusPolicy(Qt.StrongFocus)   # Esc descarta y Enter envía (ver keyPressEvent)
        self.voice_panel.setVisible(False)
        self.voice_panel.setFixedHeight(btn)
        vp_lay = QHBoxLayout(self.voice_panel)
        vp_lay.setContentsMargins(0, 0, 0, 0)
        vp_lay.setSpacing(8)
        self.btn_voice_cancel = tool_button("trash", "Descartar la nota de voz  (Esc)", size=btn, icon_size=20,
                                            color=C["danger"])
        self.btn_voice_cancel.clicked.connect(self._cancel_voice)
        vp_lay.addWidget(self.btn_voice_cancel)
        self.lbl_voice_dot = QLabel("●")
        dot_policy = self.lbl_voice_dot.sizePolicy()
        dot_policy.setRetainSizeWhenHidden(True)   # el parpadeo no mueve el resto de la barra
        self.lbl_voice_dot.setSizePolicy(dot_policy)
        vp_lay.addWidget(self.lbl_voice_dot)
        self.lbl_voice_time = QLabel("0:00")
        self.lbl_voice_time.setMinimumWidth(40)
        self.lbl_voice_time.setStyleSheet(f"color: {C['text']}; font-size: 13px;")
        vp_lay.addWidget(self.lbl_voice_time)
        self.voice_meter = LevelMeter()
        vp_lay.addWidget(self.voice_meter, stretch=1)
        self.btn_voice_pause = tool_button("pause", "Pausar", size=btn, icon_size=20)
        self.btn_voice_pause.clicked.connect(self._toggle_voice_pause)
        vp_lay.addWidget(self.btn_voice_pause)
        composer_layout.addWidget(self.voice_panel, stretch=1)
        composer_layout.addSpacing(4)

        self.btn_dictate = tool_button("dictate", "Dictar: habla y el texto aparece en el campo para revisarlo (no se envía nada)",
                                       size=btn, icon_size=20)
        self.btn_dictate.clicked.connect(self._show_dictate_menu)
        composer_layout.addWidget(self.btn_dictate, alignment=Qt.AlignBottom)

        self.btn_action = QToolButton()
        self.btn_action.setObjectName("sendButton")
        self.btn_action.setIcon(icon("send", C["on_accent"], active_color=C["on_accent"]))
        self.btn_action.setIconSize(QSize(18, 18))
        self.btn_action.setToolTip("Enviar  (Enter)")
        self.btn_action.setFixedSize(btn, btn)
        self.btn_action.setCursor(Qt.PointingHandCursor)
        self.btn_action.setStyleSheet(f"""
            QToolButton#sendButton {{ background-color: {C['accent']}; border: none; border-radius: {btn // 2}px; }}
            QToolButton#sendButton:hover {{ background-color: {C['accent_hi']}; }}
            QToolButton#sendButton:pressed {{ background-color: {C['accent']}; padding-top: 2px; }}
            QToolButton#sendButton:disabled {{ background-color: {C['raised']}; }}
        """)
        self.btn_action.clicked.connect(self._on_action_clicked)
        composer_layout.addWidget(self.btn_action, alignment=Qt.AlignBottom)
        self._update_action_button()

        # Encima del compositor: la hora elegida y cuántos mensajes de este chat están en cola
        self.schedule_bar = QFrame()
        self.schedule_bar.setObjectName("scheduleBar")
        self.schedule_bar.setStyleSheet(f"#scheduleBar {{ {bar_qss} }}")
        self.schedule_bar.setVisible(False)
        sb_lay = QHBoxLayout(self.schedule_bar)
        sb_lay.setContentsMargins(12, 4, 8, 4)
        sb_lay.setSpacing(8)
        self.lbl_schedule_icon = QLabel()
        self.lbl_schedule_icon.setPixmap(icon_pixmap("clock", C["accent_hi"], 16))
        sb_lay.addWidget(self.lbl_schedule_icon)
        self.lbl_schedule = QLabel()
        self.lbl_schedule.setStyleSheet(f"color: {C['text']}; font-size: 13px;")
        sb_lay.addWidget(self.lbl_schedule)
        self.btn_schedule_clear = tool_button("close", "No programar: volver a enviar en el momento", size=24, icon_size=14)
        self.btn_schedule_clear.clicked.connect(lambda: self._set_schedule(None))
        sb_lay.addWidget(self.btn_schedule_clear)
        sb_lay.addStretch()
        self.btn_schedule_list = QPushButton()
        self.btn_schedule_list.setFlat(True)
        self.btn_schedule_list.setCursor(Qt.PointingHandCursor)
        self.btn_schedule_list.setStyleSheet(f"color: {C['accent_hi']}; border: none; font-size: 13px; padding: 2px 6px;")
        self.btn_schedule_list.clicked.connect(self._open_chat_scheduled)
        sb_lay.addWidget(self.btn_schedule_list)
        self.chat_layout.addWidget(self.schedule_bar)

        self.chat_layout.addWidget(self.ai_extras.quick)
        self.chat_layout.addWidget(composer_frame)

        # Fila: chat + panel lateral de info del contacto
        from app.ui.contact_info_panel import ContactInfoPanel
        self.chat_row = QWidget()
        row_layout = QHBoxLayout(self.chat_row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(0)
        row_layout.addWidget(self.chat_container, stretch=1)
        self.info_panel = ContactInfoPanel()
        self.info_panel.setVisible(False)
        self.info_panel.close_requested.connect(lambda: self._set_info_visible(False))
        self.info_panel.edit_requested.connect(self._open_edit_contact_dialog)
        self.info_panel.search_requested.connect(self._focus_search)
        row_layout.addWidget(self.info_panel)
        self.layout_root.addWidget(self.chat_row)

    def _set_info_visible(self, visible: bool):
        self.info_panel.setVisible(visible)
        self.btn_edit_contact.setChecked(visible)
        if hasattr(self, "btn_notes"):
            self._refresh_notes_indicator()   # el panel de info también edita la nota

    def _focus_search(self):
        self.search_row.setVisible(True)
        self.search_bar.setFocus()
        self.search_bar.selectAll()

    def _close_search(self):
        self.search_row.setVisible(False)
        self.lbl_search_info.setText("")
        cur = self.txt_messages.textCursor()
        cur.clearSelection()
        self.txt_messages.setTextCursor(cur)
        self.input_text.setFocus()

    def _on_search_submit(self, backward: bool = False):
        text = self.search_bar.text().strip()
        if not text:
            return
        flags = QTextDocument.FindBackward if backward else QTextDocument.FindFlag(0)
        self._follow_bottom = False
        if self.txt_messages.find(text, flags):
            self.lbl_search_info.setText("")
            return
        # Búsqueda circular: volver al otro extremo y reintentar
        cur = self.txt_messages.textCursor()
        cur.movePosition(QTextCursor.End if backward else QTextCursor.Start)
        self.txt_messages.setTextCursor(cur)
        found = self.txt_messages.find(text, flags)
        self.lbl_search_info.setText("" if found else "Sin resultados")

    def _search_btn_clicked(self):
        if self.search_row.isVisible():
            self._close_search()
        else:
            self._focus_search()

    def _display_name(self) -> str:
        return self.lbl_contact_name.text()

    def toggle_info_panel(self):
        if not self.current_chat:
            return
        if self.info_panel.isVisible():
            self._set_info_visible(False)
            return
        self.info_panel.show_chat(self.current_chat, self._display_name())
        self._set_info_visible(True)

    def _on_composer_text_changed(self, has_text: bool):
        self._update_action_button()

    def show_empty_state(self):
        self._cancel_voice()
        self.current_chat = None
        self._set_schedule(None)
        self._cancel_reply()
        self._exit_select_mode(refresh=False)
        self.empty_container.setVisible(True)
        self.chat_row.setVisible(False)  # si no, la fila vacía se reparte la altura y deja una franja negra
        self.chat_container.setVisible(False)
        self._set_info_visible(False)

    def load_chat(self, chat_data: dict):
        # Borrador por chat: lo escrito queda guardado en su conversación, no viaja a la siguiente
        prev_jid = self.current_chat.get("jid") if self.current_chat else None
        if self._recorder.active and prev_jid != chat_data.get("jid"):
            self._resolve_voice_before_leaving()
        if prev_jid:
            self._drafts[prev_jid] = self.input_text.toPlainText()
            self._save_polish_state(prev_jid)
        if prev_jid != chat_data.get("jid"):
            self._remove_attachment()
            self.input_text.setPlainText(self._drafts.get(chat_data.get("jid"), ""))
            self.input_text.moveCursor(QTextCursor.End)
            self._schedule_at = None   # la hora elegida es para ese chat: no viaja al siguiente
        self.current_chat = chat_data
        self._update_schedule_bar()
        if not self._scheduled_loaded:
            self.refresh_scheduled()
        self._cancel_reply()
        self._exit_select_mode(refresh=False)
        self._refresh_voice_ui()
        self._pin_index = 0
        self._show_polish_state(chat_data.get("jid", ""))
        self.empty_container.setVisible(False)
        self.chat_row.setVisible(True)
        self.chat_container.setVisible(True)

        name = chat_data.get("name") or ""
        jid = chat_data.get("jid", "")
        phone = "" if chat_data.get("is_group") else resolve_phone(chat_data)
        avatar_path = chat_data.get("avatar_path")

        if not name or name.isdigit() or name.startswith("Usuario ("):
            if "(Tú)" in name or jid.startswith("784305"):
                name = "+54 9 294 494-0567 (Tú)"
            elif phone:
                name = f"+{phone}"
            elif name.startswith("Usuario ("):
                name = f"Contacto {name.replace('Usuario ', '')}"
            else:
                name = f"Contacto {jid.split('@')[0]}"

        self.lbl_contact_name.setText(name)

        if "(Tú)" in name:
            self.lbl_contact_status.setText("Mensajes guardados")
        elif chat_data.get("is_group"):
            self.lbl_contact_status.setText("Grupo de WhatsApp")
        else:
            self.lbl_contact_status.setText(format_phone(phone) if phone else "Número no disponible")

        self.header_avatar.set_avatar(name, avatar_path=avatar_path)
        self._refresh_notes_indicator()
        self._default_subtitle = self.lbl_contact_status.text()
        self._presence_reset()
        if not chat_data.get("is_group") and "(Tú)" not in name:
            self.gateway.chat_presence(jid, subscribe=True)

        self.search_bar.clear()
        self.search_row.setVisible(False)
        if self.info_panel.isVisible():
            self.info_panel.show_chat(chat_data, name)

        from app.db import get_chat as _get_chat
        self.ai_extras.on_chat_opened(chat_data, int((_get_chat(jid) or {}).get("unread_count") or 0))   # antes de marcar leído
        newly_read = mark_chat_as_read(jid)
        self.gateway.send_read_receipts(jid, [mid for mid, _ in newly_read])
        self._messages_limit = 40
        self._apply_chat_tags(refresh=False)
        self.refresh_messages(scroll_to_bottom=True)
        self.input_text.setFocus()

    # ── Tags del chat: cabecera y efectos de la ventana ──

    def _set_messages_bg(self, color: str):
        if color == self._msg_bg:
            return
        self._msg_bg = color
        self.txt_messages.setStyleSheet(f"""
            QTextBrowser {{
                background-color: {color};
                border: none;
                padding: 0 8px;
                selection-background-color: {C['accent']};
                selection-color: {C['on_accent']};
            }}
        """)

    def _apply_chat_tags(self, refresh: bool = True):
        """Muestra los tags del chat abierto y aplica sus efectos; si hay varios, manda el de más prioridad."""
        chat = self.current_chat
        tags = tagdb.tags_for_chat(chat.get("jid")) if chat else []
        last_time = (chat or {}).get("last_message_time", 0)
        fx = tagdb.chat_effects(tags)
        old_text = self._tag_fx.get("text")
        self._tag_fx = fx

        self._set_messages_bg(mix(C["chat_bg"], fx["bg"], fx["bg_strength"] / 100).name() if fx["bg"]
                              else C["chat_bg"])
        if fx["header"]:
            head_bg = mix(C["surface"], fx["header"], 0.28).name()
            self.header_frame.setStyleSheet(
                f"#paneHeader {{ background-color: {head_bg}; border-bottom: 2px solid {fx['header']}; }}")
        else:
            head_bg = C["surface"]
            self.header_frame.setStyleSheet("")
        self.header_tags.set_tags(tags, last_time)
        self.header_avatar.tag_ring = head_bg
        self.header_avatar.set_tags(tags)
        self.header_avatar.update()
        if refresh and chat and fx["text"] != old_text:
            self.refresh_messages(scroll_to_bottom=False)

    def _open_psych_analysis(self):
        if not self.current_chat:
            return
        from app.ui.psych_dialog import PsychAnalysisDialog
        jid = self.current_chat.get("jid", "")
        if not get_messages(jid, limit=1):
            QMessageBox.information(self, "Análisis psicológico", "Esta conversación no tiene mensajes.")
            return
        PsychAnalysisDialog(jid, self._display_name(), parent=self).exec()

    def _refresh_notes_indicator(self):
        """El botón de la nota se resalta y muestra su texto cuando el contacto tiene una."""
        from app.db import get_contact_notes
        jid = (self.current_chat or {}).get("jid")
        note = get_contact_notes(jid).strip() if jid else ""
        self.btn_notes.setIcon(icon("edit", C["accent"] if note else None))
        self.btn_notes.setToolTip(("Nota privada: " + (note if len(note) < 400 else note[:400] + "…")) if note
                                  else "Nota privada del contacto (vacía)")

    def _open_notes_dialog(self):
        if not self.current_chat:
            return
        from app.ui.notes_dialog import NotesDialog
        NotesDialog(self.current_chat["jid"], self._display_name(), parent=self).exec()
        self._refresh_notes_indicator()
        if self.info_panel.isVisible():
            self.info_panel.show_chat(self.current_chat, self._display_name())

    def _open_gallery(self):
        if not self.current_chat:
            return
        from app.ui.gallery_dialog import GalleryDialog
        GalleryDialog(self.current_chat["jid"], self._display_name(), parent=self).exec()

    def _open_edit_contact_dialog(self):
        if not self.current_chat:
            return
        from app.ui.contact_dialog import ContactEditDialog
        dlg = ContactEditDialog(self.current_chat, parent=self)
        dlg.contact_saved.connect(self._on_contact_saved_from_dialog)
        dlg.exec()

    def _on_contact_saved_from_dialog(self, updated_chat: dict):
        self.current_chat.update(updated_chat)
        new_name = updated_chat.get("name")
        if new_name:
            self.lbl_contact_name.setText(new_name)
            self.header_avatar.set_avatar(new_name, avatar_path=self.current_chat.get("avatar_path"))
        phone = resolve_phone(self.current_chat)
        if phone:
            self.lbl_contact_status.setText(format_phone(phone))
            self._default_subtitle = format_phone(phone)
        if self.info_panel.isVisible():
            self.info_panel.show_chat(self.current_chat, self._display_name())
        self.contact_updated.emit(updated_chat)

    # ── Presencia del contacto ("en línea", "escribiendo…") y la mía ──

    def _presence_reset(self):
        if not hasattr(self, "_presence_timer"):
            self._presence_timer = QTimer(self)
            self._presence_timer.setSingleShot(True)
            self._presence_timer.timeout.connect(self._presence_expired)
            # "escribiendo…" propio: se repite cada 8 s mientras se escribe y se corta a los 5 s sin teclear
            self._typing_sent_at = 0.0
            self._typing_idle = QTimer(self)
            self._typing_idle.setSingleShot(True)
            self._typing_idle.setInterval(5000)
            self._typing_idle.timeout.connect(self._typing_paused)
            self._typing_jid = None
            self.input_text.textChanged.connect(self._on_typing)
        self._presence_timer.stop()
        self._contact_online = False
        self._apply_presence_label(None)

    def _same_chat(self, jid: str) -> bool:
        cur = (self.current_chat or {}).get("jid") or ""
        if not jid or not cur:
            return False
        return jid == cur or jid.split("@")[0].split(":")[0] == cur.split("@")[0].split(":")[0]

    def on_presence(self, data: dict):
        """Llega del gateway: available / unavailable / composing / recording / paused."""
        if not self._same_chat(data.get("chat_jid") or ""):
            return
        p = data.get("presence") or "unavailable"
        if p in ("composing", "recording"):
            self._contact_online = True
            self._apply_presence_label("escribiendo…" if p == "composing" else "grabando audio…", accent=True)
            self._presence_timer.start(25000)   # si no llega "paused", se apaga solo
        elif p in ("available", "paused"):
            self._contact_online = True
            self._presence_timer.stop()
            self._apply_presence_label("en línea")
        else:
            self._contact_online = False
            self._presence_timer.stop()
            self._apply_presence_label(None)

    def _presence_expired(self):
        self._apply_presence_label("en línea" if self._contact_online else None)

    def _apply_presence_label(self, text, accent: bool = False):
        if text:
            self.lbl_contact_status.setText(text)
            color = C["accent_hi"] if accent else C["text_2"]
        else:
            self.lbl_contact_status.setText(getattr(self, "_default_subtitle", ""))
            color = C["text_2"]
        self.lbl_contact_status.setStyleSheet(f"font-size: 12px; color: {color};")

    def _on_typing(self):
        from app.config import get_pref
        jid = (self.current_chat or {}).get("jid")
        if not jid or self.current_chat.get("is_group") or not self.input_text.toPlainText().strip():
            if self._typing_jid:
                self._typing_paused()
            return
        if not get_pref("typing_indicator"):
            return
        now = time.monotonic()
        if self._typing_jid != jid or now - self._typing_sent_at > 8:
            if self._typing_jid and self._typing_jid != jid:
                self.gateway.chat_presence(self._typing_jid, state="paused")
            self.gateway.chat_presence(jid, state="composing")
            self._typing_sent_at = now
            self._typing_jid = jid
        self._typing_idle.start()

    def _typing_paused(self):
        if self._typing_jid:
            self.gateway.chat_presence(self._typing_jid, state="paused")
        self._typing_jid = None
        self._typing_sent_at = 0.0
        self._typing_idle.stop()

    def update_avatar_if_current(self, jid: str, avatar_path: str):
        if self.current_chat:
            curr_jid = self.current_chat.get("jid", "")
            clean = jid.split('@')[0]
            if curr_jid == jid or curr_jid.startswith(clean) or clean in curr_jid:
                self.current_chat["avatar_path"] = avatar_path
                self.header_avatar.set_avatar(self.lbl_contact_name.text(), avatar_path=avatar_path)
                if self.info_panel.isVisible():
                    self.info_panel.set_avatar_path(self.lbl_contact_name.text(), avatar_path)

    def _on_anchor_clicked(self, url):
        url_str = url.toString()
        if url_str == "action:load_older":
            self._load_older_messages()
        elif url_str.startswith("http://") or url_str.startswith("https://"):
            import webbrowser
            webbrowser.open(url_str)
        elif url.scheme() == "advancedwhatsapp":
            # advancedwhatsapp://<acción>/<message_id>
            action = url.host()
            if action == "select":
                self._toggle_selected(url.path().lstrip("/"))
                return
            if action == "fold":
                self._toggle_fold(url.path().lstrip("/"))
                return
            msg = get_message(url.path().lstrip("/"))
            if not msg:
                return
            if action == "open":
                path = msg.get("media_path")
                if path and os.path.exists(path):
                    QDesktopServices.openUrl(QUrl.fromLocalFile(path))
                else:
                    request_media(msg, save_copy=True)   # solo se baja y queda en la burbuja y en Descargas
            elif action == "play":
                path = msg.get("media_path")
                chip, self._voice_click_chip = self._voice_click_chip, False
                if chip:
                    voice_player().cycle_speed()
                elif path and os.path.exists(path):
                    frac, self._voice_click_frac = self._voice_click_frac, None
                    if frac is not None:
                        voice_player().seek(msg["id"], path, frac)
                    else:
                        voice_player().toggle(msg["id"], path)
                else:
                    request_media(msg)
            elif action == "transcribe":
                request_transcript(msg)
            elif action in ("original", "history"):
                from app.ui.extras_dialogs import OriginalDialog
                OriginalDialog(msg, "revoked" if action == "original" else "edits", self).exec()
            elif action == "summarize":
                self._toggle_summary(msg)
            elif action == "remind":
                self.ai_extras.open_reminder(msg)
            elif action == "untranslate":
                self.ai_extras.toggle_translation(msg)
            elif action == "resummarize":
                self._request_summary(msg)
            elif action == "react":
                self._open_reaction_picker(msg)
            elif action == "reply" and not self._select_mode:
                self._start_reply(msg)
            elif action == "forward" and not self._select_mode:
                self._forward_messages([msg])
            elif action in ("contactmsg", "contactsave"):
                self._on_shared_contact_action(action, msg, url.query())

    @staticmethod
    def _shared_contacts(msg: dict) -> list:
        """Contactos con teléfono dentro de un mensaje de contacto compartido."""
        try:
            data = json.loads(msg.get("media_raw") or "{}")
        except (ValueError, TypeError):
            return []
        return [c for c in data.get("contacts", []) if "".join(ch for ch in (c.get("phone") or "") if ch.isdigit())]

    def _contact_actions_html(self, msg: dict) -> str:
        contacts = self._shared_contacts(msg)
        parts = []
        for i, c in enumerate(contacts):
            label = f'{html.escape(c.get("name") or "")}: ' if len(contacts) > 1 else ""
            parts.append(
                f'<br>{label}<a href="advancedwhatsapp://contactmsg/{msg.get("id")}?i={i}" style="color: {C["link"]};">Mensaje</a>'
                f' · <a href="advancedwhatsapp://contactsave/{msg.get("id")}?i={i}" style="color: {C["link"]};">Guardar contacto</a>')
        return "".join(parts)

    def _on_shared_contact_action(self, action: str, msg: dict, query: str):
        contacts = self._shared_contacts(msg)
        try:
            c = contacts[int(query.split("=")[1])]
        except (IndexError, ValueError):
            return
        phone = "".join(ch for ch in c["phone"] if ch.isdigit())
        name = (c.get("name") or "").strip()
        jid = f"{phone}@s.whatsapp.net"
        if action == "contactmsg":
            self.chat_open_requested.emit(get_or_create_chat(jid, name=name or None, phone=phone))
            return
        get_or_create_chat(jid, name=name or None, phone=phone)
        if not save_or_update_contact(jid, name or phone, phone):
            return
        try:
            self.gateway.update_contact(jid, name=name or phone, phone=phone)
        except Exception:
            pass
        self.contact_updated.emit({"jid": jid})
        QMessageBox.information(self, "Contacto guardado", f"{name or phone} se guardó en tus contactos.")

    def _open_reaction_picker(self, msg: dict):
        mine = next((e for e, who in get_reactions([msg["id"]]).get(msg["id"], []) if who == "me"), "")
        picker = ReactionPicker(mine, self)
        picker.emoji_chosen.connect(lambda emoji: self._react(msg, emoji))
        picker.adjustSize()
        picker.move(QCursor.pos() - picker.rect().center())
        picker.show()

    def _react(self, msg: dict, emoji: str):
        """Guarda la reacción al instante y la envía; si falla, se revierte y se avisa."""
        msg_id = msg["id"]
        chat_jid = msg["chat_jid"]
        previous = next((e for e, who in get_reactions([msg_id]).get(msg_id, []) if who == "me"), "")
        if emoji == previous:
            return
        if self.gateway.get_status().get("status") != "connected":
            QMessageBox.warning(self, "WhatsApp no conectado", "No se puede reaccionar sin conexión a WhatsApp.")
            return
        import time
        set_reaction(msg_id, "me", emoji, int(time.time()))
        self.refresh_messages(scroll_to_bottom=False)
        participant = msg.get("sender_jid") if chat_jid.endswith("@g.us") else None
        res = self.gateway.send_reaction(chat_jid, msg_id, emoji, bool(msg.get("from_me")), participant)
        if not res.get("success"):
            set_reaction(msg_id, "me", previous)
            self.refresh_messages(scroll_to_bottom=False)
            QMessageBox.warning(self, "No se pudo reaccionar", str(res.get("error", "Desconocido")))

    _URL_RE = re.compile(r"(https?://[^\s<]+|www\.[^\s<]+)")

    @classmethod
    def _linkify(cls, text: str) -> str:
        """Escapa el texto y convierte las direcciones web en enlaces clicables."""
        out, pos = [], 0
        for m in cls._URL_RE.finditer(text):
            out.append(html.escape(text[pos:m.start()]))
            url = m.group(0).rstrip(".,;:)!?")
            tail = m.group(0)[len(url):]
            href = url if url.startswith("http") else "https://" + url
            out.append(f'<a href="{html.escape(href)}" style="color: {C["link"]};">{html.escape(url)}</a>'
                       + html.escape(tail))
            pos = m.end()
        out.append(html.escape(text[pos:]))
        return "".join(out).replace("\n", "<br/>")

    @staticmethod
    def _react_link(msg_id: str) -> str:
        return (f'<a href="advancedwhatsapp://react/{msg_id}" style="text-decoration: none;" title="Reaccionar">'
                f'<img src="icon:react" width="15" height="15" style="vertical-align: middle;"/></a>&nbsp;&nbsp;')

    @staticmethod
    def _reply_forward_links(msg: dict) -> str:
        """Botones Responder y Reenviar junto a la hora de cada mensaje."""
        mid = msg["id"]
        mt = msg.get("media_type") or "text"
        out = ""
        if mt != "revoked":
            out += (f'<a href="advancedwhatsapp://reply/{mid}" style="text-decoration: none;" title="Responder">'
                    f'<img src="icon:reply" width="15" height="15" style="vertical-align: middle;"/></a>&nbsp;&nbsp;')
        if mt in FORWARDABLE_TYPES:
            out += (f'<a href="advancedwhatsapp://forward/{mid}" style="text-decoration: none;" title="Reenviar">'
                    f'<img src="icon:forward" width="15" height="15" style="vertical-align: middle;"/></a>&nbsp;&nbsp;')
        return out

    @staticmethod
    def _summary_link(msg: dict, hidden: bool) -> str:
        """Botón ✨ junto a la hora: resume el mensaje, o muestra/oculta el resumen que ya tiene."""
        if not message_summary.can_summarize(msg):
            return ""
        shown = bool(msg.get("summary")) and not hidden
        tip = "Ocultar el resumen" if shown else ("Ver el resumen" if msg.get("summary") else
                                                 "Modo resumen: resumir e interpretar este mensaje con IA")
        return (f'<a href="advancedwhatsapp://summarize/{msg["id"]}" style="text-decoration: none;" title="{tip}">'
                f'<img src="icon:{"summary_on" if shown else "summary"}" width="14" height="14" '
                f'style="vertical-align: middle;"/></a>&nbsp;&nbsp;')

    def _summary_html(self, msg: dict) -> str:
        """Recuadro del modo resumen dentro de la burbuja (resumen, en curso o error)."""
        mid = msg["id"]
        link = f'color: {C["link"]}; font-size: 11px;'
        if message_summary.is_pending(mid):
            return (f'<div style="color: {C["text_2"]}; font-size: 11px; margin-top: 3px;">'
                    f'<i>✨ Resumiendo…</i></div>')
        err = message_summary.get_error(mid)
        if err and mid not in self._summary_hidden:
            return (f'<div style="color: #e58a84; font-size: 11px; margin-top: 3px;">'
                    f'No se pudo resumir: {html.escape(err)[:160]} · '
                    f'<a href="advancedwhatsapp://resummarize/{mid}" style="{link}">Reintentar</a></div>')
        summary = msg.get("summary")
        if not summary or mid in self._summary_hidden:
            return ""
        key = f"s/{mid}"
        header = self._fold_header(key, "SÍNTESIS IA", key in self._expanded_folds)
        if key not in self._expanded_folds:
            return '<div style="margin-top: 4px;"></div>' + self._panel_html(header, accent=C["accent"])
        rows = [header]
        for title, text in message_summary.parse_summary(summary):
            rows.append(f'<span style="color: {C["accent_hi"]}; font-size: 10px; font-weight: 600;">'
                        f'{html.escape(title.upper())}</span><br>'
                        f'<span style="color: {C["text"]};">{html.escape(text).replace(chr(10), "<br>")}</span>')
        rows.append(f'<span style="font-size: 10px;"><a href="advancedwhatsapp://resummarize/{mid}" style="{link}">Rehacer</a>'
                    f' · <a href="advancedwhatsapp://summarize/{mid}" style="{link}">Ocultar</a></span>')
        return '<div style="margin-top: 4px;"></div>' + self._panel_html('<br>'.join(rows), accent=C["accent"])

    @staticmethod
    def _fold_header(key: str, title: str, expanded: bool) -> str:
        """Encabezado clickeable de una sección plegable de la burbuja (flecha + título)."""
        return (f'<a href="advancedwhatsapp://fold/{key}" style="text-decoration: none; color: {C["accent_hi"]}; '
                f'font-size: 10px; font-weight: 600;">{"▾" if expanded else "▸"} {title}</a>')

    def _toggle_fold(self, key: str):
        """Despliega o pliega una transcripción / síntesis. Se repinta el chat entero (un solo setHtml) conservando
        el scroll: el alto de las burbujas lo recalcula el documento, no hay alturas fijas que se desacomoden."""
        if key in self._expanded_folds:
            self._expanded_folds.discard(key)
        else:
            self._expanded_folds.add(key)
        self._follow_bottom = False   # que el repintado conserve la posición en vez de saltar al final
        self.refresh_messages(scroll_to_bottom=False)

    def _toggle_summary(self, msg: dict):
        mid = msg["id"]
        if message_summary.is_pending(mid):
            return
        if msg.get("summary"):
            if mid in self._summary_hidden:
                self._summary_hidden.discard(mid)
            else:
                self._summary_hidden.add(mid)
            self._refresh_keep_scroll()
        elif message_summary.get_error(mid) and mid not in self._summary_hidden:
            self._summary_hidden.add(mid)   # el ✨ también descarta el aviso de error
            self._refresh_keep_scroll()
        else:
            self._request_summary(msg)

    def _request_summary(self, msg: dict):
        """Pide el resumen (o lo rehace) con Claude Haiku 5.5; sin clave de Anthropic, con el proveedor y
        modelo elegidos en el redactor."""
        self._summary_hidden.discard(msg["id"])
        prov, model = message_summary.summary_ai(self.cmb_polish_provider.currentText(),
                                                 self.cmb_polish_model.currentText().strip())
        if message_summary.request_summary(msg, self._display_name(), prov, model):
            self._refresh_keep_scroll()

    @staticmethod
    def _panel_html(inner: str, bg: str = "rgba(0,0,0,0.18)", accent: str = None, padding: str = "5px 9px",
                    width: str = "100%") -> str:
        """Recuadro dentro de una burbuja (cita, archivo, transcripción). ChatBrowser lo pinta redondeado;
        con `accent`, lleva una franja de ese color a la izquierda."""
        accent_css = f" border-color: {accent}; border-style: solid;" if accent else ""
        if accent:
            top, side = (padding.split() + [padding])[:2]
            padding = f"{top} {side} {top} 13px"
        return (f'<table width="{width}" border="0" cellpadding="0" cellspacing="0" '
                f'style="background-color: {bg}; margin-bottom: 4px;{accent_css}">'
                f'<tr><td style="padding: {padding};">{inner}</td></tr></table>')

    @staticmethod
    def _reactions_html(msg_id: str, reactions: dict, align: str) -> str:
        """Emojis de reacción agrupados con su conteo, en una píldora debajo de la burbuja."""
        items = reactions.get(msg_id)
        if not items:
            return ""
        counts: dict = {}
        mine = False
        for emoji, who in items:
            counts[emoji] = counts.get(emoji, 0) + 1
            mine = mine or who == "me"
        total = len(items)
        label = "".join(counts)
        bg = C["accent_dim"] if mine else C["bubble_in"]
        count_txt = f' <span style="font-size: 11px; color: {C["text_2"]};">{total}</span>' if total > 1 else ""
        return (
            f'<table align="{align}" border="0" cellpadding="0" cellspacing="0" '
            f'style="background-color: {bg}; margin-top: 2px;"><tr><td style="padding: 1px 8px;">'
            f'<a href="advancedwhatsapp://react/{msg_id}" style="text-decoration: none; color: {C["text"]}; '
            f'font-size: 15px;">{label}{count_txt}</a></td></tr></table>'
        )

    def _on_media_event(self, msg_id: str):
        """Repinta el chat cuando llega un archivo o una transcripción (agrupado en 350 ms)."""
        if self.current_chat:
            self._media_refresh_timer.start()

    def _refresh_keep_scroll(self):
        self.refresh_messages(scroll_to_bottom=False)

    def _media_placeholder(self, msg: dict, label: str) -> str:
        """Texto de estado mientras un archivo se descarga o si WhatsApp ya no lo tiene."""
        msg_id = msg.get("id")
        reason = unavailable_reason(msg)
        muted = C["text_2"]
        if reason == "view_once":
            return f'<i style="color: {muted};">{label} · de visualización única (solo se abre en el teléfono)</i>'
        if reason:
            return f'<i style="color: {muted};">{label} · no disponible (WhatsApp no envió el archivo)</i>'
        if is_pending("download", msg_id):
            return f'<i style="color: {muted};">{label} · cargando…</i>'
        if get_error("download", msg_id) or not msg.get("media_raw"):
            return f'<i style="color: {muted};">{label} · no disponible (WhatsApp ya no lo tiene)</i>'
        link = f'<a href="advancedwhatsapp://open/{msg_id}" style="color: {C["link"]};">Descargar</a>'
        if not label:
            return link
        return f'<i style="color: {muted};">{label}</i> · {link}'

    def _load_older_messages(self):
        self._messages_limit += 40
        sb = self.txt_messages.verticalScrollBar()
        old_max = sb.maximum()
        old_val = sb.value()
        self.refresh_messages(scroll_to_bottom=False)

        def restore_scroll():
            diff = sb.maximum() - old_max
            self._pending_restore = old_val + diff
            self._clear_pending_timer.start()
            sb.setValue(old_val + diff)

        QTimer.singleShot(40, restore_scroll)

    @staticmethod
    def _ticks_html(status: str, size: int = 12) -> str:
        """Tildes de WhatsApp: ✓ enviado, ✓✓ entregado (gris), ✓✓ leído (azul)."""
        color = C["read"] if status == "read" else C["text_2"]
        mark = "✓" if status not in ("read", "delivered") else "✓✓"
        title = {"read": "Leído", "delivered": "Entregado"}.get(status, "Enviado")
        return f'<span style="color: {color}; font-size: {size}px;" title="{title}">&nbsp;{mark}</span>'

    def refresh_messages(self, scroll_to_bottom: bool = True):
        """Genera el HTML del chat: burbujas, separadores de fecha, citas, reacciones y tildes."""
        if not self.current_chat:
            return

        chat_jid = self.current_chat.get("jid")
        messages = get_messages(chat_jid, limit=self._messages_limit)
        reactions = get_reactions([m["id"] for m in messages])

        html_blocks = []
        block_ids = []   # alineado con html_blocks: id del mensaje de cada bloque (None = separador)

        def push(mid, block):
            block_ids.append(mid)
            html_blocks.append(block)

        meta_style = f'color: {C["text_2"]}; font-size: 10px;'
        # Color de letra: el del tag del chat con ese efecto, si tiene
        bubble_text = f'color: {self._tag_fx.get("text") or C["text"]}; font-size: 13px;'
        panel = self._panel_html
        # Chip centrado (fecha, «cargar anteriores»): ChatBrowser lo pinta como píldora
        chip = (lambda inner: f"""
                <table width="100%" border="0" cellpadding="0" cellspacing="0" style="margin: 6px 0 8px 0;">
                  <tr><td align="center">
                    <table border="0" cellpadding="0" cellspacing="0" style="background-color: {C['surface']};">
                      <tr><td style="padding: 3px 12px;">{inner}</td></tr>
                    </table>
                  </td></tr>
                </table>""")

        if len(messages) >= self._messages_limit:
            push(None, chip(f'<a href="action:load_older" style="color: {C["link"]}; text-decoration: none; '
                            f'font-size: 11px;">Cargar mensajes anteriores</a>'))

        last_day = None
        voice_images: dict = {}
        self._voice_msgs = {}
        is_group = bool(self.current_chat.get("is_group"))

        for msg in messages:
            ts = msg.get("timestamp", 0)
            try:
                msg_dt = datetime.fromtimestamp(ts)
            except (OSError, ValueError, OverflowError):
                msg_dt = datetime.now()

            if msg_dt.date() != last_day:
                last_day = msg_dt.date()
                push(None, chip(f'<span style="color: {C["text_2"]}; font-size: 10px; font-weight: 600;">'
                                f'{format_day_label(msg_dt)}</span>'))

            from_me = bool(msg.get("from_me"))
            raw_text = (msg.get("text") or "").strip()
            hora = format_hour(ts)
            media_type = msg.get("media_type", "text")
            media_path = msg.get("media_path")
            quoted_text = msg.get("quoted_text")
            quoted_sender = msg.get("quoted_sender")

            # Descartar mensajes fantasma vacíos generados por fallas o stubs sin contenido
            if not raw_text and (media_type == "text" or not media_type) and not media_path and not quoted_text:
                continue

            # Bloque de mensaje citado / respuesta
            quote_html = ""
            if quoted_text:
                q_color = "#d58a9a" if quoted_sender == "Tú" else C["accent_hi"]
                quote_html = panel(
                    f'<span style="color: {q_color}; font-size: 11px; font-weight: 600;">'
                    f'{html.escape(quoted_sender or "")}</span><br>'
                    f'<span style="color: {C["text_2"]}; font-size: 12px;">{html.escape(quoted_text)}</span>',
                    bg="rgba(0,0,0,0.20)", accent=q_color)

            # Los archivos solo se descargan cuando el usuario hace clic (Descargar / Abrir / reproducir)
            has_file = bool(media_path and os.path.exists(media_path))
            open_link = f'<a href="advancedwhatsapp://open/{msg.get("id")}" style="color: {C["link"]};">Abrir</a>'

            media_html = ""
            transcript_html = ""
            sticker_url = None
            if media_type == "image" and has_file:
                # Se muestra una miniatura en caché (la foto original puede pesar varios MB y se redibuja seguido)
                thumb, tw, th = thumb_cache.thumb_for(media_path)
                img_url = "file:///" + os.path.abspath(thumb).replace("\\", "/")
                dims = ""
                if tw:
                    dw, dh = thumb_cache.fit_size(tw, th)
                    dims = f' width="{dw}" height="{dh}"'
                media_html = (f'<a href="advancedwhatsapp://open/{msg.get("id")}"><img src="{img_url}" border="0"{dims} '
                              f'style="max-width: 300px; max-height: 240px;"/></a>')
            elif media_type == "image":
                media_html = panel(self._media_placeholder(msg, "📷 Foto"))
            elif media_type == "document":
                doc_name = os.path.basename(media_path) if media_path else (raw_text or "Archivo adjunto")
                action_html = f" · {open_link}" if has_file else " · " + self._media_placeholder(msg, "")
                media_html = panel(f'📄 <b>{html.escape(doc_name)}</b>{action_html}')
            elif media_type == "audio":
                if has_file:
                    mid = msg.get("id")
                    self._voice_msgs[mid] = (media_path, from_me)
                    voice_player().request_peaks(mid, media_path)
                    voice_images[mid] = self._voice_image(mid)
                    media_html = (
                        f'<a href="advancedwhatsapp://play/{mid}" style="text-decoration: none;">'
                        f'<img src="{resource_url(mid).toString()}" width="{IMG_W}" height="{IMG_H}" border="0"/></a>'
                    )
                else:
                    media_html = panel(self._media_placeholder(msg, "🎵 Nota de voz"))
                transcript = msg.get("transcript")
                if transcript:
                    fold_key = f"t/{mid}"
                    open_ = fold_key in self._expanded_folds
                    transcript_html = panel(
                        self._fold_header(fold_key, "TRANSCRIPCIÓN", open_) +
                        (f'<br><span style="color: {C["text"]};">{html.escape(transcript)}</span>' if open_ else ""),
                        accent=C["accent"])
                elif is_pending("transcribe", msg.get("id")):
                    transcript_html = f'<div style="color: {C["text_2"]}; font-size: 11px; margin-bottom: 3px;"><i>Transcribiendo…</i></div>'
                elif unavailable_reason(msg):
                    transcript_html = ""
                elif (has_file or msg.get("media_raw")) and get_error("transcribe", msg.get("id")):
                    err_txt = html.escape(get_error("transcribe", msg.get("id")))[:160]
                    transcript_html = (f'<div style="color: #e58a84; font-size: 11px; margin-bottom: 3px;">'
                                       f'No se pudo transcribir: {err_txt} · '
                                       f'<a href="advancedwhatsapp://transcribe/{msg.get("id")}" style="color: {C["link"]};">Reintentar</a></div>')
                elif has_file or msg.get("media_raw"):
                    transcript_html = (f'<div style="margin-bottom: 3px;"><a href="advancedwhatsapp://transcribe/{msg.get("id")}" '
                                       f'style="color: {C["link"]}; font-size: 11px;">Transcribir</a></div>')
            elif media_type == "video":
                action_html = f" · {open_link}" if has_file else " · " + self._media_placeholder(msg, "")
                media_html = panel(f'🎥 <b>Video</b>{action_html}')
            elif media_type == "sticker" and has_file:
                sticker_url = "file:///" + os.path.abspath(media_path).replace("\\", "/")
                media_html = f'<img src="{sticker_url}" width="150" height="150" border="0"/>'
            elif media_type == "sticker":
                request_media(msg)    # son livianos: se bajan al verlos (la descarga está deduplicada)
                media_html = panel(self._media_placeholder(msg, "🎨 Sticker"))
            elif media_type == "location":
                loc_txt = raw_text or "Ubicación compartida"
                media_html = panel(f'📍 <b>{html.escape(loc_txt)}</b>')
            elif media_type == "contact":
                ct_txt = raw_text or "Contacto compartido"
                media_html = panel(f'👤 <b>{html.escape(ct_txt)}</b>{self._contact_actions_html(msg)}')

            elif media_type == "poll":
                poll = parse_poll(msg)
                if poll:
                    media_html = panel(polls.poll_html(poll, C["accent"], C["text_2"]))

            if transcript_html:
                media_html += transcript_html

            mid = msg["id"]
            anchor = f'<a name="m_{mid}"></a>'
            sel_cell = self._select_cell(mid)
            pin_mark = ('<img src="icon:pin" width="12" height="12" style="vertical-align: middle;" title="Fijado"/>'
                        '&nbsp;') if msg.get("pinned_at") else ""
            ticks = self._ticks_html(msg.get("status", "sent")) if from_me else ""
            summary_link = self._summary_link(msg, mid in self._summary_hidden)
            edited_mark = (f'<a href="advancedwhatsapp://history/{mid}" title="Ver versiones anteriores" '
                           f'style="{meta_style} font-style: italic; text-decoration: none;">Editado&nbsp;</a>'
                           if msg.get("edited") and media_type != "revoked" else "")
            star_mark = f'<span style="{meta_style}">★&nbsp;</span>' if msg.get("starred_at") else ""
            meta = (f'{self.ai_extras.reminder_link(msg)}{summary_link}{self._reply_forward_links(msg)}{self._react_link(mid)}{pin_mark}{star_mark}{edited_mark}'
                    f'<span style="{meta_style}">{hora}</span>{ticks}')

            if media_type == "audio" or media_type == "sticker" or (media_type in ["location", "contact", "poll"] and media_html):
                content_text = ""
            elif media_type == "revoked":
                revoked_txt = "Eliminaste este mensaje" if from_me else "Este mensaje fue eliminado"
                content_text = f'<span style="color: {C["text_2"]}; font-style: italic;">🚫 {revoked_txt}</span>'
                if msg.get("revoked_orig"):
                    content_text += (f' <a href="advancedwhatsapp://original/{msg["id"]}" style="color: {C["link"]};">'
                                     f'Ver original</a>')
            else:
                content_text = self._linkify(raw_text)
                if media_type == "text":
                    content_text += self._link_card(raw_text, panel)

            sender_html = ""
            sender_name = msg.get("sender_name") or ""
            if not from_me and is_group and sender_name:
                sender_html = (f'<div style="color: {C["accent_hi"]}; font-size: 11px; font-weight: 600; '
                               f'margin-bottom: 1px;">{html.escape(sender_name)}</div>')

            align = "right" if from_me else "left"
            # Margen del lado opuesto: las burbujas largas no ocupan todo el ancho del chat
            gutter = '<td width="14%"></td>'

            def row(cell: str) -> str:
                return f"{sel_cell}{gutter}{cell}" if from_me else f"{sel_cell}{cell}{gutter}"

            if sticker_url and not quote_html:
                # Los stickers van sueltos, sin burbuja, como en WhatsApp
                push(mid, f"""
                    <table width="100%" border="0" cellpadding="0" cellspacing="0" style="margin-bottom: 4px;">
                      <tr>{row(f'''<td align="{align}">{anchor}{sender_html}
                        {media_html}
                        <div style="text-align: {align};">{meta}</div>
                        {self._reactions_html(mid, reactions, align)}
                      </td>''')}</tr>
                    </table>
                """)
                continue

            if media_type == "text" and not quote_html and _is_emoji_only(raw_text):
                # Emojis sueltos: grandes y sin burbuja, como WhatsApp
                push(mid, f"""
                    <table width="100%" border="0" cellpadding="0" cellspacing="0" style="margin-bottom: 4px;">
                      <tr>{row(f'''<td align="{align}">{anchor}{sender_html}
                        <span style="font-size: 38px;">{html.escape(raw_text)}</span>
                        <div style="text-align: {align};">{meta}</div>
                        {self._reactions_html(mid, reactions, align)}
                      </td>''')}</tr>
                    </table>
                """)
                continue

            bg = C["bubble_out"] if from_me else C["bubble_in"]
            push(mid, f"""
                <table width="100%" border="0" cellpadding="0" cellspacing="0" style="margin-bottom: 4px;">
                  <tr>{row(f'''<td align="{align}">{anchor}
                    <table border="0" cellpadding="0" cellspacing="0" style="background-color: {bg};">
                      <tr><td style="{bubble_text} padding: 6px 10px 5px 10px;">
                        {sender_html}{quote_html}{media_html}{content_text}{self._summary_html(msg)}{self.ai_extras.translation_html(msg)}
                        <div style="text-align: right; margin-top: 2px;">{meta}</div>
                      </td></tr>
                    </table>
                    {self._reactions_html(mid, reactions, align)}
                  </td>''')}</tr>
                </table>
            """)

        # Sin fondo en el body: lo pinta el visor, y así no tapa las burbujas redondeadas que dibuja debajo
        full_html = f"""
            <html>
            <body style="margin: 0; padding: 0; font-family: 'Segoe UI', sans-serif;">
                {"".join(html_blocks)}
            </body>
            </html>
        """
        sb = self.txt_messages.verticalScrollBar()
        old_value = sb.value()
        if scroll_to_bottom:
            self._follow_bottom = True
            self._pending_restore = None
        elif not self._follow_bottom:
            self._pending_restore = old_value
            self._clear_pending_timer.start()

        full_html = self._scale_html_fonts(full_html)
        self._rebuilding = True
        self._msg_frames = []
        self.txt_messages.setHtml(full_html)
        self._add_voice_resources(voice_images)
        self._rebuilding = False

        # Cada bloque de mensaje es una tabla de primer nivel: sirve para saber qué mensaje hay bajo el cursor
        frames = self.txt_messages.document().rootFrame().childFrames()
        if len(frames) == len(block_ids):
            self._msg_frames = [(fr, mid) for fr, mid in zip(frames, block_ids) if mid]
        self._update_pin_bar()
        self.ai_extras.on_messages(messages)

        if self._follow_bottom:
            sb.setValue(sb.maximum())
        elif self._pending_restore is not None:
            sb.setValue(min(self._pending_restore, sb.maximum()))

    def _voice_rel_x(self, pos):
        """X relativa dentro de la imagen de una nota de voz bajo `pos`, o None si no hay nota ahí."""
        href = self.txt_messages.anchorAt(pos)
        if not href or not href.startswith("advancedwhatsapp://play/"):
            return None
        cx = self.txt_messages.cursorRect(self.txt_messages.cursorForPosition(pos)).x()
        left = cx if cx <= pos.x() <= cx + IMG_W else cx - IMG_W
        return pos.x() - left

    def _on_link_preview_ready(self, url: str):
        if self.current_chat:
            self._link_timer.start()

    def _link_card(self, raw_text: str, panel) -> str:
        """Tarjeta con título, descripción e imagen del primer link del mensaje (vacío si no hay o aún no llegó)."""
        url = link_preview.first_url(raw_text)
        prev = link_preview.lookup(url) if url else None
        return link_preview.card_html(prev, panel, C) if prev else ""

    def _on_voice_finished(self, msg_id: str):
        """Terminó una nota: sigue la próxima del chat si es un audio ya bajado (se puede apagar en Ajustes)."""
        from app.config import get_pref
        if not self.current_chat or msg_id not in self._voice_msgs or not get_pref("audio_autoplay"):
            return
        nxt = audio_queue.next_audio(self.current_chat.get("jid"), msg_id)
        if not nxt:
            return
        path = nxt.get("media_path")
        if path and os.path.exists(path):
            voice_player().toggle(nxt["id"], path)
        else:
            request_media(nxt)   # se baja ahora; para escucharla hay que tocarla

    def _on_voice_speed_changed(self):
        self._add_voice_resources({mid: self._voice_image(mid) for mid in self._voice_msgs})

    def _wave_fraction_at(self, pos):
        """Si el clic cae sobre la onda de una nota, devuelve la posición (0..1); si no, None."""
        rel = self._voice_rel_x(pos)
        if rel is None:
            return None
        if WAVE_X0 - 4 <= rel <= WAVE_X1 + 4:
            return (rel - WAVE_X0) / (WAVE_X1 - WAVE_X0)
        return None

    def _voice_image(self, msg_id: str):
        path, from_me = self._voice_msgs[msg_id]
        vp = voice_player()
        info = vp.info(path)
        peaks, total_ms = info if info else (None, 0)
        playing = vp.is_playing(msg_id)
        progress = vp.progress(msg_id)
        pos_ms = vp.position_ms(msg_id)
        clock = format_clock(pos_ms if (playing or pos_ms > 0) else total_ms)
        return render_voice_image(peaks, progress, playing, clock, from_me, speed=vp.speed)

    def _add_voice_resources(self, images: dict):
        doc = self.txt_messages.document()
        for mid, img in images.items():
            doc.addResource(QTextDocument.ImageResource, resource_url(mid), img)
        if images:
            doc.markContentsDirty(0, doc.characterCount())

    def _on_voice_changed(self, msg_id: str):
        """Redibuja solo la imagen de la nota afectada (sin reconstruir todo el chat)."""
        if msg_id not in self._voice_msgs:
            return
        self._add_voice_resources({msg_id: self._voice_image(msg_id)})

    def _scroll_to_bottom(self):
        sb = self.txt_messages.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _jump_to_bottom(self):
        self._follow_bottom = True
        self._scroll_to_bottom()
        self.btn_bottom.setVisible(False)

    def _on_value_changed(self, value: int):
        if self._rebuilding:
            return
        sb = self.txt_messages.verticalScrollBar()
        self._follow_bottom = value >= sb.maximum() - 40
        self.btn_bottom.setVisible(bool(self.current_chat) and not self._follow_bottom)

    def _on_range_changed(self, lo: int, hi: int):
        """El contenido cambió de altura (fotos que cargan, mensajes nuevos): mantener la posición."""
        sb = self.txt_messages.verticalScrollBar()
        if self._follow_bottom:
            sb.setValue(hi)
        elif self._pending_restore is not None:
            sb.setValue(min(self._pending_restore, hi))

    # ── Tamaño de fuente del chat ──
    def _scale_html_fonts(self, html_text: str) -> str:
        if self._font_scale == 100:
            return html_text
        k = self._font_scale / 100
        return re.sub(
            r"font-size:\s*(\d+)px",
            lambda m: f"font-size: {max(6, round(int(m.group(1)) * k))}px",
            html_text,
        )

    def set_font_scale(self, pct: int, persist: bool = True):
        pct = clamp_font_scale(pct)
        if pct == self._font_scale:
            return
        self._font_scale = pct
        self.input_text.set_font_scale(pct)
        self.polish_input.set_font_scale(pct)
        if persist:
            set_chat_font_scale(pct)
        if self.current_chat:
            self._refresh_keep_scroll()
        self.font_scale_changed.emit(pct)

    def _step_font_scale(self, delta: int):
        self.set_font_scale(self._font_scale + delta)

    def eventFilter(self, obj, event):
        if not hasattr(self, "info_panel"):   # todavía se está construyendo la interfaz
            return super().eventFilter(obj, event)
        ctrl = bool(event.type() in (QEvent.Wheel, QEvent.KeyPress) and event.modifiers() & Qt.ControlModifier)
        if ctrl and event.type() == QEvent.Wheel and obj is self.txt_messages.viewport():
            self._step_font_scale(10 if event.angleDelta().y() > 0 else -10)
            return True
        if ctrl and event.type() == QEvent.KeyPress and obj in (self.txt_messages, self.input_text, self.polish_input):
            key = event.key()
            if key in (Qt.Key_Plus, Qt.Key_Equal):
                self._step_font_scale(10)
                return True
            if key in (Qt.Key_Minus, Qt.Key_Underscore):
                self._step_font_scale(-10)
                return True
            if key == Qt.Key_0:
                self.set_font_scale(100)
                return True
        etype = event.type()
        if obj is self.search_bar and etype == QEvent.KeyPress:
            if event.key() == Qt.Key_Escape:
                self._close_search()
                return True
            if event.key() in (Qt.Key_Return, Qt.Key_Enter) and event.modifiers() & Qt.ShiftModifier:
                self._on_search_submit(backward=True)
                return True
        # Soltar un tag de la paleta sobre el chat abierto (mensajes o cabecera) = ponérselo
        if obj in (self.txt_messages.viewport(), self.header_frame) and self.current_chat \
                and etype in (QEvent.DragEnter, QEvent.DragMove, QEvent.Drop):
            tag_id = tag_id_from_mime(event.mimeData())
            if tag_id:
                event.setDropAction(Qt.CopyAction)
                event.accept()
                if etype == QEvent.Drop:
                    tagdb.assign_tag(self.current_chat.get("jid"), tag_id)
                return True
        # Arrastrar archivos al chat o al campo de mensaje = adjuntar
        drop_targets = (self.txt_messages.viewport(), self.input_text.viewport())
        if etype in (QEvent.DragEnter, QEvent.DragMove) and obj in drop_targets:
            files = self._dropped_files(event.mimeData())
            if files and self.current_chat and not self._att_sending:
                self.drop_overlay.show_over(len(files))
                event.acceptProposedAction()
                return True
        if etype == QEvent.DragLeave and obj in drop_targets:
            self.drop_overlay.hide()
        if etype == QEvent.Drop and obj in drop_targets:
            self.drop_overlay.hide()
            files = self._dropped_files(event.mimeData())
            if files and self.current_chat and not self._att_sending:
                self._add_attachments(files)
                event.acceptProposedAction()
                return True
        if obj is self.txt_messages and etype == QEvent.Resize:
            self.btn_bottom.move(self.txt_messages.width() - 54, self.txt_messages.height() - 54)
        elif obj is self.txt_messages.viewport() and event.type() == QEvent.MouseButtonPress                 and event.button() == Qt.LeftButton:
            pos = event.position().toPoint()
            self._voice_click_frac = self._wave_fraction_at(pos)
            rel = self._voice_rel_x(pos)
            self._voice_click_chip = rel is not None and CHIP_X0 - 2 <= rel <= CHIP_X1 + 2
        return super().eventFilter(obj, event)

    # ── Menú contextual de mensajes (responder / copiar / reenviar / fijar / seleccionar / eliminar) ──
    def _select_cell(self, mid: str) -> str:
        """Casilla de selección a la izquierda de cada mensaje (solo en modo selección)."""
        if not self._select_mode:
            return ""
        mark = "sel_on" if mid in self._selected else "sel_off"
        return (f'<td width="32" valign="middle"><a href="advancedwhatsapp://select/{mid}" style="text-decoration: none;">'
                f'<img src="icon:{mark}" width="20" height="20"/></a></td>')

    def _message_at(self, vpos):
        """Mensaje bajo la posición `vpos` (coordenadas del viewport), o None."""
        pt = QPointF(vpos.x() + self.txt_messages.horizontalScrollBar().value(),
                     vpos.y() + self.txt_messages.verticalScrollBar().value())
        layout = self.txt_messages.document().documentLayout()
        for frame, mid in self._msg_frames:
            if layout.frameBoundingRect(frame).contains(pt):
                return get_message(mid)
        return None

    @staticmethod
    def _copyable_text(msg: dict) -> str:
        mt = msg.get("media_type") or "text"
        if mt == "audio":
            return (msg.get("transcript") or "").strip()
        if mt in ("sticker", "revoked"):
            return ""
        return (msg.get("text") or "").strip()

    @staticmethod
    def _has_file(msg: dict) -> bool:
        return bool(msg.get("media_path") and os.path.exists(msg["media_path"]))

    def _quote_snippet(self, msg: dict) -> str:
        mt = msg.get("media_type") or "text"
        return self._copyable_text(msg) or _preview_for(mt, msg.get("text") or "")

    def _show_message_context_menu(self, _pos):
        if not self.current_chat or self._select_mode:
            return
        msg = self._message_at(self.txt_messages.viewport().mapFromGlobal(QCursor.pos()))
        if not msg:
            self.txt_messages.createStandardContextMenu().exec(QCursor.pos())
            return

        mt = msg.get("media_type") or "text"
        revoked = mt == "revoked"
        pinned = bool(msg.get("pinned_at"))
        has_file = self._has_file(msg)
        can_copy = bool(self._copyable_text(msg)) or (mt == "image" and has_file)
        actions = [
            ("reply", "reply", "Responder", not revoked),
            ("copy", "copy", "Copiar", can_copy),
            ("forward", "forward", "Reenviar", mt in FORWARDABLE_TYPES),
        ]
        if has_file:
            actions.append(("save", "download", "Guardar como…", True))
        if mt == "sticker" and has_file:
            actions.append(("addsticker", "smile", "Agregar a mis stickers", True))
        if message_summary.can_summarize(msg):
            shown = bool(msg.get("summary")) and msg["id"] not in self._summary_hidden
            actions.append(("summarize", "sparkle", "Ocultar resumen" if shown else "Resumir con IA",
                            not message_summary.is_pending(msg["id"])))
        if self.ai_extras.can_translate(msg):
            actions.append(("translate", "translate", "Ocultar traducción" if self.ai_extras.translation_visible(msg["id"])
                            else "Traducir", True))
        actions += [
            ("pin", "pin", "Desfijar" if pinned else "Fijar", not revoked),
            ("star", "star", "Quitar destacado" if msg.get("starred_at") else "Destacar", not revoked),
            (None, "", "", True),
            ("select", "select", "Seleccionar", True),
            (None, "", "", True),
            ("delete", "trash", "Eliminar", True),
        ]
        mine = next((e for e, who in get_reactions([msg["id"]]).get(msg["id"], []) if who == "me"), "")
        menu = MessageContextMenu(mine, actions, self)
        menu.setAttribute(Qt.WA_DeleteOnClose)
        menu.action_chosen.connect(lambda key: self._on_menu_action(key, msg))
        menu.emoji_chosen.connect(lambda emoji: self._react(msg, emoji))
        menu.popup_at_cursor()

    def _on_menu_action(self, key: str, msg: dict):
        if key == "reply":
            self._start_reply(msg)
        elif key == "copy":
            self._copy_message(msg)
        elif key == "forward":
            self._forward_messages([msg])
        elif key == "save":
            self._save_media_as(msg)
        elif key == "addsticker":
            self._add_sticker_from_msg(msg)
        elif key == "summarize":
            self._toggle_summary(msg)
        elif key == "translate":
            self.ai_extras.toggle_translation(msg)
        elif key == "pin":
            self._toggle_pin(msg)
        elif key == "star":
            set_message_starred(msg["id"], not msg.get("starred_at"))
            self.refresh_messages(scroll_to_bottom=False)
        elif key == "select":
            self._enter_select_mode(msg["id"])
        elif key == "delete":
            self._delete_messages([msg])

    # Responder
    def _start_reply(self, msg: dict):
        self._reply_to = msg
        who = "Tú" if msg.get("from_me") else (msg.get("sender_name") or self._display_name())
        self.lbl_reply.setText(
            f'<b style="color: {C["accent_hi"]};">{html.escape(who)}</b><br>{html.escape(self._quote_snippet(msg)[:140])}')
        self.reply_bar.setVisible(True)
        self.input_text.setFocus()

    def _cancel_reply(self):
        self._reply_to = None
        self.reply_bar.setVisible(False)

    def _quoted_payload(self):
        """Datos de la cita para el gateway, o None si no se está respondiendo."""
        m = self._reply_to
        if not m:
            return None
        group = bool(self.current_chat and self.current_chat.get("is_group"))
        return {"message_id": m["id"], "from_me": bool(m.get("from_me")),
                "participant": m.get("sender_jid") if group else None, "text": self._quote_snippet(m)}

    # Copiar
    def _copy_message(self, msg: dict):
        clipboard = QGuiApplication.clipboard()
        text = self._copyable_text(msg)
        if text:
            clipboard.setText(text)
        elif (msg.get("media_type") == "image") and self._has_file(msg):
            img = QImage(msg["media_path"])
            if not img.isNull():
                clipboard.setImage(img)

    def _copy_selected(self):
        msgs = self._selected_messages()
        if len(msgs) == 1:
            self._copy_message(msgs[0])
        else:
            lines = []
            for m in msgs:
                text = self._copyable_text(m)
                if not text:
                    continue
                when = datetime.fromtimestamp(m.get("timestamp", 0)).strftime("%H:%M, %d/%m/%Y")
                who = "Tú" if m.get("from_me") else (m.get("sender_name") or self._display_name())
                lines.append(f"[{when}] {who}: {text}")
            if lines:
                QGuiApplication.clipboard().setText("\n".join(lines))
        self._exit_select_mode()

    # Fijar
    def _toggle_pin(self, msg: dict):
        jid = msg["chat_jid"]
        pin = not msg.get("pinned_at")
        if pin:
            pins = get_pinned_messages(jid)
            if len(pins) >= MAX_PINNED:
                r = QMessageBox.question(
                    self, "Fijar mensaje",
                    f"Ya hay {MAX_PINNED} mensajes fijados. ¿Reemplazar el más antiguo?",
                    QMessageBox.Yes | QMessageBox.No)
                if r != QMessageBox.Yes:
                    return
                self._set_pinned(pins[-1], False)
        self._set_pinned(msg, pin)
        self._pin_index = 0
        self.refresh_messages(scroll_to_bottom=False)

    def _set_pinned(self, msg: dict, pin: bool):
        """Guarda el cambio al instante y lo replica en WhatsApp; si no se puede, avisa que fue solo local."""
        set_message_pinned(msg["id"], pin)
        problem = None
        if self.gateway.get_status().get("status") != "connected":
            problem = "WhatsApp no está conectado"
        else:
            chat_jid = msg["chat_jid"]
            participant = msg.get("sender_jid") if chat_jid.endswith("@g.us") else None
            res = self.gateway.pin_message(chat_jid, msg["id"], pin, bool(msg.get("from_me")), participant)
            if not res.get("success"):
                problem = str(res.get("error", "Desconocido"))
        if problem:
            QMessageBox.information(
                self, "Mensaje fijado solo en Advanced WhatsApp" if pin else "Mensaje desfijado solo en Advanced WhatsApp",
                f"El cambio se guardó aquí pero no se pudo enviar a WhatsApp:\n{problem}")

    def _update_pin_bar(self):
        pins = get_pinned_messages(self.current_chat.get("jid")) if self.current_chat else []
        if not pins:
            self._shown_pin = None
            self.pin_bar.setVisible(False)
            return
        self._pin_index %= len(pins)
        self._shown_pin = pins[self._pin_index]
        n = len(pins)
        counter = f"  ({self._pin_index + 1}/{n})" if n > 1 else ""
        snippet = self._quote_snippet(self._shown_pin).replace("\n", " ")[:90]
        self.lbl_pin.setText(f"{html.escape(snippet)}{counter}")
        self.pin_bar.setVisible(True)

    def _on_pin_bar_clicked(self):
        msg = getattr(self, "_shown_pin", None)
        if not msg:
            return
        if msg["id"] not in {mid for _, mid in self._msg_frames}:
            # Está más arriba de lo cargado: ampliar el historial hasta incluirlo
            self._messages_limit = max(self._messages_limit,
                                       count_messages_since(msg["chat_jid"], msg.get("timestamp", 0)) + 20)
            self.refresh_messages(scroll_to_bottom=False)
        self._pending_restore = None
        self.txt_messages.scrollToAnchor(f"m_{msg['id']}")
        self._pin_index += 1
        self._update_pin_bar()

    def _unpin_shown(self):
        msg = getattr(self, "_shown_pin", None)
        if msg:
            self._set_pinned(msg, False)
            self._pin_index = 0
            self.refresh_messages(scroll_to_bottom=False)

    # Seleccionar
    def _enter_select_mode(self, first_id: str):
        self._select_mode = True
        self._selected = {first_id}
        self.composer_frame.setVisible(False)
        self._update_polish_visibility()
        self.reply_bar.setVisible(False)
        self.select_bar.setVisible(True)
        self._update_select_bar()
        self.refresh_messages(scroll_to_bottom=False)

    def _exit_select_mode(self, refresh: bool = True):
        if not self._select_mode:
            return
        self._select_mode = False
        self._selected = set()
        self.select_bar.setVisible(False)
        self.composer_frame.setVisible(True)
        self._update_polish_visibility()
        self.reply_bar.setVisible(self._reply_to is not None)
        if refresh and self.current_chat:
            self.refresh_messages(scroll_to_bottom=False)

    def _toggle_selected(self, mid: str):
        self._selected ^= {mid}
        if not self._selected:
            self._exit_select_mode()
            return
        self._update_select_bar()
        self.refresh_messages(scroll_to_bottom=False)

    def _selected_messages(self) -> list:
        msgs = [m for m in (get_message(i) for i in self._selected) if m]
        return sorted(msgs, key=lambda m: m.get("timestamp", 0))

    def _update_select_bar(self):
        msgs = self._selected_messages()
        n = len(msgs)
        self.lbl_selected.setText(f"{n} seleccionado" + ("s" if n != 1 else ""))
        self.btn_sel_copy.setEnabled(any(self._copyable_text(m) for m in msgs))
        self.btn_sel_forward.setEnabled(any((m.get("media_type") or "text") in FORWARDABLE_TYPES for m in msgs))
        self.btn_sel_delete.setEnabled(n > 0)

    # Reenviar
    def _forward_messages(self, msgs: list):
        msgs = [m for m in msgs if (m.get("media_type") or "text") in FORWARDABLE_TYPES]
        if not msgs:
            QMessageBox.information(self, "Reenviar", "Este tipo de mensaje no se puede reenviar.")
            return
        if self.gateway.get_status().get("status") != "connected":
            QMessageBox.warning(self, "WhatsApp no conectado", "No se puede reenviar sin conexión a WhatsApp.")
            return
        dlg = ForwardDialog(len(msgs), self)
        if dlg.exec() != QDialog.Accepted:
            return
        failures = []
        for chat in dlg.selected_chats():
            for m in msgs:
                ok, err = self._forward_one(chat["jid"], m)
                if not ok:
                    failures.append(f"{resolve_display_name(chat)}: {err}")
        self._exit_select_mode(refresh=False)
        self.refresh_messages(scroll_to_bottom=False)
        if self.current_chat:
            self.message_sent.emit({"chat_jid": self.current_chat.get("jid"), "text": ""})
        if failures:
            QMessageBox.warning(self, "Reenvío incompleto", "No se pudo reenviar:\n" + "\n".join(failures[:8]))

    def _forward_one(self, to_jid: str, msg: dict):
        """Reenvía un mensaje a otro chat y lo guarda allí. Devuelve (ok, error)."""
        import time
        mt = msg.get("media_type") or "text"
        text = (msg.get("text") or "").strip()
        path = msg.get("media_path")
        kwargs = {}
        if mt != "text":
            if not self._has_file(msg):
                return False, "el archivo todavía no está descargado"
            if mt == "image":
                kwargs["image_path"] = path
            elif mt == "video":
                kwargs["video_path"] = path
            elif mt == "audio":
                kwargs["audio_path"] = path
            elif mt == "sticker":
                kwargs["sticker_path"] = path
            elif mt == "document":
                kwargs["document_path"] = path
                kwargs["document_name"] = text if os.path.splitext(text)[1] else os.path.basename(path)
        if mt in ("audio", "sticker"):
            text = ""
        res = self.gateway.send_message(to=to_jid, text=text, **kwargs)
        if not res.get("success"):
            return False, str(res.get("error", "Desconocido"))
        now = int(time.time())
        save_message(
            msg_id=res.get("message_id") or f"fwd_{time.time_ns()}", chat_jid=to_jid, sender_jid="me",
            sender_name="Yo", from_me=True, text=text, timestamp=now, media_type=mt, media_path=path, status="sent")
        return True, ""

    # Eliminar
    def _delete_messages(self, msgs: list):
        if not msgs:
            return
        import time
        n = len(msgs)
        connected = self.gateway.get_status().get("status") == "connected"
        # WhatsApp solo deja eliminar para todos los mensajes propios recientes
        can_all = connected and all(
            m.get("from_me") and m.get("media_type") != "revoked"
            and time.time() - (m.get("timestamp") or 0) < REVOKE_WINDOW_SECONDS for m in msgs)

        box = QMessageBox(self)
        box.setIcon(QMessageBox.Question)
        box.setWindowTitle("Eliminar mensaje" if n == 1 else f"Eliminar {n} mensajes")
        box.setText("¿Quieres eliminar este mensaje?" if n == 1 else f"¿Quieres eliminar {n} mensajes?")
        btn_all = box.addButton("Eliminar para todos", QMessageBox.DestructiveRole) if can_all else None
        btn_me = box.addButton("Eliminar para mí", QMessageBox.DestructiveRole)
        box.addButton("Cancelar", QMessageBox.RejectRole)
        box.exec()
        clicked = box.clickedButton()

        if btn_all is not None and clicked is btn_all:
            failures = []
            for m in msgs:
                res = self.gateway.delete_message_for_all(m["chat_jid"], m["id"])
                if res.get("success"):
                    revoke_message_local(m["id"])
                else:
                    failures.append(str(res.get("error", "Desconocido")))
            if failures:
                QMessageBox.warning(self, "No se pudo eliminar para todos",
                                    f"{len(failures)} de {n} mensajes no se eliminaron:\n{failures[0]}")
        elif clicked is btn_me:
            delete_messages([m["id"] for m in msgs])
        else:
            return
        self._exit_select_mode(refresh=False)
        self._update_pin_bar()
        self.refresh_messages(scroll_to_bottom=False)
        if self.current_chat:
            self.message_sent.emit({"chat_jid": self.current_chat.get("jid"), "text": ""})

    def keyPressEvent(self, event):
        if self._recorder.active and event.key() in (Qt.Key_Escape, Qt.Key_Return, Qt.Key_Enter):
            if event.key() == Qt.Key_Escape:
                self._cancel_voice()
            else:
                self._commit_recording()
            return
        if event.key() == Qt.Key_Escape:
            if self._select_mode:
                self._exit_select_mode()
                return
            if self._reply_to is not None:
                self._cancel_reply()
                return
        super().keyPressEvent(event)

    def _save_media_as(self, msg: dict):
        src = msg["media_path"]
        ext = os.path.splitext(src)[1]
        suggested = f"WhatsApp_{msg.get('timestamp', 0)}{ext}"
        dest, _ = QFileDialog.getSaveFileName(self, "Guardar como", suggested, f"Archivo (*{ext});;Todos los archivos (*)")
        if dest:
            try:
                shutil.copyfile(src, dest)
            except OSError as e:
                QMessageBox.warning(self, "No se pudo guardar", str(e))

    def _show_chat_options(self):
        menu = QMenu(self)
        act_info = menu.addAction(icon("info"), "Info del contacto")
        act_search = menu.addAction(icon("search"), "Buscar en el chat\tCtrl+F")
        act_psych = menu.addAction(icon("analysis"), "Análisis psicológico…")
        act_edit = menu.addAction(icon("user"), "Editar / guardar contacto…")
        act_notes = menu.addAction(icon("edit"), "Nota privada…")
        act_gallery = menu.addAction(icon("image"), "Galería (fotos, documentos, links, audios)…")
        from app import organization as _org
        jid = (self.current_chat or {}).get("jid")
        act_silent = menu.addAction(icon("eye"), "Lectura invisible (sin tildes azules en este chat)")
        act_silent.setCheckable(True)
        act_silent.setChecked(_org.is_silent_read(jid))
        menu.addSeparator()
        act_poll = menu.addAction(icon("list"), "Crear encuesta…")
        act_starred = menu.addAction(icon("star"), "Mensajes destacados…")
        act_tpl = menu.addAction(icon("edit"), "Plantillas y atajos…")
        act_many = menu.addAction(icon("forward"), "Enviar a varios…")
        menu.addSeparator()
        act_clear = menu.addAction(icon("eraser", C["danger"], active_color=C["danger"]), "Vaciar chat…")
        act = menu.exec(menu_below(menu, self.btn_options))
        if act == act_info:
            self.toggle_info_panel()
        elif act == act_search:
            self._focus_search()
        elif act == act_psych:
            self._open_psych_analysis()
        elif act == act_edit:
            self._open_edit_contact_dialog()
        elif act == act_poll:
            self._create_poll()
        elif act == act_starred:
            self.open_starred()
        elif act == act_tpl:
            from app.ui.extras_dialogs import TemplatesDialog
            TemplatesDialog(self).exec()
        elif act == act_many:
            from app.ui.extras_dialogs import SendManyDialog
            SendManyDialog(self.gateway, self).exec()
        elif act == act_notes:
            self._open_notes_dialog()
        elif act == act_gallery:
            self._open_gallery()
        elif act == act_silent:
            _org.set_silent_read(jid, act_silent.isChecked())
        elif act == act_clear:
            self._clear_current_chat()

    def _create_poll(self):
        from app.ui.extras_dialogs import PollDialog
        if not self.current_chat:
            return
        dlg = PollDialog(self)
        if dlg.exec() != QDialog.Accepted:
            return
        name, options, multi = dlg.values()
        res = self.gateway.send_poll(self.current_chat["jid"], name, options, multi)
        if not res.get("success"):
            QMessageBox.warning(self, "Encuesta", res.get("error") or "No se pudo enviar la encuesta.")

    def open_starred(self):
        """Lista de destacados de todos los chats; un doble clic va al mensaje."""
        from app.ui.extras_dialogs import StarredDialog
        dlg = StarredDialog(self)
        dlg.open_chat.connect(lambda jid, mid: (dlg.accept(), self._goto_message(jid, mid)))
        dlg.exec()

    def _goto_message(self, chat_jid: str, msg_id: str):
        chat = get_chat(chat_jid)
        if not chat:
            return
        if not self.current_chat or self.current_chat.get("jid") != chat_jid:
            self.chat_open_requested.emit(chat)
            QTimer.singleShot(300, lambda: self.show_message(msg_id))
        else:
            self.show_message(msg_id)

    def show_message(self, msg_id: str):
        """Desplaza el chat abierto hasta un mensaje (amplía el historial cargado si hace falta)."""
        msg = get_message(msg_id)
        if not msg or not self.current_chat or msg["chat_jid"] != self.current_chat.get("jid"):
            return
        if msg_id not in {mid for _, mid in self._msg_frames}:
            self._messages_limit = max(self._messages_limit, count_messages_since(msg["chat_jid"], msg.get("timestamp", 0)) + 20)
            self.refresh_messages(scroll_to_bottom=False)
        self._pending_restore = None
        self.txt_messages.scrollToAnchor(f"m_{msg_id}")

    def _choose_attachment(self):
        files, _ = QFileDialog.getOpenFileNames(
            self,
            "Seleccionar archivos",
            "",
            "Todos los archivos (*.*);;Imágenes (*.png *.jpg *.jpeg *.webp *.gif);;Documentos (*.pdf *.docx *.xlsx *.zip)"
        )
        if files:
            self._add_attachments(files)

    @staticmethod
    def _dropped_files(mime) -> list:
        if not mime.hasUrls():
            return []
        return [u.toLocalFile() for u in mime.urls() if u.isLocalFile() and os.path.isfile(u.toLocalFile())]

    def _add_attachments(self, paths):
        if self._att_sending:
            return
        added = [p for p in paths if self.attachment_bar.add(p)]
        if len(added) < len(paths) and len(self.attachment_bar.paths()) >= MAX_ATTACHMENTS:
            QMessageBox.information(self, "Adjuntos", f"Puedes enviar hasta {MAX_ATTACHMENTS} archivos a la vez.")
        self.input_text.setFocus()

    def _attach_pasted_files(self, paths: list):
        if self.current_chat:
            self._add_attachments(paths)

    def _attach_pasted_image(self, img: QImage):
        """Ctrl+V con una imagen en el portapapeles (captura, 'Copiar imagen'): se guarda como PNG y se adjunta."""
        if not self.current_chat:
            return
        folder = os.path.join(MEDIA_DIR, "enviados")
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, f"imagen_{datetime.now():%Y%m%d_%H%M%S_%f}.png")
        if img.save(path, "PNG"):
            self._set_attachment(path)
        else:
            QMessageBox.warning(self, "Pegar imagen", "No se pudo guardar la imagen del portapapeles.")

    def _set_attachment(self, file_path: str):
        self._add_attachments([file_path])

    def _remove_attachment(self):
        if not self._att_sending:
            self.attachment_bar.clear()

    def _send_message(self):
        if not self.current_chat or self._att_sending:
            return

        text = self.input_text.toPlainText().strip()
        att_paths = self.attachment_bar.paths()

        if not text and not att_paths:
            return

        chat_jid = self.current_chat.get("jid")
        at = self._schedule_at
        if at and self._schedule_is_past():
            return

        # Lo programado no necesita conexión ahora: sale cuando llegue la hora
        if not at and self.gateway.get_status().get("status") != "connected":
            QMessageBox.warning(
                self,
                "WhatsApp no conectado",
                "WhatsApp no está vinculado o está desconectado.\n"
                "Ve a Configuración → WhatsApp para vincularlo. Tu mensaje quedó en el campo de texto."
            )
            return

        quoted_text, quoted_sender = self._quote_fields()
        quoted = self._quoted_payload()

        if att_paths:
            self._start_attachment_send(chat_jid, text, att_paths, quoted, quoted_text, quoted_sender, at)
            return

        res = self.gateway.send_message(to=chat_jid, text=text, quoted=quoted,
                                        **self._schedule_kwargs(at, quoted_text, quoted_sender))
        if res.get("success"):
            self.input_text.clear()
            self._drafts.pop(chat_jid, None)
            self._cancel_reply()
            if at:
                self._on_scheduled(at)
                return
            self._store_sent(res, chat_jid, text, None, None, quoted_text, quoted_sender)
            self.refresh_messages()
            self.message_sent.emit({"chat_jid": chat_jid, "text": text})
        else:
            title, what = ("No se pudo programar", "programar") if at else ("Error al enviar", "enviar")
            QMessageBox.critical(self, title, f"No se pudo {what} el mensaje:\n{res.get('error', 'Desconocido')}")

    # ── Emojis y stickers ──
    def _toggle_emoji_panel(self):
        if not self.current_chat:
            return
        if getattr(self, "_emoji_panel", None) is None:
            from app.ui.emoji_panel import EmojiStickerPanel
            self._emoji_panel = EmojiStickerPanel(self)
            self._emoji_panel.emoji_chosen.connect(self._insert_emoji)
            self._emoji_panel.sticker_chosen.connect(self._send_sticker)
        self._emoji_panel.show_above(self.btn_emoji)

    def _insert_emoji(self, emoji: str):
        """Escribe el emoji donde está el cursor del campo de mensaje (reemplaza la selección)."""
        if self.input_text.isVisible():
            self.input_text.insertPlainText(emoji)

    def _send_sticker(self, path: str, sid: str):
        """Manda un sticker de la biblioteca al chat abierto; la subida corre en un hilo."""
        if not self.current_chat or self._att_sending or not os.path.exists(path):
            return
        at = self._schedule_at
        if at and self._schedule_is_past():
            return
        if not at and self.gateway.get_status().get("status") != "connected":
            QMessageBox.warning(self, "WhatsApp no conectado",
                                "WhatsApp no está vinculado o está desconectado.\n"
                                "Ve a Configuración → WhatsApp para vincularlo.")
            return
        chat_jid = self.current_chat.get("jid")
        quoted_text, quoted_sender = self._quote_fields()
        quoted = self._quoted_payload()
        extra = self._schedule_kwargs(at, quoted_text, quoted_sender)
        animated = bool((stickerlib.get(sid) or {}).get("animated"))
        gateway = self.gateway

        def work():
            sent = path
            if not at:
                # El mensaje enviado conserva su archivo aunque después se borre el sticker de la biblioteca
                try:
                    folder = os.path.join(MEDIA_DIR, "enviados")
                    os.makedirs(folder, exist_ok=True)
                    sent = os.path.join(folder, f"sticker_{sid}.webp")
                    if not os.path.exists(sent):
                        shutil.copyfile(path, sent)
                except OSError:
                    sent = path
            res = gateway.send_message(to=chat_jid, sticker_path=path, sticker_animated=animated, quoted=quoted, **extra)
            if res.get("success"):
                try:
                    stickerlib.mark_used(sid)
                except Exception:
                    pass
            try:
                self._sticker_result.emit(chat_jid, res, sent, int(at or 0), quoted_text or "", quoted_sender or "")
            except RuntimeError:   # la ventana se cerró antes de terminar
                pass

        threading.Thread(target=work, daemon=True, name="advancedwhatsapp-sticker").start()

    def _on_sticker_result(self, chat_jid: str, res: dict, sent_path: str, at: int, quoted_text: str, quoted_sender: str):
        if not res.get("success"):
            QMessageBox.critical(self, "No se pudo programar" if at else "Error al enviar",
                                 f"No se pudo enviar el sticker:\n{res.get('error', 'Desconocido')}")
            return
        self._cancel_reply()
        if at:
            self._on_scheduled(at)
            return
        self._store_sent(res, chat_jid, "", sent_path, "sticker", quoted_text or None, quoted_sender or None)
        if self.current_chat and self.current_chat.get("jid") == chat_jid:
            self.refresh_messages()
        self.message_sent.emit({"chat_jid": chat_jid, "text": ""})

    def _add_sticker_from_msg(self, msg: dict):
        rec = stickerlib.add_from_message(msg)
        if rec is None:
            text = "No se pudo agregar el sticker."
        elif rec.get("existing"):
            text = "Ese sticker ya estaba en tus stickers."
        else:
            text = "Agregado a tus stickers."
        QToolTip.showText(QCursor.pos(), text, self)

    # ── Programar envío ──
    def _choose_schedule_time(self):
        from app.ui.scheduled_messages import ScheduleTimeDialog
        if not self.current_chat:
            return
        dlg = ScheduleTimeDialog(self._schedule_at, repeat=getattr(self, "_schedule_repeat", None), parent=self)
        accepted = dlg.exec() == QDialog.Accepted
        at = dlg.values()[0]
        dlg_repeat = dlg.repeat()
        dlg.deleteLater()
        if not accepted:
            return
        if at <= time.time():
            QMessageBox.warning(self, "Fecha pasada", "Elegí una fecha y hora futuras.")
            return
        self._schedule_repeat = dlg_repeat
        self._set_schedule(at)
        if self.input_text.isVisible():
            self.input_text.setFocus()

    def _set_schedule(self, at):
        self._schedule_at = at
        if not at:
            self._schedule_repeat = None
        self._update_schedule_bar()
        self._update_action_button()

    def _schedule_is_past(self) -> bool:
        if self._schedule_at > time.time():
            return False
        QMessageBox.warning(self, "Fecha pasada", "La hora que elegiste ya pasó. Elegí otra para programar el envío.")
        return True

    def _schedule_kwargs(self, at, quoted_text, quoted_sender) -> dict:
        if not at:
            return {}
        kw = {"at": at, "chat_name": self._display_name(), "quoted_text": quoted_text, "quoted_sender": quoted_sender}
        if getattr(self, "_schedule_repeat", None):
            kw["repeat"] = self._schedule_repeat
        return kw

    def _on_scheduled(self, at: int):
        from app.ui.scheduled_messages import when_text
        if self._schedule_at == at:   # si mientras tanto se eligió otra hora, esa sigue vigente
            self._set_schedule(None)
        self.refresh_scheduled()
        QToolTip.showText(self.btn_action.mapToGlobal(QPoint(0, -34)), f"Programado: {when_text(at)}", self.btn_action)

    def _update_schedule_bar(self):
        from app.ui.scheduled_messages import when_text
        at = self._schedule_at
        count = self._scheduled_counts.get(self.current_chat.get("jid"), 0) if self.current_chat else 0
        for w in (self.lbl_schedule_icon, self.lbl_schedule, self.btn_schedule_clear):
            w.setVisible(bool(at))
        if at:
            from app.recurrence import describe_repeat
            rep = describe_repeat(getattr(self, "_schedule_repeat", None))
            self.lbl_schedule.setText(f"Lo próximo que mandes sale: {when_text(at)}" + (f" · {rep}" if rep else ""))
        self.btn_schedule_list.setVisible(count > 0)
        self.btn_schedule_list.setText(f"{count} programado{'s' if count != 1 else ''} en este chat")
        self.schedule_bar.setVisible(bool(at) or count > 0)

    def refresh_scheduled(self):
        """Recuenta en segundo plano; la barra y el rail usan la copia local."""
        gateway = self.gateway

        def work():
            res = gateway.list_scheduled_messages()
            if not res.get("success"):
                return
            counts: dict = {}
            for m in res.get("items", []):
                if m.get("state") in ("pending", "sending"):
                    counts[m.get("to")] = counts.get(m.get("to"), 0) + 1
            try:
                self._scheduled_counts_ready.emit(counts)
            except RuntimeError:   # la ventana se cerró antes de terminar
                pass

        threading.Thread(target=work, daemon=True, name="advancedwhatsapp-programados").start()

    def _on_scheduled_counts(self, counts: dict):
        self._scheduled_loaded = True
        self._scheduled_counts = counts
        self._update_schedule_bar()
        self.scheduled_total_changed.emit(sum(counts.values()))

    def _open_chat_scheduled(self):
        from app.ui.scheduled_messages import ScheduledMessagesDialog
        if not self.current_chat:
            return
        dlg = ScheduledMessagesDialog(self.gateway, self.current_chat.get("jid"), self)
        dlg.exec()
        dlg.deleteLater()
        self.refresh_scheduled()

    def _store_sent(self, res, chat_jid, text, path, mtype, quoted_text, quoted_sender):
        import time
        now = int(time.time())
        msg_id = res.get("message_id") or f"self_{now}_{abs(hash(path or text)) % 10000}"
        save_message(
            msg_id=msg_id, chat_jid=chat_jid, sender_jid="me", sender_name="Yo", from_me=True,
            text=text or (os.path.basename(path) if mtype == "document" else ""),
            timestamp=now, media_type=mtype or "text", media_path=path,
            quoted_text=quoted_text, quoted_sender=quoted_sender, status="sent"
        )

    # ── Envío de adjuntos: hilo aparte + barra de progreso ──
    def _start_attachment_send(self, chat_jid, text, paths, quoted, quoted_text, quoted_sender, at=None):
        self._att_sending = True
        self._att_state = {"jid": chat_jid, "text": text, "paths": paths, "done": 0, "cur": 0.0,
                           "errors": [], "ok": [], "quoted_text": quoted_text, "quoted_sender": quoted_sender,
                           "text_sent": False, "at": at}
        scheduling = self._schedule_kwargs(at, quoted_text, quoted_sender)
        later = self._schedule_kwargs(at, None, None)   # la cita va solo en el primer adjunto
        for p in paths:
            self.attachment_bar.set_state(p, "")
        self.attachment_bar.set_state(paths[0], "sending")
        self.attachment_bar.show_progress(True)
        self.attachment_bar.set_progress(0.0, f"Enviando 1 de {len(paths)}…")
        for w in (self.input_text, self.btn_attach, self.btn_polish, self.btn_emoji, self.btn_action):
            w.setEnabled(False)
        self._att_timer.start()

        gateway = self.gateway

        def work():
            for i, path in enumerate(paths):
                kind = kind_for(path)
                caption = text if i == 0 else ""
                res = gateway.send_message(
                    to=chat_jid, text=caption,
                    image_path=path if kind == "image" else None,
                    video_path=path if kind == "video" else None,
                    document_path=path if kind == "document" else None,
                    document_name=os.path.basename(path) if kind == "document" else None,
                    quoted=quoted if i == 0 else None,
                    **(scheduling if i == 0 else later))
                self._att_result.emit(i, res)
            self._att_finished.emit()

        threading.Thread(target=work, daemon=True).start()

    def _on_att_tick(self):
        """El gateway no informa bytes subidos: el tramo del archivo en curso avanza con curva asintótica hasta 90 %."""
        st = self._att_state
        if not st:
            return
        st["cur"] += (0.9 - st["cur"]) * 0.06
        n = len(st["paths"])
        self.attachment_bar.set_progress((st["done"] + st["cur"]) / n, self._att_label())

    def _att_label(self) -> str:
        st = self._att_state
        n = len(st["paths"])
        pct = int((st["done"] + st["cur"]) / n * 100)
        return f"Enviando {min(st['done'] + 1, n)} de {n}… {pct}%"

    def _on_att_result(self, i: int, res: dict):
        st = self._att_state
        if not st:
            return
        path = st["paths"][i]
        n = len(st["paths"])
        if res.get("success"):
            kind = kind_for(path)
            caption = st["text"] if i == 0 else ""
            if not st["at"]:   # lo programado llega al chat recién cuando sale
                self._store_sent(res, st["jid"], caption, path, kind, st["quoted_text"] if i == 0 else None,
                                 st["quoted_sender"] if i == 0 else None)
            st["ok"].append(path)
            if i == 0:
                st["text_sent"] = True
            self.attachment_bar.set_state(path, "done")
        else:
            st["errors"].append(f"{os.path.basename(path)}: {res.get('error', 'Desconocido')}")
            self.attachment_bar.set_state(path, "error")
        st["done"] = i + 1
        st["cur"] = 0.0
        if st["done"] < n:
            self.attachment_bar.set_state(st["paths"][st["done"]], "sending")
        self.attachment_bar.set_progress(st["done"] / n, self._att_label())
        if self.current_chat and self.current_chat.get("jid") == st["jid"]:
            self.refresh_messages()

    def _on_att_finished(self):
        st, self._att_state = self._att_state, None
        self._att_timer.stop()
        self._att_sending = False
        for w in (self.input_text, self.btn_attach, self.btn_polish, self.btn_emoji, self.btn_action):
            w.setEnabled(True)
        if not st:
            return
        n = len(st["paths"])
        self.attachment_bar.set_progress(1.0 if not st["errors"] else st["done"] / n,
                                         "Listo" if not st["errors"] else f"{len(st['errors'])} con error")
        if st["text_sent"]:
            self.input_text.clear()
            self._drafts.pop(st["jid"], None)
            self._cancel_reply()
        if not st["at"]:
            self.message_sent.emit({"chat_jid": st["jid"], "text": st["text"]})
        elif st["errors"]:
            self.refresh_scheduled()   # la hora sigue elegida para reintentar los que fallaron
        else:
            self._on_scheduled(st["at"])
        # Los enviados salen de la barra; los fallidos quedan para reintentar
        QTimer.singleShot(700, lambda ok=list(st["ok"]), err=bool(st["errors"]): self._finish_att_bar(ok, err))
        if st["errors"]:
            QMessageBox.critical(self, "Error al enviar", f"No se pudo {'programar' if st['at'] else 'enviar'}:\n"
                                 + "\n".join(st["errors"]))
        self._update_action_button()

    def _finish_att_bar(self, ok_paths, had_errors):
        if self._att_sending:
            return
        for p in ok_paths:
            self.attachment_bar.remove(p)
        if not had_errors:
            self.attachment_bar.clear()
        else:
            self.attachment_bar.show_progress(False)
            for p in self.attachment_bar.paths():
                self.attachment_bar.set_state(p, "error")

    def _quote_fields(self):
        """(texto, autor) de la cita que se guarda junto al mensaje propio, o (None, None)."""
        if not self._reply_to:
            return None, None
        sender = "Tú" if self._reply_to.get("from_me") else (self._reply_to.get("sender_name") or self._display_name())
        return self._quote_snippet(self._reply_to)[:300], sender

    # ── Notas de voz ──
    def _composer_has_content(self) -> bool:
        return bool(self.input_text.toPlainText().strip() or self.attachment_bar.paths())

    def _voice_sending_here(self) -> bool:
        return bool(self.current_chat) and self.current_chat.get("jid") in self._voice_sending

    def _update_action_button(self):
        """Con texto o adjunto el botón envía; con el campo vacío graba una nota de voz, como en WhatsApp."""
        if self._recorder.active and self._dictation:
            name, tip = "check", "Terminar el dictado y pasarlo a texto  (Enter)"
        elif self._recorder.active or self._composer_has_content():
            verb, name = ("Programar", "clock") if self._schedule_at else ("Enviar", "send")
            tip = f"{verb} nota de voz  (Enter)" if self._recorder.active else f"{verb}  (Enter)"
        else:
            name, tip = "mic", "Grabar nota de voz"
        self.btn_action.setIcon(icon(name, C["on_accent"], active_color=C["on_accent"]))
        self.btn_action.setToolTip(tip)
        self.btn_action.setEnabled(self._recorder.active or not self._voice_sending_here())

    def _on_action_clicked(self):
        if self._recorder.active:
            self._commit_recording()
        elif self._composer_has_content():
            self._send_message()
        else:
            self._start_voice()

    def _refresh_voice_ui(self):
        """Muestra la barra de grabación (grabando, o enviando la nota de este chat) en lugar del campo de texto."""
        recording = self._recorder.active
        sending = self._voice_sending_here() and not recording
        show = recording or sending
        for w in (self.btn_attach, self.btn_polish, self.btn_emoji, self.input_text, self.btn_dictate):
            w.setVisible(not show)
        self.voice_panel.setVisible(show)
        for w in (self.btn_voice_cancel, self.btn_voice_pause, self.voice_meter):
            w.setVisible(recording)
        if sending:
            self.lbl_voice_dot.setVisible(False)
            dictating = self.current_chat.get("jid") in self._dictating
            self.lbl_voice_time.setText("Transcribiendo el dictado…" if dictating else "Enviando nota de voz…")
        self._update_action_button()

    def _set_voice_paused_ui(self, paused: bool):
        self.btn_voice_pause.setIcon(icon("mic" if paused else "pause", C["danger"] if paused else None))
        self.btn_voice_pause.setToolTip("Seguir grabando" if paused else "Pausar")
        self.lbl_voice_dot.setVisible(True)
        self.lbl_voice_dot.setStyleSheet(f"color: {C['text_3'] if paused else C['danger']}; font-size: 12px;")

    def _start_voice(self, dictate=None):
        """Empieza a grabar. Con `dictate` ('plain' | 'polish') es un dictado: al terminar se transcribe y el texto
        va al campo de mensaje en vez de enviarse como nota de voz."""
        if not self.current_chat or self._voice_sending_here():
            return
        if not dictate and not self._schedule_at and self.gateway.get_status().get("status") != "connected":
            QMessageBox.warning(self, "WhatsApp no conectado", "No se pueden enviar notas de voz sin conexión a WhatsApp.")
            return
        err = self._recorder.start()
        if err:
            QMessageBox.warning(self, "Nota de voz", err)
            return
        voice_player().stop()   # que no se grabe una nota que esté sonando
        self._dictation = dictate
        self._voice_chat_jid = self.current_chat.get("jid")
        self.voice_meter.clear()
        self.lbl_voice_time.setText("0:00")
        self._set_voice_paused_ui(False)
        self._refresh_voice_ui()
        self._voice_clock.start()
        self.voice_panel.setFocus()

    def _on_voice_tick(self):
        if not self._recorder.active:
            self._voice_clock.stop()
            return
        secs = self._recorder.seconds()
        self.lbl_voice_time.setText(format_clock(int(secs) * 1000))
        if secs >= MAX_SECONDS and not self._recorder.paused:
            self._recorder.pause()
            self._set_voice_paused_ui(True)
        if not self._recorder.paused:
            self._voice_blink = not self._voice_blink
            self.lbl_voice_dot.setVisible(self._voice_blink)

    def _toggle_voice_pause(self):
        if self._recorder.paused:
            self._recorder.resume()
        else:
            self._recorder.pause()
        self._set_voice_paused_ui(self._recorder.paused)
        self.voice_panel.setFocus()

    def _cancel_voice(self):
        self._voice_clock.stop()
        self._recorder.cancel()
        self._voice_chat_jid = None
        self._dictation = None
        self._refresh_voice_ui()
        if self.input_text.isVisible():
            self.input_text.setFocus()

    def _on_recorder_failed(self, err: str):
        self._cancel_voice()
        QMessageBox.warning(self, "Nota de voz", err)

    def _resolve_voice_before_leaving(self):
        """Se cambia de chat con una grabación en curso: enviarla a su chat o descartarla."""
        if self._recorder.seconds() < MIN_SECONDS:
            self._cancel_voice()
            return
        if self._dictation:   # un dictado no se envía: se transcribe y queda en el borrador de su chat
            self._finish_dictation()
            return
        self._recorder.pause()
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Question)
        box.setWindowTitle("Nota de voz")
        box.setText(f"Estabas grabando una nota de voz para {self._display_name()}. ¿Quieres enviarla?")
        btn_send = box.addButton("Enviar", QMessageBox.AcceptRole)
        box.addButton("Descartar", QMessageBox.DestructiveRole)
        box.exec()
        if box.clickedButton() is btn_send:
            self._send_voice(leaving=True)
        else:
            self._cancel_voice()

    def _commit_recording(self):
        if self._dictation:
            self._finish_dictation()
        else:
            self._send_voice()

    def _show_dictate_menu(self):
        if not self.current_chat or self._recorder.active or self._voice_sending_here():
            return
        menu = QMenu(self)
        act_plain = menu.addAction(icon("dictate"), "Dictar a texto")
        act_polish = menu.addAction(icon("sparkle"), "Dictar y emprolijar con IA")
        act = menu.exec(menu_below(menu, self.btn_dictate))
        if act == act_plain:
            self._start_voice("plain")
        elif act == act_polish:
            self._start_voice("polish")

    def _finish_dictation(self):
        """Termina el dictado: la conversión y la transcripción (y el emprolijado) corren en segundo plano y el
        texto aparece en el campo de mensaje, sin enviarse."""
        if not self._recorder.active or not self.current_chat:
            return
        secs = self._recorder.seconds()
        if secs < MIN_SECONDS:
            self._cancel_voice()
            QToolTip.showText(self.btn_action.mapToGlobal(QPoint(0, -34)), "Dictado demasiado corto", self.btn_action)
            return
        polish = self._dictation == "polish"
        pcm = self._recorder.stop()
        self._voice_clock.stop()
        chat_jid = self._voice_chat_jid or self.current_chat.get("jid")
        self._voice_chat_jid = None
        self._dictation = None
        if peak_level(pcm) == 0:
            self._refresh_voice_ui()
            QMessageBox.warning(self, "Dictado",
                                f"La grabación salió en silencio total. Revisa que el micrófono no esté silenciado.\n"
                                f"{MIC_PRIVACY_HINT}")
            return
        prov, model = message_summary.summary_ai(self.cmb_polish_provider.currentText(),
                                                 self.cmb_polish_model.currentText().strip())
        self._voice_sending.add(chat_jid)
        self._dictating.add(chat_jid)
        self._refresh_voice_ui()

        def run():
            base = os.path.join(tempfile.gettempdir(), f"dictado_{datetime.now():%Y%m%d_%H%M%S_%f}")
            files = []
            text, note = "", ""
            try:   # nunca dejar la barra en «Transcribiendo…»
                path = base + ".ogg"
                files.append(path)
                if encode_ogg_opus(pcm, path):   # sin FFmpeg: WAV, que Whisper también acepta
                    path = base + ".wav"
                    files.append(path)
                    pcm_to_wav(pcm, path)
                text = transcribe_audio(path)
                if not text:
                    note = "No se entendió nada en el dictado."
                elif polish:
                    out = reply_polisher.polish_dictation(text, prov, model)
                    if AIService.is_error_response(out):
                        note = f"No se pudo emprolijar con la IA ({out.strip()[:160]}); quedó el texto tal cual se dictó."
                    else:
                        text = out
            except Exception as e:
                note = f"No se pudo transcribir el dictado: {e}"
            for f in files:
                try:
                    os.remove(f)
                except OSError:
                    pass
            try:
                self._dictation_done.emit(chat_jid, text, note)
            except RuntimeError:   # la ventana se cerró antes de terminar
                pass
        threading.Thread(target=run, daemon=True).start()

    def _on_dictation_done(self, jid: str, text: str, note: str):
        self._voice_sending.discard(jid)
        self._dictating.discard(jid)
        if text:
            if self.current_chat and self.current_chat.get("jid") == jid:
                self.input_text.setPlainText(merge_dictation(self.input_text.toPlainText(), text))
                self.input_text.moveCursor(QTextCursor.End)
            else:   # se cambió de chat mientras se transcribía: queda en el borrador de ese chat
                self._drafts[jid] = merge_dictation(self._drafts.get(jid, ""), text)
        self._refresh_voice_ui()
        if text and self.input_text.isVisible() and self.current_chat and self.current_chat.get("jid") == jid:
            self.input_text.setFocus()
        if note:
            QMessageBox.warning(self, "Dictado", note)

    def _send_voice(self, leaving: bool = False):
        """Termina la grabación; la conversión a OGG/Opus y la subida corren en segundo plano."""
        if not self._recorder.active or not self.current_chat:
            return
        secs = self._recorder.seconds()
        if secs < MIN_SECONDS:
            self._cancel_voice()
            QToolTip.showText(self.btn_action.mapToGlobal(QPoint(0, -34)),
                              "Nota de voz demasiado corta: no se envió", self.btn_action)
            return
        at = self._schedule_at
        if at and at <= time.time():
            if leaving:   # al cambiar de chat no queda dónde elegir otra hora: mejor no enviarla a destiempo
                self._cancel_voice()
                QMessageBox.warning(self, "Fecha pasada", "La hora elegida ya pasó: la nota de voz se descartó.")
            else:
                self._recorder.pause()
                self._set_voice_paused_ui(True)
                self._schedule_is_past()
            return
        if not at and self.gateway.get_status().get("status") != "connected":
            if leaving:
                self._cancel_voice()
                QMessageBox.warning(self, "WhatsApp no conectado",
                                    "WhatsApp está desconectado: la nota de voz no se pudo enviar y se descartó.")
            else:
                self._recorder.pause()
                self._set_voice_paused_ui(True)
                QMessageBox.warning(self, "WhatsApp no conectado",
                                    "WhatsApp está desconectado. La grabación quedó en pausa: "
                                    "envíala cuando vuelva la conexión o descártala.")
            return

        pcm = self._recorder.stop()
        self._voice_clock.stop()
        chat_jid = self._voice_chat_jid or self.current_chat.get("jid")
        self._voice_chat_jid = None
        if peak_level(pcm) == 0:
            self._refresh_voice_ui()
            QMessageBox.warning(self, "Nota de voz",
                                f"La grabación salió en silencio total y no se envió. "
                                f"Revisa que el micrófono no esté silenciado.\n{MIC_PRIVACY_HINT}")
            return

        quoted_text, quoted_sender = self._quote_fields()
        job = {"chat_jid": chat_jid, "timestamp": int(time.time()), "quoted": self._quoted_payload(),
               "quoted_text": quoted_text, "quoted_sender": quoted_sender, "at": at}
        scheduling = self._schedule_kwargs(at, quoted_text, quoted_sender)
        if scheduling:
            scheduling["consume"] = True   # el archivo recién convertido no se usa para nada más
        self._cancel_reply()
        self._voice_sending.add(chat_jid)
        self._refresh_voice_ui()

        def run():
            path = os.path.join(VOICE_DIR, f"voz_{datetime.now():%Y%m%d_%H%M%S_%f}.ogg")
            try:   # nunca dejar la barra en «Enviando…»
                os.makedirs(VOICE_DIR, exist_ok=True)
                err = encode_ogg_opus(pcm, path)
                if err:
                    res = {"success": False, "error": err}
                else:
                    res = self.gateway.send_message(to=chat_jid, audio_path=path, quoted=job["quoted"],
                                                    audio_seconds=round(secs, 1), audio_waveform=whatsapp_waveform(pcm),
                                                    **scheduling)
            except Exception as e:
                res = {"success": False, "error": str(e)}
            try:
                self._voice_done.emit({**job, "path": path, "res": res})
            except RuntimeError:   # la ventana se cerró antes de terminar
                pass
        threading.Thread(target=run, daemon=True).start()

    def _on_voice_done(self, job: dict):
        chat_jid, res = job["chat_jid"], job["res"]
        at = job.get("at")
        self._voice_sending.discard(chat_jid)
        if at and res.get("success"):
            self._refresh_voice_ui()
            self._on_scheduled(at)
            return
        if res.get("success"):
            save_message(
                msg_id=res.get("message_id") or f"self_{job['timestamp']}", chat_jid=chat_jid, sender_jid="me",
                sender_name="Yo", from_me=True, text="", timestamp=job["timestamp"], media_type="audio",
                media_path=job["path"], quoted_text=job["quoted_text"], quoted_sender=job["quoted_sender"],
                status="sent")
        self._refresh_voice_ui()
        if not res.get("success"):
            QMessageBox.critical(self, "Error al enviar",
                                 f"No se pudo {'programar' if at else 'enviar'} la nota de voz:\n{res.get('error', 'Desconocido')}")
            return
        if self.current_chat and self.current_chat.get("jid") == chat_jid:
            self.refresh_messages()
            if self.input_text.isVisible():
                self.input_text.setFocus()
        if self.current_chat:
            self.message_sent.emit({"chat_jid": self.current_chat.get("jid"), "text": ""})

    # ── Redactor de respuestas (filtro de salida) ──
    def _toggle_polish(self):
        self._polish_open = not self._polish_open
        self._update_polish_visibility()
        self.btn_polish.setChecked(self._polish_open)
        if self._polish_open:
            if not self._polish_models_loaded:
                self._polish_models_loaded = True
                self._load_polish_models(self.cmb_polish_provider.currentText())
            self.polish_input.setFocus()
        else:
            self.input_text.setFocus()

    def _update_polish_visibility(self):
        self.polish_bar.setVisible(self._polish_open and not self._select_mode)

    def _save_polish_state(self, jid: str):
        """Guarda el redactor del chat que se deja; su petición en curso, si hay, sigue corriendo."""
        self._polish_saved[jid] = {
            "draft": self.polish_input.toPlainText(),
            "extra": self.polish_extra.text(),
            "status": "" if jid in self._polish_jobs else self.lbl_polish_status.text(),
        }

    def _show_polish_state(self, jid: str):
        """Muestra el redactor del chat: su borrador, su indicación y su petición en curso o el último resultado."""
        saved = self._polish_saved.get(jid, {})
        job = self._polish_jobs.get(jid)
        self.polish_input.setPlainText(saved.get("draft", ""))
        self.polish_extra.setText(saved.get("extra", ""))
        self._set_polish_busy(bool(job))
        self.lbl_polish_status.setText(job["status"] if job else saved.get("status", ""))
        if not job:
            self.ai_state_changed.emit(jid, "")   # ya visto: se quita la marca de la lista

    def _set_polish_busy(self, busy: bool):
        self._polish_busy = busy
        for w in (self.cmb_polish_provider, self.cmb_polish_model, self.cmb_polish_mode, self.polish_extra,
                  self.polish_input, self.btn_polish_run, self.btn_polish_suggest):
            w.setEnabled(not busy)

    def _set_polish_model(self, model: str):
        """Selecciona el modelo; si todavía no está en la lista lo agrega."""
        if model and self.cmb_polish_model.findText(model) < 0:
            self.cmb_polish_model.insertItem(0, model)
        if model:
            self.cmb_polish_model.setCurrentText(model)

    def _on_polish_provider_changed(self, prov: str):
        self.cmb_polish_model.clear()
        self._set_polish_model(pa.config_model(prov))
        self._load_polish_models(prov)
        self._save_polish_choice()

    def _save_polish_choice(self):
        set_polish_ai(self.cmb_polish_provider.currentText(), self.cmb_polish_model.currentText().strip())

    def _load_polish_models(self, prov: str):
        def run():
            try:
                models = pa.list_models(prov)
            except Exception:
                return
            try:
                self._polish_models_ready.emit(prov, models)
            except RuntimeError:  # el panel se cerró antes de terminar
                pass
        threading.Thread(target=run, daemon=True).start()

    def _fill_polish_models(self, prov: str, models: list):
        if prov != self.cmb_polish_provider.currentText():
            return
        current = self.cmb_polish_model.currentText()
        self.cmb_polish_model.clear()
        self.cmb_polish_model.addItems(models)
        self._set_polish_model(current)

    def _run_polish(self):
        """Transforma el borrador según «Qué hacer»."""
        if not self.current_chat or self._polish_busy:
            return
        draft = self.polish_input.toPlainText().strip()
        if not draft:
            self.lbl_polish_status.setText(
                "Escribe primero tu respuesta en el campo de arriba, o pulsa «💡 Sugerir respuesta».")
            return
        mode, extra = self.cmb_polish_mode.currentData(), self.polish_extra.text().strip()
        if mode == "libre" and not extra:
            self.lbl_polish_status.setText("Escribe tu indicación en el campo «Indicación extra».")
            self.polish_extra.setFocus()
            return
        profile = self._reply_profile()
        self._start_polish_job(
            "improve", f"✨ {MODES[mode][0]}",
            lambda msgs, name, prov, model: reply_polisher.polish(draft, msgs, name, prov, model, mode, extra,
                                                                  profile))

    def _run_suggest(self):
        """Propone una respuesta desde cero; el borrador, si hay, se toma como ideas a incluir."""
        if not self.current_chat or self._polish_busy:
            return
        mode, extra = self.cmb_polish_mode.currentData(), self.polish_extra.text().strip()
        notes = self.polish_input.toPlainText().strip()
        profile = self._reply_profile()
        self._start_polish_job(
            "suggest", "💡 Sugiriendo una respuesta",
            lambda msgs, name, prov, model: reply_polisher.suggest(msgs, name, prov, model, mode, extra, notes,
                                                                   profile))

    def _reply_profile(self) -> str:
        """Notas y último análisis del contacto, si el usuario lo permite y existen."""
        if not self.chk_polish_profile.isChecked():
            return ""
        return pa.reply_profile(self.current_chat.get("jid", ""))

    def _start_polish_job(self, kind: str, label: str, job):
        """Corre `job(mensajes, nombre, proveedor, modelo)` en un hilo y entrega el texto a _on_polish_done.
        Cada chat tiene su propia petición: al cambiar de chat sigue corriendo y su resultado queda en ese chat."""
        prov = self.cmb_polish_provider.currentText()
        model = self.cmb_polish_model.currentText().strip()
        jid = self.current_chat.get("jid", "")
        name = self._display_name()
        self._polish_req += 1
        req = self._polish_req
        status = f"{label} con {prov} {model}… <span style='color: {C['text_3']};'>" \
                 f"(puedes ir a otro chat mientras tanto)</span>"
        self._polish_jobs[jid] = {"req": req, "kind": kind, "status": status}
        self._set_polish_busy(True)
        self.lbl_polish_status.setText(status)
        self.ai_state_changed.emit(jid, "pending")

        def run():
            try:
                messages = get_messages(jid, limit=CONTEXT_MESSAGES)
                text = job(messages, name, prov, model)
            except Exception as e:  # nunca dejar el panel bloqueado
                text = f"Error inesperado: {e}"
            try:
                self._polish_done.emit(req, jid, text)
            except RuntimeError:
                pass
        threading.Thread(target=run, daemon=True).start()

    def _on_polish_done(self, req: int, jid: str, text: str):
        job = self._polish_jobs.get(jid)
        if not job or job["req"] != req:
            return
        del self._polish_jobs[jid]
        is_current = bool(self.current_chat and self.current_chat.get("jid") == jid)
        failed = AIService.is_error_response(text)
        if failed:
            status = f"<span style='color: #e58a84;'>Error: {html.escape(text.strip()[:220])}</span>"
        else:
            retry = ("Pulsa «Sugerir respuesta» otra vez para otra versión."
                     if job["kind"] == "suggest"
                     else "Si no te convence, cambia «Qué hacer» y vuelve a pulsar Mejorar.")
            status = (f"<span style='color: {C['accent_hi']};'>Listo en el campo de mensaje:</span> "
                      f"revísalo y envíalo cuando quieras. {retry}")

        if not is_current:
            # Quedó en segundo plano: el resultado espera en el borrador de ese chat
            if not failed:
                self._drafts[jid] = text
            self._polish_saved.setdefault(jid, {})["status"] = status
            self.ai_state_changed.emit(jid, "error" if failed else "ready")
            return

        self._set_polish_busy(False)
        self.lbl_polish_status.setText(status)
        self.ai_state_changed.emit(jid, "")
        if failed:
            return
        self.input_text.setPlainText(text)
        cur = self.input_text.textCursor()
        cur.movePosition(cur.MoveOperation.End)
        self.input_text.setTextCursor(cur)
        self.input_text.setFocus()

    def _clear_current_chat(self):
        if not self.current_chat:
            return
        r = QMessageBox.question(
            self,
            "Limpiar chat",
            "¿Estás seguro de que deseas limpiar los mensajes de este chat?",
            QMessageBox.Yes | QMessageBox.No
        )
        if r == QMessageBox.Yes:
            clear_chat_messages(self.current_chat.get("jid"))
            self.refresh_messages()
