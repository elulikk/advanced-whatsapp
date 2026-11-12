"""Guarda contraseñas de la app (copia automática, sincronización) protegidas por el sistema.

En Windows se cifran con DPAPI (`CryptProtectData`): solo el mismo usuario de Windows en esta PC puede leerlas.
En otros sistemas (solo desarrollo y tests) quedan con un prefijo `plain:` porque no hay almacén seguro a mano.
Archivo: ~/.magsapp/secretos.json  {nombre: "dpapi:<base64>" | "plain:<base64>"}
"""
import base64
import json
import os
import sys
import threading

from app.config import DATA_DIR

PATH = os.path.join(DATA_DIR, "secretos.json")
_lock = threading.Lock()


def _protect(data: bytes) -> str:
    if sys.platform == "win32":
        try:
            import ctypes
            from ctypes import wintypes

            class Blob(ctypes.Structure):
                _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

            buf = ctypes.create_string_buffer(data, len(data))
            src = Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
            out = Blob()
            if ctypes.windll.crypt32.CryptProtectData(ctypes.byref(src), "AdvancedWhatsapp", None, None, None, 0,
                                                      ctypes.byref(out)):
                try:
                    return "dpapi:" + base64.b64encode(ctypes.string_at(out.pbData, out.cbData)).decode()
                finally:
                    ctypes.windll.kernel32.LocalFree(out.pbData)
        except Exception:
            pass
    return "plain:" + base64.b64encode(data).decode()


def _unprotect(token: str) -> bytes:
    kind, _, b64 = token.partition(":")
    raw = base64.b64decode(b64)
    if kind == "plain":
        return raw
    if kind == "dpapi" and sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        class Blob(ctypes.Structure):
            _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

        buf = ctypes.create_string_buffer(raw, len(raw))
        src = Blob(len(raw), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
        out = Blob()
        if ctypes.windll.crypt32.CryptUnprotectData(ctypes.byref(src), None, None, None, None, 0, ctypes.byref(out)):
            try:
                return ctypes.string_at(out.pbData, out.cbData)
            finally:
                ctypes.windll.kernel32.LocalFree(out.pbData)
    raise ValueError("no se pudo leer el secreto")


def _read() -> dict:
    try:
        with open(PATH, "r", encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def get(name: str) -> str:
    with _lock:
        token = _read().get(name)
    if not token:
        return ""
    try:
        return _unprotect(token).decode("utf-8")
    except Exception:
        return ""


def set(name: str, value: str) -> None:   # noqa: A001  (mismo nombre que usa el resto de la app: secret_store.set)
    with _lock:
        data = _read()
        if value:
            data[name] = _protect(value.encode("utf-8"))
        else:
            data.pop(name, None)
        tmp = PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f)
        os.replace(tmp, PATH)
