"""
Advanced WhatsApp - Modo resumen
======================
Resume, simplifica e interpreta UN mensaje del chat: qué dice en pocas palabras, qué quiere decir
de verdad (intención y tono) y qué espera de mí. Usa los mensajes anteriores como contexto para
interpretarlo bien. El resultado se guarda en la base (columna `summary`) para no repetir la consulta.
"""

import re
import threading
from typing import Dict, List, Optional, Tuple

from PySide6.QtCore import QObject, Signal

from app import db
from app.ai import psych_analysis as pa
from app.ai.ai_service import AIService

CONTEXT_MESSAGES = 8

# (etiqueta en la respuesta de la IA, título que se muestra)
SECTIONS = [("RESUMEN", "En pocas palabras"), ("INTERPRETACIÓN", "Qué quiere decir"), ("ESPERA", "Qué espera")]

_RULES = (
    "Eres mi asistente para entender mensajes de WhatsApp. Te paso UN mensaje y, como contexto, los "
    "mensajes anteriores de la conversación. Tu trabajo es que yo entienda ese mensaje rápido y bien: "
    "resumirlo, simplificarlo e interpretar correctamente qué quiere decir.\n"
    "Reglas:\n"
    "- Céntrate SOLO en el mensaje indicado; el contexto sirve para interpretarlo (a qué responde, "
    "ironías, sobreentendidos), no para resumir la conversación.\n"
    "- Conserva los datos concretos (nombres, números, precios, fechas, horarios, direcciones). No "
    "inventes nada que no esté en el mensaje o en el contexto; si algo es ambiguo, dilo.\n"
    "- Lenguaje simple y directo, en el idioma del mensaje. Si el mensaje es corto o ya es claro, "
    "responde igual de corto: no estires la explicación.\n"
    "- Responde EXACTAMENTE con estas tres líneas, sin nada antes ni después:\n"
    "RESUMEN: <lo esencial en una o dos frases cortas>\n"
    "INTERPRETACIÓN: <qué quiere decir de verdad: intención, tono o emoción y lo que queda implícito>\n"
    "ESPERA: <qué espera o pide (respuesta, decisión, acción, plazo); si no pide nada, «Nada en particular»>"
)


def message_text(msg: Dict) -> str:
    """Texto resumible de un mensaje: el texto, o la transcripción si es una nota de voz."""
    mt = (msg.get("media_type") or "text").lower()
    if mt in ("revoked", "sticker"):
        return ""
    if mt == "audio":
        t = (msg.get("transcript") or "").strip()
        return "" if t.startswith("(sin voz") else t
    return (msg.get("text") or "").strip()


def can_summarize(msg: Dict) -> bool:
    return bool(message_text(msg))


def build_summary_prompt(text: str, author: str, context: str) -> str:
    parts = [_RULES]
    if context.strip():
        parts.append(f"=== MENSAJES ANTERIORES (contexto) ===\n{context.strip()}\n=== FIN ===")
    parts.append(f"=== MENSAJE A RESUMIR (de {author}) ===\n{text.strip()}\n=== FIN ===")
    return "\n\n".join(parts)


def parse_summary(raw: str) -> List[Tuple[str, str]]:
    """[(título, texto)] según las secciones; si la IA no respetó el formato, todo va como resumen."""
    t = (raw or "").strip()
    fence = re.match(r"^```[\w-]*\n(.*?)\n?```$", t, re.DOTALL)
    if fence:
        t = fence.group(1).strip()
    labels = "|".join(re.escape(k) for k, _ in SECTIONS) + "|INTERPRETACION"
    found: Dict[str, str] = {}
    for m in re.finditer(rf"^\W*({labels})\W*:\s*(.*?)(?=^\W*(?:{labels})\W*:|\Z)", t, re.M | re.S | re.I):
        key = m.group(1).upper().replace("INTERPRETACION", "INTERPRETACIÓN")
        value = m.group(2).strip().strip("*").strip()
        if value:
            found[key] = value
    if not found:
        return [(SECTIONS[0][1], t)] if t else []
    return [(title, found[key]) for key, title in SECTIONS if key in found]


def summarize(msg: Dict, previous: List[Dict], contact_name: str, provider: str, model: str) -> str:
    """Devuelve el resumen en bruto (formato RESUMEN/INTERPRETACIÓN/ESPERA) o un texto de error
    (ver AIService.is_error_response). Bloqueante: usar en un hilo."""
    text = message_text(msg)
    if not text:
        return "Error: este mensaje no tiene texto para resumir."
    author = "mí (Yo)" if msg.get("from_me") else (msg.get("sender_name") or contact_name or "el contacto")
    lines = []
    for m in previous[-CONTEXT_MESSAGES:]:
        who = "Yo" if m.get("from_me") else (m.get("sender_name") or contact_name or "Contacto")
        line = pa._message_line(m, who)
        if line:
            lines.append(line)
    out = pa.ask_llm(build_summary_prompt(text, author, "\n".join(lines)), provider, model)
    if AIService.is_error_response(out):
        return out
    return out.strip() if parse_summary(out) else "Error: la IA devolvió una respuesta vacía."


# ─── Peticiones en segundo plano ─────────────────────────────────────────────

class SummarySignals(QObject):
    summary_ready = Signal(str)   # message_id (terminó, con resumen o con error)


summary_signals = SummarySignals()
_lock = threading.Lock()
_pending: set = set()
_errors: Dict[str, str] = {}


def is_pending(msg_id: str) -> bool:
    with _lock:
        return msg_id in _pending


def get_error(msg_id: str) -> Optional[str]:
    with _lock:
        return _errors.get(msg_id)


def request_summary(msg: Dict, contact_name: str, provider: str, model: str) -> bool:
    """Lanza el resumen de un mensaje en un hilo. False si ya hay uno en curso o no hay texto."""
    msg_id = msg.get("id")
    if not msg_id or not can_summarize(msg):
        return False
    with _lock:
        if msg_id in _pending:
            return False
        _pending.add(msg_id)
        _errors.pop(msg_id, None)

    def work():
        try:
            previous = db.get_messages(msg.get("chat_jid", ""), limit=CONTEXT_MESSAGES,
                                       before_timestamp=msg.get("timestamp") or None)
            out = summarize(msg, previous, contact_name, provider, model)
        except Exception as e:
            out = f"Error: {e}"
        with _lock:
            if AIService.is_error_response(out):
                _errors[msg_id] = out.strip()
            else:
                db.set_message_summary(msg_id, out)
            _pending.discard(msg_id)
        summary_signals.summary_ready.emit(msg_id)

    threading.Thread(target=work, name="advancedwhatsapp-summary", daemon=True).start()
    return True
