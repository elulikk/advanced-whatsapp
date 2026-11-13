"""
Advanced WhatsApp - Widgets de las funciones inteligentes extra
===============================================================
Parte visual de app/ai/smart_features.py: banner «Lo que te perdiste», chips de respuestas rápidas, traducción de
mensajes y de borradores, recordatorios detectados en la burbuja y la búsqueda por significado. ChatViewPanel solo
crea un `AiExtras` y lo llama en unos pocos puntos de enganche; el resto vive acá. Toda consulta a la IA corre en un
hilo (smart_features.run_async) y vuelve por una Signal.
"""

import html
import time
from datetime import datetime
from typing import Dict, List, Optional

from PySide6.QtCore import Qt, QObject, Signal, QDateTime, QTimer
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QVBoxLayout, QLabel, QPushButton, QToolButton, QScrollArea, QDialog, QLineEdit,
    QListWidget, QListWidgetItem, QDateTimeEdit, QMenu, QMessageBox
)

from app import db, reminders
from app.ai import message_summary, psych_analysis as pa, smart_features as sf
from app.ai.ai_service import AIService
from app.config import get_pref, get_polish_ai
from app.ui.icons import icon, pixmap as icon_pixmap, tool_button
from app.ui.theme import C

MAX_CHIP_WIDTH = 300


def ai_enabled() -> bool:
    try:
        return AIService().is_enabled()
    except Exception:
        return False


def choose_ai() -> tuple:
    """(proveedor, modelo) para estas funciones: Claude Haiku si hay clave de Anthropic; si no, lo último que se eligió
    en el redactor de respuestas, o el proveedor de la configuración."""
    prov, model = get_polish_ai()
    if prov not in pa.PROVIDERS:
        prov, model = pa.default_provider_model()
    return message_summary.summary_ai(prov, model or pa.config_model(prov))


def _elide(text: str, n: int = 46) -> str:
    return text if len(text) <= n else text[:n - 1] + "…"


def format_missed_html(raw: str) -> str:
    """Resumen de «lo que te perdiste» como HTML: títulos en color y viñetas."""
    out = []
    for title, lines in sf.parse_missed(raw):
        out.append(f'<div style="color: {C["accent_hi"]}; font-size: 11px; font-weight: 600; margin-top: 6px;">'
                   f'{html.escape(title)}</div>')
        if title == "RESUMEN":
            out.append(f'<div style="color: {C["text"]};">{"<br>".join(html.escape(l) for l in lines)}</div>')
        else:
            out.append("".join(f'<div style="color: {C["text"]};">• {html.escape(l)}</div>' for l in lines))
    return "".join(out)


# ─── Banner «Lo que te perdiste» ─────────────────────────────────────────────

class MissedBanner(QFrame):
    summarize_clicked = Signal()
    dismissed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("missedBanner")
        self.setStyleSheet(f"#missedBanner {{ background-color: {C['surface']}; border-bottom: 1px solid {C['border']}; }}")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 5, 8, 5)
        lay.setSpacing(4)
        row = QHBoxLayout()
        row.setSpacing(8)
        ico = QLabel()
        ico.setPixmap(icon_pixmap("sparkle", C["accent"], 16))
        row.addWidget(ico)
        self.lbl = QLabel()
        self.lbl.setStyleSheet(f"color: {C['text']}; font-size: 12px;")
        row.addWidget(self.lbl, stretch=1)
        self.btn = QPushButton("Resumir lo que te perdiste")
        self.btn.setCursor(Qt.PointingHandCursor)
        self.btn.setStyleSheet(f"color: {C['accent_hi']}; border: none; font-size: 12px; padding: 2px 6px; "
                               f"background: transparent;")
        self.btn.clicked.connect(self.summarize_clicked.emit)
        row.addWidget(self.btn)
        close = tool_button("close", "Cerrar", size=26, icon_size=14)
        close.clicked.connect(self.dismissed.emit)
        row.addWidget(close)
        lay.addLayout(row)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setMaximumHeight(240)
        self.scroll.setStyleSheet("background: transparent;")
        self.body = QLabel()
        self.body.setWordWrap(True)
        self.body.setTextFormat(Qt.RichText)
        self.body.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.body.setStyleSheet(f"color: {C['text']}; font-size: 12px; padding: 0 4px 4px 22px; background: transparent;")
        self.scroll.setWidget(self.body)
        self.scroll.setVisible(False)
        lay.addWidget(self.scroll)

    def show_state(self, unread: int, busy: bool, text: str, error: str):
        self.lbl.setText(f"Tenés {unread} mensajes sin leer en este chat." if not text else
                         f"Lo que te perdiste ({unread} mensajes):")
        self.btn.setEnabled(not busy)
        self.btn.setText("Resumiendo…" if busy else ("Rehacer" if text else "Resumir lo que te perdiste"))
        if text:
            self.body.setText(format_missed_html(text))
        elif error:
            self.body.setText(f'<span style="color: {C["danger"]};">{html.escape(error[:200])}</span>')
        self.scroll.setVisible(bool(text or error))


# ─── Chips de respuestas rápidas ─────────────────────────────────────────────

class QuickReplyBar(QFrame):
    chosen = Signal(str)
    suggest_clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("quickBar")
        self.setStyleSheet(f"#quickBar {{ background-color: {C['panel']}; border-top: 1px solid {C['border']}; }}")
        self._lay = QHBoxLayout(self)
        self._lay.setContentsMargins(10, 4, 10, 4)
        self._lay.setSpacing(6)
        self.btn_suggest = tool_button("sparkle", "Sugerir respuestas rápidas con IA", size=26, icon_size=15)
        self.btn_suggest.clicked.connect(self.suggest_clicked.emit)
        self._lay.addWidget(self.btn_suggest)
        self.lbl = QLabel()
        self.lbl.setStyleSheet(f"color: {C['text_3']}; font-size: 11px;")
        self._lay.addWidget(self.lbl)
        self._chips: List[QToolButton] = []
        self._lay.addStretch(1)
        self.setVisible(False)

    def _clear(self):
        for b in self._chips:
            self._lay.removeWidget(b)
            b.deleteLater()
        self._chips = []

    def show_idle(self):
        self._clear()
        self.lbl.setText("Respuestas rápidas con IA")

    def show_busy(self):
        self._clear()
        self.lbl.setText("Pensando respuestas…")

    def show_error(self, msg: str):
        self._clear()
        self.lbl.setText(_elide(msg.replace("Error:", "").strip() or "No se pudo sugerir", 70))

    def show_replies(self, replies: List[str]):
        self._clear()
        self.lbl.setText("")
        for r in replies:
            b = QToolButton()
            b.setObjectName("chip")
            b.setText(_elide(r, 52))
            b.setToolTip(r)
            b.setCursor(Qt.PointingHandCursor)
            b.setMaximumWidth(MAX_CHIP_WIDTH)
            b.clicked.connect(lambda _=False, t=r: self.chosen.emit(t))
            self._lay.insertWidget(self._lay.count() - 1, b)
            self._chips.append(b)


# ─── Recordatorio detectado ──────────────────────────────────────────────────

class ReminderDialog(QDialog):
    """Pregunta qué hacer con una fecha/hora detectada: recordatorio local o mensaje programado."""

    def __init__(self, found: Dict, snippet: str, can_schedule: bool, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Fecha detectada")
        self.setMinimumWidth(380)
        self.action = ""
        lay = QVBoxLayout(self)
        lay.setSpacing(8)
        q = QLabel(f"“{html.escape(_elide(snippet, 140))}”")
        q.setWordWrap(True)
        q.setStyleSheet(f"color: {C['text_2']};")
        lay.addWidget(q)
        lay.addWidget(QLabel("¿Cuándo?" + ("" if found.get("has_time") else "  (el mensaje no dice la hora: ajustala)")))
        self.when = QDateTimeEdit(QDateTime.fromSecsSinceEpoch(int(found["ts"])))
        self.when.setCalendarPopup(True)
        self.when.setDisplayFormat("dddd d/M/yyyy HH:mm")
        lay.addWidget(self.when)
        lay.addWidget(QLabel("Texto del recordatorio:"))
        self.text = QLineEdit(_elide(snippet, 120))
        lay.addWidget(self.text)
        row = QHBoxLayout()
        b_cancel = QPushButton("Cancelar")
        b_cancel.clicked.connect(self.reject)
        row.addWidget(b_cancel)
        row.addStretch(1)
        self.b_sched = QPushButton("Programar un mensaje")
        self.b_sched.setToolTip("Lo próximo que escribas en este chat se envía a esa hora")
        self.b_sched.setEnabled(can_schedule)
        self.b_sched.clicked.connect(lambda: self._done("schedule"))
        row.addWidget(self.b_sched)
        b_rem = QPushButton("Recordármelo")
        b_rem.setDefault(True)
        b_rem.clicked.connect(lambda: self._done("remind"))
        row.addWidget(b_rem)
        lay.addLayout(row)

    def _done(self, action: str):
        self.action = action
        self.accept()

    def values(self) -> tuple:
        return self.action, int(self.when.dateTime().toSecsSinceEpoch()), self.text.text().strip()


# ─── Búsqueda por significado ────────────────────────────────────────────────

class AiSearchDialog(QDialog):
    """Busca mensajes (y transcripciones de audios) por sentido. Doble clic en un resultado abre ese chat."""
    chat_requested = Signal(str)
    _finished = Signal(object)

    def __init__(self, query: str = "", parent=None):
        super().__init__(parent)
        self.setWindowTitle("Buscar con IA")
        self.setMinimumSize(520, 460)
        self._busy = False
        lay = QVBoxLayout(self)
        row = QHBoxLayout()
        self.txt = QLineEdit(query)
        self.txt.setPlaceholderText("Describí lo que buscás: «cuándo era la cena con Marta»")
        self.txt.returnPressed.connect(self._search)
        row.addWidget(self.txt, stretch=1)
        self.btn = QPushButton("Buscar")
        self.btn.clicked.connect(self._search)
        row.addWidget(self.btn)
        lay.addLayout(row)
        self.status = QLabel("Encuentra mensajes por significado, incluidas las transcripciones de los audios.")
        self.status.setWordWrap(True)
        self.status.setStyleSheet(f"color: {C['text_3']}; font-size: 12px;")
        lay.addWidget(self.status)
        self.list = QListWidget()
        self.list.itemActivated.connect(self._open)
        self.list.itemDoubleClicked.connect(self._open)
        lay.addWidget(self.list, stretch=1)
        self._finished.connect(self._on_finished)
        if query.strip():
            QTimer.singleShot(0, self._search)

    def _search(self):
        q = self.txt.text().strip()
        if self._busy or len(q) < 3:
            return
        self._busy = True
        self.btn.setEnabled(False)
        self.list.clear()
        if not ai_enabled():
            self.status.setText("La IA está desactivada en Ajustes: se busca solo por palabras.")
        else:
            self.status.setText("Buscando…")
        prov, model = choose_ai()
        sf.run_async(lambda: sf.semantic_search(q, prov, model), self._finished.emit)

    def _on_finished(self, res):
        self._busy = False
        self.btn.setEnabled(True)
        if isinstance(res, Exception):
            self.status.setText(f"Error: {res}")
            return
        msgs, note = res
        self.status.setText(note or (f"{len(msgs)} resultado(s). Doble clic para abrir el chat." if msgs else
                                     "No encontré nada parecido."))
        for m in msgs:
            name = m.get("chat_custom_name") or m.get("chat_name") or m.get("chat_jid", "").split("@")[0]
            who = "Yo" if m.get("from_me") else (m.get("sender_name") or name)
            when = datetime.fromtimestamp(int(m.get("timestamp") or 0)).strftime("%d/%m/%y %H:%M") if m.get("timestamp") else ""
            kind = "🎤 " if (m.get("media_type") or "") == "audio" else ""
            text = " ".join(sf._msg_text(m).split())
            it = QListWidgetItem(f"{name} · {when}\n{who}: {kind}{_elide(text, 140)}")
            it.setData(Qt.UserRole, m.get("chat_jid"))
            it.setToolTip(text[:600])
            self.list.addItem(it)

    def _open(self, item: QListWidgetItem):
        jid = item.data(Qt.UserRole)
        if jid:
            self.chat_requested.emit(jid)
            self.accept()


# ─── Controlador dentro del chat abierto ─────────────────────────────────────

class AiExtras(QObject):
    """Estado y acciones de las funciones extra para un ChatViewPanel (banner, chips, traducción, recordatorios)."""
    _missed_done = Signal(str, str, str, int)       # jid, texto, error, cantidad
    _quick_done = Signal(str, str, object, str)     # jid, id del último mensaje, respuestas, error
    _translated = Signal(str, str, str)             # id del mensaje, traducción, error
    _draft_translated = Signal(str, str, str)       # jid, traducción, error

    def __init__(self, panel):
        super().__init__(panel)
        self.panel = panel
        self.banner = MissedBanner()
        self.banner.setVisible(False)
        self.banner.summarize_clicked.connect(lambda: self.request_missed(force=True))
        self.banner.dismissed.connect(self._dismiss_banner)
        self.quick = QuickReplyBar()
        self.quick.chosen.connect(self._use_reply)
        self.quick.suggest_clicked.connect(lambda: self._request_quick(force=True))
        self._opened_jid = ""
        self._missed: Dict[str, Dict] = {}          # jid -> {unread, text, error, busy, dismissed}
        self._quick_state: Dict[tuple, str] = {}    # (jid, last_id) -> "busy" | "error:<msg>"
        self._last_msgs: List[Dict] = []
        self.translations: Dict[str, str] = {}      # id mensaje -> traducción | "…" | "Error: …"
        self._draft_busy: set = set()
        self._remind_cache: Dict[tuple, Optional[Dict]] = {}
        self._missed_done.connect(self._on_missed_done)
        self._quick_done.connect(self._on_quick_done)
        self._translated.connect(self._on_translated)
        self._draft_translated.connect(self._on_draft_translated)

    # ── Helpers ──
    def _jid(self) -> str:
        chat = self.panel.current_chat
        return (chat or {}).get("jid", "")

    def _name(self) -> str:
        return self.panel._display_name()

    # ── Lo que te perdiste ──
    def on_chat_opened(self, chat_data: dict, unread: int):
        """Se llama al abrir un chat, ANTES de marcarlo como leído (por eso recibe la cantidad que había)."""
        jid = chat_data.get("jid", "")
        changed_chat = jid != self._opened_jid
        self._opened_jid = jid
        st = self._missed.get(jid)
        if unread >= sf.MISSED_MIN_UNREAD and ai_enabled():
            if st is None or st.get("unread") != unread:
                self._missed[jid] = {"unread": unread, "text": "", "error": "", "busy": False, "dismissed": False}
        elif changed_chat and st and not st.get("busy"):
            self._missed.pop(jid, None)   # volvió al chat con todo leído: el resumen viejo ya no hace falta
        self._show_banner()
        self.quick.setVisible(False)

    def _show_banner(self):
        st = self._missed.get(self._jid())
        if not st or st.get("dismissed"):
            self.banner.setVisible(False)
            return
        self.banner.show_state(st["unread"], st["busy"], st["text"], st["error"])
        self.banner.setVisible(True)

    def _dismiss_banner(self):
        st = self._missed.get(self._jid())
        if st:
            st["dismissed"] = True
        self._show_banner()

    def request_missed(self, force: bool = False):
        jid = self._jid()
        st = self._missed.get(jid)
        if not st or st["busy"]:
            return
        st.update(busy=True, error="")
        if force and st["text"]:
            st["text"] = ""
        self._show_banner()
        n, name, is_group = st["unread"], self._name(), bool(self.panel.current_chat.get("is_group"))
        prov, model = choose_ai()

        def work():
            msgs = sf.missed_messages(jid, n)
            text, err = sf.summarize_missed(jid, msgs, name, is_group, n, prov, model, force=False)
            return text, err

        def done(res):
            if isinstance(res, Exception):
                res = ("", f"Error: {res}")
            self._missed_done.emit(jid, res[0], res[1], n)
        sf.run_async(work, done)

    def _on_missed_done(self, jid: str, text: str, err: str, n: int):
        st = self._missed.get(jid)
        if not st:
            return
        st.update(busy=False, text=text, error=err)
        if self._jid() == jid:
            self._show_banner()

    # ── Respuestas rápidas ──
    def on_messages(self, messages: List[Dict]):
        """Al repintar el chat: actualiza la barra de respuestas rápidas según el último mensaje."""
        self._last_msgs = messages
        jid = self._jid()
        last = messages[-1] if messages else None
        if (not jid or not get_pref("quick_replies") or not ai_enabled() or not sf.should_suggest(last)
                or self.panel._select_mode):
            self.quick.setVisible(False)
            return
        key = (jid, last["id"])
        cached = sf.cached_quick_replies(jid, last["id"])
        state = self._quick_state.get(key, "")
        if cached:
            self.quick.show_replies(cached)
        elif state == "busy":
            self.quick.show_busy()
        elif state.startswith("error:"):
            self.quick.show_error(state[6:])
        elif sf.looks_like_request(sf._msg_text(last)) and key not in self._quick_state:
            self._request_quick()
        else:
            self.quick.show_idle()   # sin pregunta clara: solo el botón ✨ (no se gasta una llamada)
        self.quick.setVisible(True)

    def _request_quick(self, force: bool = False):
        jid = self._jid()
        msgs = list(self._last_msgs)
        if not jid or not msgs:
            return
        last_id = msgs[-1]["id"]
        key = (jid, last_id)
        if self._quick_state.get(key) == "busy":
            return
        self._quick_state[key] = "busy"
        self.quick.show_busy()
        self.quick.setVisible(True)
        name, prov_model = self._name(), choose_ai()
        profile = pa.reply_profile(jid)

        def work():
            return sf.quick_replies(jid, msgs, name, prov_model[0], prov_model[1], profile, force=force)

        def done(res):
            if isinstance(res, Exception):
                res = ([], f"Error: {res}")
            self._quick_done.emit(jid, last_id, res[0], res[1])
        sf.run_async(work, done)

    def _on_quick_done(self, jid: str, last_id: str, replies, err: str):
        key = (jid, last_id)
        if err:
            self._quick_state[key] = f"error:{err}"
        else:
            self._quick_state.pop(key, None)
        if self._jid() == jid and self._last_msgs and self._last_msgs[-1]["id"] == last_id:
            self.on_messages(self._last_msgs)

    def _use_reply(self, text: str):
        edit = self.panel.input_text
        edit.setPlainText(text)
        cur = edit.textCursor()
        cur.movePosition(cur.MoveOperation.End)
        edit.setTextCursor(cur)
        edit.setFocus()

    # ── Traducción de un mensaje ──
    def can_translate(self, msg: dict) -> bool:
        return bool(sf._msg_text(msg))

    def translation_visible(self, mid: str) -> bool:
        return mid in self.translations

    def toggle_translation(self, msg: dict):
        mid = msg["id"]
        if mid in self.translations:
            if self.translations[mid] != "…":
                self.translations.pop(mid, None)
                self.panel._refresh_keep_scroll()
            return
        if not ai_enabled():
            QMessageBox.information(self.panel, "IA desactivada", "Activá las funciones de IA en Ajustes para traducir.")
            return
        self.translations[mid] = "…"
        self.panel._refresh_keep_scroll()
        target, text = sf.get_translate_lang(), sf._msg_text(msg)
        prov, model = choose_ai()

        def done(res):
            if isinstance(res, Exception):
                res = ("", f"Error: {res}")
            self._translated.emit(mid, res[0], res[1])
        sf.run_async(lambda: sf.translate(text, target, prov, model), done)

    def _on_translated(self, mid: str, text: str, err: str):
        if mid in self.translations:
            self.translations[mid] = text if not err else f"Error: {err}"
            self.panel._refresh_keep_scroll()

    def translation_html(self, msg: dict) -> str:
        mid = msg.get("id")
        t = self.translations.get(mid)
        if t is None:
            return ""
        lang = html.escape(sf.get_translate_lang())
        if t == "…":
            body = f'<i style="color: {C["text_2"]};">🌐 Traduciendo…</i>'
        elif t.startswith("Error"):
            body = f'<span style="color: #e58a84; font-size: 11px;">No se pudo traducir: {html.escape(t[:160])}</span>'
        else:
            body = (f'<span style="color: {C["accent_hi"]}; font-size: 10px; font-weight: 600;">🌐 TRADUCCIÓN · {lang.upper()}</span>'
                    f'<br><span style="color: {C["text"]};">{html.escape(t).replace(chr(10), "<br>")}</span>')
        hide = (f'<br><span style="font-size: 10px;"><a href="advancedwhatsapp://untranslate/{mid}" '
                f'style="color: {C["link"]};">Ocultar</a></span>') if t != "…" else ""
        return '<div style="margin-top: 4px;"></div>' + self.panel._panel_html(body + hide, accent=C["accent"])

    # ── Traducir antes de enviar ──
    def show_draft_menu(self, anchor: QToolButton):
        text = self.panel.input_text.toPlainText().strip()
        if not text:
            QMessageBox.information(self.panel, "Traducir", "Escribí el mensaje y después elegí el idioma al que lo querés traducir.")
            return
        menu = QMenu(self.panel)
        menu.addSection("Traducir el borrador a…")
        for lang in sf.LANGUAGES:
            menu.addAction(lang.capitalize(), lambda l=lang: self.translate_draft(l))
        menu.exec(anchor.mapToGlobal(anchor.rect().topLeft()))

    def translate_draft(self, lang: str):
        jid = self._jid()
        text = self.panel.input_text.toPlainText().strip()
        if not jid or not text or jid in self._draft_busy:
            return
        if not ai_enabled():
            QMessageBox.information(self.panel, "IA desactivada", "Activá las funciones de IA en Ajustes para traducir.")
            return
        self._draft_busy.add(jid)
        self.panel.input_text.setPlaceholderText(f"Traduciendo al {lang}…")
        prov, model = choose_ai()

        def done(res):
            if isinstance(res, Exception):
                res = ("", f"Error: {res}")
            self._draft_translated.emit(jid, res[0], res[1])
        sf.run_async(lambda: sf.translate(text, lang, prov, model), done)

    def _on_draft_translated(self, jid: str, text: str, err: str):
        self._draft_busy.discard(jid)
        self.panel.input_text.setPlaceholderText("Escribe un mensaje  (Enter envía, Shift+Enter salto de línea)")
        if self._jid() != jid:
            return
        if err:
            QMessageBox.warning(self.panel, "No se pudo traducir", err)
            return
        edit = self.panel.input_text
        cur = edit.textCursor()
        cur.select(cur.SelectionType.Document)
        cur.insertText(text)        # con undo: Ctrl+Z vuelve al original
        edit.setTextCursor(cur)
        edit.setFocus()

    # ── Recordatorios detectados ──
    def _detect(self, msg: dict) -> Optional[Dict]:
        key = (msg.get("id"), msg.get("text"), msg.get("transcript"))
        if key not in self._remind_cache:
            if len(self._remind_cache) > 2000:
                self._remind_cache.clear()
            text = sf._msg_text(msg)
            self._remind_cache[key] = sf.detect_datetime(text, msg.get("timestamp")) if text else None
        found = self._remind_cache[key]
        return found if found and found["ts"] > time.time() + 30 else None

    def reminder_link(self, msg: dict) -> str:
        """⏰ junto a la hora de la burbuja si el mensaje menciona una fecha/hora futura (barato: regex cacheada)."""
        if not get_pref("date_reminders") or (msg.get("media_type") or "text") == "revoked":
            return ""
        found = self._detect(msg)
        if not found:
            return ""
        tip = f"Detecté «{found['label']}»: crear un recordatorio o programar un mensaje"
        return (f'<a href="advancedwhatsapp://remind/{msg["id"]}" style="text-decoration: none;" title="{html.escape(tip)}">'
                f'⏰</a>&nbsp;&nbsp;')

    def open_reminder(self, msg: dict):
        found = self._detect(msg)
        if not found:
            QMessageBox.information(self.panel, "Recordatorio", "Esa fecha ya pasó.")
            return
        snippet = sf._msg_text(msg)
        dlg = ReminderDialog(found, snippet, can_schedule=True, parent=self.panel)
        ok = dlg.exec() == QDialog.Accepted
        action, at, text = dlg.values()
        dlg.deleteLater()
        if not ok:
            return
        if at <= time.time():
            QMessageBox.warning(self.panel, "Fecha pasada", "Elegí una fecha y hora futuras.")
            return
        if action == "remind":
            reminders.add_reminder(at, text or snippet, self._jid(), self._name())
            self._tip("Recordatorio creado")
        elif action == "schedule":
            self.panel._set_schedule(at)
            self._tip("Escribí el mensaje: sale a la hora elegida")
            self.panel.input_text.setFocus()

    def _tip(self, text: str):
        from PySide6.QtWidgets import QToolTip
        from PySide6.QtCore import QPoint
        btn = self.panel.btn_action
        QToolTip.showText(btn.mapToGlobal(QPoint(0, -34)), text, btn)
