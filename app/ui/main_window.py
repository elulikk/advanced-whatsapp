import sys
import threading
from urllib.parse import urlparse, parse_qs

from PySide6.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QSplitter, QStackedWidget, QApplication,
    QSystemTrayIcon, QMenu, QMessageBox
)
from PySide6.QtCore import Qt, QTimer, QEvent, QSettings, QRectF
from PySide6.QtGui import QShortcut, QKeySequence, QIcon, QPixmap, QPainter, QColor, QFont

from app.config import APP_NAME, get_pref, set_pref
from app.db import get_or_create_chat, get_chats, get_chat, mark_chat_as_read
from app.gateway_manager import GatewayManager
from app.webhook_server import signals, WebhookServer
from app.notifications import NotificationCenter
from app.windows_integration import TaskbarBadge
from app.ui.nav_rail import NavRailWidget
from app.ui.chat_list import ChatListPanel
from app.ui.chat_view import ChatViewPanel
from app.ui.status_view import StatusPanel
from app.ui.settings_view import SettingsPanel
from app.ui.qr_dialog import QRDialog
from app.ui.theme import QSS_STYLESHEET, C
from app.ui.effects import WindowPolisher


class MainWindow(QMainWindow):
    """Ventana Principal de MAGSAPP - WhatsApp Desktop Oficial."""

    def __init__(self, start_hidden: bool = False):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.resize(1160, 740)
        self.setMinimumSize(900, 520)  # deja lugar al panel de info del contacto
        # Hoja global: así los diálogos y mensajes emergentes también toman el tema
        app = QApplication.instance()
        app.setStyle("Fusion")
        app.setStyleSheet(QSS_STYLESHEET)
        # Menús, desplegables y tooltips redondeados por Windows 11; barras de título oscuras
        WindowPolisher.install(app)

        self.gateway = GatewayManager.get_instance()
        self.webhook_server = WebhookServer.get_instance()
        self._start_hidden = start_hidden
        self._unread_total = 0
        self._qr_open = False
        self._told_about_tray = False
        self._settings = QSettings("MagTecnologia", "MAGSAPP")

        # Timers para evitar sobrecarga de la UI por ráfagas de red
        self._history_sync_timer = QTimer(self)
        self._history_sync_timer.setSingleShot(True)
        self._history_sync_timer.setInterval(600)
        self._history_sync_timer.timeout.connect(self._do_history_sync_reload)

        self._chat_reload_timer = QTimer(self)
        self._chat_reload_timer.setSingleShot(True)
        self._chat_reload_timer.setInterval(200)
        self._chat_reload_timer.timeout.connect(self._do_chat_list_reload)

        # "En línea" solo con la ventana al frente (con un pequeño margen para no parpadear al cambiar de ventana)
        self._presence_timer = QTimer(self)
        self._presence_timer.setSingleShot(True)
        self._presence_timer.setInterval(1500)
        self._presence_timer.timeout.connect(self._sync_presence)
        self._presence_sent = None

        self._init_ui()

        # Bandeja del sistema (siempre: es la forma de seguir recibiendo avisos con la ventana cerrada)
        self._quitting = False
        self._tray = None
        self._base_icon = QApplication.instance().windowIcon()
        self._ensure_tray()

        self.notifications = NotificationCenter(
            is_viewing=self._is_viewing_chat, is_foreground=self._is_foreground,
            alert_widget=self, tray_provider=lambda: self._tray, parent=self)
        self.notifications.open_chat_requested.connect(lambda jid: self.handle_uri(f"magsapp://open?chat={jid}"))
        self._badge = TaskbarBadge()

        self._connect_signals()

        # Estados programados: evitar la suspensión mientras haya pendientes
        self._keep_awake_on = False
        self._awake_timer = QTimer(self)
        self._awake_timer.setInterval(60000)
        self._awake_timer.timeout.connect(self._update_keep_awake)
        self._awake_timer.start()
        QTimer.singleShot(15000, self._update_keep_awake)

        # Iniciar servidores en segundo plano; el gateway queda vigilado (si se cae, se relanza solo)
        self.webhook_server.start()
        threading.Thread(target=self._start_gateway, daemon=True, name="magsapp-gateway-start").start()

        # La comprobación de vinculación la hace main.py dentro de la pantalla de carga

    def _start_gateway(self):
        self.gateway.start()
        self.gateway.start_watchdog()

    def _init_ui(self):
        central_widget = QWidget()
        central_widget.setObjectName("centralArea")
        self.setCentralWidget(central_widget)

        main_layout = QHBoxLayout(central_widget)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # 1. Barra de Navegación Vertical (Rail izquierdo con solo Estados, Chats y Configuración)
        self.nav_rail = NavRailWidget()
        main_layout.addWidget(self.nav_rail)

        # 2. Contenedor de Vistas Principales (QStackedWidget)
        self.stacked_widget = QStackedWidget()

        # Vista 0: Chats (Splitter horizontal)
        self.splitter = QSplitter(Qt.Horizontal)
        self.splitter.setHandleWidth(1)

        self.chat_list_panel = ChatListPanel()
        self.splitter.addWidget(self.chat_list_panel)

        self.chat_view_panel = ChatViewPanel()
        self.splitter.addWidget(self.chat_view_panel)

        self.chat_list_panel.setMinimumWidth(260)
        self.splitter.setSizes([320, 790])
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setCollapsible(0, False)
        self.splitter.setCollapsible(1, False)

        self.stacked_widget.addWidget(self.splitter)  # Índice 0: Chats

        # Vista 1: Estados (⭕)
        self.status_panel = StatusPanel()
        self.stacked_widget.addWidget(self.status_panel)  # Índice 1: Estados

        # Vista 2: Configuración (⚙️)
        self.settings_panel = SettingsPanel()
        self.stacked_widget.addWidget(self.settings_panel)  # Índice 2: Configuración

        main_layout.addWidget(self.stacked_widget, stretch=1)

        # Atajos de teclado
        def sc(keys, fn):
            QShortcut(QKeySequence(keys), self, activated=fn)
        sc("Ctrl+1", lambda: self.nav_rail.set_tab("chats"))
        sc("Ctrl+2", lambda: self.nav_rail.set_tab("status"))
        sc("Ctrl+,", lambda: self.nav_rail.set_tab("settings"))
        sc("Ctrl+N", self._on_new_chat_requested)
        sc("Ctrl+K", self._focus_chat_search)
        sc("Ctrl+F", self._find_in_chat)
        sc("Ctrl+Q", self.quit_app)

    # ── Arranque ──

    def restore_geometry_and_show(self):
        """Tamaño, posición y ancho de la lista de chats como se dejaron la última vez."""
        try:
            geo = self._settings.value("window/geometry")
            if geo is not None:
                self.restoreGeometry(geo)
            split = self._settings.value("window/splitter")
            if split is not None:
                self.splitter.restoreState(split)
        except Exception:
            pass
        if self._settings.value("window/maximized", False) in (True, "true"):
            self.showMaximized()
        else:
            self.show()
        self.raise_()
        self.activateWindow()

    def after_startup(self):
        self._update_unread_ui(self._unread_total)
        self._presence_timer.start()
        QTimer.singleShot(4000, self.status_panel.refresh_contacts_badge)

    def _save_geometry(self):
        try:
            self._settings.setValue("window/maximized", self.isMaximized())
            if not self.isMaximized():
                self._settings.setValue("window/geometry", self.saveGeometry())
            self._settings.setValue("window/splitter", self.splitter.saveState())
        except Exception:
            pass

    def _focus_chat_search(self):
        self.nav_rail.set_tab("chats")
        self.chat_list_panel.focus_search()

    def _find_in_chat(self):
        if self.stacked_widget.currentWidget() is self.splitter and self.chat_view_panel.current_chat:
            self.chat_view_panel._focus_search()
        else:
            self._focus_chat_search()

    def _update_window_title(self, unread: int):
        self.setWindowTitle(f"({unread}) {APP_NAME}" if unread > 0 else APP_NAME)

    def _connect_signals(self):
        # Conexiones del Rail Lateral
        self.nav_rail.tab_changed.connect(self._on_tab_changed)
        self.nav_rail.qr_requested.connect(self._open_qr_dialog)
        self.nav_rail.self_analysis_requested.connect(self._open_self_analysis)
        self.chat_list_panel.unread_total_changed.connect(self.nav_rail.set_unread_badge)
        self.chat_list_panel.unread_total_changed.connect(self._update_window_title)
        self.chat_list_panel.unread_total_changed.connect(self._update_unread_ui)

        # Conexión de Configuración
        self.settings_panel.qr_requested.connect(self._open_qr_dialog)
        self.settings_panel.font_scale_changed.connect(self.chat_view_panel.set_font_scale)
        self.chat_view_panel.font_scale_changed.connect(self.settings_panel.set_font_scale_silently)
        self.settings_panel.test_notification_requested.connect(self._test_notification)

        # Conexiones internas de Paneles
        self.chat_list_panel.chat_selected.connect(self._on_chat_selected)
        self.chat_list_panel.open_qr_requested.connect(self._open_qr_dialog)
        self.chat_list_panel.new_chat_requested.connect(self._on_new_chat_requested)
        self.chat_list_panel.refresh_requested.connect(self._on_refresh_requested)
        self.chat_list_panel.chat_deleted.connect(self._on_chat_removed)
        self.chat_list_panel.chat_cleared.connect(self._on_chat_cleared)
        self.chat_list_panel.all_marked_read.connect(self.notifications.all_read)
        self.chat_list_panel.chat_marked_read.connect(self.notifications.chat_opened)
        self.chat_view_panel.message_sent.connect(self._on_message_sent)
        self.chat_view_panel.chat_open_requested.connect(self._on_chat_selected)
        self.chat_view_panel.ai_state_changed.connect(self.chat_list_panel.set_ai_state)
        self.chat_view_panel.contact_updated.connect(lambda data: self.chat_list_panel.reload_chats(select_jid=data.get("jid")))
        self.status_panel.unseen_changed.connect(self.nav_rail.set_status_badge)
        self.status_panel.open_chat_requested.connect(lambda jid: self.handle_uri(f"magsapp://open?chat={jid}"))

        # Conexiones del servidor webhook
        signals.message_received.connect(self._on_incoming_message)
        signals.message_received.connect(self.notifications.on_message)
        signals.connection_changed.connect(self._on_connection_changed)
        signals.history_synced.connect(self._on_history_synced)
        signals.avatar_updated.connect(self._on_avatar_updated)
        signals.message_status.connect(self._on_message_status)
        # Métodos (no lambdas): así Qt los ejecuta en el hilo de la interfaz y no en el del servidor web
        signals.reaction_changed.connect(self._on_reaction_changed)
        signals.presence_changed.connect(self.chat_view_panel.on_presence)
        signals.call_event.connect(self.notifications.on_call)
        signals.call_event.connect(self._on_call_event)
        signals.contact_status.connect(self.status_panel.on_contact_status)
        signals.app_activate.connect(self.handle_uri)

    def _check_initial_connection(self):
        st = self.gateway.get_status()
        if st.get("status") != "connected":
            self._open_qr_dialog()

    def _on_chat_selected(self, chat_data: dict):
        self.chat_view_panel.load_chat(chat_data)
        self.chat_list_panel.reload_chats(select_jid=chat_data.get("jid"))
        self.notifications.chat_opened(chat_data.get("jid"))

    def _is_current(self, jid: str) -> bool:
        cur = self.chat_view_panel.current_chat
        return bool(cur and jid and cur.get("jid") == jid)

    # ── Estado de la ventana (para decidir notificaciones y "en línea") ──

    def _is_foreground(self) -> bool:
        return self.isVisible() and not self.isMinimized() and self.isActiveWindow()

    def _is_viewing_chat(self, jid: str) -> bool:
        return (self._is_foreground() and self.stacked_widget.currentWidget() is self.splitter
                and self._is_current(jid))

    def changeEvent(self, event):
        super().changeEvent(event)
        if not hasattr(self, "notifications"):
            return   # todavía construyéndose
        if event.type() in (QEvent.ActivationChange, QEvent.WindowStateChange):
            self._presence_timer.start()
            if self._is_foreground() and self.chat_view_panel.current_chat:
                # Volver a la ventana con un chat abierto = leerlo (tildes azules y fuera la notificación)
                jid = self.chat_view_panel.current_chat.get("jid")
                if self.stacked_widget.currentWidget() is self.splitter and jid:
                    newly = mark_chat_as_read(jid)
                    if newly:
                        self.gateway.send_read_receipts(jid, [mid for mid, _ in newly])
                        self._chat_reload_timer.start()
                    self.notifications.chat_opened(jid)

    def _sync_presence(self):
        want = self._is_foreground()
        if want != self._presence_sent:
            self._presence_sent = want
            self.gateway.set_presence(want)

    # ── Notificaciones / magsapp:// ──

    def handle_uri(self, uri: str):
        """Pedidos que llegan de las notificaciones o de otra instancia: magsapp://open?chat=..., reply, read, show."""
        try:
            parsed = urlparse(uri or "")
            action = (parsed.netloc or parsed.path.strip("/") or "show").lower()
            chat = (parse_qs(parsed.query).get("chat") or [""])[0]
        except Exception:
            action, chat = "show", ""
        if action == "read" and chat:
            newly = mark_chat_as_read(chat)
            if newly:
                self.gateway.send_read_receipts(chat, [mid for mid, _ in newly])
            self.notifications.chat_opened(chat)
            self._chat_reload_timer.start()
            return
        self.show_from_background()
        if action in ("open", "reply") and chat:
            data = get_chat(chat) or get_or_create_chat(chat)
            self.nav_rail.set_tab("chats")
            self._on_chat_selected(data)
            if action == "reply":
                QTimer.singleShot(50, self.chat_view_panel.input_text.setFocus)
        elif action == "status":
            self.nav_rail.set_tab("status")
        elif action == "link":
            QTimer.singleShot(100, self._open_qr_dialog)

    def show_from_background(self):
        if not self.isVisible():
            self.restore_geometry_and_show()
        if self.isMinimized():
            self.showNormal() if not self._settings.value("window/maximized", False) in (True, "true") else self.showMaximized()
        self.raise_()
        self.activateWindow()
        if sys.platform == "win32":
            try:
                import ctypes
                ctypes.windll.user32.SetForegroundWindow(int(self.winId()))
            except Exception:
                pass

    def _test_notification(self):
        """Botón de Configuración: muestra una notificación de ejemplo."""
        ok = self.notifications.toaster.show_message(
            "magsapp-prueba", "MAGSAPP", "Así se ven las notificaciones de mensajes nuevos.",
            attribution="Prueba", sound=get_pref("notify_sound"), with_actions=False)
        if not ok:
            self.notifications.notify_simple("MAGSAPP", "Así se ven las notificaciones de mensajes nuevos.")
        if self.notifications.toaster.disabled_by_user:
            QMessageBox.information(
                self, "Notificaciones",
                "Windows tiene desactivadas las notificaciones de MAGSAPP.\n\n"
                "Activalas en Configuración de Windows › Sistema › Notificaciones › MAGSAPP.")

    def _on_call_event(self, data: dict):
        if data.get("status") in ("timeout", "terminate"):
            self._chat_reload_timer.start()

    def _on_chat_removed(self, jid: str):
        if self._is_current(jid):
            self.chat_view_panel.show_empty_state()

    def _on_chat_cleared(self, jid: str):
        if self._is_current(jid):
            self.chat_view_panel.refresh_messages()

    def _on_tab_changed(self, tab_name: str):
        if tab_name == "chats":
            self.stacked_widget.setCurrentWidget(self.splitter)
        elif tab_name == "status":
            self.stacked_widget.setCurrentWidget(self.status_panel)
        elif tab_name == "settings":
            self.settings_panel.refresh_whatsapp_status()
            self.stacked_widget.setCurrentWidget(self.settings_panel)

    def _open_self_analysis(self):
        from app.ui.psych_dialog import PsychAnalysisDialog
        PsychAnalysisDialog(focus="interno", parent=self).exec()

    def _open_qr_dialog(self):
        if self._qr_open:
            return
        self._qr_open = True
        try:
            self.show_from_background()
            dlg = QRDialog(self)
            dlg.exec()
        finally:
            self._qr_open = False
        self.chat_list_panel.reload_chats()
        self.settings_panel.refresh_whatsapp_status()

    def _on_new_chat_requested(self):
        """Abre la agenda de contactos y diálogo modal de nuevo chat oficial de WhatsApp."""
        from app.ui.contact_dialog import NewChatDialog
        dlg = NewChatDialog(self)
        def on_chat_ready(chat_data):
            jid = chat_data.get("jid")
            self.chat_list_panel.reload_chats(select_jid=jid)
            self.chat_view_panel.load_chat(chat_data)
        dlg.chat_ready.connect(on_chat_ready)
        dlg.exec()

    def _on_refresh_requested(self):
        active = self.chat_view_panel.current_chat
        if active:
            self.chat_view_panel.refresh_messages()

    def _on_message_sent(self, data: dict):
        self.chat_list_panel.reload_chats(select_jid=data.get("chat_jid"))

    def _on_incoming_message(self, data: dict):
        chat_jid = data.get("chat_jid")
        active_chat = self.chat_view_panel.current_chat

        if active_chat and (active_chat.get("jid") == chat_jid or (chat_jid and chat_jid.startswith(active_chat.get("jid").split("@")[0]))):
            self.chat_view_panel.refresh_messages(scroll_to_bottom=False)
            # Mensaje que llega al chat que estás mirando: queda leído (como en la app oficial)
            if self._is_viewing_chat(active_chat.get("jid")) and not data.get("from_me"):
                newly = mark_chat_as_read(active_chat.get("jid"))
                if newly:
                    self.gateway.send_read_receipts(active_chat.get("jid"), [mid for mid, _ in newly])

        self._pending_select_jid = active_chat.get("jid") if active_chat else None
        self._chat_reload_timer.start()

    def _do_chat_list_reload(self):
        jid = getattr(self, "_pending_select_jid", None)
        if jid is None and self.chat_view_panel.current_chat:
            jid = self.chat_view_panel.current_chat.get("jid")
        self.chat_list_panel.reload_chats(select_jid=jid)

    def _on_reaction_changed(self, data: dict):
        self.chat_view_panel._on_media_event(data.get("message_id"))

    def _on_message_status(self, data: dict):
        if self.chat_view_panel.current_chat:
            self.chat_view_panel._on_media_event(data.get("message_id"))

    def _on_history_synced(self):
        self._history_sync_timer.start()

    def _do_history_sync_reload(self):
        active_chat = self.chat_view_panel.current_chat
        current_jid = active_chat.get("jid") if active_chat else None
        self.chat_list_panel.reload_chats(select_jid=current_jid)
        if active_chat:
            self.chat_view_panel.refresh_messages(scroll_to_bottom=False)

    def _on_avatar_updated(self, data: dict):
        jid = data.get("jid")
        avatar_path = data.get("avatar_path")
        if jid and avatar_path:
            self.chat_view_panel.update_avatar_if_current(jid, avatar_path)
            self.chat_list_panel.update_avatar_in_place(jid, avatar_path)

    def _on_connection_changed(self, data: dict):
        self.settings_panel.refresh_whatsapp_status()
        status = data.get("status")
        self.chat_list_panel.set_connection_state(status)
        if status == "connected":
            self._presence_sent = None
            self._presence_timer.start()
        elif status == "logged_out":
            # Se desvinculó desde el celular: avisar y ofrecer el QR nuevo
            if self.isVisible() and not self.isMinimized():
                QTimer.singleShot(1500, self._open_qr_dialog)
            else:
                self.notifications.notify_simple(
                    "Se cerró la sesión de WhatsApp en MAGSAPP",
                    "Tocá acá para volver a vincular con el código QR.", uri="magsapp://link", tag="link", sound=True)

    # ── Contador de no leídos: título, barra de tareas y bandeja ──

    def _update_unread_ui(self, unread: int):
        self._unread_total = unread
        try:
            self._badge.set_count(int(self.winId()), unread)
        except Exception:
            pass
        if self._tray:
            self._tray.setIcon(self._tray_icon(unread))
            self._tray.setToolTip(f"{APP_NAME}\n{unread} chats sin leer" if unread else APP_NAME)

    def _tray_icon(self, unread: int) -> QIcon:
        base = self._base_icon if not self._base_icon.isNull() else self.windowIcon()
        if unread <= 0 or base.isNull():
            return base
        pm = base.pixmap(64, 64)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(C["danger"]))
        p.drawEllipse(QRectF(34, 0, 30, 30))
        p.setPen(QColor("#ffffff"))
        f = QFont("Segoe UI")
        f.setBold(True)
        f.setPixelSize(19 if unread < 10 else 15)
        p.setFont(f)
        p.drawText(QRectF(34, 0, 30, 30), Qt.AlignCenter, "9+" if unread > 9 else str(unread))
        p.end()
        return QIcon(pm)

    def showEvent(self, event):
        super().showEvent(event)
        # La barra de tareas recrea el botón al mostrar la ventana: volver a poner el contador
        self._badge.reset()
        QTimer.singleShot(300, lambda: self._update_unread_ui(self._unread_total))

    # ── Segundo plano: los estados programados necesitan que el gateway siga vivo ──

    def _update_keep_awake(self):
        """Pide a Windows que no se suspenda mientras haya estados por publicar (como un reproductor de video).
        No impide apagar la PC ni la suspensión al cerrar la tapa de una notebook."""
        if sys.platform != "win32":
            return
        want = self.gateway.pending_scheduled_count() > 0
        if want == self._keep_awake_on:
            return
        import ctypes
        ES_CONTINUOUS, ES_SYSTEM_REQUIRED = 0x80000000, 0x00000001
        ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS | (ES_SYSTEM_REQUIRED if want else 0))
        self._keep_awake_on = want

    def _ensure_tray(self):
        if self._tray or not QSystemTrayIcon.isSystemTrayAvailable():
            return self._tray
        self._tray = QSystemTrayIcon(self._base_icon if not self._base_icon.isNull() else self.windowIcon(), self)
        self._tray.setToolTip(APP_NAME)
        menu = QMenu(self)
        menu.addAction("Abrir MAGSAPP", self.show_from_background)
        menu.addAction("Estados", lambda: (self.show_from_background(), self.nav_rail.set_tab("status")))
        menu.addSeparator()
        self._act_notify = menu.addAction("Notificaciones activadas")
        self._act_notify.setCheckable(True)
        self._act_notify.setChecked(get_pref("notify_enabled"))
        self._act_notify.toggled.connect(self._toggle_notifications)
        menu.addSeparator()
        menu.addAction("Salir", self.quit_app)
        menu.aboutToShow.connect(lambda: self._act_notify.setChecked(get_pref("notify_enabled")))
        self._tray.setContextMenu(menu)
        self._tray.activated.connect(self._on_tray_activated)
        self._tray.show()
        return self._tray

    def _toggle_notifications(self, on: bool):
        set_pref("notify_enabled", on)
        self.settings_panel.load_prefs()

    def _on_tray_activated(self, reason):
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            if self._is_foreground():
                self._hide_to_tray()
            else:
                self.show_from_background()

    def _hide_to_tray(self, pending: int = 0):
        tray = self._ensure_tray()
        if not tray:
            return False
        self._save_geometry()
        tray.show()
        self.hide()
        self._presence_timer.start()
        if pending:
            msg = f"Sigue funcionando en segundo plano para publicar {pending} estado(s)."
        elif not self._told_about_tray and not self._settings.value("tray/told", False):
            msg = "MAGSAPP sigue abierto en la bandeja y te va a avisar de los mensajes nuevos."
            self._settings.setValue("tray/told", True)
        else:
            msg = ""
        self._told_about_tray = True
        if msg:
            self.notifications.notify_simple(APP_NAME, msg, tag="tray")
        return True

    def _restore_from_tray(self):
        self.show_from_background()

    def quit_app(self):
        pending = self.gateway.pending_scheduled_count()
        if pending and QMessageBox.question(
            self, APP_NAME, f"Quedan {pending} estado(s) sin publicar. Si salís no se van a publicar.\n\n¿Salir igual?"
        ) != QMessageBox.Yes:
            return
        self._quitting = True
        self.close()

    def _quit_from_tray(self):
        self.quit_app()

    def closeEvent(self, event):
        if not self._quitting:
            pending = self.gateway.pending_scheduled_count()
            if get_pref("close_to_tray") and self._hide_to_tray(pending):
                event.ignore()
                return
            if pending:
                box = QMessageBox(self)
                box.setIcon(QMessageBox.Question)
                box.setWindowTitle(APP_NAME)
                box.setText(f"Tenés {pending} estado(s) programado(s).")
                box.setInformativeText("Si cerrás MAGSAPP no se van a publicar. Podés dejarlo funcionando en la bandeja.")
                btn_tray = box.addButton("Dejar en la bandeja", QMessageBox.AcceptRole)
                btn_quit = box.addButton("Cerrar igual", QMessageBox.DestructiveRole)
                box.addButton("Cancelar", QMessageBox.RejectRole)
                box.setDefaultButton(btn_tray)
                box.exec()
                clicked = box.clickedButton()
                if clicked is btn_tray and self._hide_to_tray(pending):
                    event.ignore()
                    return
                if clicked is not btn_quit:
                    event.ignore()
                    return
        self._save_geometry()
        self.shutdown()
        super().closeEvent(event)
        QApplication.instance().quit()

    def shutdown(self):
        """Cierre ordenado (una sola vez): bandeja, notificaciones, webhook y gateway."""
        if getattr(self, "_shut_down", False):
            return
        self._shut_down = True
        try:
            if self._tray:
                self._tray.hide()
            self.notifications.close()
            self.webhook_server.stop()
            self.gateway.stop()
        except Exception as e:
            print(f"[MAGSAPP] Error al cerrar: {e}")

    def on_session_ending(self, *_):
        """Windows se apaga o cierra sesión: salir de verdad (la bandeja no debe frenar el apagado)."""
        self._quitting = True
        self._save_geometry()
