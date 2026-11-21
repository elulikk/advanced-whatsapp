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

from PySide6.QtCore import QObject, QTimer, QUrl, Signal
from PySide6.QtWidgets import QApplication, QSystemTrayIcon

from app.config import get_pref
from app import chat_lock
from app.db import get_chat, is_chat_muted, find_chat_avatar, _preview_for, get_message, get_revoked_original
from app.windows_integration import Toaster, thumbnail_from_media_raw, ICON_PNG, make_uri

# Mensajes enviados antes de abrir Advanced WhatsApp: no se anuncian (ya se ven como "no leídos" en la lista)
BACKLOG_GRACE = 30
MAX_AGE = 12 * 3600
# Un audio con transcripción automática espera hasta este tiempo para avisar ya con el texto (se lee en la notificación)
TRANSCRIPT_WAIT_MS = 15000
TRANSCRIPT_MAX_CHARS = 300
# Las vistas de un estado llegan en ráfaga: se espera un poco para avisar de todas juntas
STATUS_VIEW_BATCH_MS = 5000


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
        self._waiting_audio: Dict[str, dict] = {}   # message_id -> aviso armado que espera la transcripción
        self._pending_views: list = []   # nombres de quienes vieron un estado, a juntar en un solo aviso
        try:
            from app.media_service import media_signals
            media_signals.transcript_ready.connect(self._flush_audio)
            media_signals.job_failed.connect(self._flush_audio)
        except Exception as e:
            print(f"[Advanced WhatsApp Notificaciones] sin espera de transcripciones: {e}")
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
            if data.get("revoked"):
                self._on_deleted(data)
            else:
                self._on_message(data)
        except Exception as e:
            print(f"[Advanced WhatsApp Notificaciones] Error: {e}")

    def _can_alert(self, chat_jid: str, chat: Optional[dict]) -> bool:
        """Reglas comunes de los avisos que no son mensajes nuevos (borrados): mismas que un mensaje."""
        if not get_pref("notify_enabled") or chat_lock.is_hidden(chat_jid) or is_chat_muted(chat):
            return False
        if chat_jid.endswith("@g.us") and not get_pref("notify_groups"):
            return False
        return not self._is_viewing(chat_jid) and not self._is_foreground()

    def _on_deleted(self, data: dict):
        """«Ana eliminó un mensaje: …» con lo que decía (si se conservó)."""
        if data.get("from_me") or not get_pref("notify_deleted") or data.get("upsert_type") not in (None, "notify"):
            return
        chat_jid = data.get("chat_jid") or ""
        msg_id = data.get("message_id") or ""
        ts = int(data.get("timestamp") or 0)
        if not chat_jid or (ts and time.time() - ts > MAX_AGE):
            return
        chat = get_chat(chat_jid)
        if not self._can_alert(chat_jid, chat):
            return
        title = _display_name(chat, data)
        if chat_jid.endswith("@g.us") and data.get("push_name"):
            title = f"{data['push_name']} · {title}"
        body = "🗑 Eliminó un mensaje"
        orig = get_revoked_original(msg_id) if msg_id else None
        if orig and get_pref("notify_preview") and not chat_lock.is_locked(chat_jid):
            preview = _preview_for(orig.get("media_type") or "text", (orig.get("text") or "").strip())
            if preview:
                body += f": «{preview[:TRANSCRIPT_MAX_CHARS]}»"
        self.notify_simple(title, body, uri=make_uri("open", chat_jid), tag=f"del_{msg_id[:40]}",
                           sound=False)

    # ── «Ana vio tu estado» (se juntan los que llegan seguidos en un solo aviso) ──

    def on_status_view(self, data: dict):
        try:
            if not get_pref("notify_enabled") or not get_pref("notify_status_views") or self._is_foreground():
                return
            name = (data.get("name") or "").strip() or ("+" + data["phone"] if data.get("phone") else "")
            if not name:
                return
            if not self._pending_views:
                QTimer.singleShot(STATUS_VIEW_BATCH_MS, self._flush_status_views)
            if name not in self._pending_views:
                self._pending_views.append(name)
        except Exception as e:
            print(f"[Advanced WhatsApp Notificaciones] Error en vista de estado: {e}")

    def _flush_status_views(self):
        names, self._pending_views = self._pending_views, []
        if not names:
            return
        if len(names) == 1:
            body = f"{names[0]} vio tu estado"
        elif len(names) == 2:
            body = f"{names[0]} y {names[1]} vieron tu estado"
        else:
            body = f"{names[0]}, {names[1]} y {len(names) - 2} más vieron tu estado"
        self.notify_simple("Tu estado", body, tag="status_views")

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

        toast = {"chat_jid": chat_jid, "title": title, "body": body, "avatar": avatar or ICON_PNG, "hero": hero,
                 "attribution": attribution}
        msg_id = data.get("message_id") or ""
        if show_preview and media_type == "audio" and msg_id and self._will_transcribe():
            # Se avisa cuando llega el texto (o a los 15 s): así la notificación, y el reloj, muestran lo que dice
            self._waiting_audio[msg_id] = toast
            QTimer.singleShot(TRANSCRIPT_WAIT_MS, lambda: self._flush_audio(msg_id))
            return
        self._show_toast(toast)

    @staticmethod
    def _will_transcribe() -> bool:
        try:
            from app.media_service import auto_transcribe_enabled
            from app.ai.transcription import has_transcription_key
            return auto_transcribe_enabled() and has_transcription_key()
        except Exception:
            return False

    def _flush_audio(self, msg_id: str):
        toast = self._waiting_audio.pop(msg_id, None)
        if toast is None:
            return
        try:
            text = ((get_message(msg_id) or {}).get("transcript") or "").strip()
            if text and not text.startswith("("):   # "(sin voz detectable)" no aporta
                if len(text) > TRANSCRIPT_MAX_CHARS:
                    text = text[:TRANSCRIPT_MAX_CHARS].rstrip() + "…"
                toast["body"] = f"🎤 {text}"
        except Exception as e:
            print(f"[Advanced WhatsApp Notificaciones] transcripción para el aviso: {e}")
        self._show_toast(toast)

    def _show_toast(self, t: dict):
        sound = get_pref("notify_sound")
        shown = False
        if self.toaster.available and not self.toaster.disabled_by_user:
            shown = self.toaster.show_message(t["chat_jid"], t["title"], t["body"], avatar=t["avatar"], hero=t["hero"],
                                              attribution=t["attribution"], sound=sound)
        if not shown:
            self._balloon(t["chat_jid"], t["title"], t["body"])
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
