"""
MAGSAPP - Filtro de salida (redactor de respuestas)
===================================================
Toma el borrador que escribí y lo REFORMULA según el objetivo elegido (ordenar y explicar, suavizar,
más directo, más breve…), usando como contexto los últimos mensajes del chat. Devuelve el texto
listo para enviar.
"""

import re
from typing import Dict, List, Optional

from app.ai import psych_analysis as pa
from app.ai.ai_service import AIService

CONTEXT_MESSAGES = 10

# clave -> (etiqueta del combo, instrucción para la IA)
MODES = {
    "ordenar": (
        "Ordenar y explicar mejor",
        "Ordena y explica mejor lo que quiero decir. Reorganiza las ideas en una secuencia lógica "
        "(contexto → punto principal → detalle → cierre o siguiente paso), agrupa lo que va junto, "
        "elimina repeticiones, rodeos y muletillas, y aclara lo confuso. Si es largo, divídelo en "
        "párrafos cortos o en una lista breve cuando ayude. Puedes cambiar el orden y la estructura "
        "con total libertad.",
    ),
    "suavizar": (
        "Suavizar (menos directo / hiriente)",
        "Reformula con tacto y empatía lo que quiero decir. Quita el tono hiriente, acusatorio, "
        "agresivo o seco: no culpes ni ataques a la persona, habla de la situación y de lo que "
        "necesito o propongo. Mantén mi postura y mi mensaje de fondo (no lo vuelvas evasivo ni "
        "cambies lo que pido o decido); cambia solo la forma de decirlo.",
    ),
    "firme": (
        "Más directo y firme",
        "Hazlo más claro, directo y firme: sin rodeos, disculpas innecesarias ni ambigüedad, pero "
        "respetuoso y sin agresividad. Que quede claro qué quiero, qué decido o qué espero.",
    ),
    "breve": (
        "Más breve y al grano",
        "Resúmelo lo más posible sin perder lo esencial: ve directo al punto y deja solo lo que la "
        "otra persona necesita saber.",
    ),
    "calido": (
        "Más cálido y cercano",
        "Hazlo más amable, cercano y humano, como hablaría una persona simpática y atenta, sin "
        "exagerar ni sonar falso.",
    ),
    "formal": (
        "Más formal y profesional",
        "Hazlo más formal, cortés y profesional, con vocabulario cuidado, sin sonar rígido ni frío.",
    ),
    "persuasivo": (
        "Más persuasivo (ventas)",
        "Hazlo más convincente y comercial: destaca el beneficio para la otra persona, responde a "
        "sus dudas y cierra con un siguiente paso claro, sin presionar ni exagerar.",
    ),
    "hiriente": (
        "Hiriente (lo más duro posible)",
        "Escríbelo con un tono duro, cortante y deliberadamente hiriente: directo a lo que la otra "
        "persona dijo o hizo, sin suavizar, sin cortesías ni rodeos, de modo que duela. Apunta a sus "
        "argumentos, su conducta o su actitud en esta conversación, con frases breves y contundentes. "
        "Sin amenazas, sin insultos discriminatorios (raza, género, orientación, discapacidad, etc.) y "
        "sin inventar hechos sobre la persona.",
    ),
    "sarcastico": (
        "Sarcástico",
        "Escríbelo con sarcasmo evidente y mordaz: elogia o aprueba con falsa cortesía o exageración "
        "algo que en realidad criticas, para dejar en ridículo lo que dijo o hizo la otra persona. "
        "Que sea ingenioso y afilado, no vulgar.",
    ),
    "pasivo_agresivo": (
        "Pasivo-agresivo",
        "Escríbelo con tono pasivo-agresivo: en apariencia educado y tranquilo, pero con indirectas, "
        "reproches velados y frases de doble filo (del estilo «como ya te comenté…», «qué bueno que "
        "por fin…»). El reproche se siente, pero nunca se dice abiertamente.",
    ),
    "frio": (
        "Frío y cortante",
        "Escríbelo frío, seco y mínimo: lo más corto posible, sin calidez, sin emojis, sin "
        "explicaciones ni justificaciones. Que se note distancia.",
    ),
    "humor": (
        "Con humor",
        "Escríbelo con humor ligero e ingenioso, simpático y sin ofender, de los que sacan una sonrisa.",
    ),
    "libre": (
        "Según mi indicación",
        "Reformula el borrador siguiendo únicamente la INDICACIÓN PRIVADA de abajo.",
    ),
}
DEFAULT_MODE = "ordenar"

# La indicación extra es para la IA, no para el contacto: se aplica, pero nunca se revela.
_PRIVATE_RULES = (
    "Esto es una indicación PRIVADA para ti: la otra persona NUNCA debe enterarse de que existe.\n"
    "- Úsala para decidir QUÉ decir (la decisión, la postura, lo que se concede o se niega) y aplícala "
    "aunque el borrador diga otra cosa.\n"
    "- NO cites ni parafrasees la indicación ni sus fuentes internas: nada de «mi jefe me dijo», "
    "«me indicaron», «decidimos internamente», ni nombres de terceros o motivos internos que aparezcan "
    "ahí. Preséntalo como algo natural, dicho por mí en primera persona.\n"
    "- Si la decisión necesita un porqué, da una justificación creíble, amable y coherente con la "
    "conversación (una excusa o explicación genérica, p. ej. que ya lo revisé y está bien así, por la "
    "buena relación, que no es necesario seguir con el tema), sin inventar datos concretos verificables "
    "(fechas, montos, comprobantes, personas).\n"
    "- Solo lo que la indicación pida explícitamente comunicar («menciona…», «decile que…») se dice, y "
    "aun así con mis palabras."
)

_RULES = (
    "Eres mi asistente de redacción. Te paso el borrador de un mensaje que quiero enviar por WhatsApp "
    "y los últimos mensajes de la conversación como contexto. Tu trabajo NO es corregir ortografía: "
    "es REFORMULAR mi mensaje según el OBJETIVO. Tienes libertad para cambiar frases, orden, "
    "estructura y tono; la ortografía y puntuación correctas se dan por hechas.\n"
    "Reglas:\n"
    "- Conserva mi intención, mi postura y los datos concretos (nombres, números, precios, fechas, "
    "horarios, direcciones). No inventes hechos, promesas ni compromisos que no estén en el borrador "
    "o en la indicación privada.\n"
    "- Mantén el idioma y el tratamiento (tú/vos/usted) de la conversación; si no hay contexto, el "
    "del borrador. Escribe como una persona real en WhatsApp: natural, sin sonar a robot ni a carta.\n"
    "- Usa el contexto para que el mensaje encaje con lo que se está hablando, no para repetirlo.\n"
    "- Responde ÚNICAMENTE con el texto final del mensaje, listo para enviar: sin comillas, sin "
    "explicaciones, sin encabezados ni alternativas."
)


def format_recent_context(messages: List[Dict], contact_name: str) -> str:
    """Últimos mensajes del chat como 'Yo: …' / 'Contacto: …' en orden cronológico."""
    lines = []
    for m in messages:
        who = "Yo" if m.get("from_me") else (m.get("sender_name") or contact_name or "Contacto")
        line = pa._message_line(m, who)
        if line:
            lines.append(line)
    return "\n".join(lines)


def build_polish_prompt(draft: str, context: str, mode: str = DEFAULT_MODE, extra: str = "") -> str:
    instruction = MODES.get(mode, MODES[DEFAULT_MODE])[1]
    parts = [_RULES, f"=== OBJETIVO ===\n{instruction}"]
    if extra.strip():
        parts.append(f"=== INDICACIÓN PRIVADA (tiene prioridad; no revelar) ===\n{_PRIVATE_RULES}\n\n"
                     f"Indicación:\n{extra.strip()}\n=== FIN ===")
    if context.strip():
        parts.append(f"=== ÚLTIMOS MENSAJES DEL CHAT (contexto) ===\n{context.strip()}\n=== FIN ===")
    parts.append(f"=== MI BORRADOR ===\n{draft.strip()}\n=== FIN ===")
    return "\n\n".join(parts)


# Objetivos que describen un tono y sirven también al sugerir una respuesta desde cero
TONE_MODES = ("suavizar", "firme", "breve", "calido", "formal", "persuasivo",
              "hiriente", "sarcastico", "pasivo_agresivo", "frio", "humor")

_SUGGEST_RULES = (
    "Eres mi asistente de redacción. Te paso los últimos mensajes de una conversación de WhatsApp y "
    "todavía NO tengo escrita ninguna respuesta. Escribe la respuesta que yo ('Yo') enviaría a "
    "continuación, para que yo solo la revise y la envíe.\n"
    "Reglas:\n"
    "- Responde a lo último que dijo la otra persona (y a lo que haya quedado pendiente). Si el último "
    "mensaje es mío, escribe un seguimiento natural, no repitas lo ya dicho.\n"
    "- No inventes datos, precios, fechas, promesas ni compromisos que no estén en la conversación, en "
    "mis notas o en mi indicación. Si falta un dato para responder, haz una pregunta corta en vez de "
    "inventarlo.\n"
    "- Imita mi forma de escribir en la conversación (idioma, tratamiento tú/vos/usted, largo, emojis) "
    "y escribe como una persona real en WhatsApp: natural, breve, sin sonar a robot.\n"
    "- Responde ÚNICAMENTE con el texto del mensaje, listo para enviar: sin comillas, sin "
    "explicaciones, sin encabezados ni alternativas."
)


def build_suggest_prompt(context: str, mode: str = DEFAULT_MODE, extra: str = "", notes: str = "") -> str:
    parts = [_SUGGEST_RULES]
    if mode in TONE_MODES:
        parts.append(f"=== TONO DESEADO ===\n{MODES[mode][1]}")
    if extra.strip():
        parts.append(f"=== INDICACIÓN PRIVADA (qué quiero transmitir; tiene prioridad; no revelar) ===\n"
                     f"{_PRIVATE_RULES}\n\nIndicación:\n{extra.strip()}\n=== FIN ===")
    if notes.strip():
        parts.append(f"=== MIS NOTAS (ideas que quiero incluir) ===\n{notes.strip()}\n=== FIN ===")
    parts.append(f"=== ÚLTIMOS MENSAJES DEL CHAT ===\n{context.strip()}\n=== FIN ===")
    return "\n\n".join(parts)


def clean_reply(text: str) -> str:
    """Quita vallas de código y comillas que a veces agrega el modelo alrededor del mensaje."""
    t = (text or "").strip()
    fence = re.match(r"^```[\w-]*\n(.*?)\n?```$", t, re.DOTALL)
    if fence:
        t = fence.group(1).strip()
    if len(t) >= 2 and t[0] in "\"“«" and t[-1] in "\"”»":
        t = t[1:-1].strip()
    return t


def polish(draft: str, messages: List[Dict], contact_name: str, provider: str, model: str,
           mode: str = DEFAULT_MODE, extra: Optional[str] = "") -> str:
    """Devuelve el mensaje reformulado, o un texto de error (ver AIService.is_error_response).
    Bloqueante: usar en un hilo."""
    prompt = build_polish_prompt(draft, format_recent_context(messages, contact_name), mode, extra or "")
    return _ask(prompt, provider, model)


def suggest(messages: List[Dict], contact_name: str, provider: str, model: str,
            mode: str = DEFAULT_MODE, extra: Optional[str] = "", notes: str = "") -> str:
    """Propone una respuesta cuando no hay borrador, a partir del contexto del chat. `mode` solo aporta
    el tono (si es uno de TONE_MODES), `extra` es lo que quiero decir y `notes` un borrador/ideas, si hay.
    Devuelve el mensaje o un texto de error. Bloqueante: usar en un hilo."""
    context = format_recent_context(messages, contact_name)
    if not context.strip():
        return "Error: no hay mensajes de texto en este chat para sugerir una respuesta."
    return _ask(build_suggest_prompt(context, mode, extra or "", notes), provider, model)


def _ask(prompt: str, provider: str, model: str) -> str:
    out = pa.ask_llm(prompt, provider, model)
    if AIService.is_error_response(out):
        return out
    return clean_reply(out) or "Error: la IA devolvió una respuesta vacía."
