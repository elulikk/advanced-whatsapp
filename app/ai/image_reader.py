"""
Advanced WhatsApp - Leer imágenes con IA
========================================
Descripción corta + texto (OCR) de una foto o sticker YA descargado, usando el modelo con visión del proveedor
configurado (ver AIService.generate_vision_completion). Bloqueante: se llama desde el hilo de media_service.
"""

import io
import os
import re
from typing import Optional, Tuple

from app.ai.ai_service import AIService

MAX_FILE_BYTES = 25 * 1024 * 1024   # más que esto no se manda (y no vale la pena abrirlo)
MAX_SIDE = 1568                      # lado máximo al que se achica antes de enviarlo: menos tokens, misma lectura
NO_TEXT = "NINGUNO"

_MIMES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp", ".gif": "image/gif"}

PROMPT = (
    "Mirá esta imagen de un chat de WhatsApp y respondé en español rioplatense, con exactamente este formato:\n"
    "DESCRIPCION: <una o dos oraciones cortas que digan qué se ve>\n"
    f"TEXTO: <todo el texto legible que aparezca en la imagen, transcripto tal cual y en el mismo orden; si no hay "
    f"texto, escribí {NO_TEXT}>\n"
    "No agregues nada más ni uses markdown.")


def prepare_image(path: str) -> Tuple[bytes, str]:
    """(bytes, mime) listos para enviar: achicada y como JPEG/PNG si se puede (Pillow); si no, el archivo tal cual.
    Lanza RuntimeError si no existe o es demasiado grande."""
    if not path or not os.path.isfile(path):
        raise RuntimeError("La imagen no está descargada")
    if os.path.getsize(path) > MAX_FILE_BYTES:
        raise RuntimeError("La imagen es demasiado grande para leerla")
    ext = os.path.splitext(path)[1].lower()
    try:
        from PIL import Image
        with Image.open(path) as im:
            im.seek(0)   # stickers/GIF animados: primer cuadro
            im.thumbnail((MAX_SIDE, MAX_SIDE))
            has_alpha = im.mode in ("RGBA", "LA", "P")
            buf = io.BytesIO()
            if has_alpha:
                im.convert("RGBA").save(buf, "PNG")
                return buf.getvalue(), "image/png"
            im.convert("RGB").save(buf, "JPEG", quality=85)
            return buf.getvalue(), "image/jpeg"
    except Exception:
        mime = _MIMES.get(ext)
        if not mime:
            raise RuntimeError("Formato de imagen no soportado")
        with open(path, "rb") as f:
            return f.read(), mime


def parse_reading(raw: str) -> Tuple[str, str]:
    """(descripción, texto) a partir de la respuesta de la IA. Tolera que no respete el formato: sin etiquetas
    todo es descripción. «NINGUNO» (o equivalentes) en el texto se guarda como vacío."""
    text = (raw or "").strip()
    text = re.sub(r"^```\w*\n?|\n?```$", "", text).strip()
    m = re.search(r"DESCRIPCI[ÓO]N\s*:\s*(.*?)(?:\n\s*TEXTO\s*:|\Z)", text, re.S | re.I)
    t = re.search(r"TEXTO\s*:\s*(.*)\Z", text, re.S | re.I)
    desc = m.group(1).strip() if m else ("" if t else text)
    ocr = t.group(1).strip() if t else ""
    if re.fullmatch(r"[\W_]*(ninguno|ninguna|no hay texto|sin texto|none|n/a)[\W_]*", ocr, re.I):
        ocr = ""
    return desc, ocr


def read_image(path: str, service: Optional[AIService] = None) -> Tuple[str, str]:
    """Lee una imagen en disco con IA. Devuelve (descripción, texto). Lanza RuntimeError si no se pudo."""
    svc = service or AIService()
    data, mime = prepare_image(path)
    out = svc.generate_vision_completion(PROMPT, data, mime)
    if AIService.is_error_response(out):
        raise RuntimeError(out.strip().splitlines()[0][:200] if out and out.strip() else "La IA no respondió")
    desc, ocr = parse_reading(out)
    if not desc and not ocr:
        raise RuntimeError("La IA no devolvió nada legible")
    return desc, ocr


def has_vision_key(service: Optional[AIService] = None) -> bool:
    return (service or AIService()).has_vision_key()
