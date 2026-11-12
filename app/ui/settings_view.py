"""
Advanced WhatsApp - Panel de Configuración (SettingsPanel)
================================================
Gestión integral de Inteligencia Artificial (Google Gemini, Groq, OpenAI, Claude),
estado de la sesión de WhatsApp y parámetros del sistema.
"""

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QCheckBox, QGroupBox, QMessageBox,
    QComboBox, QFrame, QScrollArea, QTabWidget, QSlider, QGridLayout
)
from PySide6.QtCore import Qt, QTimer, QThread, Signal, QUrl
from PySide6.QtGui import QDesktopServices

from app.config import (
    load_app_config, save_app_config, DATA_DIR, DB_PATH, GATEWAY_URL,
    FONT_SCALE_MIN, FONT_SCALE_MAX, get_chat_font_scale, APP_VERSION, get_pref, set_pref,
    GATEWAY_PORT, WEBHOOK_PORT,
)
from app import windows_integration, chat_lock
from app.ui import lock_dialogs
from app.ui.theme import C, HEADER_H
from app.ui.icons import icon, tool_button, flame_pixmap
from app.ai.ai_service import AIService
from app.gateway_manager import GatewayManager


class PopulateModelsThread(QThread):
    """Hilo asíncrono para descubrir modelos disponibles en Groq, Gemini, OpenAI y Claude."""
    done = Signal(list, list, list, list)

    def __init__(self, ai_svc: AIService, keys: dict):
        super().__init__()
        self.ai_svc = ai_svc
        self.keys = keys

    def run(self):
        try:
            gemini = self.ai_svc.get_gemini_models(custom_key=self.keys.get("gemini"))
            groq = self.ai_svc.get_groq_models(custom_key=self.keys.get("groq"))
            openai = self.ai_svc.get_openai_models(custom_key=self.keys.get("openai"))
            claude = self.ai_svc.get_anthropic_models(custom_key=self.keys.get("anthropic"))
            self.done.emit(groq or [], gemini or [], openai or [], claude or [])
        except Exception as e:
            print(f"[SettingsPanel] Error descubriendo modelos: {e}")
            self.done.emit([], [], [], [])


class TestConnectionThread(QThread):
    """Hilo asíncrono para probar conectividad y latencia con un proveedor de IA."""
    done = Signal(dict)

    def __init__(self, ai_svc: AIService, provider: str, api_key: str, model: str):
        super().__init__()
        self.ai_svc = ai_svc
        self.provider = provider
        self.api_key = api_key
        self.model = model

    def run(self):
        try:
            res = self.ai_svc.test_connection(
                provider=self.provider,
                api_key=self.api_key,
                model_name=self.model
            )
            self.done.emit(res)
        except Exception as e:
            self.done.emit({
                "success": False,
                "provider": self.provider,
                "model": self.model,
                "latency_ms": 0,
                "response": "",
                "error": str(e)
            })


class SettingsPanel(QWidget):
    """Panel de Configuración de Advanced WhatsApp con IA y WhatsApp."""
    qr_requested = Signal()
    config_saved = Signal()
    font_scale_changed = Signal(int)
    test_notification_requested = Signal()
    locks_cleared = Signal()   # se quitó la contraseña: todos los chats quedaron desbloqueados

    def __init__(self, parent=None):
        super().__init__(parent)
        self.config = load_app_config()
        self.ai_service = AIService(self.config)
        self.gateway = GatewayManager.get_instance()

        self._cached_groq_models = []
        self._cached_gemini_models = []
        self._cached_openai_models = []
        self._cached_claude_models = []

        self._init_ui()
        self.load_settings()

        # Descubrimiento inicial de modelos en segundo plano
        QTimer.singleShot(400, self.refrescar_modelos)

    def _init_ui(self):
        self.setObjectName("settingsPanel")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet(f"#settingsPanel {{ background-color: {C['panel']}; }}")
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        header = QFrame()
        header.setObjectName("paneHeader")
        header.setFixedHeight(HEADER_H)
        hl = QHBoxLayout(header)
        hl.setContentsMargins(16, 0, 16, 0)
        lbl_title = QLabel("Configuración")
        lbl_title.setObjectName("paneTitle")
        hl.addWidget(lbl_title)
        hl.addStretch()
        root_layout.addWidget(header)

        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(16, 12, 16, 16)
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(False)
        body_layout.addWidget(self.tabs)
        root_layout.addWidget(body, stretch=1)

        # Ojo: en las pestañas un "&" se toma como atajo de teclado; para mostrarlo hay que escribir "&&"
        self.tab_ai = self._create_ai_tab()
        self.tabs.addTab(self.tab_ai, "Inteligencia artificial")
        self.tab_whatsapp = self._create_whatsapp_tab()
        self.tabs.addTab(self.tab_whatsapp, "WhatsApp y conexión")
        self.tab_notify = self._create_notifications_tab()
        self.tabs.addTab(self.tab_notify, "Notificaciones y Windows")
        self.tab_appearance = self._create_appearance_tab()
        self.tabs.addTab(self.tab_appearance, "Apariencia")
        from app.ui.backup_panel import BackupTab
        self.backup_tab = BackupTab(self.gateway)
        self.tabs.addTab(self.backup_tab, "Copias y sincronización")
        self.tab_about = self._create_about_tab()
        self.tabs.addTab(self.tab_about, "Acerca de")

    @staticmethod
    def _page() -> tuple:
        """Página de pestaña con scroll; devuelve (widget de la pestaña, layout del contenido)."""
        tab = QWidget()
        tab_layout = QVBoxLayout(tab)
        tab_layout.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        container = QWidget()
        lay = QVBoxLayout(container)
        lay.setContentsMargins(16, 16, 16, 16)
        lay.setSpacing(12)
        scroll.setWidget(container)
        tab_layout.addWidget(scroll)
        return tab, lay

    @staticmethod
    def _hint(text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setObjectName("hint")
        lbl.setWordWrap(True)
        return lbl

    # ──────────────────────────────────────────────────────────────────────────
    # PESTAÑA 1: INTELIGENCIA ARTIFICIAL
    # ──────────────────────────────────────────────────────────────────────────

    def _create_ai_tab(self) -> QWidget:
        tab, c_layout = self._page()

        self.check_ai_enabled = QCheckBox("Habilitar funciones de inteligencia artificial")
        c_layout.addWidget(self.check_ai_enabled)

        # Transcripción automática de audios (get_pref/set_pref; la cola vive en media_service)
        self.check_auto_transcribe = QCheckBox("Transcribir audios automáticamente")
        self.check_auto_transcribe.setChecked(get_pref("auto_transcribe"))
        self.check_auto_transcribe.toggled.connect(lambda on: set_pref("auto_transcribe", on))
        c_layout.addWidget(self.check_auto_transcribe)
        c_layout.addWidget(self._hint(
            "Las notas de voz que llegan (y las que mandás) se transcriben solas en segundo plano. "
            "Necesita una clave de Groq u OpenAI. Si falla alguna, queda el botón «Transcribir» en el mensaje."))

        # Proveedor y modelo principal
        group_active = QGroupBox("Proveedor y modelo principal")
        grid = QGridLayout(group_active)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(8)
        grid.addWidget(QLabel("Proveedor:"), 0, 0)
        self.cmb_provider = QComboBox()
        self.cmb_provider.addItems(["Gemini", "Groq", "OpenAI", "Claude"])
        self.cmb_provider.currentTextChanged.connect(self._on_provider_changed)
        grid.addWidget(self.cmb_provider, 0, 1)
        grid.addWidget(QLabel("Modelo:"), 0, 2)
        self.cmb_model = QComboBox()
        self.cmb_model.setMinimumWidth(220)
        self.cmb_model.setEditable(True)
        grid.addWidget(self.cmb_model, 0, 3)
        self.btn_refresh_models = QPushButton("Descubrir modelos")
        self.btn_refresh_models.setIcon(icon("refresh"))
        self.btn_refresh_models.setCursor(Qt.PointingHandCursor)
        self.btn_refresh_models.clicked.connect(self.refrescar_modelos)
        grid.addWidget(self.btn_refresh_models, 0, 4)
        grid.setColumnStretch(3, 1)
        grid.addWidget(self._hint("Se usa para el procesamiento de mensajes, las respuestas asistidas y los análisis."),
                       1, 0, 1, 5)
        c_layout.addWidget(group_active)

        # Claves de API: una fila por proveedor
        group_keys = QGroupBox("Claves de API")
        kgrid = QGridLayout(group_keys)
        kgrid.setHorizontalSpacing(8)
        kgrid.setVerticalSpacing(8)
        providers = [
            ("Gemini", "gemini", "AIzaSy...", "Google Gemini: recomendado, multimodal y con cuotas amplias."),
            ("Groq", "groq", "gsk_...", "Groq Cloud: muy rápido (Llama, Qwen y otros modelos abiertos)."),
            ("OpenAI", "openai", "sk-proj-...", "OpenAI: familia GPT (gpt-4o-mini, gpt-4o, o3-mini…)."),
            ("Claude", "anthropic", "sk-ant-...", "Anthropic Claude: Sonnet, Opus y Haiku (console.anthropic.com)."),
        ]
        for row, (label, key, placeholder, tip) in enumerate(providers):
            lbl = QLabel(f"{label}:")
            lbl.setToolTip(tip)
            kgrid.addWidget(lbl, row, 0)
            edit = QLineEdit()
            edit.setPlaceholderText(placeholder)
            edit.setEchoMode(QLineEdit.Password)
            edit.setToolTip(tip)
            kgrid.addWidget(edit, row, 1)
            btn_eye = tool_button("eye", "Mostrar / ocultar la clave", icon_size=18)
            btn_eye.setCheckable(True)
            btn_eye.toggled.connect(lambda on, e=edit: e.setEchoMode(QLineEdit.Normal if on else QLineEdit.Password))
            kgrid.addWidget(btn_eye, row, 2)
            btn_test = QPushButton(f"Probar {label}")
            btn_test.setCursor(Qt.PointingHandCursor)
            btn_test.clicked.connect(lambda _=False, p=label: self._test_provider(p))
            kgrid.addWidget(btn_test, row, 3)
            setattr(self, f"edit_{key}_key", edit)
            setattr(self, f"btn_toggle_{key}", btn_eye)
            setattr(self, f"btn_test_{key}", btn_test)
        kgrid.setColumnStretch(1, 1)
        c_layout.addWidget(group_keys)

        # Resultado de las pruebas de conexión
        self.lbl_test_result = QLabel("Usa «Probar» para verificar la conexión con un proveedor.")
        self.lbl_test_result.setWordWrap(True)
        self.lbl_test_result.setObjectName("subtle")
        self.lbl_test_result.setTextInteractionFlags(Qt.TextSelectableByMouse)
        c_layout.addWidget(self.lbl_test_result)

        save_row = QHBoxLayout()
        save_row.addStretch()
        self.btn_save_ai = QPushButton("Guardar cambios")
        self.btn_save_ai.setObjectName("btnPrimary")
        self.btn_save_ai.setCursor(Qt.PointingHandCursor)
        self.btn_save_ai.clicked.connect(self.save_settings)
        save_row.addWidget(self.btn_save_ai)
        c_layout.addLayout(save_row)
        c_layout.addStretch()
        return tab

    # ──────────────────────────────────────────────────────────────────────────
    # PESTAÑA 2: WHATSAPP Y CONEXIÓN
    # ──────────────────────────────────────────────────────────────────────────

    def _create_whatsapp_tab(self) -> QWidget:
        tab, layout = self._page()

        group = QGroupBox("Sesión de WhatsApp")
        g = QVBoxLayout(group)
        g.setSpacing(8)
        self.lbl_wa_status = QLabel("Consultando estado de WhatsApp...")
        self.lbl_wa_status.setStyleSheet("font-size: 14px; font-weight: 600;")
        g.addWidget(self.lbl_wa_status)
        self.lbl_wa_details = self._hint(f"Puertos internos: gateway {GATEWAY_PORT} · webhook {WEBHOOK_PORT}")
        g.addWidget(self.lbl_wa_details)

        actions_layout = QHBoxLayout()
        actions_layout.setSpacing(8)
        self.btn_open_qr = QPushButton("Vincular con código QR")
        self.btn_open_qr.setObjectName("btnPrimary")
        self.btn_open_qr.setIcon(icon("qr", C["on_accent"], active_color=C["on_accent"]))
        self.btn_open_qr.setCursor(Qt.PointingHandCursor)
        self.btn_open_qr.clicked.connect(self.qr_requested.emit)
        actions_layout.addWidget(self.btn_open_qr)

        self.btn_refresh_wa = QPushButton("Comprobar estado")
        self.btn_refresh_wa.setIcon(icon("refresh"))
        self.btn_refresh_wa.setCursor(Qt.PointingHandCursor)
        self.btn_refresh_wa.clicked.connect(self.refresh_whatsapp_status)
        actions_layout.addWidget(self.btn_refresh_wa)
        actions_layout.addStretch()

        self.btn_disconnect = QPushButton("Cerrar sesión (desvincular)…")
        self.btn_disconnect.setObjectName("btnDanger")
        self.btn_disconnect.setCursor(Qt.PointingHandCursor)
        self.btn_disconnect.clicked.connect(self._on_disconnect_clicked)
        actions_layout.addWidget(self.btn_disconnect)
        g.addLayout(actions_layout)
        layout.addWidget(group)
        layout.addStretch()

        QTimer.singleShot(600, self.refresh_whatsapp_status)
        return tab

    # ──────────────────────────────────────────────────────────────────────────
    # PESTAÑA: NOTIFICACIONES Y WINDOWS
    # ──────────────────────────────────────────────────────────────────────────

    def _create_notifications_tab(self) -> QWidget:
        tab, layout = self._page()
        self._pref_checks = {}

        def check(group_layout, key, text, hint=None):
            cb = QCheckBox(text)
            cb.toggled.connect(lambda on, k=key: self._on_pref_toggled(k, on))
            group_layout.addWidget(cb)
            if hint:
                group_layout.addWidget(self._hint(hint))
            self._pref_checks[key] = cb
            return cb

        g1 = QGroupBox("Mensajes")
        l1 = QVBoxLayout(g1)
        l1.setSpacing(6)
        check(l1, "notify_enabled", "Mostrar notificaciones de mensajes nuevos",
              "Aparecen en Windows cuando Advanced WhatsApp está minimizado, en la bandeja o detrás de otra ventana. "
              "Tocá la notificación para abrir el chat; también podés responder o marcarlo como leído desde ahí.")
        check(l1, "notify_preview", "Mostrar el texto del mensaje en la notificación")
        check(l1, "notify_sound", "Reproducir sonido",
              "Con la ventana abierta en otro chat suena un aviso suave, sin notificación.")
        check(l1, "notify_groups", "Notificar mensajes de grupos",
              "Apagado: los grupos no avisan ni suman en el contador del ícono (se siguen viendo en la pestaña Grupos).")
        check(l1, "notify_calls", "Avisar de llamadas entrantes (se atienden desde el celular)")
        row = QHBoxLayout()
        btn_test = QPushButton("Probar notificación")
        btn_test.setIcon(icon("bell"))
        btn_test.setCursor(Qt.PointingHandCursor)
        btn_test.clicked.connect(self.test_notification_requested.emit)
        row.addWidget(btn_test)
        btn_win = QPushButton("Ajustes de notificaciones de Windows…")
        btn_win.setCursor(Qt.PointingHandCursor)
        btn_win.clicked.connect(lambda: QDesktopServices.openUrl(QUrl("ms-settings:notifications")))
        row.addWidget(btn_win)
        row.addStretch()
        l1.addLayout(row)
        l1.addWidget(self._hint("Para silenciar un chat: clic derecho sobre él en la lista › Silenciar notificaciones."))
        layout.addWidget(g1)

        g2 = QGroupBox("Windows")
        l2 = QVBoxLayout(g2)
        l2.setSpacing(6)
        self.chk_autostart = QCheckBox("Abrir Advanced WhatsApp al iniciar Windows (minimizado en la bandeja)")
        self.chk_autostart.toggled.connect(self._on_autostart_toggled)
        l2.addWidget(self.chk_autostart)
        check(l2, "close_to_tray", "Al cerrar la ventana, seguir en segundo plano en la bandeja",
              "Así no te perdés mensajes. Para salir del todo: clic derecho en el icono de la bandeja › Salir "
              "(o Ctrl+Q).")
        layout.addWidget(g2)

        g3 = QGroupBox("Privacidad")
        l3 = QVBoxLayout(g3)
        l3.setSpacing(6)
        check(l3, "status_receipts", "Al ver un estado, avisar al contacto que lo viste",
              "Igual que en el celular. Si lo desactivás, los estados se marcan como vistos solo en esta PC.")
        check(l3, "typing_indicator", "Mostrar \"escribiendo…\" a la otra persona mientras escribo")
        l3.addWidget(self._hint("Advanced WhatsApp figura \"en línea\" solo mientras la ventana está al frente, como WhatsApp "
                                "Desktop; en segundo plano tu celular sigue recibiendo sus notificaciones."))
        layout.addWidget(g3)

        g4 = QGroupBox("Chats bloqueados")
        l4 = QVBoxLayout(g4)
        l4.setSpacing(6)
        l4.addWidget(self._hint("Un chat bloqueado se ve apagado en la lista, sin vista previa, pide la contraseña para abrirlo "
                                "y sus notificaciones no muestran el mensaje. Para bloquear uno: clic derecho sobre el chat."))
        row4 = QHBoxLayout()
        self.btn_lock_pw = QPushButton()
        self.btn_lock_pw.clicked.connect(self._on_lock_password)
        row4.addWidget(self.btn_lock_pw)
        self.btn_lock_remove = QPushButton("Quitar contraseña y desbloquear todo")
        self.btn_lock_remove.clicked.connect(self._on_lock_remove)
        row4.addWidget(self.btn_lock_remove)
        row4.addStretch()
        l4.addLayout(row4)
        layout.addWidget(g4)
        self._refresh_lock_buttons()
        layout.addStretch()
        self.load_prefs()
        return tab

    def _refresh_lock_buttons(self):
        has = chat_lock.has_password()
        self.btn_lock_pw.setText("Cambiar contraseña de bloqueo" if has else "Crear contraseña de bloqueo")
        self.btn_lock_remove.setVisible(has)

    def _on_lock_password(self):
        lock_dialogs.change_password(self)
        self._refresh_lock_buttons()

    def _on_lock_remove(self):
        if lock_dialogs.remove_password(self):
            QMessageBox.information(self, "Chats bloqueados", "Se quitó la contraseña y todos los chats quedaron desbloqueados.")
            self.locks_cleared.emit()
        self._refresh_lock_buttons()

    def load_prefs(self):
        for key, cb in getattr(self, "_pref_checks", {}).items():
            cb.blockSignals(True)
            cb.setChecked(get_pref(key))
            cb.blockSignals(False)
        self._sync_pref_enabled()
        if hasattr(self, "chk_autostart"):
            self.chk_autostart.blockSignals(True)
            self.chk_autostart.setChecked(windows_integration.is_autostart_enabled())
            self.chk_autostart.blockSignals(False)

    def _sync_pref_enabled(self):
        on = get_pref("notify_enabled")
        for key in ("notify_preview", "notify_sound"):
            if key in self._pref_checks:
                self._pref_checks[key].setEnabled(on)

    def _on_pref_toggled(self, key: str, on: bool):
        set_pref(key, on)
        self._sync_pref_enabled()

    def _on_autostart_toggled(self, on: bool):
        if not windows_integration.set_autostart(on):
            QMessageBox.warning(self, "Inicio con Windows", "No se pudo cambiar la opción de inicio con Windows.")
            self.load_prefs()

    # ──────────────────────────────────────────────────────────────────────────
    # PESTAÑA 3: APARIENCIA (TAMAÑO DE LETRA DE LOS CHATS)
    # ──────────────────────────────────────────────────────────────────────────

    def _create_appearance_tab(self) -> QWidget:
        tab, layout = self._page()

        group = QGroupBox("Tamaño de letra de los chats")
        g_layout = QVBoxLayout(group)
        g_layout.setSpacing(8)

        row = QHBoxLayout()
        row.setSpacing(8)
        self.btn_font_minus = QPushButton("A−")
        self.btn_font_minus.setFixedWidth(40)
        self.btn_font_minus.setStyleSheet("padding: 5px 0;")
        self.btn_font_minus.setToolTip("Achicar letra")
        self.btn_font_minus.clicked.connect(lambda: self.slider_font.setValue(self.slider_font.value() - 10))
        row.addWidget(self.btn_font_minus)

        self.slider_font = QSlider(Qt.Horizontal)
        self.slider_font.setRange(FONT_SCALE_MIN, FONT_SCALE_MAX)
        self.slider_font.setSingleStep(5)
        self.slider_font.setPageStep(10)
        self.slider_font.setValue(get_chat_font_scale())
        self.slider_font.valueChanged.connect(self._on_font_slider_changed)
        row.addWidget(self.slider_font, 1)

        self.btn_font_plus = QPushButton("A+")
        self.btn_font_plus.setFixedWidth(40)
        self.btn_font_plus.setStyleSheet("padding: 5px 0;")
        self.btn_font_plus.setToolTip("Agrandar letra")
        self.btn_font_plus.clicked.connect(lambda: self.slider_font.setValue(self.slider_font.value() + 10))
        row.addWidget(self.btn_font_plus)

        self.lbl_font_value = QLabel(f"{self.slider_font.value()}%")
        self.lbl_font_value.setMinimumWidth(40)
        self.lbl_font_value.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        row.addWidget(self.lbl_font_value)

        self.btn_font_reset = QPushButton("Restablecer")
        self.btn_font_reset.clicked.connect(lambda: self.slider_font.setValue(100))
        for b in (self.btn_font_minus, self.btn_font_plus, self.btn_font_reset):
            b.setCursor(Qt.PointingHandCursor)
        row.addWidget(self.btn_font_reset)
        g_layout.addLayout(row)

        g_layout.addWidget(self._hint(
            "El cambio se aplica al instante y se guarda solo. Dentro de un chat también puedes usar "
            "Ctrl + rueda del mouse, o Ctrl + / Ctrl − / Ctrl 0."))
        layout.addWidget(group)

        group_keys = QGroupBox("Atajos de teclado")
        kl = QGridLayout(group_keys)
        kl.setHorizontalSpacing(20)
        kl.setVerticalSpacing(6)
        shortcuts = [
            ("Ctrl+1 / Ctrl+2", "Chats / Estados"), ("Ctrl+,", "Configuración"),
            ("Ctrl+N", "Nuevo chat"), ("Ctrl+K", "Buscar chat"),
            ("Ctrl+F", "Buscar dentro del chat"), ("Esc", "Cancelar respuesta / selección / búsqueda"),
            ("Enter / Shift+Enter", "Enviar / salto de línea"), ("Ctrl+Q", "Salir de Advanced WhatsApp"),
            ("← / → · Espacio", "Visor de estados: anterior / siguiente · pausa"),
        ]
        for i, (k, d) in enumerate(shortcuts):
            lk = QLabel(k)
            lk.setStyleSheet(f"color: {C['text']}; font-family: Consolas, monospace; font-size: 12px;")
            kl.addWidget(lk, i, 0)
            ld = QLabel(d)
            ld.setObjectName("subtle")
            kl.addWidget(ld, i, 1)
        kl.setColumnStretch(1, 1)
        layout.addWidget(group_keys)
        layout.addStretch()
        return tab

    def _on_font_slider_changed(self, value: int):
        self.lbl_font_value.setText(f"{value}%")
        self.font_scale_changed.emit(value)

    def set_font_scale_silently(self, value: int):
        """Sincroniza el deslizador cuando el tamaño cambia desde el chat (Ctrl + rueda)."""
        if self.slider_font.value() != value:
            self.slider_font.blockSignals(True)
            self.slider_font.setValue(value)
            self.slider_font.blockSignals(False)
            self.lbl_font_value.setText(f"{value}%")

    # ──────────────────────────────────────────────────────────────────────────
    # PESTAÑA 4: ACERCA DE
    # ──────────────────────────────────────────────────────────────────────────

    def _create_about_tab(self) -> QWidget:
        tab, layout = self._page()

        lbl_app = QLabel(f"Advanced WhatsApp <span style='color: {C['text_2']}; font-weight: 400;'>· versión {APP_VERSION}</span>")
        lbl_app.setStyleSheet("font-size: 15px; font-weight: 600;")
        title_row = QHBoxLayout()
        title_row.setSpacing(10)
        flame = QLabel()
        flame.setPixmap(flame_pixmap(36))
        title_row.addWidget(flame)
        title_row.addWidget(lbl_app)
        title_row.addStretch()
        layout.addLayout(title_row)
        layout.addWidget(self._hint("Cliente de WhatsApp de escritorio · Mag Tecnología"))

        group = QGroupBox("Datos locales")
        grid = QGridLayout(group)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(6)
        rows = [("Carpeta de datos:", DATA_DIR), ("Base de datos:", DB_PATH), ("Gateway interno:", GATEWAY_URL)]
        for i, (k, v) in enumerate(rows):
            lk = QLabel(k)
            lk.setObjectName("subtle")
            grid.addWidget(lk, i, 0)
            lv = QLabel(v)
            lv.setTextInteractionFlags(Qt.TextSelectableByMouse)
            grid.addWidget(lv, i, 1)
        grid.setColumnStretch(1, 1)
        btn_open = QPushButton("Abrir carpeta de datos")
        btn_open.setIcon(icon("folder"))
        btn_open.setCursor(Qt.PointingHandCursor)
        btn_open.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(DATA_DIR)))
        grid.addWidget(btn_open, len(rows), 0, 1, 2, Qt.AlignLeft)
        layout.addWidget(group)
        layout.addStretch()
        return tab

    # ──────────────────────────────────────────────────────────────────────────
    # MÉTODOS DE SOPORTE Y LÓGICA DE NEGOCIO
    # ──────────────────────────────────────────────────────────────────────────

    def _set_result(self, text: str, kind: str = "info"):
        color = {"ok": C["accent_hi"], "error": "#e58a84", "busy": C["text"]}.get(kind, C["text_2"])
        self.lbl_test_result.setStyleSheet(f"color: {color}; font-size: 12px;")
        self.lbl_test_result.setText(text)

    def load_settings(self):
        self.config = load_app_config()

        # Estado IA
        self.check_ai_enabled.setChecked(self.config.getboolean("IA", "enabled", fallback=True))

        # Keys
        self.edit_gemini_key.setText(self.config.get("GEMINI", "api_key", fallback=""))
        self.edit_groq_key.setText(self.config.get("GROQ", "api_key", fallback=""))
        self.edit_openai_key.setText(self.config.get("OPENAI", "api_key", fallback=""))
        self.edit_anthropic_key.setText(self.config.get("ANTHROPIC", "api_key", fallback=""))

        # Proveedor y Modelo
        saved_provider = self.config.get("IA", "provider", fallback="Gemini")
        idx_p = self.cmb_provider.findText(saved_provider, Qt.MatchFixedString)
        if idx_p >= 0:
            self.cmb_provider.setCurrentIndex(idx_p)

        self._saved_model = self.config.get("IA", "model", fallback="gemini-2.5-flash")
        self.cmb_model.setCurrentText(self._saved_model)

    def save_settings(self):
        self.config.set("IA", "enabled", "true" if self.check_ai_enabled.isChecked() else "false")
        self.config.set("GEMINI", "api_key", self.edit_gemini_key.text().strip())
        self.config.set("GROQ", "api_key", self.edit_groq_key.text().strip())
        self.config.set("OPENAI", "api_key", self.edit_openai_key.text().strip())
        self.config.set("ANTHROPIC", "api_key", self.edit_anthropic_key.text().strip())

        chosen_provider = self.cmb_provider.currentText().strip() or "Gemini"
        chosen_model = self.cmb_model.currentText().strip() or "gemini-2.5-flash"

        self.config.set("IA", "provider", chosen_provider)
        self.config.set("IA", "model", chosen_model)

        if chosen_provider.lower() == "gemini":
            self.config.set("GEMINI", "model", chosen_model)
        elif chosen_provider.lower() == "groq":
            self.config.set("GROQ", "model", chosen_model)
        elif chosen_provider.lower() == "openai":
            self.config.set("OPENAI", "model", chosen_model)
        elif chosen_provider.lower() == "claude":
            self.config.set("ANTHROPIC", "model", chosen_model)

        if save_app_config(self.config):
            self.ai_service.reload_config(self.config)
            self._set_result("Configuración guardada.", "ok")
            self.config_saved.emit()
        else:
            QMessageBox.critical(self, "Error", "No se pudo guardar el archivo de configuración.")

    def stop_threads(self, wait_ms: int = 1500):
        """Al cerrar la app: espera a los hilos de red; un QThread en marcha que se destruye aborta el proceso."""
        for name in ("thread_models", "thread_test"):
            t = getattr(self, name, None)
            if t is not None and t.isRunning():
                t.wait(wait_ms)

    def refrescar_modelos(self):
        prev = getattr(self, "thread_models", None)
        if prev is not None and prev.isRunning():
            return   # ya hay una búsqueda en curso: reemplazar el QThread en marcha lo destruiría
        self.btn_refresh_models.setEnabled(False)
        self.btn_refresh_models.setText("Buscando…")

        keys = {
            "gemini": self.edit_gemini_key.text().strip(),
            "groq": self.edit_groq_key.text().strip(),
            "openai": self.edit_openai_key.text().strip(),
            "anthropic": self.edit_anthropic_key.text().strip()
        }

        self.thread_models = PopulateModelsThread(self.ai_service, keys)
        self.thread_models.done.connect(self._on_models_loaded)
        self.thread_models.start()

    def _on_models_loaded(self, groq_models, gemini_models, openai_models, claude_models):
        self.btn_refresh_models.setEnabled(True)
        self.btn_refresh_models.setText("Descubrir modelos")

        self._cached_groq_models = groq_models or ["qwen/qwen3.8-27b", "openai/gpt-oss-120b", "llama-3.3-70b-versatile"]
        self._cached_gemini_models = gemini_models or ["gemini-2.5-flash", "gemini-2.5-flash-lite", "gemini-3.5-flash-lite"]
        self._cached_openai_models = openai_models or ["gpt-4o-mini", "gpt-4o", "gpt-5.6-luna"]
        self._cached_claude_models = claude_models or ["claude-sonnet-5-5", "claude-opus-5-5", "claude-haiku-5-5"]

        current_target = self.cmb_model.currentText() or getattr(self, "_saved_model", "gemini-2.5-flash")
        self._on_provider_changed(self.cmb_provider.currentText(), preferred_model=current_target)

    def _on_provider_changed(self, provider: str, preferred_model: str = None):
        target_val = preferred_model if preferred_model is not None else self.cmb_model.currentText()
        if not target_val:
            target_val = getattr(self, "_saved_model", "")

        self.cmb_model.clear()

        prov_lower = provider.lower()
        if "claude" in prov_lower and not target_val.startswith("claude"):
            target_val = ""   # el modelo de otro proveedor no se mezcla en la lista de Claude
        if "gemini" in prov_lower:
            models = self._cached_gemini_models or ["gemini-2.5-flash", "gemini-2.5-flash-lite"]
            default_choice = "gemini-2.5-flash"
        elif "openai" in prov_lower:
            models = self._cached_openai_models or ["gpt-4o-mini", "gpt-4o"]
            default_choice = "gpt-4o-mini"
        elif "claude" in prov_lower:
            models = self._cached_claude_models or ["claude-sonnet-5-5", "claude-opus-5-5", "claude-haiku-5-5"]
            default_choice = "claude-sonnet-5-5"
        else:
            models = self._cached_groq_models or ["qwen/qwen3.8-27b", "openai/gpt-oss-120b"]
            default_choice = "qwen/qwen3.8-27b"

        self.cmb_model.addItems(models)

        if target_val and target_val in models:
            self.cmb_model.setCurrentText(target_val)
        elif target_val:
            self.cmb_model.addItem(target_val)
            self.cmb_model.setCurrentText(target_val)
        else:
            self.cmb_model.setCurrentText(default_choice)

    def _test_provider(self, provider: str):
        if provider.lower() == "gemini":
            key = self.edit_gemini_key.text().strip()
            model = self.cmb_model.currentText().strip() if self.cmb_provider.currentText() == "Gemini" else "gemini-2.5-flash"
        elif provider.lower() == "groq":
            key = self.edit_groq_key.text().strip()
            model = self.cmb_model.currentText().strip() if self.cmb_provider.currentText() == "Groq" else "qwen/qwen3.8-27b"
        elif provider.lower() == "claude":
            key = self.edit_anthropic_key.text().strip()
            model = (self.cmb_model.currentText().strip() if self.cmb_provider.currentText() == "Claude"
                     else "claude-sonnet-5-5")
        else:
            key = self.edit_openai_key.text().strip()
            model = self.cmb_model.currentText().strip() if self.cmb_provider.currentText() == "OpenAI" else "gpt-4o-mini"

        if not key:
            QMessageBox.warning(self, "API Key Requerida", f"Por favor ingresa la API Key de {provider} antes de probar.")
            return

        prev = getattr(self, "thread_test", None)
        if prev is not None and prev.isRunning():
            return   # prueba en curso: reemplazar el QThread en marcha lo destruiría
        self._set_result(f"Conectando con {provider} ({model})…", "busy")

        self.thread_test = TestConnectionThread(self.ai_service, provider, key, model)
        self.thread_test.done.connect(self._on_test_completed)
        self.thread_test.start()

    def _on_test_completed(self, result: dict):
        success = result.get("success", False)
        provider = result.get("provider", "")
        model = result.get("model", "")
        latency = result.get("latency_ms", 0)

        if success:
            self._set_result(f"Conexión correcta con {provider} ({model}) · {latency} ms · "
                             f"respuesta: {result.get('response', 'OK')}", "ok")
        else:
            err = result.get("error", "Error desconocido.")
            self._set_result(f"No se pudo conectar con {provider} ({model}): {err}", "error")

    def refresh_whatsapp_status(self):
        try:
            st = self.gateway.get_status()
            status_text = st.get("status", "disconnected")
            phone = st.get("phone")
            push_name = st.get("push_name")

            if status_text == "connected":
                display_phone = f"+{phone}" if phone else "número desconocido"
                text, color = f"● Conectado como {push_name or 'Yo'} ({display_phone})", C["accent_hi"]
            elif status_text == "qr_ready":
                text, color = "● Esperando que escanees el código QR", C["warn"]
            elif status_text == "connecting":
                text, color = "● Conectando…", C["warn"]
            else:
                text, color = "● Desconectado", "#e58a84"
            if not st.get("online"):
                err = self.gateway.last_error
                text, color = ("● El servicio de WhatsApp no está funcionando" + (f": {err}" if err else
                               " (reintentando…)")), "#e58a84"
            self.lbl_wa_status.setText(text)
            self.lbl_wa_status.setStyleSheet(f"font-size: 14px; font-weight: 600; color: {color};")
            self.btn_disconnect.setEnabled(status_text == "connected")
            extra = f" · reinicios automáticos: {self.gateway.restarts}" if self.gateway.restarts else ""
            self.lbl_wa_details.setText(f"Puertos internos: gateway {GATEWAY_PORT} · webhook {WEBHOOK_PORT} · versión del gateway "
                                        f"{self.gateway.build}{extra}")
        except Exception as e:
            self.lbl_wa_status.setText(f"Error consultando el estado: {e}")

    def _on_disconnect_clicked(self):
        reply = QMessageBox.question(
            self,
            "Cerrar sesión",
            "¿Desvincular esta cuenta de WhatsApp?\n"
            "Tendrás que escanear un nuevo código QR para volver a ingresar.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        )
        if reply == QMessageBox.Yes:
            res = self.gateway.disconnect_session()
            if res.get("success"):
                self.refresh_whatsapp_status()
                self.qr_requested.emit()
            else:
                QMessageBox.critical(self, "Error", f"No se pudo cerrar la sesión: {res.get('error')}")
