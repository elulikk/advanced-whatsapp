"""
Advanced WhatsApp - Integración con Windows
=================================
- Identidad propia (AppUserModelID): nombre e icono de Advanced WhatsApp en las notificaciones, la barra de tareas y el
  menú Inicio, en lugar de "Python".
- Protocolo advancedwhatsapp:// : al tocar una notificación Windows abre "advancedwhatsapp://open?chat=..."; la segunda instancia
  le pasa el pedido a la que ya está abierta y se cierra.
- Notificaciones nativas (toasts) con foto del contacto, botones y agrupadas por chat. Se muestran con un
  proceso de PowerShell que queda abierto (sin dependencias extra); si falla, se usa el globo de la bandeja.
- Inicio con Windows (clave Run del usuario) y contador de no leídos sobre el icono de la barra de tareas.
Todo es "mejor esfuerzo": si algo no está disponible, la app sigue funcionando sin eso.
"""

import base64
import ctypes
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
from ctypes import wintypes
from pathlib import Path
from typing import Optional
from urllib.parse import quote

from app.config import BASE_DIR, DATA_DIR, FROZEN, TEST_COPY, WEBHOOK_PORT

APP_ID = "MagTecnologia.AdvancedWhatsapp.1"
APP_DISPLAY_NAME = "Advanced WhatsApp"
PROTOCOL = "advancedwhatsapp"
ICON_ICO = os.path.join(BASE_DIR, "assets", "advancedwhatsapp.ico")
ICON_PNG = os.path.join(BASE_DIR, "assets", "advancedwhatsapp.png")
MAIN_SCRIPT = os.path.join(BASE_DIR, "main.py")
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
IS_WINDOWS = sys.platform == "win32"


def _log(msg: str) -> None:
    print(f"[Advanced WhatsApp Windows] {msg}")


# ── Cómo relanzar la app ─────────────────────────────────────────────────────

def pythonw_executable() -> str:
    """pythonw.exe (sin consola) junto al intérprete actual; si no existe, el intérprete actual."""
    exe = sys.executable or "pythonw.exe"
    folder = os.path.dirname(exe)
    for name in ("pythonw.exe",):
        cand = os.path.join(folder, name)
        if os.path.isfile(cand):
            return cand
    return exe


def launch_target():
    """(ejecutable, argumentos) para abrir Advanced WhatsApp: AdvancedWhatsapp.exe en la versión portable, pythonw main.py si no."""
    if FROZEN:
        return sys.executable, ""
    return pythonw_executable(), f'"{MAIN_SCRIPT}"'


def launch_command(*args: str) -> str:
    exe, base_args = launch_target()
    return " ".join([f'"{exe}"'] + ([base_args] if base_args else []) + [f'"{a}"' for a in args])


# ── Registro (HKCU, no requiere permisos de administrador) ───────────────────

def _reg_set(path: str, values: dict) -> None:
    import winreg
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_SET_VALUE) as k:
        for name, val in values.items():
            winreg.SetValueEx(k, name, 0, winreg.REG_SZ, val)


def register_app_identity() -> None:
    """Nombre e icono que Windows muestra en las notificaciones de Advanced WhatsApp."""
    if not IS_WINDOWS:
        return
    try:
        _reg_set(rf"Software\Classes\AppUserModelId\{APP_ID}", {
            "DisplayName": APP_DISPLAY_NAME,
            "IconUri": ICON_PNG if os.path.exists(ICON_PNG) else ICON_ICO,
            "IconBackgroundColor": "FF12191D",
        })
    except Exception as e:
        _log(f"No se pudo registrar la identidad: {e}")


def register_protocol() -> None:
    """advancedwhatsapp:// abre (o activa) Advanced WhatsApp: lo usan los botones de las notificaciones."""
    if not IS_WINDOWS:
        return
    try:
        base = rf"Software\Classes\{PROTOCOL}"
        _reg_set(base, {"": "URL:Advanced WhatsApp", "URL Protocol": ""})
        _reg_set(base + r"\DefaultIcon", {"": f'"{ICON_ICO}",0'})
        _reg_set(base + r"\shell\open\command", {"": launch_command("%1")})
    except Exception as e:
        _log(f"No se pudo registrar el protocolo advancedwhatsapp://: {e}")


def _known_folder(folder_id: str) -> Optional[str]:
    """Carpeta especial de Windows (SHGetKnownFolderPath): sigue al Escritorio aunque esté en OneDrive."""
    try:
        ptr = ctypes.c_wchar_p()
        hr = ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(_guid(folder_id)), 0, None, ctypes.byref(ptr))
        if hr != 0:
            return None
        path = ptr.value
        ctypes.windll.ole32.CoTaskMemFree(ptr)
        return path
    except Exception:
        return None


FOLDERID_DESKTOP = "{B4BFCC3A-DB2C-424C-B029-7FE99A87C641}"
FOLDERID_PROGRAMS = "{A77F5D77-2E2B-44C3-A6A2-ABA601054A51}"   # menú Inicio > Programas del usuario


def _ensure_shortcut(folder: Optional[str]) -> bool:
    """Crea (o actualiza si la carpeta de la app cambió) AdvancedWhatsapp.lnk en `folder`, con icono e identidad propios."""
    if not IS_WINDOWS or not folder:
        return False
    try:
        import pythoncom
        from win32com.shell import shell
        from win32com.propsys import propsys, pscon
    except Exception:
        return False   # pywin32 no instalado: las notificaciones igual funcionan por el registro
    try:
        lnk_path = os.path.join(folder, f"{APP_DISPLAY_NAME}.lnk")
        target, args = launch_target()
        pythoncom.CoInitialize()
        link = pythoncom.CoCreateInstance(shell.CLSID_ShellLink, None, pythoncom.CLSCTX_INPROC_SERVER,
                                          shell.IID_IShellLink)
        persist = link.QueryInterface(pythoncom.IID_IPersistFile)
        if os.path.exists(lnk_path):
            try:
                persist.Load(lnk_path)
                icon_ok = (not os.path.exists(ICON_ICO)
                           or os.path.normcase(link.GetIconLocation()[0]) == os.path.normcase(ICON_ICO))
                if (os.path.normcase(link.GetPath(0)[0]) == os.path.normcase(target)
                        and link.GetArguments() == args and icon_ok):
                    store = link.QueryInterface(propsys.IID_IPropertyStore)
                    if str(store.GetValue(pscon.PKEY_AppUserModel_ID).GetValue()) == APP_ID:
                        return True
            except Exception:
                pass
            link = pythoncom.CoCreateInstance(shell.CLSID_ShellLink, None, pythoncom.CLSCTX_INPROC_SERVER,
                                              shell.IID_IShellLink)
            persist = link.QueryInterface(pythoncom.IID_IPersistFile)
        link.SetPath(target)
        link.SetArguments(args)
        link.SetWorkingDirectory(BASE_DIR)
        link.SetDescription("Advanced WhatsApp - WhatsApp para escritorio")
        if os.path.exists(ICON_ICO):
            link.SetIconLocation(ICON_ICO, 0)
        store = link.QueryInterface(propsys.IID_IPropertyStore)
        store.SetValue(pscon.PKEY_AppUserModel_ID, propsys.PROPVARIANTType(APP_ID, pythoncom.VT_LPWSTR))
        store.Commit()
        os.makedirs(folder, exist_ok=True)
        persist.Save(lnk_path, 0)
        return True
    except Exception as e:
        _log(f"No se pudo crear el acceso directo en {folder}: {e}")
        return False


def ensure_start_menu_shortcut() -> bool:
    """Acceso directo en el menú Inicio con la identidad de Advanced WhatsApp (buscar "Advanced WhatsApp", anclar, notificaciones)."""
    programs = _known_folder(FOLDERID_PROGRAMS) or os.path.join(
        os.environ.get("APPDATA", ""), "Microsoft", "Windows", "Start Menu", "Programs")
    return _ensure_shortcut(programs)


DESKTOP_FLAG = os.path.join(DATA_DIR, "acceso_escritorio_creado")


def ensure_desktop_shortcut(first_run_only: bool = False) -> bool:
    """Acceso directo en el escritorio. Con first_run_only (al abrir la app) se crea solo la primera vez:
    si después el usuario lo borra no se vuelve a crear; si existe, se actualiza si la carpeta cambió."""
    folder = _known_folder(FOLDERID_DESKTOP)
    if not folder:
        return False
    exists = os.path.exists(os.path.join(folder, f"{APP_DISPLAY_NAME}.lnk"))
    if first_run_only and not exists and os.path.exists(DESKTOP_FLAG):
        return False
    done = _ensure_shortcut(folder)
    if done:
        try:
            open(DESKTOP_FLAG, "a").close()
        except OSError:
            pass
    return done


def integrate_with_windows() -> None:
    """Se llama al arrancar (en segundo plano): deja todo registrado y actualizado si la carpeta cambió."""
    if TEST_COPY:
        return
    register_app_identity()
    register_protocol()
    ensure_start_menu_shortcut()
    ensure_desktop_shortcut(first_run_only=True)


# ── Inicio con Windows ───────────────────────────────────────────────────────

def is_autostart_enabled() -> bool:
    if not IS_WINDOWS:
        return False
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            winreg.QueryValueEx(k, APP_DISPLAY_NAME)
            return True
    except OSError:
        return False


def set_autostart(enabled: bool) -> bool:
    """Arranca Advanced WhatsApp al iniciar sesión, minimizado en la bandeja (para no perder notificaciones)."""
    if not IS_WINDOWS:
        return False
    try:
        import winreg
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
            if enabled:
                winreg.SetValueEx(k, APP_DISPLAY_NAME, 0, winreg.REG_SZ, launch_command("--background"))
            else:
                try:
                    winreg.DeleteValue(k, APP_DISPLAY_NAME)
                except FileNotFoundError:
                    pass
        return True
    except OSError as e:
        _log(f"No se pudo cambiar el inicio con Windows: {e}")
        return False


def refresh_autostart_command() -> None:
    """Si la carpeta de la app se movió, la entrada de inicio apunta al lugar nuevo."""
    if not TEST_COPY and is_autostart_enabled():
        set_autostart(True)


# ── Una sola instancia ───────────────────────────────────────────────────────

_mutex_handle = None


def acquire_single_instance() -> bool:
    """True si esta es la única Advanced WhatsApp abierta (mutex con nombre de Windows, se libera solo al salir)."""
    global _mutex_handle
    if not IS_WINDOWS:
        return True
    try:
        # use_last_error: ctypes guarda el error justo después de la llamada (GetLastError directo puede quedar pisado)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.restype = wintypes.HANDLE
        kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
        name = "Local\\ADVANCEDWHATSAPP_SingleInstance" + (f"_{WEBHOOK_PORT}" if TEST_COPY else "")
        _mutex_handle = kernel32.CreateMutexW(None, False, name)
        return ctypes.get_last_error() != 183   # ERROR_ALREADY_EXISTS
    except Exception:
        return True


def forward_to_running_instance(uri: str, timeout: float = 25.0) -> bool:
    """Le pasa el pedido (advancedwhatsapp://...) a la instancia abierta por el webhook local. Espera si recién arranca."""
    import urllib.request
    from app.config import WEBHOOK_PORT, TOKEN_HEADER, get_gateway_token
    try:
        # La instancia abierta puede traer su ventana al frente (Windows lo bloquea si no se lo permitimos)
        ctypes.windll.user32.AllowSetForegroundWindow(-1)
    except Exception:
        pass
    body = json.dumps({"uri": uri}).encode("utf-8")
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            req = urllib.request.Request(
                f"http://127.0.0.1:{WEBHOOK_PORT}/api/app/activate", data=body, method="POST",
                headers={"Content-Type": "application/json", TOKEN_HEADER: get_gateway_token()})
            with urllib.request.urlopen(req, timeout=3) as r:
                if r.status == 200:
                    return True
        except Exception:
            pass
        time.sleep(0.5)
    return False


# ── Notificaciones nativas (toasts) ──────────────────────────────────────────

_TOAST_HOST = r"""
$ErrorActionPreference = 'Stop'
$null = [Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime]
$null = [Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime]
$aumid = $env:ADVANCEDWHATSAPP_AUMID
$notifier = [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($aumid)
$history = [Windows.UI.Notifications.ToastNotificationManager]::History
[Console]::Out.WriteLine('ready ' + $notifier.Setting)
[Console]::Out.Flush()
while ($true) {
  $line = [Console]::In.ReadLine()
  if ($line -eq $null) { break }
  try {
    $cmd = $line | ConvertFrom-Json
    switch ($cmd.op) {
      'show' {
        $xml = New-Object Windows.Data.Xml.Dom.XmlDocument
        $xml.LoadXml($cmd.xml)
        $t = New-Object Windows.UI.Notifications.ToastNotification $xml
        if ($cmd.tag) { $t.Tag = $cmd.tag }
        if ($cmd.group) { $t.Group = $cmd.group }
        if ($cmd.expire) { $t.ExpirationTime = [DateTimeOffset]::Now.AddSeconds([double]$cmd.expire) }
        $notifier.Show($t)
      }
      'remove' { $history.Remove($cmd.tag, $cmd.group, $aumid) }
      'remove_group' { $history.RemoveGroup($cmd.group, $aumid) }
      'clear' { $history.Clear($aumid) }
      'ping' { }
    }
    [Console]::Out.WriteLine('ok')
  } catch {
    [Console]::Out.WriteLine('err ' + $_.Exception.Message)
  }
  [Console]::Out.Flush()
}
"""


_XML_INVALID = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f￾￿]")


def _xml(text: str) -> str:
    # Los caracteres de control no son válidos en XML: XmlDocument.LoadXml rechazaría todo el toast
    return (_XML_INVALID.sub("", str(text or "")).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def _file_uri(path: Optional[str]) -> Optional[str]:
    if not path or not os.path.exists(path):
        return None
    try:
        return Path(path).resolve().as_uri()
    except Exception:
        return None


def make_uri(action: str, chat: str = "") -> str:
    return f"{PROTOCOL}://{action}" + (f"?chat={quote(chat, safe='')}" if chat else "")


def _short_id(value: str) -> str:
    """Tag/Group de un toast: máximo 64 caracteres."""
    import hashlib
    return hashlib.sha1((value or "").encode("utf-8")).hexdigest()[:16]


class Toaster:
    """Muestra toasts de Windows con un PowerShell residente (arranca una vez, ~1 s; luego es instantáneo)."""

    def __init__(self, aumid: str = APP_ID):
        self.aumid = aumid
        self._proc: Optional[subprocess.Popen] = None
        self._lock = threading.Lock()
        self._failures = 0
        self.ready = False
        self.last_error: Optional[str] = None
        self.disabled_by_user = False   # el usuario apagó las notificaciones de Advanced WhatsApp en Windows

    @property
    def available(self) -> bool:
        return IS_WINDOWS and self._failures < 3

    def _ensure(self) -> bool:
        if self._proc and self._proc.poll() is None:
            return True
        if not self.available:
            return False
        try:
            encoded = base64.b64encode(_TOAST_HOST.encode("utf-16-le")).decode("ascii")
            env = os.environ.copy()
            env["ADVANCEDWHATSAPP_AUMID"] = self.aumid
            self._proc = subprocess.Popen(
                ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                 "-EncodedCommand", encoded],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW, env=env, text=True, encoding="ascii",
                errors="replace", bufsize=1)
            threading.Thread(target=self._reader, args=(self._proc,), daemon=True,
                             name="advancedwhatsapp-toast-reader").start()
            return True
        except Exception as e:
            self._failures += 1
            self.last_error = str(e)
            _log(f"No se pudo iniciar el servicio de notificaciones: {e}")
            return False

    def _reader(self, proc):
        try:
            for line in proc.stdout:
                line = line.strip()
                if line.startswith("ready"):
                    self.ready = True
                    self._failures = 0   # arrancó bien: fallos viejos no deben acumularse hasta apagar los toasts
                    # Setting: Enabled / DisabledForApplication / DisabledForUser / DisabledByGroupPolicy
                    self.disabled_by_user = "Disabled" in line
                    if self.disabled_by_user:
                        _log(f"Windows tiene desactivadas las notificaciones de Advanced WhatsApp ({line})")
                elif line.startswith("err"):
                    self.last_error = line[4:]
                    _log(f"Notificación: {self.last_error}")
        except Exception:
            pass
        if proc.poll() is not None and not self.ready:
            self._failures += 1

    def _send(self, cmd: dict) -> bool:
        with self._lock:
            if not self._ensure():
                return False
            try:
                self._proc.stdin.write(json.dumps(cmd, ensure_ascii=True) + "\n")
                self._proc.stdin.flush()
                return True
            except Exception as e:
                self.last_error = str(e)
                self._failures += 1
                try:
                    self._proc.kill()
                except Exception:
                    pass
                self._proc = None
                return False

    def warm_up(self) -> None:
        threading.Thread(target=lambda: self._send({"op": "ping"}), daemon=True).start()

    def show_message(self, chat_jid: str, title: str, body: str, avatar: Optional[str] = None,
                     hero: Optional[str] = None, attribution: str = "", sound: bool = True,
                     with_actions: bool = True) -> bool:
        launch = _xml(make_uri("open", chat_jid))
        img = _file_uri(avatar)
        hero_uri = _file_uri(hero)
        parts = [f'<toast launch="{launch}" activationType="protocol">',
                 '<visual><binding template="ToastGeneric">',
                 f'<text hint-maxLines="1">{_xml(title)}</text>',
                 f'<text>{_xml(body)}</text>']
        if attribution:
            parts.append(f'<text placement="attribution">{_xml(attribution)}</text>')
        if img:
            parts.append(f'<image placement="appLogoOverride" hint-crop="circle" src="{_xml(img)}"/>')
        if hero_uri:
            parts.append(f'<image src="{_xml(hero_uri)}"/>')
        parts.append('</binding></visual>')
        if with_actions:
            parts.append('<actions>'
                         f'<action content="Responder" activationType="protocol" arguments="{_xml(make_uri("reply", chat_jid))}"/>'
                         f'<action content="Marcar como leído" activationType="protocol" arguments="{_xml(make_uri("read", chat_jid))}"/>'
                         '</actions>')
        parts.append('<audio src="ms-winsoundevent:Notification.IM"/>' if sound else '<audio silent="true"/>')
        parts.append('</toast>')
        return self._send({"op": "show", "xml": "".join(parts), "tag": _short_id(chat_jid), "group": "chats"})

    def show_call(self, call_id: str, chat_jid: str, name: str, is_video: bool, avatar: Optional[str] = None,
                  sound: bool = True) -> bool:
        img = _file_uri(avatar)
        kind = "Videollamada" if is_video else "Llamada de voz"
        parts = [f'<toast launch="{_xml(make_uri("open", chat_jid))}" activationType="protocol" scenario="incomingCall">',
                 '<visual><binding template="ToastGeneric">',
                 f'<text hint-maxLines="1">{_xml(name)}</text>',
                 f'<text>{kind} entrante · atendé desde el celular</text>']
        if img:
            parts.append(f'<image placement="appLogoOverride" hint-crop="circle" src="{_xml(img)}"/>')
        parts.append('</binding></visual>'
                     '<actions>'
                     f'<action content="Abrir chat" activationType="protocol" arguments="{_xml(make_uri("open", chat_jid))}"/>'
                     '<action content="Ocultar" activationType="system" arguments="dismiss"/>'
                     '</actions>')
        parts.append('<audio src="ms-winsoundevent:Notification.Looping.Call" loop="true"/>' if sound
                     else '<audio silent="true"/>')
        parts.append('</toast>')
        return self._send({"op": "show", "xml": "".join(parts), "tag": _short_id("call" + call_id),
                           "group": "calls", "expire": 90})

    def show_simple(self, title: str, body: str, uri: str = "", tag: str = "general", sound: bool = False) -> bool:
        parts = [f'<toast launch="{_xml(uri or make_uri("show"))}" activationType="protocol">',
                 '<visual><binding template="ToastGeneric">',
                 f'<text>{_xml(title)}</text><text>{_xml(body)}</text>',
                 '</binding></visual>',
                 '<audio src="ms-winsoundevent:Notification.Default"/>' if sound else '<audio silent="true"/>',
                 '</toast>']
        return self._send({"op": "show", "xml": "".join(parts), "tag": _short_id(tag), "group": "app"})

    def remove_chat(self, chat_jid: str) -> None:
        self._send({"op": "remove", "tag": _short_id(chat_jid), "group": "chats"})

    def remove_call(self, call_id: str) -> None:
        self._send({"op": "remove", "tag": _short_id("call" + call_id), "group": "calls"})

    def clear_all(self) -> None:
        self._send({"op": "clear"})

    def close(self) -> None:
        with self._lock:
            if self._proc:
                try:
                    self._proc.stdin.close()
                    self._proc.wait(timeout=2)
                except Exception:
                    try:
                        self._proc.kill()
                    except Exception:
                        pass
                self._proc = None


def thumbnail_from_media_raw(media_raw: Optional[str], msg_id: str) -> Optional[str]:
    """Miniatura JPEG que viene dentro del mensaje (foto/video) guardada en un temporal para el toast."""
    if not media_raw:
        return None
    try:
        data = json.loads(media_raw)
        msg = data.get("message") or {}
        for _ in range(4):   # desenvolver efímeros / un solo uso
            inner = (msg.get("ephemeralMessage") or msg.get("viewOnceMessage") or msg.get("viewOnceMessageV2") or {}).get("message")
            if not inner:
                break
            msg = inner
        media = msg.get("imageMessage") or msg.get("videoMessage") or {}
        thumb = media.get("jpegThumbnail")
        if isinstance(thumb, dict):
            thumb = thumb.get("data")
        if not thumb:
            return None
        raw = base64.b64decode(thumb)
        folder = os.path.join(tempfile.gettempdir(), "advancedwhatsapp_toasts")
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, f"{_short_id(msg_id)}.jpg")
        with open(path, "wb") as f:
            f.write(raw)
        return path
    except Exception:
        return None


# ── Contador sobre el icono de la barra de tareas (ITaskbarList3::SetOverlayIcon) ──

class _GUID(ctypes.Structure):
    _fields_ = [("Data1", ctypes.c_ulong), ("Data2", ctypes.c_ushort), ("Data3", ctypes.c_ushort),
                ("Data4", ctypes.c_ubyte * 8)]


def _guid(text: str) -> _GUID:
    g = _GUID()
    ctypes.windll.ole32.CLSIDFromString(ctypes.c_wchar_p(text), ctypes.byref(g))
    return g


class TaskbarBadge:
    """Círculo verde con el número de chats sin leer sobre el icono de la barra de tareas."""

    def __init__(self):
        self._ptr = None
        self._set_overlay = None
        self._icons = {}
        self._last = None
        if not IS_WINDOWS:
            return
        try:
            ptr = ctypes.c_void_p()
            hr = ctypes.windll.ole32.CoCreateInstance(
                ctypes.byref(_guid("{56FDF344-FD6D-11d0-958A-006097C9A090}")), None, 1,
                ctypes.byref(_guid("{ea1afb91-9e28-4b86-90e9-9e9f8a5eefaf}")), ctypes.byref(ptr))
            if hr != 0 or not ptr.value:
                return
            vtbl = ctypes.cast(ptr, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
            hr_init = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p)(vtbl[3])
            if hr_init(ptr) != 0:
                return
            self._set_overlay = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, wintypes.HWND, wintypes.HICON,
                                                   wintypes.LPCWSTR)(vtbl[18])
            self._ptr = ptr
        except Exception as e:
            _log(f"Contador de barra de tareas no disponible: {e}")

    def _icon_for(self, text: str):
        if text in self._icons:
            return self._icons[text]
        from PySide6.QtGui import QPixmap, QPainter, QColor, QFont
        from PySide6.QtCore import Qt, QRectF
        size = 32
        pm = QPixmap(size, size)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#1fa37f"))
        p.drawEllipse(QRectF(0, 0, size, size))
        p.setPen(QColor("#ffffff"))
        f = QFont("Segoe UI")
        f.setBold(True)
        f.setPixelSize(20 if len(text) == 1 else 15 if len(text) == 2 else 12)
        p.setFont(f)
        p.drawText(QRectF(0, 0, size, size), Qt.AlignCenter, text)
        p.end()
        folder = os.path.join(DATA_DIR, "cache")
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, f"badge_{text.replace('+', 'p')}.ico")
        pm.save(path, "ICO")
        user32 = ctypes.windll.user32
        user32.LoadImageW.restype = wintypes.HANDLE
        hicon = user32.LoadImageW(None, path, 1, 16, 16, 0x10)   # IMAGE_ICON, LR_LOADFROMFILE
        self._icons[text] = hicon
        return hicon

    def set_count(self, hwnd: int, count: int) -> None:
        if not self._ptr or not hwnd:
            return
        text = "" if count <= 0 else ("9+" if count > 9 else str(count))
        if text == self._last:
            return
        try:
            if text:
                hicon = self._icon_for(text)
                self._set_overlay(self._ptr, hwnd, hicon, f"{count} chats sin leer")
            else:
                self._set_overlay(self._ptr, hwnd, None, None)
            self._last = text
        except Exception as e:
            _log(f"No se pudo actualizar el contador de la barra de tareas: {e}")

    def reset(self):
        """La barra de tareas se recreó (Explorer reiniciado o ventana re-creada): volver a pintar."""
        self._last = None
