import os
import threading
from typing import Optional
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QLabel, QTextBrowser, QPushButton, QHBoxLayout, QComboBox, QTabWidget,
    QWidget, QLineEdit, QPlainTextEdit, QMenu, QCheckBox, QGridLayout, QInputDialog
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QGuiApplication, QDesktopServices, QTextCursor, QColor, QPalette
from PySide6.QtCore import QUrl

from app.ai.ai_service import AIService
from app.ai import psych_analysis as pa
from app.db import get_messages, get_chats_for_self_analysis
from app.ui.theme import C

# El tema global (app/ui/theme.py) define botones, combos, pestañas, campos y menús
_BTN = _COMBO = _TABS = _INPUT = _MENU = ""
_TEXT = (
    "background-color: #201b19; color: #e6e0dc; border: 1px solid #372d28; border-radius: 8px;"
    " padding: 8px 10px; font-size: 13px;"
)
_MODEL_OK, _MODEL_FAIL, _MODEL_NEW = "#25b88f", "#e5534b", "#dce3e6"


class PsychAnalysisDialog(QDialog):
    """Análisis psicológico del contacto (según el tipo de relación y, si se quiere, con preguntas
    puntuales como «¿me quiere estafar?») o interno (de mí en todos los chats). Nunca arranca solo.
    Guarda notas acumuladas por sujeto, permite preguntas de seguimiento y exportar el análisis para
    seguirlo en otro chatbot. Sin jid solo está disponible el análisis interno."""
    _done = Signal(str, str, object)          # tipo ('analysis' | 'answer'), texto, meta
    _models_ready = Signal(str, list)         # proveedor, modelos

    def __init__(self, jid: Optional[str] = None, name: str = "", focus: Optional[str] = None, parent=None):
        super().__init__(parent)
        self.chat_jid, self.chat_name = jid or "", name or "contacto"
        self._analysis = ""
        self._qa = []                # [(pregunta, respuesta)] de esta sesión
        self._material = ""          # texto enviado al LLM (se reutiliza en las preguntas)
        self._material_info = (0, 0)  # (mensajes, chats)
        self._busy = False
        self._notes_jid = ""         # sujeto cuyas notas están cargadas en el editor
        self.resize(720, 680)
        self.setStyleSheet(
            "QDialog QLabel { color: #8a979e; font-size: 12px; }"
            f"QPushButton#chip {{ padding: 4px 12px; border-radius: 13px; background-color: {C['raised']};"
            f" border: 1px solid {C['border']}; color: {C['text_2']}; font-size: 12px; text-align: left; }}"
            f"QPushButton#chip:hover {{ border-color: {C['border_hi']}; color: {C['text']}; }}"
            f"QPushButton#chip:checked {{ background-color: {C['ember_dim']}; border-color: {C['ember']};"
            f" color: {C['ember_hi']}; }}"
            f"QPushButton#chipAdd {{ padding: 4px 12px; border-radius: 13px; background: transparent;"
            f" border: 1px dashed {C['border_hi']}; color: {C['text_2']}; font-size: 12px; }}"
            f"QPushButton#chipAdd:hover {{ color: {C['ember_hi']}; border-color: {C['ember']}; }}")

        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(10)

        opts = QHBoxLayout()
        opts.setSpacing(8)
        opts.addWidget(QLabel("Enfoque:"))
        self.cmb_focus = QComboBox()
        for key, label in pa.FOCUSES.items():
            self.cmb_focus.addItem(label, key)
            if key != "interno" and not self.chat_jid:
                self.cmb_focus.model().item(self.cmb_focus.count() - 1).setEnabled(False)
        opts.addWidget(self.cmb_focus)
        opts.addSpacing(8)
        opts.addWidget(QLabel("Longitud:"))
        self.cmb_length = QComboBox()
        for key, label in pa.LENGTHS.items():
            self.cmb_length.addItem(label, key)
        opts.addWidget(self.cmb_length)
        opts.addSpacing(8)
        self.lbl_count = QLabel("Mensajes:")
        opts.addWidget(self.lbl_count)
        self.cmb_count = QComboBox()
        for n in pa.MESSAGE_COUNTS:
            self.cmb_count.addItem(f"Últimos {n}", n)
        self.cmb_count.setCurrentIndex(pa.MESSAGE_COUNTS.index(pa.DEFAULT_MESSAGES))
        opts.addWidget(self.cmb_count)
        self.chk_context = QCheckBox("Incluir lo que escribieron los demás (contexto)")
        self.chk_context.setChecked(True)
        self.chk_context.setToolTip(
            "La IA ve a qué respondías y analiza mejor cómo reaccionas, pero el análisis es solo sobre ti.\n"
            "Ocupa más espacio: con IAs de límite bajo (Groq) entran menos mensajes tuyos.")
        self.chk_context.toggled.connect(self._on_context_toggled)
        self._with_context = True
        opts.addWidget(self.chk_context)
        opts.addStretch(1)
        lay.addLayout(opts)

        # Tipo de análisis (combo editable) y búsquedas opcionales
        kind_row = QHBoxLayout()
        kind_row.setSpacing(8)
        self.lbl_kind = QLabel("Tipo de análisis:")
        kind_row.addWidget(self.lbl_kind)
        self.cmb_kind = QComboBox()
        self.cmb_kind.setEditable(True)
        self.cmb_kind.setMinimumWidth(200)
        self.cmb_kind.addItems(pa.load_kinds())
        self.cmb_kind.setToolTip("Qué relación tienes con esta persona (amigo, familia, trabajo…): orienta la lectura.\n"
                                 "Puedes escribir otro tipo; queda guardado para la próxima.")
        kind_row.addWidget(self.cmb_kind)
        kind_row.addStretch(1)
        lay.addLayout(kind_row)

        self.search_box = QWidget()
        sb = QVBoxLayout(self.search_box)
        sb.setContentsMargins(0, 0, 0, 0)
        sb.setSpacing(6)
        sb.addWidget(QLabel("Qué buscar en la conversación (opcional; si no marcas nada, se hace el análisis general):"))
        self.search_grid = QGridLayout()
        self.search_grid.setSpacing(6)
        sb.addLayout(self.search_grid)
        lay.addWidget(self.search_box)
        self._search_chips = []
        self._rebuild_searches()

        ia = QHBoxLayout()
        ia.setSpacing(8)
        ia.addWidget(QLabel("IA:"))
        self.cmb_provider = QComboBox()
        self.cmb_provider.addItems(pa.PROVIDERS)
        ia.addWidget(self.cmb_provider)
        self.cmb_model = QComboBox()
        self.cmb_model.setMinimumWidth(220)
        self.cmb_model.setToolTip("Modelos disponibles del proveedor (se cargan automáticamente)")
        ia.addWidget(self.cmb_model, stretch=1)
        lay.addLayout(ia)
        for c in (self.cmb_focus, self.cmb_length, self.cmb_count, self.cmb_provider, self.cmb_kind):
            c.setStyleSheet(_COMBO)
        self.cmb_model.setStyleSheet(_COMBO.replace(" color: #e9edef;", ""))

        self.lbl_status = QLabel("")
        self.lbl_status.setWordWrap(True)
        lay.addWidget(self.lbl_status)

        self.tabs = QTabWidget()
        self.tabs.setStyleSheet(_TABS)
        lay.addWidget(self.tabs, stretch=1)

        # Pestaña Análisis: resultado + preguntas de seguimiento
        tab_an = QWidget()
        an_lay = QVBoxLayout(tab_an)
        an_lay.setContentsMargins(10, 10, 10, 10)
        an_lay.setSpacing(8)
        self.browser = QTextBrowser()
        self.browser.setStyleSheet(f"QTextBrowser {{ {_TEXT} }}")
        an_lay.addWidget(self.browser, stretch=1)
        ask = QHBoxLayout()
        ask.setSpacing(8)
        self.input_question = QLineEdit()
        self.input_question.setPlaceholderText("Hazle una pregunta sobre este análisis…")
        self.input_question.setStyleSheet(_INPUT)
        self.input_question.returnPressed.connect(self._ask)
        self.btn_ask = QPushButton("Preguntar")
        self.btn_ask.clicked.connect(self._ask)
        ask.addWidget(self.input_question, stretch=1)
        ask.addWidget(self.btn_ask)
        an_lay.addLayout(ask)
        self.tabs.addTab(tab_an, "Análisis")

        # Pestaña Notas: notas.txt del sujeto, solo las del usuario (esto sí se envía a la IA como contexto)
        tab_notes = QWidget()
        nt_lay = QVBoxLayout(tab_notes)
        nt_lay.setContentsMargins(10, 10, 10, 10)
        nt_lay.setSpacing(8)
        self.notes_edit = QPlainTextEdit()
        self.notes_edit.setStyleSheet(f"QPlainTextEdit {{ {_TEXT} }}")
        self.notes_edit.setPlaceholderText(
            "Tus propias notas sobre esta persona (por ejemplo: «es mi primo», «me debe plata»). Se le envían "
            "a la IA como contexto en cada análisis. Los análisis anteriores NO van acá: están en la pestaña "
            "Historial y no se reenvían.")
        nt_lay.addWidget(self.notes_edit, stretch=1)
        nt_row = QHBoxLayout()
        self.lbl_notes = QLabel("")
        nt_row.addWidget(self.lbl_notes, stretch=1)
        self.btn_save_notes = QPushButton("Guardar notas")
        self.btn_save_notes.clicked.connect(self._save_notes)
        nt_row.addWidget(self.btn_save_notes)
        nt_lay.addLayout(nt_row)
        self.tabs.addTab(tab_notes, "Notas")

        # Pestaña Historial: análisis y respuestas anteriores, solo lectura (nunca se reenvían a la IA)
        tab_hist = QWidget()
        hs_lay = QVBoxLayout(tab_hist)
        hs_lay.setContentsMargins(10, 10, 10, 10)
        hs_lay.setSpacing(8)
        self.history_view = QPlainTextEdit()
        self.history_view.setReadOnly(True)
        self.history_view.setStyleSheet(f"QPlainTextEdit {{ {_TEXT} }}")
        self.history_view.setPlaceholderText("Todavía no hay análisis guardados de este sujeto.")
        hs_lay.addWidget(self.history_view, stretch=1)
        hs_lay.addWidget(QLabel("Solo para consultar. Esto no se le envía a la IA al analizar ni al rehacer."))
        self.tabs.addTab(tab_hist, "Historial")

        row = QHBoxLayout()
        row.setSpacing(8)
        self.btn_folder = QPushButton("Abrir carpeta")
        self.btn_folder.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(pa.subject_dir(self._subject_jid()))))
        self.btn_copy = QPushButton("Copiar ▾")
        copy_menu = QMenu(self.btn_copy)
        copy_menu.setStyleSheet(_MENU)
        copy_menu.addAction("Copiar análisis", lambda: self._copy("plain"))
        copy_menu.addAction("Copiar para otro chatbot", lambda: self._copy("export"))
        copy_menu.addAction("Copiar para otro chatbot + mensajes", lambda: self._copy("export_material"))
        self.btn_copy.setMenu(copy_menu)
        self.btn_run = QPushButton("Analizar")
        self.btn_run.clicked.connect(self._start)
        btn_close = QPushButton("Cerrar")
        btn_close.clicked.connect(self.accept)
        row.addWidget(self.btn_folder)
        row.addStretch(1)
        for b in (self.btn_copy, self.btn_run, btn_close):
            row.addWidget(b)
        for b in (self.btn_folder, self.btn_copy, self.btn_run, btn_close, self.btn_ask, self.btn_save_notes):
            b.setCursor(Qt.PointingHandCursor)
        self.btn_run.setObjectName("btnPrimary")
        lay.addLayout(row)

        self._done.connect(self._on_done)
        self._models_ready.connect(self._fill_models)

        # Restaurar lo usado la última vez (enfoque, longitud, mensajes, contexto, IA y modelo)
        self._prefs = pa.load_prefs()
        prov, model = pa.default_provider_model()
        prov = self._prefs.get("provider") if self._prefs.get("provider") in pa.PROVIDERS else prov
        model = self._prefs.get("models", {}).get(prov) or model
        self.cmb_length.setCurrentIndex(max(0, self.cmb_length.findData(self._prefs.get("length", "short"))))
        self.cmb_count.setCurrentIndex(max(0, self.cmb_count.findData(self._prefs.get("count", pa.DEFAULT_MESSAGES))))
        self.chk_context.setChecked(bool(self._prefs.get("with_context", True)))
        self.cmb_kind.setCurrentText(self._prefs.get("kind") or pa.DEFAULT_KIND)
        self.cmb_provider.setCurrentText(prov)
        self._set_model(model)
        self._load_models_async(prov)
        self.cmb_provider.currentTextChanged.connect(self._on_provider_changed)
        self.cmb_model.currentTextChanged.connect(self._color_current_model)

        focus = focus or ("normal" if self.chat_jid else "interno")
        self.cmb_focus.setCurrentIndex(max(0, self.cmb_focus.findData(focus)))
        self.cmb_focus.currentIndexChanged.connect(self._on_focus_changed)
        self._on_focus_changed()

    # ─── sujeto / enfoque ─────────────────────────────────────────────────────

    def _focus(self) -> str:
        return self.cmb_focus.currentData()

    def _subject_jid(self) -> str:
        return pa.SELF_JID if self._focus() == "interno" else self.chat_jid

    def _subject_name(self) -> str:
        return "yo" if self._focus() == "interno" else self.chat_name

    # ─── búsquedas rápidas ────────────────────────────────────────────────────

    def _rebuild_searches(self):
        """Dibuja los botones de búsqueda (predeterminados y propios), conservando los marcados."""
        checked = {c.text() for c in self._search_chips if c.isChecked()}
        while self.search_grid.count():
            w = self.search_grid.takeAt(0).widget()
            if w:
                w.deleteLater()
        self._search_chips = []
        widgets = []
        for text, own in pa.load_searches():
            chip = QPushButton(text)
            chip.setObjectName("chip")
            chip.setCheckable(True)
            chip.setChecked(text in checked)
            chip.setCursor(Qt.PointingHandCursor)
            chip.setToolTip("Marca una o varias para que la IA responda solo eso"
                            + (" (clic derecho para quitarla)" if own else ""))
            chip.toggled.connect(self._refresh_buttons)
            if own:
                chip.setContextMenuPolicy(Qt.CustomContextMenu)
                chip.customContextMenuRequested.connect(lambda _pos, t=text: self._remove_search(t))
            self._search_chips.append(chip)
            widgets.append(chip)
        add = QPushButton("＋ Otra pregunta…")
        add.setObjectName("chipAdd")
        add.setCursor(Qt.PointingHandCursor)
        add.clicked.connect(self._add_search)
        widgets.append(add)
        for i, w in enumerate(widgets):
            self.search_grid.addWidget(w, i // 3, i % 3)

    def _selected_searches(self) -> list:
        return [c.text() for c in self._search_chips if c.isChecked()]

    def _add_search(self):
        text, ok = QInputDialog.getText(self, "Nueva pregunta", "¿Qué quieres que busque la IA en la conversación?")
        if ok and text.strip():
            pa.add_search(text.strip())
            self._rebuild_searches()
            for c in self._search_chips:
                if c.text() == text.strip():
                    c.setChecked(True)

    def _remove_search(self, text: str):
        pa.remove_search(text)
        self._rebuild_searches()
        self._refresh_buttons()

    def _on_focus_changed(self, *_):
        focus = self._focus()
        interno = focus == "interno"
        self.setWindowTitle("Análisis interno — yo" if interno else f"Análisis psicológico — {self.chat_name}")
        self.lbl_count.setVisible(not interno)
        self.cmb_count.setVisible(not interno)
        self.chk_context.setVisible(interno)
        self.lbl_kind.setVisible(not interno)
        self.cmb_kind.setVisible(not interno)
        self.search_box.setVisible(not interno)
        self._analysis, self._qa, self._material, self._material_info = "", [], "", (0, 0)
        self._flush_notes_editor()
        self._load_notes_editor()

        saved = pa.load_analysis(self._subject_jid(), focus)
        if saved:
            self._analysis = saved["text"]
            self._render()
            self.lbl_status.setText(
                f"💾 Análisis guardado ({saved['fecha']}, {saved['proveedor']} {saved['modelo']}). "
                "Usa «Rehacer» para generar uno nuevo o hazle preguntas abajo.")
        elif interno:
            self.browser.setMarkdown(
                "### Análisis interno\n\nSe toman **tus conversaciones de todos los chats individuales** (lo que "
                "escribieron los demás va solo como contexto) y la IA te devuelve un análisis psicológico tuyo. Después puedes "
                "hacerle preguntas o copiarlo para seguir en otro chatbot.\n\n"
                "Tus mensajes se envían al proveedor de IA elegido. Pulsa **🧠 Analizar** para empezar.")
            self.lbl_status.setText("")
        else:
            self.browser.setMarkdown(
                f"### {self.chat_name}\n\nElige el **tipo de análisis** (amigo, familia, trabajo… o escribe uno) "
                "y, si quieres, marca **qué buscar** (por ejemplo «¿Me quiere estafar?»). Después pulsa "
                "**Analizar**: no se hace nada hasta que lo pidas.\n\n"
                "Los mensajes se envían al proveedor de IA elegido.")
            self.lbl_status.setText("")
        self._refresh_buttons()

    # ─── modelos ──────────────────────────────────────────────────────────────

    def _on_provider_changed(self, prov: str):
        self.cmb_model.clear()
        self._set_model(self._prefs.get("models", {}).get(prov) or self._config_model(prov))
        self._load_models_async(prov)

    @staticmethod
    def _config_model(prov: str) -> str:
        return pa.config_model(prov)

    def _load_models_async(self, prov: str):
        def run():
            try:
                self._models_ready.emit(prov, pa.list_models(prov))
            except Exception:
                pass
        threading.Thread(target=run, daemon=True).start()

    def _set_model(self, model: str):
        """Selecciona el modelo; si todavía no está en la lista lo agrega."""
        if model and self.cmb_model.findText(model) < 0:
            self.cmb_model.insertItem(0, model)
        if model:
            self.cmb_model.setCurrentText(model)
        self._color_models()

    def _fill_models(self, prov: str, models: list):
        if prov != self.cmb_provider.currentText():
            return
        current = self.cmb_model.currentText()
        self.cmb_model.clear()
        self.cmb_model.addItems(models)
        self._set_model(current)

    def _color_models(self):
        """Verde: el último uso funcionó; rojo: falló; color normal: nunca usado en el análisis."""
        results = pa.model_results(self.cmb_provider.currentText())
        for i in range(self.cmb_model.count()):
            res = results.get(self.cmb_model.itemText(i))
            color = _MODEL_NEW if res is None else (_MODEL_OK if res.get("ok") else _MODEL_FAIL)
            tip = "Todavía no usado en el análisis" if res is None else (
                f"Funcionó la última vez ({res.get('fecha', '')})" if res.get("ok")
                else f"Falló la última vez ({res.get('fecha', '')}): {res.get('error', '')}")
            self.cmb_model.setItemData(i, QColor(color), Qt.ForegroundRole)
            self.cmb_model.setItemData(i, tip, Qt.ToolTipRole)
        self._color_current_model()

    def _color_current_model(self, *_):
        # Por paleta y no por hoja de estilo: un «color:» en el QSS taparía el color de cada ítem
        res = pa.model_results(self.cmb_provider.currentText()).get(self.cmb_model.currentText())
        color = QColor(_MODEL_NEW if res is None else (_MODEL_OK if res.get("ok") else _MODEL_FAIL))
        pal = self.cmb_model.palette()
        for role in (QPalette.Text, QPalette.ButtonText, QPalette.WindowText):
            pal.setColor(role, color)
        self.cmb_model.setPalette(pal)
        view_pal = self.cmb_model.view().palette()
        view_pal.setColor(QPalette.Text, QColor(_MODEL_NEW))
        self.cmb_model.view().setPalette(view_pal)

    def _remember_settings(self):
        prov, model = self.cmb_provider.currentText(), self.cmb_model.currentText().strip()
        prefs = {"length": self.cmb_length.currentData(), "count": self.cmb_count.currentData(),
                 "provider": prov, "with_context": self.chk_context.isChecked(),
                 "kind": self.cmb_kind.currentText().strip() or pa.DEFAULT_KIND}
        if model:
            prefs["models"] = {prov: model}
        pa.save_prefs(**prefs)
        self._prefs = pa.load_prefs()

    # ─── material ─────────────────────────────────────────────────────────────

    def _build_material(self, focus: str, prov: str, count: int):
        """Arma el texto a enviar (bloqueante: lee la base). Devuelve (texto, mensajes, chats)."""
        if focus == "interno":
            chats = get_chats_for_self_analysis(include_others=self._with_context)
            return pa.format_self_material(chats, pa.CHAR_BUDGET.get(prov, 18_000))
        messages = get_messages(self.chat_jid, limit=count)
        text = pa.format_conversation(messages, self.chat_name, focus)
        return text, len(messages), 1

    def _on_context_toggled(self, checked: bool):
        self._with_context = checked
        self._material = ""  # las preguntas siguientes usan el material con el ajuste nuevo

    def _ensure_material(self, focus: str, prov: str, count: int):
        if not self._material:
            self._material, *info = self._build_material(focus, prov, count)
            self._material_info = tuple(info)
        return self._material

    # ─── análisis y preguntas ─────────────────────────────────────────────────

    def _set_busy(self, busy: bool):
        self._busy = busy
        for w in (self.cmb_focus, self.cmb_length, self.cmb_count, self.cmb_provider, self.cmb_model,
                  self.input_question, self.chk_context, self.cmb_kind, self.search_box):
            w.setEnabled(not busy)
        self._refresh_buttons()

    def _refresh_buttons(self, *_):
        has = bool(self._analysis)
        searching = self._focus() == "normal" and bool(self._selected_searches())
        self.btn_run.setText("Buscar" if searching else ("Rehacer" if has else "Analizar"))
        self.btn_run.setEnabled(not self._busy)
        self.btn_copy.setEnabled(has and not self._busy)
        self.btn_ask.setEnabled(has and not self._busy)
        self.input_question.setEnabled(has and not self._busy)

    def _emit(self, kind: str, text: str, meta: dict):
        try:
            self._done.emit(kind, text, meta)
        except RuntimeError:  # el diálogo se cerró mientras la IA respondía
            pass

    def _start(self):
        focus, length = self._focus(), self.cmb_length.currentData()
        prov, model = self.cmb_provider.currentText(), self.cmb_model.currentText().strip()
        count = self.cmb_count.currentData()
        jid, name = self._subject_jid(), self._subject_name()
        notes = self.notes_edit.toPlainText()
        interno = focus == "interno"
        relation = "" if interno else (self.cmb_kind.currentText().strip() or pa.DEFAULT_KIND)
        searches = [] if interno else self._selected_searches()
        if relation:
            pa.remember_kind(relation)
            if self.cmb_kind.findText(relation, Qt.MatchFixedString) < 0:
                self.cmb_kind.addItem(relation)
        self._remember_settings()
        self._set_busy(True)
        what = "tus conversaciones de todos los chats" if interno else f"los últimos {count} mensajes"
        verb = "🔎 Buscando en" if searches else "🧠 Analizando"
        self.lbl_status.setText(f"{verb} {what} con {prov} {model}…")
        self.browser.setPlainText("Consultando a la IA, esto puede tardar unos segundos…")
        meta = {"focus": focus, "length": length, "prov": prov, "model": model, "jid": jid, "name": name,
                "relation": relation, "searches": searches}
        with_context = self._with_context

        def run():
            try:
                material, n_msgs, n_chats = self._build_material(focus, prov, count)
                meta.update(material=material, n_msgs=n_msgs, n_chats=n_chats)
                if not material.strip():
                    text = "Error: No hay mensajes de texto para analizar."
                else:
                    prompt = pa.build_prompt(material, focus, length, name, n_msgs, n_chats, notes,
                                             with_context, relation, searches)
                    text = pa.ask_llm(prompt, prov, model)
                    meta["called_llm"] = True
            except Exception as e:  # nunca dejar el diálogo colgado
                text = f"Error inesperado: {e}"
            self._emit("analysis", text, meta)
        threading.Thread(target=run, daemon=True).start()

    def _ask(self):
        question = self.input_question.text().strip()
        if not question or not self._analysis or self._busy:
            return
        focus, prov = self._focus(), self.cmb_provider.currentText()
        model, count = self.cmb_model.currentText().strip(), self.cmb_count.currentData()
        name, analysis, qa = self._subject_name(), self._analysis, list(self._qa)
        meta = {"focus": focus, "jid": self._subject_jid(), "question": question, "prov": prov, "model": model}
        self._remember_settings()
        self._set_busy(True)
        self.lbl_status.setText(f"💬 Preguntando a {prov} {model}…")

        def run():
            try:
                material = self._ensure_material(focus, prov, count)
                prompt = pa.build_followup_prompt(material, focus, name, analysis, qa, question)
                text = pa.ask_llm(prompt, prov, model)
                meta["called_llm"] = True
            except Exception as e:
                text = f"Error inesperado: {e}"
            self._emit("answer", text, meta)
        threading.Thread(target=run, daemon=True).start()

    def _on_done(self, kind: str, text: str, meta: dict):
        self._set_busy(False)
        failed = AIService.is_error_response(text)
        if meta.get("called_llm"):
            pa.record_model_result(meta["prov"], meta["model"], not failed, text if failed else "")
            if meta["prov"] == self.cmb_provider.currentText():
                self._color_models()
        if meta["focus"] != self._focus():
            return
        if failed:
            self.lbl_status.setText("❌ La IA no respondió (no se guardó nada). Prueba otra IA o modelo.")
            if kind == "analysis":
                self.browser.setPlainText(text)
            else:
                self.browser.append(f"\n❌ {text}")
            return
        self._flush_notes_editor()
        if kind == "analysis":
            self._analysis, self._qa = text, []
            self._material, self._material_info = meta["material"], (meta["n_msgs"], meta["n_chats"])
            try:
                path = pa.save_analysis(meta["jid"], meta["name"], text, meta["focus"], meta["length"],
                                        meta["prov"], meta["model"], meta["n_msgs"],
                                        meta.get("relation", ""), meta.get("searches"))
                extra = f" ({meta['n_msgs']} mensajes tuyos de {meta['n_chats']} chats)" \
                    if meta["focus"] == "interno" else ""
                self.lbl_status.setText(f"💾 Guardado en {path}{extra}\n"
                                        "Lectura orientativa, no es un diagnóstico clínico.")
            except OSError as e:
                self.lbl_status.setText(f"⚠️ Análisis listo pero no se pudo guardar: {e}")
        else:
            self._qa.append((meta["question"], text))
            self.input_question.clear()
            try:
                pa.save_question(meta["jid"], meta["focus"], meta["question"], text)
                self.lbl_status.setText("💬 Respuesta agregada al historial.")
            except OSError as e:
                self.lbl_status.setText(f"⚠️ No se pudo guardar la respuesta en el historial: {e}")
        self._load_notes_editor()
        self._render(scroll_to_end=kind == "answer")
        self._refresh_buttons()

    def _render(self, scroll_to_end: bool = False):
        md = self._analysis
        for q, a in self._qa:
            md += f"\n\n---\n\n**🙋 {q}**\n\n{a}"
        self.browser.setMarkdown(md)
        if scroll_to_end:
            self.browser.moveCursor(QTextCursor.End)
            self.browser.ensureCursorVisible()

    # ─── copiar / exportar ────────────────────────────────────────────────────

    def _copy(self, kind: str):
        if not self._analysis:
            return
        if kind == "plain":
            text = self._analysis
        else:
            material = ""
            if kind == "export_material":
                material = self._ensure_material(self._focus(), self.cmb_provider.currentText(),
                                                 self.cmb_count.currentData())
            text = pa.build_export(self._focus(), self._subject_name(), self._analysis, self._qa, material)
        QGuiApplication.clipboard().setText(text)
        self.lbl_status.setText("📋 Copiado al portapapeles."
                                + (" Pégalo en el chatbot que quieras." if kind != "plain" else ""))

    # ─── notas ────────────────────────────────────────────────────────────────

    def _load_notes_editor(self):
        jid = self._notes_jid = self._subject_jid()
        self.notes_edit.setPlainText(pa.load_notes(jid))
        self.notes_edit.document().setModified(False)
        self.notes_edit.moveCursor(QTextCursor.End)
        self.lbl_notes.setText(pa.notes_path(jid))
        self.history_view.setPlainText(pa.load_history(jid))
        self.history_view.moveCursor(QTextCursor.End)

    def _flush_notes_editor(self):
        """Guarda las notas editadas a mano (al cambiar de sujeto o cerrar)."""
        if self._notes_jid and self.notes_edit.document().isModified():
            self._save_notes()

    def _save_notes(self):
        jid = self._notes_jid
        try:
            pa.save_notes(jid, self.notes_edit.toPlainText())
            self.notes_edit.document().setModified(False)
            self.lbl_notes.setText(f"💾 Notas guardadas — {pa.notes_path(jid)}")
        except OSError as e:
            self.lbl_notes.setText(f"⚠️ No se pudieron guardar las notas: {e}")

    def done(self, result: int):
        self._flush_notes_editor()
        self._remember_settings()
        super().done(result)
