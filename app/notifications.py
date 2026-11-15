"""
Advanced WhatsApp - Centro de notificaciones
==================================
Decide cuándo y cómo avisar de un mensaje nuevo, como WhatsApp Desktop:
- Ventana en segundo plano (minimizada, en la bandeja o detrás de otra) → notificación de Windows con la foto
  del contacto, el texto y botones (Responder / Marcar como leído), y el icono de la barra de tareas parpadea.
- Ventana al frente pero en otro chat → solo un sonido suave.
- Mirando ese mismo chat → nada.
- Nunca: mensajes propios, chats silenciados, ediciones/borrados, ni la tanda de mensajes viejos que llega
  al abrir la app.
Una notificación por chat: la nueva reemplaza a la anterior ("3 mensajes nuevos") y se borra al abrir el chat.
"""

import os
import time
from typing import Callable, Dict, Optional

from PySide6.QtCore import QObject, QUrl, Signal
from PySide6.QtWidgets import QApplication, QSystemTrayIcon

from app.config import get_pref
from app import chat_lock
from app.db import get_chat, is_chat_muted, find_chat_avatar, _preview_for, get_message
from app.windows_integration import Toaster, thumbnail_from_media_raw, ICON_PNG

# Mensajes enviados antes de abrir Advanced WhatsApp: no se anuncian (ya se ven como "no leídos" en la lista)
BACKLOG_GRACE = 30
MAX_AGE = 12 * 3600


def _display_name(chat: Optional[dict], data: dict) -> str:
    from app.ui.chat_list import resolve_display_name
    if chat:
        try:
            return resolve_display_name(chat)
        except Exception:
            pass
    return data.get("push_name") or ("+" + data["phone"] if data.get("phone") else "Mensaje nuevo")


class NotificationCenter(QObject):
    open_chat_requested = Signal(str)     # jid (clic en el globo de la bandeja, si no hay toasts)

    def __init__(self, is_viewing: Callable[[str], bool], is_foreground: Callable[[], bool],
                 alert_widget=None, tray_provider: Callable[[], Optional[QSystemTrayIcon]] = None, parent=None):
        super().__init__(parent)
        self._is_viewing = is_viewing
        self._is_foreground = is_foreground
        self._alert_widget = alert_widget
        self._tray_provider = tray_provider
        self.toaster = Toaster()
        self.started_at = time.time()
        self._counts: Dict[str, int] = {}       # chat -> mensajes anunciados desde la última vez que se abrió
        self._last_balloon_chat: Optional[str] = None
        self._sound = None
        self._tray_signal_connected = False
        if get_pref("notify_enabled"):
            self.toaster.warm_up()

    # ── Sonido suave con la ventana al frente ──

    def _play_soft_sound(self):
        if not get_pref("notify_sound"):
            return
        try:
            if self._sound is None:
                from PySide6.QtMultimedia import QSoundEffect
                wav = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Media", "Windows Notify Messaging.wav")
                if not os.path.exists(wav):
                    wav = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Media", "Windows Notify.wav")
                self._sound = QSoundEffect(self)
                self._sound.setSource(QUrl.fromLocalFile(wav))
                self._sound.setVolume(0.45)
            self._sound.play()
        except Exception:
            pass

    # ── Mensajes ──

    def on_message(self, data: dict):
        try:
            self._on_message(data)
        except Exception as e:
            print(f"[Advanced WhatsApp Notificaciones] Error: {e}")

    def _on_message(self, data: dict):
        if not data.get("is_new") or data.get("from_me") or not get_pref("notify_enabled"):
            return
        if data.get("already_read"):
            return   # ya lo leíste en el celular
        if data.get("upsert_type") not in (None, "notify"):
            return   # "append": historial o copias de otros dispositivos
        chat_jid = data.get("chat_jid") or ""
        if chat_lock.is_hidden(chat_jid):
            return   # chat oculto: ninguna notificación, ni siquiera "Nuevo mensaje"
        ts = int(data.get("timestamp") or 0)
        now = time.time()
        if ts and (ts < self.started_at - BACKLOG_GRACE or now - ts > MAX_AGE):
            return
        if chat_jid.endswith("@g.us") and not get_pref("notify_groups"):
            return
        chat = get_chat(chat_jid)
        if is_chat_muted(chat):
            return
        if self._is_viewing(chat_jid):
            return
        if self._is_foreground():
            self._play_soft_sound()
            return

        count = self._counts.get(chat_jid, 0) + 1
        self._counts[chat_jid] = count
        title = _display_name(chat, data)
        media_type = data.get("media_type") or "text"
        locked = chat_lock.is_locked(chat_jid)   # chat bloqueado: nunca se muestra el contenido
        show_preview = get_pref("notify_preview") and not locked
        if show_preview:
            body = _preview_for(media_type, (data.get("text") or "").strip()) or "Mensaje nuevo"
        else:
            body = chat_lock.LOCKED_NOTIFICATION if locked else "Mensaje nuevo"
        attribution = f"{count} mensajes nuevos" if count > 1 else ""
        avatar = (chat or {}).get("avatar_path")
        if not avatar or not os.path.exists(avatar):
            avatar = find_chat_avatar(chat_jid)
        hero = None
        if show_preview and media_type in ("image", "video"):
            raw = data.get("media_raw")
            if not raw:
                msg = get_message(data.get("message_id") or "")
                raw = (msg or {}).get("media_raw")
            hero = thumbnail_from_media_raw(raw, data.get("message_id") or chat_jid)

        sound = get_pref("notify_sound")
        shown = False
        if self.toaster.available and not self.toaster.disabled_by_user:
            shown = self.toaster.show_message(chat_jid, title, body, avatar=avatar or ICON_PNG, hero=hero,
                                              attribution=attribution, sound=sound)
        if not shown:
            self._balloon(chat_jid, title, body)
        if self._alert_widget is not None:
            QApplication.alert(self._alert_widget, 0)   # parpadeo del icono en la barra de tareas

    def _balloon(self, chat_jid: str, title: str, body: str):
        """Respaldo si los toasts no están disponibles: el globo de la bandeja del sistema."""
        tray = self._tray_provider() if self._tray_provider else None
        if not tray or not tray.isVisible():
            return
        if not self._tray_signal_connected:
            tray.messageClicked.connect(lambda: self._last_balloon_chat and
                                        self.open_chat_requested.emit(self._last_balloon_chat))
            self._tray_signal_connected = True
        self._last_balloon_chat = chat_jid
        tray.showMessage(title, body, tray.icon(), 6000)

    def chat_opened(self, chat_jid: str):
        """Al abrir un chat se borran sus notificaciones (como en la app oficial)."""
        if not chat_jid:
            return
        if self._counts.pop(chat_jid, None) is not None:
            self.toaster.remove_chat(chat_jid)

    def all_read(self):
        if self._counts:
            self._counts.clear()
            self.toaster.clear_all()

    # ── Llamadas ──

    def on_call(self, data: dict):
        try:
            status = data.get("status")
            call_id = str(data.get("call_id") or "")
            chat_jid = data.get("chat_jid") or ""
            if status == "offer" and not data.get("offline"):
                if not get_pref("notify_calls") or is_chat_muted(get_chat(chat_jid)):
                    return
                name = data.get("name") or _display_name(get_chat(chat_jid), data)
                avatar = find_chat_avatar(chat_jid) or ICON_PNG
                if not self.toaster.show_call(call_id, chat_jid, name, bool(data.get("is_video")), avatar,
                                              sound=get_pref("notify_sound")):
                    self._balloon(chat_jid, name, "Llamada entrante · atendé desde el celular")
                if self._alert_widget is not None:
                    QApplication.alert(self._alert_widget, 0)
            elif status in ("accept", "reject", "timeout", "terminate"):
                self.toaster.remove_call(call_id)
        except Exception as e:
            print(f"[Advanced WhatsApp Notificaciones] Error en llamada: {e}")

    def notify_simple(self, title: str, body: str, uri: str = "", tag: str = "general", sound: bool = False):
        if not self.toaster.show_simple(title, body, uri=uri, tag=tag, sound=sound):
            tray = self._tray_provider() if self._tray_provider else None
            if tray and tray.isVisible():
                tray.showMessage(title, body, tray.icon(), 6000)

    def close(self):
        self.toaster.close()
