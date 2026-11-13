import hmac
import json
import threading
import time
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from PySide6.QtCore import QObject, Signal

from app.config import WEBHOOK_PORT, TOKEN_HEADER, get_gateway_token
from app.db import (
    save_message, save_contact, save_sync_batch, update_chat_avatar, update_message_status, set_reaction,
    message_exists, revoke_message_local, apply_message_edit, set_message_poll, is_sync_doc,
    mark_messages_read_elsewhere, mark_chat_read_elsewhere, mark_chat_unread_elsewhere
)


class WebhookSignals(QObject):
    message_received = Signal(dict)
    connection_changed = Signal(dict)
    history_synced = Signal()
    avatar_updated = Signal(dict)
    message_status = Signal(dict)
    reaction_changed = Signal(dict)
    presence_changed = Signal(dict)     # {chat_jid, presence: available|unavailable|composing|recording|paused}
    call_event = Signal(dict)           # {call_id, chat_jid, name, status: offer|timeout|reject|accept|terminate}
    contact_status = Signal(dict)       # estado nuevo (o borrado) de un contacto
    app_activate = Signal(str)          # otra instancia (o una notificación) pide abrir/mostrar algo: advancedwhatsapp://...
    read_elsewhere = Signal(list)       # [chat_jid]: chats que leíste (o marcaste no leídos) en el celular
    scheduled_changed = Signal(dict)    # {id, state: sent|failed|expired, to, chat_name, type, text, error}
    poll_changed = Signal(dict)         # encuesta con votos nuevos: {id, chat_jid, name, multi, options: [{name, votes, mine}]}
    bulk_changed = Signal(dict)         # terminó un envío a varios: {id, state, total, sent, failed, targets}
    sync_received = Signal(dict)        # llegó un documento de sincronización del celular (no se guarda como mensaje)


signals = WebhookSignals()


_TOKEN = get_gateway_token()

# Los lotes de historial pueden ser enormes: se guardan de a uno para no pelear por el lock de SQLite
_sync_lock = threading.Lock()


class _Server(ThreadingHTTPServer):
    # Con HTTPServer de un solo hilo y backlog de 5, los lotes de historial que llegaban en paralelo
    # se rechazaban ("fetch failed") y se perdían chats enteros.
    request_queue_size = 64
    daemon_threads = True


class WebhookHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        # Bytes y no str: compare_digest lanza TypeError con caracteres no ASCII en el encabezado
        if not hmac.compare_digest(self.headers.get(TOKEN_HEADER, "").encode("utf-8", "replace"),
                                   _TOKEN.encode("utf-8")):
            try:   # vaciar el cuerpo (acotado): cerrar con datos sin leer hace que el cliente reciba un RST en vez del 401
                self.rfile.read(min(max(0, int(self.headers.get("Content-Length") or 0)), 1 << 20))
            except (ValueError, OSError):
                pass
            self.send_response(401)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        try:
            content_length = max(0, int(self.headers.get("Content-Length") or 0))
        except ValueError:
            self.send_response(400)
            self.end_headers()
            return
        body = self.rfile.read(content_length).decode("utf-8", "replace")

        try:
            data = json.loads(body)
        except Exception:
            data = {}
        if not isinstance(data, dict):
            data = {}   # un JSON que no es un objeto (lista, número…) se trata como vacío

        if self.path == "/api/whatsapp/incoming":
            try:
                msg_id = data.get("message_id")
                chat_jid = data.get("chat_jid")
                sender_jid = data.get("sender_jid") or chat_jid
                push_name = data.get("push_name") or ""
                chat_name = data.get("chat_name")
                phone = data.get("phone")
                from_me = data.get("from_me", False)
                text = data.get("text", "")
                media_type = data.get("media_type", "text")
                media_path = data.get("media_path")
                media_raw = data.get("media_raw")
                quoted_text = data.get("quoted_text")
                quoted_sender = data.get("quoted_sender")
                timestamp = data.get("timestamp") or int(time.time())   # NOT NULL en la base

                if chat_jid and msg_id and is_sync_doc(media_type, text):
                    signals.sync_received.emit(dict(data))
                    self._ok()
                    return

                if chat_jid and msg_id:
                    # Guardar contacto solo si no es el propio usuario
                    # (los integrantes de un grupo no son contactos: no se agregan a la agenda)
                    if (push_name or phone) and not from_me and not chat_jid.endswith("@g.us") \
                            and push_name.lower() not in ["yo", "null", "none", "mag tecnologia"]:
                        save_contact(sender_jid, name=push_name, phone=phone, push_name=push_name)

                    existed = message_exists(msg_id)
                    # Borrado para todos / edición: se aplican sobre el original (no aparece un mensaje nuevo)
                    if existed and data.get("revoked"):
                        revoke_message_local(msg_id)
                        signals.message_received.emit({**data, "is_new": False})
                        self._ok()
                        return
                    if existed and data.get("edited"):
                        apply_message_edit(msg_id, text)
                        signals.message_received.emit({**data, "is_new": False})
                        self._ok()
                        return

                    # Guardar mensaje en base de datos
                    saved = save_message(
                        msg_id=msg_id,
                        chat_jid=chat_jid,
                        sender_jid=sender_jid,
                        sender_name=push_name,
                        from_me=from_me,
                        text=text,
                        timestamp=timestamp,
                        media_type=media_type,
                        media_path=media_path,
                        quoted_text=quoted_text,
                        quoted_sender=quoted_sender,
                        status="delivered" if not from_me else "sent",
                        chat_name=chat_name,
                        media_raw=media_raw,
                        already_read=bool(data.get("already_read"))
                    )

                    if saved and data.get("poll"):
                        set_message_poll(msg_id, data["poll"])

                    # Emitir señal a la UI únicamente si el mensaje fue almacenado válidamente.
                    # is_new: llegó ahora por primera vez (no es una edición, un borrado ni un reenvío del gateway)
                    if saved:
                        signals.message_received.emit({
                            **data, "is_new": not existed and not data.get("edited") and not data.get("revoked")})

                self._ok()
                return
            except Exception as e:
                # 500 = el gateway lo reintenta más tarde (queda en su bandeja de salida)
                _log_error("incoming", e)
                self.send_response(500)
                self.end_headers()
                return

        elif self.path == "/api/app/activate":
            signals.app_activate.emit(str(data.get("uri") or "advancedwhatsapp://show"))
            self._ok()
            return

        elif self.path == "/api/whatsapp/poll":
            try:
                if data.get("id"):
                    set_message_poll(data["id"], data)
                    signals.poll_changed.emit(data)
            except Exception as e:
                _log_error(self.path, e)
            self._ok()
            return

        elif self.path in ("/api/whatsapp/presence", "/api/whatsapp/call", "/api/whatsapp/contact_status",
                           "/api/whatsapp/scheduled", "/api/whatsapp/bulk"):
            sig = {"/api/whatsapp/bulk": signals.bulk_changed,
                   "/api/whatsapp/presence": signals.presence_changed,
                   "/api/whatsapp/call": signals.call_event,
                   "/api/whatsapp/contact_status": signals.contact_status,
                   "/api/whatsapp/scheduled": signals.scheduled_changed}[self.path]
            try:
                sig.emit(data)
            except Exception as e:
                _log_error(self.path, e)
            self._ok()
            return

        elif self.path == "/api/whatsapp/sync_batch":
            try:
                chats = data.get("chats", [])
                contacts = data.get("contacts", [])
                messages = data.get("messages", [])

                with _sync_lock:
                    save_sync_batch(chats=chats, contacts=contacts, messages=messages,
                                    rename_chats=bool(data.get("rename_chats")))

                # Notificar a la UI para recargar listas
                signals.history_synced.emit()

                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"status":"ok"}')
                return
            except Exception as e:
                _log_error("sync_batch", e)
                self.send_response(500)
                self.end_headers()
                return

        elif self.path == "/api/whatsapp/read_elsewhere":
            # Lo que leíste en el celular: mensajes sueltos o el chat entero (o marcado como no leído)
            try:
                changed = set()
                for ch in data.get("chats") or []:
                    jids = [j for j in (ch.get("chat_jid"), ch.get("alt_jid")) if j]
                    if ch.get("message_ids"):
                        changed.update(mark_messages_read_elsewhere(ch["message_ids"]))
                    for jid in jids:
                        if ch.get("all") and mark_chat_read_elsewhere(jid):
                            changed.add(jid)
                        elif ch.get("unread") and mark_chat_unread_elsewhere(jid):
                            changed.add(jid)
                if changed:
                    signals.read_elsewhere.emit(sorted(changed))
                self._ok()
                return
            except Exception as e:
                _log_error("read_elsewhere", e)   # 500 = el gateway lo reintenta
                self.send_response(500)
                self.end_headers()
                return

        elif self.path == "/api/whatsapp/avatar":
            try:
                jid = data.get("jid")
                avatar_path = data.get("avatar_path")
                if jid and avatar_path:
                    update_chat_avatar(jid, avatar_path)
                    signals.avatar_updated.emit(data)

                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"status":"ok"}')
                return
            except Exception:
                self.send_response(500)
                self.end_headers()
                return

        elif self.path == "/api/whatsapp/status":
            try:
                msg_id = data.get("message_id")
                status = data.get("status")
                if msg_id and status:
                    if update_message_status(msg_id, status):
                        signals.message_status.emit({"message_id": msg_id, "status": status})
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"status":"ok"}')
                return
            except Exception:
                self.send_response(500)
                self.end_headers()
                return

        elif self.path == "/api/whatsapp/reaction":
            try:
                msg_id = data.get("message_id")
                reactor = data.get("reactor_jid")
                if msg_id and reactor:
                    set_reaction(msg_id, reactor, data.get("emoji") or "", int(data.get("timestamp") or 0))
                    signals.reaction_changed.emit({"message_id": msg_id, "chat_jid": data.get("chat_jid")})
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"status":"ok"}')
                return
            except Exception:
                self.send_response(500)
                self.end_headers()
                return

        elif self.path == "/api/whatsapp/connection":
            try:
                signals.connection_changed.emit(data)
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"status":"ok"}')
                return
            except Exception:
                self.send_response(500)
                self.end_headers()
                return

        self.send_response(404)
        self.end_headers()

    def _ok(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"status":"ok"}')

    def log_message(self, format, *args):
        pass


def _log_error(where: str, exc: Exception) -> None:
    import traceback
    print(f"[Advanced WhatsApp WebhookServer] Error en {where}: {exc}\n{traceback.format_exc()}")


class WebhookServer:
    _instance = None

    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(self):
        self.server = None
        self.thread = None

    def start(self):
        if self.server is not None:
            return

        # Si el puerto quedó tomado un instante (cierre anterior), se reintenta unos segundos
        last = None
        for _ in range(10):
            try:
                self.server = _Server(("127.0.0.1", WEBHOOK_PORT), WebhookHandler)
                self.thread = threading.Thread(target=self.server.serve_forever, daemon=True,
                                               name="advancedwhatsapp-webhook")
                self.thread.start()
                return
            except Exception as e:
                last = e
                self.server = None
                time.sleep(0.5)
        print(f"[Advanced WhatsApp WebhookServer] Error iniciando en el puerto {WEBHOOK_PORT}: {last}")

    def stop(self):
        if self.server:
            try:
                self.server.shutdown()
                self.server.server_close()
            except Exception:
                pass
            self.server = None
            self.thread = None
