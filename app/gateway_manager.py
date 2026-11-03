import os
import sys
import shutil
import subprocess
import threading
import time
import mimetypes
import requests
from typing import Optional, Dict, Any

from app.config import (
    FROZEN, RUNTIME_DIR, GATEWAY_DIR, DATA_DIR, AUTH_DIR, MEDIA_DIR,
    GATEWAY_PORT, GATEWAY_URL, WEBHOOK_URL, CONNECTION_WEBHOOK_URL,
    TOKEN_HEADER, get_gateway_token
)

_http = requests.Session()
_http.headers[TOKEN_HEADER] = get_gateway_token()

RUNTIME_GATEWAY_DIR = os.path.join(DATA_DIR, "gateway")


def gateway_build_id() -> str:
    """Huella de los archivos del gateway: si cambia el código, se reinicia el proceso que haya quedado abierto."""
    import hashlib
    h = hashlib.sha1()
    for fname in ("server.js", "package.json"):
        try:
            with open(os.path.join(GATEWAY_DIR, fname), "rb") as f:
                h.update(f.read())
        except OSError:
            pass
    return h.hexdigest()[:12]


def find_node_executable() -> Optional[str]:
    bundled = os.path.join(RUNTIME_DIR, "node.exe")   # versión portable
    if os.path.isfile(bundled):
        return bundled
    node = shutil.which("node")
    if node:
        return node
    common_paths = [
        r"C:\Program Files\nodejs\node.exe",
        r"C:\Program Files (x86)\nodejs\node.exe",
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "node", "node.exe"),
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "AdvancedWhatsapp-Tools", "node", "node.exe"),  # INSTALAR_ADVANCEDWHATSAPP.ps1
    ]
    for p in common_paths:
        if os.path.isfile(p):
            return p
    return None


class GatewayManager:
    _instance = None

    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(self):
        self.process: Optional[subprocess.Popen] = None
        self.log_file = None
        self.is_running = False
        self._we_started = False
        self._stopping = False
        self._start_lock = threading.Lock()
        self._watchdog = None
        self.last_error: Optional[str] = None
        self.restarts = 0
        self.build = gateway_build_id()

    def ensure_runtime_files(self):
        """Copia los scripts actualizados del gateway a la carpeta de ejecución local."""
        os.makedirs(RUNTIME_GATEWAY_DIR, exist_ok=True)
        pkg_changed = False
        for fname in ["package.json", "server.js"]:
            src = os.path.join(GATEWAY_DIR, fname)
            dest = os.path.join(RUNTIME_GATEWAY_DIR, fname)
            if os.path.exists(src):
                if fname == "package.json":
                    try:
                        with open(src, "rb") as a, open(dest, "rb") as b:
                            pkg_changed = a.read() != b.read()
                    except OSError:
                        pkg_changed = True
                shutil.copy2(src, dest)
        return pkg_changed

    def ensure_dependencies(self, pkg_changed: bool) -> Optional[str]:
        """Instala las dependencias de Node si faltan. Retorna un mensaje de error o None."""
        modules = os.path.join(RUNTIME_GATEWAY_DIR, "node_modules", "@whiskeysockets")
        if os.path.isdir(modules) and not pkg_changed:
            return None
        node = find_node_executable()
        beside_node = os.path.join(os.path.dirname(node), "npm.cmd") if node else ""
        npm = beside_node if os.path.isfile(beside_node) else (shutil.which("npm") or shutil.which("npm.cmd"))
        if not npm:
            return "npm no está instalado o no se encuentra en el PATH."
        flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        try:
            with open(os.path.join(DATA_DIR, "npm_install.log"), "w", encoding="utf-8") as log:
                r = subprocess.run(
                    [npm, "install", "--omit=dev", "--no-audit", "--no-fund"],
                    cwd=RUNTIME_GATEWAY_DIR, stdout=log, stderr=subprocess.STDOUT,
                    creationflags=flags, timeout=600
                )
            if r.returncode != 0:
                return "Falló 'npm install' (ver ~/.magsapp/npm_install.log)."
        except Exception as e:
            return f"Falló 'npm install': {e}"
        return None

    @staticmethod
    def _rotate_log(path: str, max_bytes: int = 5 * 1024 * 1024):
        try:
            if os.path.getsize(path) > max_bytes:
                os.replace(path, path + ".old")
        except OSError:
            pass

    def start(self) -> Dict[str, Any]:
        """Inicia el gateway de Node.js si no está ya en ejecución (seguro de llamar desde varios hilos)."""
        with self._start_lock:
            if self._stopping:
                return {"success": False, "error": "La app se está cerrando"}
            res = self._start_locked()
        self.last_error = None if res.get("success") else res.get("error")
        if not res.get("success"):
            print(f"[Advanced WhatsApp Gateway] No se pudo iniciar: {res.get('error')}")
        return res

    def _shutdown_foreign(self, status: Dict[str, Any]) -> None:
        """Apaga un gateway que quedó abierto de otra versión (o de una sesión que se cerró mal)."""
        print(f"[Advanced WhatsApp Gateway] Reiniciando gateway viejo (build {status.get('build') or '?'} -> {self.build})")
        try:
            _http.post(f"{GATEWAY_URL}/shutdown", timeout=3)
        except Exception:
            pass
        for _ in range(20):
            time.sleep(0.3)
            if not self.get_status().get("online"):
                return

    def _start_locked(self) -> Dict[str, Any]:
        # 1. ¿Ya hay uno respondiendo? Si es de esta versión se adopta; si no, se reemplaza
        status = self.get_status()
        if status.get("online"):
            if status.get("build") == self.build:
                self.is_running = True
                self._we_started = True   # se apaga al salir: si no, queda huérfano con código viejo
                return {"success": True, "message": "Gateway ya activo", "status": status}
            self._shutdown_foreign(status)

        node_bin = find_node_executable()
        if not node_bin:
            return {"success": False, "error": "Node.js no está instalado o no se encuentra en el PATH."}

        if FROZEN:
            # La versión portable trae el gateway con sus node_modules ya instalados
            gateway_cwd = GATEWAY_DIR
        else:
            gateway_cwd = RUNTIME_GATEWAY_DIR
            try:
                dep_error = self.ensure_dependencies(self.ensure_runtime_files())
            except OSError as e:
                dep_error = f"No se pudieron copiar los archivos del gateway: {e}"
            if dep_error:
                return {"success": False, "error": dep_error}

        env = os.environ.copy()
        env["PORT"] = str(GATEWAY_PORT)
        env["PYTHON_WEBHOOK_URL"] = WEBHOOK_URL
        env["PYTHON_CONNECTION_URL"] = CONNECTION_WEBHOOK_URL
        env["AUTH_DIR"] = AUTH_DIR
        env["MEDIA_DIR"] = MEDIA_DIR
        env["ADVANCEDWHATSAPP_TOKEN"] = get_gateway_token()
        env["ADVANCEDWHATSAPP_BUILD"] = self.build
        env["ADVANCEDWHATSAPP_PARENT_PID"] = str(os.getpid())

        flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        log_path = os.path.join(DATA_DIR, "gateway.log")
        self._rotate_log(log_path)
        # Node escribe su propio log; acá solo queda lo que muera antes de poder escribirlo (errores de arranque)
        err_path = os.path.join(DATA_DIR, "gateway_stderr.log")
        self._rotate_log(err_path, 1024 * 1024)
        if self.log_file:
            try:
                self.log_file.close()
            except Exception:
                pass
        try:
            self.log_file = open(err_path, "a", encoding="utf-8", errors="replace")
        except OSError:
            self.log_file = None

        try:
            self.process = subprocess.Popen(
                [node_bin, "server.js"],
                cwd=gateway_cwd,
                env=env,
                creationflags=flags,
                stdout=subprocess.DEVNULL,
                stderr=self.log_file or subprocess.DEVNULL
            )

            self._we_started = True

            # Esperar a que el servidor Express levante
            for _ in range(8):
                time.sleep(0.5)
                st = self.get_status()
                if st.get("online"):
                    self.is_running = True
                    return {"success": True, "message": "Gateway iniciado exitosamente", "status": st}

            self.is_running = True
            return {"success": True, "message": "Gateway iniciado (arrancando socket)", "status": self.get_status()}
        except Exception as e:
            return {"success": False, "error": str(e)}

    # ── Vigilancia: si el gateway se cae, se vuelve a levantar solo ──

    def start_watchdog(self, interval: float = 10.0):
        if self._watchdog:
            return

        def _loop():
            failures = 0
            while not self._stopping:
                time.sleep(interval)
                if self._stopping:
                    break
                died = self.process is not None and self.process.poll() is not None
                if not died and self.get_status().get("online"):
                    failures = 0
                    continue
                failures += 1
                # El proceso terminó, o dos chequeos seguidos sin respuesta: se reinicia
                if died or failures >= 2:
                    code = self.process.poll() if self.process else None
                    print(f"[Advanced WhatsApp Gateway] El gateway no responde (código {code}); reiniciando…")
                    if self.process is not None and not died:
                        try:
                            self.process.kill()
                        except Exception:
                            pass
                    self.process = None
                    self.restarts += 1
                    self.start()
                    failures = 0

        self._watchdog = threading.Thread(target=_loop, daemon=True, name="advancedwhatsapp-gateway-watchdog")
        self._watchdog.start()

    def stop(self):
        """Detiene el proceso del gateway limpiamente."""
        self._stopping = True
        if not self._we_started:
            # El gateway ya existía antes de abrir la app: lo dejamos funcionando
            self.is_running = False
            return

        try:
            _http.post(f"{GATEWAY_URL}/shutdown", timeout=2)
        except Exception:
            pass
        self._we_started = False

        if self.process:
            try:
                self.process.terminate()
                self.process.wait(timeout=2)
            except Exception:
                try:
                    self.process.kill()
                except Exception:
                    pass
            self.process = None

        if self.log_file:
            try:
                self.log_file.close()
            except Exception:
                pass
            self.log_file = None

        self.is_running = False

    def get_status(self) -> Dict[str, Any]:
        try:
            r = _http.get(f"{GATEWAY_URL}/status", timeout=2)
            if r.status_code == 200:
                data = r.json()
                data["online"] = True
                return data
        except Exception:
            pass
        return {
            "online": False,
            "status": "disconnected",
            "phone": None,
            "push_name": None,
            "has_qr": False
        }

    def get_qr(self) -> Dict[str, Any]:
        try:
            r = _http.get(f"{GATEWAY_URL}/qr", timeout=3)
            if r.status_code == 200:
                return r.json()
        except Exception as e:
            return {"status": "error", "qr_data_url": None, "message": str(e)}
        return {"status": "error", "qr_data_url": None, "message": "No se pudo conectar al gateway"}

    def disconnect_session(self) -> Dict[str, Any]:
        return self.disconnect()

    def disconnect(self) -> Dict[str, Any]:
        try:
            r = _http.post(f"{GATEWAY_URL}/disconnect", timeout=5)
            if r.status_code == 200:
                return r.json()
        except Exception as e:
            return {"success": False, "error": str(e)}
        return {"success": False, "error": "Fallo al desconectar"}

    def send_message(
        self,
        to: str,
        text: str = "",
        image_path: Optional[str] = None,
        document_path: Optional[str] = None,
        document_name: Optional[str] = None,
        video_path: Optional[str] = None,
        audio_path: Optional[str] = None,
        sticker_path: Optional[str] = None,
        quoted: Optional[Dict[str, Any]] = None,
        audio_seconds: Optional[float] = None,
        audio_waveform: Optional[list] = None
    ) -> Dict[str, Any]:
        """`quoted` = {message_id, from_me, participant, text}: envía el mensaje como respuesta a otro.
        `audio_seconds` / `audio_waveform` (0..100): duración y onda que verá el destinatario de una nota de voz."""
        payload: Dict[str, Any] = {"to": to, "text": text}
        if quoted:
            payload["quoted"] = quoted
        if video_path:
            payload["video_path"] = video_path
        if audio_path:
            payload["audio_path"] = audio_path
            if audio_seconds:
                payload["audio_seconds"] = audio_seconds
            if audio_waveform:
                payload["audio_waveform"] = audio_waveform
        if sticker_path:
            payload["sticker_path"] = sticker_path
        if image_path:
            payload["image_path"] = image_path
        if document_path:
            payload["document_path"] = document_path
            mime = mimetypes.guess_type(document_name or document_path)[0]
            if mime:
                payload["mimetype"] = mime
            if document_name:
                payload["document_name"] = document_name

        try:
            # La subida a WhatsApp la hace el gateway: con archivos grandes tarda mucho más de 25 s
            size = 0
            for key in ("document_path", "image_path", "video_path", "audio_path", "sticker_path"):
                if payload.get(key) and os.path.isfile(payload[key]):
                    size = max(size, os.path.getsize(payload[key]))
            timeout = max(25, min(1800, 60 + size / (1024 * 1024) * 15))
            r = _http.post(f"{GATEWAY_URL}/send", json=payload, timeout=timeout)
            if r.status_code == 200:
                return r.json()
            return {"success": False, "error": f"HTTP {r.status_code}: {r.text}"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def send_reaction(self, to: str, message_id: str, emoji: str, from_me: bool = False,
                      participant: Optional[str] = None) -> Dict[str, Any]:
        """Reacciona a un mensaje; emoji vacío retira la reacción."""
        payload = {"to": to, "message_id": message_id, "emoji": emoji,
                   "from_me": bool(from_me), "participant": participant}
        try:
            r = _http.post(f"{GATEWAY_URL}/react", json=payload, timeout=15)
            if r.status_code == 200:
                return r.json()
            return {"success": False, "error": f"HTTP {r.status_code}: {r.text}"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _post_simple(self, endpoint: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        try:
            r = _http.post(f"{GATEWAY_URL}/{endpoint}", json=payload, timeout=15)
            if r.status_code == 200:
                return r.json()
            return {"success": False, "error": f"HTTP {r.status_code}: {r.text}"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def delete_message_for_all(self, to: str, message_id: str) -> Dict[str, Any]:
        """Elimina un mensaje propio para todos los participantes."""
        return self._post_simple("delete", {"to": to, "message_id": message_id})

    def send_read_receipts(self, chat_jid: str, message_ids) -> None:
        """Avisa a WhatsApp (en segundo plano) que se leyeron estos mensajes recibidos.
        WhatsApp respeta tu ajuste de privacidad: si tienes desactivadas las confirmaciones de lectura, no se envían."""
        ids = [i for i in (message_ids or []) if i]
        if not chat_jid or not ids:
            return

        def _run():
            for start in range(0, len(ids), 100):
                self._post_simple("read", {"to": chat_jid, "messages": ids[start:start + 100]})

        threading.Thread(target=_run, daemon=True, name="advancedwhatsapp-read-receipts").start()

    def pin_message(self, to: str, message_id: str, pinned: bool, from_me: bool = False,
                    participant: Optional[str] = None) -> Dict[str, Any]:
        """Fija o desfija un mensaje en el chat de WhatsApp."""
        return self._post_simple("pin", {"to": to, "message_id": message_id, "pinned": bool(pinned),
                                         "from_me": bool(from_me), "participant": participant})

    def download_media(self, message_id: str, media_type: str, media_raw: str) -> Dict[str, Any]:
        """Pide al gateway descargar el archivo de un mensaje multimedia. Devuelve {success, media_path|error}."""
        try:
            r = _http.post(
                f"{GATEWAY_URL}/media/download",
                json={"message_id": message_id, "media_type": media_type, "media_raw": media_raw},
                timeout=90
            )
            if r.status_code == 200:
                return r.json()
            try:
                err = r.json().get("error", r.text)
            except Exception:
                err = r.text
            return {"success": False, "error": err}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def update_contact(self, jid: str, name: str, phone: Optional[str] = None) -> Dict[str, Any]:
        """Notifica al gateway de Baileys para actualizar el contacto en su almacén en memoria."""
        try:
            r = _http.post(
                f"{GATEWAY_URL}/contact/update",
                json={"jid": jid, "name": name, "phone": phone},
                timeout=5
            )
            if r.status_code == 200:
                return r.json()
            return {"success": False}
        except Exception as e:
            return {"success": False, "error": str(e)}


    # ── Estados programados (la cola vive en el gateway) ──

    def _json_call(self, method: str, endpoint: str, payload: Optional[Dict[str, Any]] = None,
                       timeout: float = 15) -> Dict[str, Any]:
        try:
            r = _http.request(method, f"{GATEWAY_URL}/{endpoint}", json=payload, timeout=timeout)
            try:
                data = r.json()
            except Exception:
                data = {"error": r.text}
            if r.status_code == 200:
                data.setdefault("success", True)
                return data
            return {"success": False, "error": data.get("error") or f"HTTP {r.status_code}"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def list_scheduled_statuses(self) -> Dict[str, Any]:
        """Devuelve {success, items: [...], audience: n} o {success: False, error}."""
        return self._json_call("GET", "status-schedule", timeout=5)

    def schedule_status(self, kind: str, at: int, text: str = "", media_path: Optional[str] = None,
                        background: Optional[str] = None, font: Optional[int] = None) -> Dict[str, Any]:
        """Programa un estado. `kind` = text | image | video; `at` = epoch en segundos (0 = ahora)."""
        payload: Dict[str, Any] = {"type": kind, "text": text, "at": int(at)}
        if media_path:
            payload["media_path"] = media_path
        if background:
            payload["background"] = background
        if font is not None:
            payload["font"] = int(font)
        return self._json_call("POST", "status-schedule", payload, timeout=60)

    def update_scheduled_status(self, item_id: str, at: Optional[int] = None,
                                text: Optional[str] = None) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"id": item_id}
        if at:
            payload["at"] = int(at)
        if text is not None:
            payload["text"] = text
        return self._json_call("POST", "status-schedule/update", payload)

    def cancel_scheduled_status(self, item_id: str) -> Dict[str, Any]:
        return self._json_call("POST", "status-schedule/cancel", {"id": item_id})

    def remove_scheduled_status(self, item_id: str) -> Dict[str, Any]:
        return self._json_call("POST", "status-schedule/remove", {"id": item_id})

    def pending_scheduled_count(self) -> int:
        res = self.list_scheduled_statuses()
        return sum(1 for s in res.get("items", []) if s.get("state") in ("pending", "sending"))

    # ── Mis estados activos (últimas 24 h) ──

    def list_my_statuses(self) -> Dict[str, Any]:
        """Devuelve {success, items: [{id, type, text, background, timestamp, thumb, views, viewers}]}."""
        return self._json_call("GET", "my-statuses", timeout=5)

    def sync_my_statuses(self) -> Dict[str, Any]:
        """Le pide al celular que reenvíe los estados de las últimas 24 h."""
        return self._json_call("POST", "my-statuses/sync", {}, timeout=20)

    def my_status_media(self, item_id: str) -> Dict[str, Any]:
        """Descarga la foto o el video de un estado propio. Devuelve {success, media_path|error}."""
        return self._json_call("POST", "my-statuses/media", {"id": item_id}, timeout=90)

    def delete_my_status(self, item_id: str) -> Dict[str, Any]:
        """Elimina el estado para todos los contactos."""
        return self._json_call("POST", "my-statuses/delete", {"id": item_id}, timeout=60)

    # ── Estados de los contactos (últimas 24 h) ──

    def list_contact_statuses(self) -> Dict[str, Any]:
        """{success, contacts: [{sender, sender_jid, name, avatar_path, unseen, last_ts, items: [...]}]}."""
        return self._json_call("GET", "contact-statuses", timeout=5)

    def contact_status_media(self, item_id: str) -> Dict[str, Any]:
        """Baja la foto/video/audio de un estado de contacto. Devuelve {success, media_path|error}."""
        return self._json_call("POST", "contact-statuses/media", {"id": item_id}, timeout=90)

    def mark_contact_statuses_seen(self, ids, receipt: bool = True) -> Dict[str, Any]:
        return self._json_call("POST", "contact-statuses/seen", {"ids": list(ids), "receipt": bool(receipt)}, timeout=15)

    # ── Presencia ──

    def set_presence(self, available: bool) -> None:
        """'En línea' solo mientras la ventana está en primer plano (en segundo plano, sin bloquear la UI)."""
        threading.Thread(target=self._json_call, args=("POST", "presence", {"available": bool(available)}),
                         kwargs={"timeout": 5}, daemon=True, name="advancedwhatsapp-presence").start()

    def chat_presence(self, to: str, state: Optional[str] = None, subscribe: bool = False) -> None:
        """'Escribiendo…' hacia un contacto y/o suscripción a su 'en línea'."""
        if not to:
            return
        payload: Dict[str, Any] = {"to": to, "subscribe": bool(subscribe)}
        if state:
            payload["state"] = state
        threading.Thread(target=self._json_call, args=("POST", "chat-presence", payload),
                         kwargs={"timeout": 5}, daemon=True, name="advancedwhatsapp-chat-presence").start()
