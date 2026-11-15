"""Envía texto o archivos por WhatsApp usando el gateway local de Advanced WhatsApp (sin abrir la ventana).

Uso:
    python tools/enviar.py ARCHIVO [ARCHIVO...] [--a yo|NUMERO|JID] [--texto "mensaje"]
    python tools/enviar.py --texto "hola"            # solo texto, a uno mismo

Por defecto manda a tu propio número ("Mensaje a mí mismo"). Imágenes y videos salen como
imagen/video; el resto como documento. Necesita que Advanced WhatsApp esté abierto y conectado.
Solo usa la biblioteca estándar, así que corre con cualquier Python 3.
"""
import argparse
import json
import mimetypes
import os
import sys
import urllib.error
import urllib.request

DATA_DIR = os.path.join(os.path.expanduser("~"), ".magsapp")
PORT = int(os.environ.get("ADVANCEDWHATSAPP_GATEWAY_PORT") or 5016)
URL = f"http://127.0.0.1:{PORT}"

mimetypes.add_type("application/vnd.android.package-archive", ".apk")


def _token() -> str:
    with open(os.path.join(DATA_DIR, "gateway.token"), encoding="utf-8") as f:
        return f.read().strip()


def _call(method: str, path: str, body: dict | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(URL + path, data=data, method=method, headers={
        "Content-Type": "application/json", "X-AdvancedWhatsapp-Token": _token()})
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            return json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        try:
            msg = json.loads(e.read().decode()).get("error")
        except Exception:
            msg = str(e)
        raise SystemExit(f"Error del gateway ({e.code}): {msg}")
    except urllib.error.URLError:
        raise SystemExit("No responde el gateway: ¿está abierto Advanced WhatsApp?")


def mi_numero() -> str:
    st = _call("GET", "/status")
    if st.get("status") != "connected" or not st.get("phone"):
        raise SystemExit(f"WhatsApp no está conectado (estado: {st.get('status')}).")
    return "".join(c for c in str(st["phone"]) if c.isdigit())


def enviar(destino: str, archivo: str | None = None, texto: str = "") -> str:
    """Manda un archivo (o solo texto) y devuelve el id del mensaje."""
    to = mi_numero() if destino in ("yo", "me", "") else destino
    body = {"to": to, "text": texto, "external": True}  # para que aparezca también en la ventana
    if archivo:
        archivo = os.path.abspath(archivo)
        if not os.path.isfile(archivo):
            raise SystemExit(f"No existe: {archivo}")
        mime = mimetypes.guess_type(archivo)[0] or "application/octet-stream"
        if mime.startswith("image/") and not mime.endswith("gif"):
            body["image_path"] = archivo
        elif mime.startswith("video/"):
            body["video_path"] = archivo
        else:
            body.update(document_path=archivo, document_name=os.path.basename(archivo), mimetype=mime)
    elif not texto:
        raise SystemExit("Nada para mandar: pasá un archivo o --texto.")
    return _call("POST", "/send", body).get("message_id", "")


def main() -> None:
    p = argparse.ArgumentParser(description="Enviar por WhatsApp vía el gateway local de Advanced WhatsApp")
    p.add_argument("archivos", nargs="*", help="archivos a mandar")
    p.add_argument("--a", dest="destino", default="yo", help='"yo" (default), número con código de país, o JID')
    p.add_argument("--texto", default="", help="texto (con archivos va como pie del primero)")
    a = p.parse_args()
    if not a.archivos:
        print("Enviado:", enviar(a.destino, None, a.texto))
        return
    for i, f in enumerate(a.archivos):
        print("Enviado:", os.path.basename(f), enviar(a.destino, f, a.texto if i == 0 else ""))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
