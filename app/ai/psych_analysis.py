"""
Advanced WhatsApp - Análisis psicológico
==============================
Dos enfoques:
  - normal:  lectura psicológica del contacto (personalidad, puntos fuertes y débiles, etc.), ajustada al
             tipo de relación (amigo, familia, pareja, trabajo… o uno propio) y, si se quiere, centrada en
             preguntas puntuales («¿me quiere estafar?», «¿está enojado?»…).
  - interno: análisis de MÍ en todos los chats (lo que escriben los demás va solo como contexto).
La profundidad del análisis de un chat se ajusta a cuánto escribió el sujeto (ver _depth_rule):
si casi no escribió ("gracias"), la respuesta es de 1-3 líneas y sin inferencias.
El análisis nunca arranca solo: lo pide el usuario.

Cada sujeto (un chat, o "_yo" para el análisis interno) tiene su carpeta en ANALYSIS_DIR con:
  - notas.txt:            SOLO las notas del usuario (editables a mano). Es lo único que se le envía a la IA
                          como contexto.
  - historial.txt:        registro de cada análisis y cada pregunta/respuesta, para consultarlos. Nunca se le
                          vuelve a enviar a la IA: así un «Rehacer» parte de cero y no se refuerza a sí mismo.
  - ultimo_<enfoque>.txt: el último análisis de ese enfoque, para mostrarlo al reabrir.
"""

import json
import os
import re
import shutil
from datetime import datetime
from typing import List, Dict, Optional, Tuple

from app.ai.ai_service import AIService
from app.config import ANALYSIS_DIR, load_app_config

PROVIDERS = ["Gemini", "Groq", "OpenAI", "Claude"]
FOCUSES = {"normal": "Contacto", "interno": "Análisis interno (yo)"}

# Tipo de relación: orienta la lectura. El combo del diálogo es editable: se puede escribir cualquier otro.
DEFAULT_KIND = "General"
KIND_HINTS = {
    "General": "",
    "Amigo": "Es un AMIGO: mira la reciprocidad, la confianza, el apoyo mutuo y si la amistad se siente pareja o desbalanceada.",
    "Familia": "Es un FAMILIAR: mira la dinámica familiar, los roles, los reproches o cargas históricas y cómo se cuidan o se presionan.",
    "Pareja": "Es mi PAREJA (o alguien con quien hay un vínculo afectivo): mira el afecto, el compromiso, los conflictos y la comunicación emocional.",
    "Trabajo": "Es alguien del TRABAJO: mira el trato profesional, la jerarquía, los acuerdos, los límites y si hay presión o ambigüedad.",
}
# Búsquedas rápidas (opcionales): preguntas puntuales sobre la conversación. Se pueden agregar propias.
DEFAULT_SEARCHES = [
    "¿Me quiere estafar?",
    "¿Me está poniendo a prueba?",
    "¿Está enojado/a conmigo?",
    "¿Me está mintiendo?",
    "¿Me está manipulando?",
    "¿Está interesado/a en mí?",
    "¿Qué quiere realmente de mí?",
    "¿Cómo está de ánimo?",
]
LENGTHS = {"short": "Corto", "full": "Detallado"}
MESSAGE_COUNTS = [12, 30, 50, 100, 200]
DEFAULT_MESSAGES = 50
SELF_JID = "_yo"
# Tope de caracteres del material enviado al LLM (Groq tiene límites de tokens por minuto bajos)
CHAR_BUDGET = {"Gemini": 150_000, "OpenAI": 80_000, "Claude": 150_000, "Groq": 18_000}
NOTES_IN_PROMPT = 4000
AI_TIMEOUT = 120
_SEP = "-" * 40

_MEDIA_LABELS = {
    "image": "[imagen]", "video": "[video]", "audio": "[nota de voz]", "ptt": "[nota de voz]",
    "document": "[documento]", "sticker": "[sticker]", "location": "[ubicación]",
}
_DISCLAIMER = (
    "Sé concreto y honesto: si hay poca información, indícalo y no inventes. "
    "Es una lectura orientativa, no un diagnóstico clínico."
)


# ─── Material: mensajes → texto ───────────────────────────────────────────────

def _message_line(m: Dict, who: str) -> str:
    text = (m.get("text") or "").strip()
    transcript = (m.get("transcript") or "").strip()
    media = _MEDIA_LABELS.get((m.get("media_type") or "").lower(), "")
    if transcript:
        text = f"{media} (transcripción) {transcript}".strip()
    elif media:
        text = f"{media} {text}".strip()
    if not text:
        return ""
    stamp = ""
    try:
        ts = int(m.get("timestamp") or 0)
        if ts:
            if ts > 10**12:
                ts //= 1000
            stamp = datetime.fromtimestamp(ts).strftime("%d/%m/%y %H:%M")
    except (ValueError, OSError, OverflowError):
        pass
    return f"[{stamp}] {who}: {text}" if stamp else f"{who}: {text}"


def format_conversation(messages: List[Dict], name: str, focus: str = "normal") -> str:
    """Transcript cronológico 'Yo / Contacto: texto' de un chat."""
    me, other = "Yo", name
    lines = (_message_line(m, me if m.get("from_me") else other) for m in messages)
    return "\n".join(l for l in lines if l)


def format_self_material(chats: List[Dict], budget: int) -> Tuple[str, int, int]:
    """Conversaciones agrupadas por chat (mis mensajes como «Yo»; los del otro, si vienen, con su
    nombre como contexto), priorizando los más recientes de cada chat por turnos para que todos los
    chats queden representados dentro del presupuesto de caracteres.
    Devuelve (texto, mensajes míos incluidos, chats incluidos)."""
    per_chat = [[(l, bool(m.get("from_me"))) for m in c["messages"]
                 for l in [_message_line(m, "Yo" if m.get("from_me") else c["name"])] if l] for c in chats]
    headers = [f"### Chat con {c['name']}" for c in chats]
    taken = [0] * len(chats)
    used, full = 0, False
    while not full:
        progress = False
        for i, lines in enumerate(per_chat):
            if taken[i] >= len(lines):
                continue
            cost = len(lines[-1 - taken[i]][0]) + 1 + (len(headers[i]) + 2 if taken[i] == 0 else 0)
            if used + cost > budget:
                full = True
                break
            used += cost
            taken[i] += 1
            progress = True
        if not progress:
            break
    kept = [(i, per_chat[i][-taken[i]:]) for i in range(len(chats)) if taken[i]]
    blocks = [headers[i] + "\n" + "\n".join(l for l, _ in lines) for i, lines in kept]
    n_mine = sum(mine for _, lines in kept for _, mine in lines)
    return "\n\n".join(blocks), n_mine, len(blocks)


# ─── Prompts ──────────────────────────────────────────────────────────────────

_FORMATS = {
    ("normal", "short"): (
        "Responde en español, breve (máximo 150 palabras), en viñetas cortas: personalidad y estilo de "
        "comunicación, estado emocional, puntos fuertes, puntos débiles, y cómo conviene tratarla."
    ),
    ("normal", "full"): (
        "Responde en español, con texto claro (sin tablas) y estas secciones:\n"
        "1. Perfil de personalidad (rasgos predominantes, estilo de comunicación)\n"
        "2. Estado emocional y lo que parece estar viviendo\n"
        "3. Puntos fuertes\n"
        "4. Puntos débiles, inseguridades o temas sensibles\n"
        "5. Necesidades, motivaciones y valores que se perciben\n"
        "6. Cómo se relaciona conmigo (dinámica de la conversación)\n"
        "7. Recomendaciones para comunicarme mejor con esta persona\n"
        "8. Notas clave para recordar (viñetas cortas)"
    ),
    ("interno", "short"): (
        "Responde en español, hablándome de tú, breve (máximo 200 palabras), en viñetas: rasgos de "
        "personalidad, estilo de comunicación, puntos fuertes, puntos débiles y patrones a trabajar."
    ),
    ("interno", "full"): (
        "Responde en español, hablándome de tú, con texto claro (sin tablas) y estas secciones:\n"
        "1. Perfil de personalidad (rasgos predominantes; si se puede, una aproximación a los Big Five)\n"
        "2. Estilo de comunicación (tono, y cómo cambia según con quién hablo)\n"
        "3. Estado emocional y temas recurrentes\n"
        "4. Puntos fuertes\n"
        "5. Puntos débiles y patrones que me juegan en contra\n"
        "6. Cómo me relaciono con los demás (afecto, límites, conflicto, trabajo)\n"
        "7. Recomendaciones concretas de crecimiento personal\n"
        "8. Notas clave para recordar (viñetas cortas)"
    ),
}


def _intro(focus: str, name: str, n_messages: int, n_chats: int, with_context: bool = True,
           relation: str = "") -> str:
    if focus == "interno" and with_context:
        return (
            "Eres un psicólogo experto en análisis de personalidad y comunicación. A continuación tienes "
            f"mis conversaciones de WhatsApp con {n_chats} personas distintas, agrupadas por chat "
            f"({n_messages} mensajes míos). «Yo» soy yo; el resto son las personas con las que hablo. "
            "Haz un análisis psicológico de MÍ. Los mensajes de los demás son solo contexto para "
            "interpretar los míos (a qué respondo, cómo reacciono, cómo me tratan y cómo cambio según la "
            "persona): no analices a los demás."
        )
    if focus == "interno":
        return (
            "Eres un psicólogo experto en análisis de personalidad y comunicación. A continuación tienes "
            f"SOLO mis propios mensajes de WhatsApp ({n_messages} mensajes de {n_chats} conversaciones "
            "distintas, agrupados por chat; no ves lo que me respondieron). Haz un análisis psicológico "
            "de MÍ, el autor de todos estos mensajes."
        )
    return (
        "Eres un psicólogo experto en análisis de personalidad y comunicación interpersonal. "
        f"A continuación tienes los últimos mensajes de una conversación de WhatsApp entre yo y {name}. "
        f"Analiza SOLO a {name}, basándote en lo que escribió/dijo (mis mensajes son contexto)."
        + _relation_line(relation)
    )


def _relation_line(relation: str) -> str:
    """Frase que le cuenta a la IA qué tipo de relación hay (vacía en el análisis general)."""
    relation = (relation or "").strip()
    if not relation or relation.lower() == DEFAULT_KIND.lower():
        return ""
    hint = next((h for k, h in KIND_HINTS.items() if k.lower() == relation.lower()), "")
    return " " + (hint or f"El tipo de relación es: {relation}. Tenlo en cuenta al interpretar el trato y los silencios.")


MINIMAL_WORDS = 25    # por debajo: la persona casi no escribió ("gracias", "ok", "precio?")
MODERATE_WORDS = 120  # por debajo: conversación corta, análisis breve aunque se pida detallado
_STAMP = re.compile(r"^\[[^\]]*\] ")
_MEDIA_TAG = re.compile(r"\[[^\]]*\]|\(transcripción\)")


def subject_stats(material: str, focus: str, name: str) -> Tuple[int, int]:
    """(mensajes, palabras) que escribió el sujeto (el contacto) dentro del material."""
    me, other = "Yo", name
    n_msgs = n_words = 0
    current = None
    for line in material.splitlines():
        body = _STAMP.sub("", line, count=1)
        if body.startswith(other + ": "):
            current, text = "other", body[len(other) + 2:]
            n_msgs += 1
        elif body.startswith(me + ": "):
            current, text = "me", ""
        else:  # continuación de un mensaje de varias líneas
            text = line if current == "other" else ""
        n_words += len(_MEDIA_TAG.sub(" ", text).split())
    return n_msgs, n_words


def _depth_rule(focus: str, n_msgs: int, n_words: int) -> Tuple[str, str]:
    """Devuelve ('minimal' | 'moderate' | 'rich', instrucción) según cuánto escribió el sujeto."""
    who = "esta persona"
    amount = f"{n_msgs} mensaje{'s' if n_msgs != 1 else ''}, ~{n_words} palabras"
    if n_words < MINIMAL_WORDS:
        return "minimal", (
            f"IMPORTANTE: {who} escribió muy poco ({amount}). Responde en español en 1 a 3 líneas: qué "
            "dijo o pidió y, solo si es evidente, qué quiere. NO deduzcas ni infieras nada más "
            "(personalidad, emociones, intenciones, motivaciones): si solo saludó, "
            "agradeció o confirmó, dilo así y termina. No uses las notas previas para rellenar."
        )
    if n_words < MODERATE_WORDS:
        return "moderate", (
            f"La conversación es corta ({who}: {amount}). El análisis debe ser proporcional: breve, "
            "incluye solo los puntos que se apoyan en algo que escribió y omite el resto. No infieras "
            "rasgos ni intenciones sin evidencia directa."
        )
    return "rich", (
        "Cada conclusión debe apoyarse en algo concreto que escribió (cítalo brevemente cuando ayude). "
        "La profundidad debe corresponder a lo que hay en la conversación: no rellenes."
    )


def search_block(searches: List[str], name: str, n_words: int) -> str:
    """Instrucción para responder solo preguntas puntuales sobre el contacto, con veredicto y evidencia."""
    numbered = "\n".join(f"{i}. {q}" for i, q in enumerate(searches, 1))
    parts = [
        f"Responde SOLO estas preguntas sobre {name}, una por una y en español:\n{numbered}",
        "Para cada una indica: Veredicto (Sí / Probable / Poco probable / No / Sin evidencia suficiente), "
        "Confianza (baja / media / alta) y la evidencia concreta, citando brevemente lo que escribió. "
        "No supongas mala intención por defecto ni inventes: si los mensajes no alcanzan, responde "
        "«Sin evidencia suficiente». Cierra con una recomendación práctica de 1 o 2 líneas.",
    ]
    if n_words < MINIMAL_WORDS:
        parts.append(f"{name} escribió muy poco: es esperable que casi todo sea «Sin evidencia suficiente».")
    return "\n\n".join(parts)


def build_prompt(material: str, focus: str, length: str, name: str, n_messages: int,
                 n_chats: int = 1, notes: str = "", with_context: bool = True,
                 relation: str = "", searches: Optional[List[str]] = None) -> str:
    parts = [_intro(focus, name, n_messages, n_chats, with_context, relation)]
    searches = [q.strip() for q in (searches or []) if q and q.strip()]
    if focus == "interno":
        parts += [_FORMATS[(focus, length)], _DISCLAIMER]
    elif searches:
        parts += [search_block(searches, name, subject_stats(material, focus, name)[1]), _DISCLAIMER]
    else:
        level, rule = _depth_rule(focus, *subject_stats(material, focus, name))
        if level == "minimal":
            parts.append(rule)
        else:
            parts += [_FORMATS[(focus, "short" if level == "moderate" else length)], rule, _DISCLAIMER]
    notes = notes.strip()
    if notes:
        parts.append(
            "=== MIS NOTAS (las escribí yo; son mi contexto, no conclusiones de un análisis anterior) ===\n"
            f"{notes[-NOTES_IN_PROMPT:]}\n=== FIN NOTAS ==="
        )
    parts.append("Haz la lectura desde cero, solo con la evidencia de los mensajes: no hay análisis previos.")
    label = ("MIS CONVERSACIONES" if with_context else "MIS MENSAJES") if focus == "interno" else f"CONVERSACIÓN (últimos {n_messages} mensajes)"
    parts.append(f"=== {label} ===\n{material}\n=== FIN ===")
    return "\n\n".join(parts)


def _qa_text(qa: List[Tuple[str, str]]) -> str:
    return "\n\n".join(f"Pregunta: {q}\nRespuesta: {a}" for q, a in qa)


def build_followup_prompt(material: str, focus: str, name: str, analysis: str,
                          qa: List[Tuple[str, str]], question: str) -> str:
    subject = "mí (el autor de los mensajes; háblame de tú)" if focus == "interno" else name
    parts = [
        f"Eres el psicólogo que hizo el análisis de abajo sobre {subject}. Responde en español la nueva "
        "pregunta basándote en el material y el análisis. Si el material no alcanza para responder, "
        "dilo en lugar de inventar.",
        f"=== MATERIAL (los mensajes de otras personas son solo contexto) ===\n{material}\n=== FIN MATERIAL ===",
        f"=== ANÁLISIS ===\n{analysis}\n=== FIN ANÁLISIS ===",
    ]
    if qa:
        parts.append(f"=== PREGUNTAS ANTERIORES ===\n{_qa_text(qa)}\n=== FIN ===")
    parts.append(f"=== NUEVA PREGUNTA ===\n{question}")
    return "\n\n".join(parts)


def build_export(focus: str, name: str, analysis: str, qa: List[Tuple[str, str]],
                 material: str = "") -> str:
    """Texto listo para pegar en otro chatbot y seguir el análisis allí."""
    subject = {
        "interno": "de mí mismo, hecho a partir de mis conversaciones de WhatsApp en todos mis chats",
    }.get(focus, f"de una persona con la que hablo ({name}), hecho a partir de nuestra conversación de WhatsApp")
    parts = [
        f"Te comparto un análisis psicológico {subject}, generado por una IA. Quiero seguir "
        "profundizándolo contigo: tómalo como punto de partida, hazme preguntas si te falta contexto y "
        "señala lo que te parezca dudoso. Es una lectura orientativa, no un diagnóstico clínico.",
        f"=== ANÁLISIS ===\n{analysis.strip()}",
    ]
    if qa:
        parts.append(f"=== PREGUNTAS Y RESPUESTAS ===\n{_qa_text(qa)}")
    if material.strip():
        parts.append(f"=== MENSAJES ANALIZADOS ===\n{material.strip()}")
    return "\n\n".join(parts)


# ─── LLM ──────────────────────────────────────────────────────────────────────

_CONFIG_SECTION = {"Claude": "ANTHROPIC"}


def config_model(provider: str) -> str:
    """Modelo guardado en config.ini para ese proveedor (la sección de Claude es [ANTHROPIC])."""
    return load_app_config().get(_CONFIG_SECTION.get(provider, provider.upper()), "model", fallback="")


def default_provider_model() -> Tuple[str, str]:
    """Proveedor y modelo activos en la configuración de Advanced WhatsApp."""
    cfg = load_app_config()
    prov = cfg.get("IA", "provider", fallback="Gemini").strip()
    prov = "Claude" if "anthropic" in prov.lower() else prov
    prov = next((p for p in PROVIDERS if p.lower() in prov.lower()), "Gemini")
    model = (cfg.get("IA", "model", fallback="").strip()
             or cfg.get(_CONFIG_SECTION.get(prov, prov.upper()), "model", fallback=""))
    return prov, model


def list_models(provider: str) -> List[str]:
    svc = AIService()
    return {"Gemini": svc.get_gemini_models, "Groq": svc.get_groq_models,
            "OpenAI": svc.get_openai_models, "Claude": svc.get_anthropic_models}[provider]()


def ask_llm(prompt: str, provider: Optional[str] = None, model: Optional[str] = None) -> str:
    """Envía el prompt al LLM elegido, sin fallback a otros (bloqueante; usar en un hilo)."""
    svc = AIService()
    model = model or None
    prov = (provider or "").strip().lower()
    if prov == "groq":
        return svc.get_groq_response(prompt, model=model, timeout=AI_TIMEOUT)
    if prov == "openai":
        return svc.get_openai_response(prompt, model=model, timeout=AI_TIMEOUT)
    if prov in ("claude", "anthropic"):
        return svc.get_anthropic_response(prompt, model=model, timeout=AI_TIMEOUT)
    return svc.get_gemini_response(prompt, model_name=model)


# ─── Persistencia: una carpeta por sujeto ────────────────────────────────────

def _safe(jid: str) -> str:
    # sin puntos en los extremos: ".." apuntaría fuera de ANALYSIS_DIR
    return re.sub(r"[^\w.-]", "_", (jid or "desconocido").split("@")[0]).strip(".") or "desconocido"


# Encabezado que la app ponía a cada análisis o respuesta dentro de notas.txt (versiones anteriores)
_AUTO_HEADER = re.compile(r"^===== \d{4}-\d{2}-\d{2} \d{2}:\d{2} · .*? =====[ \t]*$", re.M)


def _split_legacy_notes(path: str) -> None:
    """Versiones anteriores guardaban análisis y notas juntos en notas.txt: lo escrito por el usuario (antes del
    primer encabezado con fecha) queda en notas.txt y el resto pasa a historial.txt."""
    notes = os.path.join(path, "notas.txt")
    try:
        with open(notes, "r", encoding="utf-8", errors="replace") as f:
            raw = f.read()
    except OSError:
        return
    m = _AUTO_HEADER.search(raw)
    if not m:
        return
    with open(os.path.join(path, "historial.txt"), "a", encoding="utf-8") as f:
        f.write("\n" + raw[m.start():].rstrip() + "\n")
    with open(notes, "w", encoding="utf-8") as f:
        f.write(raw[:m.start()].rstrip() + ("\n" if raw[:m.start()].strip() else ""))


def subject_dir(jid: str) -> str:
    path = os.path.join(ANALYSIS_DIR, _safe(jid))
    os.makedirs(path, exist_ok=True)
    _split_legacy_notes(path)
    legacy = os.path.join(ANALYSIS_DIR, f"{_safe(jid)}.txt")
    if os.path.isfile(legacy):  # formato viejo: un .txt suelto por cliente (modo comerciante)
        target = os.path.join(path, "ultimo_comerciante.txt")
        if not os.path.exists(target):
            shutil.move(legacy, target)
            saved = _read_analysis(target)
            if saved:
                append_history(jid, f"{saved['fecha']} · Comerciante (importado)", saved["text"])
        else:
            os.remove(legacy)
    return path


def notes_path(jid: str) -> str:
    return os.path.join(subject_dir(jid), "notas.txt")


def analysis_path(jid: str, focus: str) -> str:
    return os.path.join(subject_dir(jid), f"ultimo_{focus}.txt")


def load_notes(jid: str) -> str:
    try:
        with open(notes_path(jid), "r", encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return ""


def save_notes(jid: str, text: str) -> None:
    with open(notes_path(jid), "w", encoding="utf-8") as f:
        f.write(text)


def history_path(jid: str) -> str:
    return os.path.join(subject_dir(jid), "historial.txt")


def load_history(jid: str) -> str:
    try:
        with open(history_path(jid), "r", encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return ""


def append_history(jid: str, title: str, body: str) -> None:
    """Agrega un análisis o una respuesta al historial (solo para consultar; no se envía a la IA)."""
    with open(history_path(jid), "a", encoding="utf-8") as f:
        f.write(f"\n===== {title} =====\n{body.strip()}\n")


def save_analysis(jid: str, name: str, text: str, focus: str, length: str, provider: str, model: str,
                  n_messages: int, relation: str = "", searches: Optional[List[str]] = None) -> str:
    """Guarda el último análisis del enfoque y lo agrega al historial del sujeto."""
    path = analysis_path(jid, focus)
    fecha = datetime.now().strftime("%Y-%m-%d %H:%M")
    header = (
        f"Sujeto: {name}\nJID: {jid}\nFecha: {fecha}\nEnfoque: {focus}\nModo: {length}\n"
        f"Proveedor: {provider}\nModelo: {model}\nMensajes analizados: {n_messages}\n"
        + (f"Relación: {relation}\n" if relation else "")
        + (f"Búsquedas: {' | '.join(searches)}\n" if searches else "")
        + f"{_SEP}\n"
    )
    with open(path, "w", encoding="utf-8") as f:
        f.write(header + text.strip() + "\n")
    detail = "Búsquedas" if searches else LENGTHS.get(length, length)
    kind = f" ({relation})" if relation and focus != "interno" else ""
    append_history(jid, f"{fecha} · {FOCUSES[focus]}{kind} · {detail} · "
                     f"{provider} {model} · {n_messages} mensajes", text)
    if searches:
        append_history(jid, f"{fecha} · Preguntas de la búsqueda", "\n".join(f"- {q}" for q in searches))
    return path


def save_question(jid: str, focus: str, question: str, answer: str) -> None:
    fecha = datetime.now().strftime("%Y-%m-%d %H:%M")
    append_history(jid, f"{fecha} · Pregunta ({FOCUSES[focus]})", f"P: {question}\n\nR: {answer}")


def _read_analysis(path: str) -> Optional[Dict[str, str]]:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            raw = f.read()
    except OSError:
        return None
    head, sep, body = raw.partition(_SEP + "\n")
    if not sep:
        return {"text": raw.strip(), "fecha": "", "modo": "short", "proveedor": "", "modelo": ""}
    meta = dict(l.split(": ", 1) for l in head.splitlines() if ": " in l)
    return {"text": body.strip(), "fecha": meta.get("Fecha", ""), "modo": meta.get("Modo", "short"),
            "proveedor": meta.get("Proveedor", ""), "modelo": meta.get("Modelo", "")}


def load_analysis(jid: str, focus: str) -> Optional[Dict[str, str]]:
    """Devuelve {'text','fecha','modo','proveedor','modelo'} o None si no hay análisis guardado."""
    return _read_analysis(analysis_path(jid, focus))


def reply_profile(jid: str, notes_limit: int = 1500, analysis_limit: int = 2500) -> str:
    """Lo que se sabe de un contacto para redactar respuestas: mis notas y el último análisis del contacto.
    Nunca incluye el historial. Vacío si no hay nada (y no crea carpetas)."""
    if not jid or not os.path.isdir(os.path.join(ANALYSIS_DIR, _safe(jid))):
        return ""
    parts = []
    notes = load_notes(jid).strip()
    if notes:
        parts.append(f"Mis notas sobre esta persona:\n{notes[-notes_limit:]}")
    saved = load_analysis(jid, "normal")
    if saved and saved["text"].strip():
        parts.append("Lectura psicológica (orientativa, hecha por una IA; puede estar equivocada):\n"
                     f"{saved['text'].strip()[:analysis_limit]}")
    return "\n\n".join(parts)


# ─── Preferencias del diálogo y resultados por modelo ─────────────────────────

def _prefs_path() -> str:
    return os.path.join(ANALYSIS_DIR, "preferencias.json")


def load_prefs() -> Dict:
    """Última configuración usada ('focus_chat','length','count','provider','models','with_context')
    y resultado del último uso de cada modelo ('model_results': {proveedor: {modelo: {...}}})."""
    try:
        with open(_prefs_path(), "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_prefs(prefs: Dict) -> None:
    try:
        with open(_prefs_path(), "w", encoding="utf-8") as f:
            json.dump(prefs, f, ensure_ascii=False, indent=2)
    except OSError:
        pass


def save_prefs(**changes) -> None:
    """Mezcla los cambios con las preferencias guardadas ('models' se mezcla por proveedor)."""
    prefs = load_prefs()
    models = changes.pop("models", None)
    prefs.update(changes)
    if models:
        prefs.setdefault("models", {}).update(models)
    _write_prefs(prefs)


def record_model_result(provider: str, model: str, ok: bool, error: str = "") -> None:
    """Guarda si el último uso del modelo funcionó (verde en el combo) o falló (rojo)."""
    if not model:
        return
    prefs = load_prefs()
    prefs.setdefault("model_results", {}).setdefault(provider, {})[model] = {
        "ok": ok, "fecha": datetime.now().strftime("%Y-%m-%d %H:%M"), "error": error.strip()[:300]}
    _write_prefs(prefs)


def model_results(provider: str) -> Dict[str, Dict]:
    return load_prefs().get("model_results", {}).get(provider, {})


# ─── Tipos de relación y búsquedas propias (se guardan en las preferencias) ───

def load_kinds() -> List[str]:
    """Tipos de relación para el combo: los predeterminados y los que el usuario escribió."""
    custom = [k for k in load_prefs().get("kinds", []) if isinstance(k, str) and k.strip()]
    return list(KIND_HINTS) + [k for k in custom if k not in KIND_HINTS]


def remember_kind(kind: str) -> None:
    """Guarda un tipo de relación escrito a mano para que aparezca la próxima vez."""
    kind = (kind or "").strip()
    if not kind or kind.lower() in (k.lower() for k in load_kinds()):
        return
    save_prefs(kinds=[k for k in load_kinds() if k not in KIND_HINTS] + [kind])


def load_searches() -> List[Tuple[str, bool]]:
    """(texto, es_propia) de cada búsqueda rápida: las predeterminadas y después las del usuario."""
    custom = [q for q in load_prefs().get("searches", []) if isinstance(q, str) and q.strip()]
    return [(q, False) for q in DEFAULT_SEARCHES] + [(q, True) for q in custom if q not in DEFAULT_SEARCHES]


def add_search(text: str) -> None:
    text = (text or "").strip()
    if text and text not in (q for q, _ in load_searches()):
        save_prefs(searches=[q for q, own in load_searches() if own] + [text])


def remove_search(text: str) -> None:
    save_prefs(searches=[q for q, own in load_searches() if own and q != text])
