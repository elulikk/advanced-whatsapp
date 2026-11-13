"""
Advanced WhatsApp - Funciones inteligentes extra
================================================
Lógica (sin widgets) de seis ayudas:
  1. «Lo que te perdiste»: resumen con IA de los mensajes sin leer de un chat con muchos pendientes.
  2. Respuestas rápidas sugeridas: 2-3 respuestas cortas al último mensaje recibido, con caché por mensaje.
  3. Búsqueda por significado: candidatos baratos por LIKE (con sinónimos de la IA) y reranking con IA.
  4. Recordatorios detectados: fecha/hora concreta en un mensaje, detectada con regex (sin IA).
  5. Traducción de mensajes y borradores.
  6. «Requiere respuesta»: chats donde queda sin contestar una pregunta o pedido.
Todo lo que llama a la IA es bloqueante: usarlo en un hilo. Las tres primeras guardan resultados en caché para no
gastar llamadas de más.
"""

import re
import threading
import unicodedata
from collections import OrderedDict
from datetime import datetime, timedelta
from typing import Callable, Dict, List, Optional, Tuple

from app import db
from app.ai import psych_analysis as pa
from app.ai import reply_polisher
from app.ai.ai_service import AIService
from app.config import load_app_config, save_app_config

MISSED_MIN_UNREAD = 15       # desde cuántos sin leer se ofrece «Lo que te perdiste»
MISSED_MAX_MESSAGES = 150    # tope de mensajes que se le mandan a la IA
MISSED_MAX_CHARS = 14000


# ─── Caché chica (en memoria) ────────────────────────────────────────────────

class LruCache:
    """Caché LRU con candado: guarda el resultado de una consulta a la IA para no repetirla."""

    def __init__(self, maxsize: int = 64):
        self.maxsize = maxsize
        self._data: "OrderedDict" = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key):
        with self._lock:
            if key in self._data:
                self._data.move_to_end(key)
                return self._data[key]
        return None

    def put(self, key, value) -> None:
        with self._lock:
            self._data[key] = value
            self._data.move_to_end(key)
            while len(self._data) > self.maxsize:
                self._data.popitem(last=False)

    def __contains__(self, key) -> bool:
        with self._lock:
            return key in self._data

    def clear(self) -> None:
        with self._lock:
            self._data.clear()


def _is_error(out) -> bool:
    return not isinstance(out, str) or not out.strip() or AIService.is_error_response(out)


def _error_text(out) -> str:
    return out.strip() if isinstance(out, str) and out.strip() else "Error: la IA devolvió una respuesta vacía."


def strip_accents(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", text or "") if unicodedata.category(c) != "Mn")


def _msg_text(m: Dict) -> str:
    """Texto de un mensaje para analizarlo: el texto, o la transcripción de una nota de voz."""
    mt = (m.get("media_type") or "text").lower()
    if mt in ("revoked", "sticker"):
        return ""
    if mt == "audio":
        t = (m.get("transcript") or "").strip()
        return "" if t.startswith("(sin voz") else t
    return (m.get("text") or "").strip()


# ─── 6. «Requiere respuesta» ─────────────────────────────────────────────────

_REQUEST_RE = re.compile(
    r"\b(avis[aá]me|avis[aá]|decime|dec[ií]me|d[ií]game|contestame|respondeme|respond[eé]me|confirm[aá]me|confirmame|confirm[aá]|"
    r"pasame|p[aá]same|mandame|m[aá]ndame|enviame|env[ií]ame|pas[aá]|mand[aá]|fijate|fij[aá]te|me (?:pas[aá]s|pas[aá]s|mand[aá]s|"
    r"envi[aá]s|avis[aá]s|confirm[aá]s|dec[ií]s|ayud[aá]s|llam[aá]s)|me puedes|me podr[ií]as|me podés|me podes|"
    r"podr[ií]as|podr[ií]a|podés|podes|puedes|puedo|ten[eé]s|tienes|necesito|te pido|te quer[ií]a pedir|quer[eé]s|quieres|"
    r"cu[aá]ndo|d[oó]nde|c[oó]mo|qu[eé]|cu[aá]l|cu[aá]nto|qui[eé]n|por qu[eé]|hac[eé]s|vas a|vienes|ven[ií]s|vamos|llegas|llegás)\b",
    re.I)
_ACK_RE = re.compile(r"^\W*(ok|oka|okey|dale|listo|gracias|graciass*|jaja+j*|jeje+|bueno|genial|perfecto|👍|👌|🙏)\W*$", re.I)


def looks_like_request(text: str) -> bool:
    """¿El mensaje hace una pregunta o pide algo? Heurística barata: signo de pregunta, o pedido explícito."""
    t = (text or "").strip()
    if not t or _ACK_RE.match(t):
        return False
    if "?" in t or "¿" in t:
        return True
    return bool(len(t) >= 8 and _REQUEST_RE.search(t.split("\n")[-1] if len(t) > 400 else t) and
                re.search(r"\b(avis|decime|dec[ií]me|pasame|p[aá]same|mandame|m[aá]ndame|enviame|confirm|contest|respond|"
                          r"fijate|necesito|te pido|podr[ií]as|podés|podes|puedes|me pas|me mand|me avis|me conf)", t, re.I))


def needs_reply_from_messages(msgs: List[Dict]) -> bool:
    """msgs en orden cronológico (los últimos del chat). True si, después de mi último mensaje, la otra persona dejó
    una pregunta o pedido (en cualquiera de sus últimos mensajes seguidos) sin que yo haya respondido."""
    tail: List[Dict] = []
    for m in reversed(msgs):
        if (m.get("media_type") or "") == "revoked":
            continue
        if m.get("from_me"):
            break
        tail.append(m)
        if len(tail) >= 5:
            break
    return any(looks_like_request(_msg_text(m)) for m in tail)


def _recent_by_chat(jids: List[str], per_chat: int = 6) -> Dict[str, List[Dict]]:
    """Últimos `per_chat` mensajes de cada chat, en orden cronológico."""
    out: Dict[str, List[Dict]] = {j: [] for j in jids}
    if not jids:
        return out
    with db.db_conn() as conn:
        marks = ",".join("?" * len(jids))
        try:
            rows = conn.execute(
                "SELECT * FROM (SELECT m.*, ROW_NUMBER() OVER (PARTITION BY chat_jid ORDER BY timestamp DESC) AS rn "
                f"FROM messages m WHERE chat_jid IN ({marks})) WHERE rn <= ?", [*jids, per_chat]).fetchall()
        except Exception:   # SQLite sin funciones de ventana: una consulta por chat
            rows = []
            for j in jids:
                rows += conn.execute("SELECT * FROM messages WHERE chat_jid = ? ORDER BY timestamp DESC LIMIT ?",
                                     (j, per_chat)).fetchall()
        for r in rows:
            d = dict(r)
            out.setdefault(d["chat_jid"], []).append(d)
    for lst in out.values():
        lst.sort(key=lambda m: (m.get("timestamp") or 0))
    return out


def needs_reply_jids(jids: List[str], max_age_days: int = 14) -> set:
    """Subconjunto de `jids` (chats individuales) con una pregunta/pedido mío sin contestar."""
    jids = [j for j in jids if j and not j.endswith("@g.us")]
    if not jids:
        return set()
    from app import chat_lock
    recent = _recent_by_chat(jids)
    limit = int(datetime.now().timestamp()) - max_age_days * 86400
    result = set()
    for j, msgs in recent.items():
        if not msgs or chat_lock.is_locked(j):
            continue
        if (msgs[-1].get("timestamp") or 0) < limit:
            continue
        if needs_reply_from_messages(msgs):
            result.add(j)
    return result


def unanswered_chats(limit: int = 40, offset: int = 0, max_age_days: int = 14, scan: int = 250) -> List[Dict]:
    """Chats individuales con algo sin responder, del más reciente al más viejo (para el filtro «Sin responder»)."""
    since = int(datetime.now().timestamp()) - max_age_days * 86400
    with db.db_conn() as conn:
        rows = conn.execute(
            f"SELECT * FROM chats WHERE {db.INDIVIDUAL_CHAT_SQL} AND last_message_time >= ? "
            "AND jid NOT IN (SELECT chat_jid FROM chat_locks) ORDER BY last_message_time DESC LIMIT ?",
            (since, scan)).fetchall()
    chats = [dict(r) for r in rows]
    pending = needs_reply_jids([c["jid"] for c in chats], max_age_days)
    out = []
    for c in chats:
        if c["jid"] in pending:
            c["needs_reply"] = True
            out.append(c)
    return out[offset:offset + limit]


# ─── 4. Recordatorios detectados (fechas y horas en español) ─────────────────

_WEEKDAYS = {"lunes": 0, "martes": 1, "miercoles": 2, "jueves": 3, "viernes": 4, "sabado": 5, "domingo": 6}
_WEEKDAY_NAMES = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
_MONTHS = {"enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6, "julio": 7, "agosto": 8,
           "septiembre": 9, "setiembre": 9, "octubre": 10, "noviembre": 11, "diciembre": 12}

_TIME_RE = re.compile(
    r"(?:\ba la?s?\s+(\d{1,2})(?:[:.h](\d{2}))?\s*(?:hs?\.?|horas?)?(?:\s*(am|pm|a\.m\.|p\.m\.))?"
    r"(?:\s*(?:de la|del|en la|por la)\s+(manana|tarde|noche|madrugada|mediodia))?)"
    r"|(?:\b(\d{1,2}):(\d{2})\s*(?:hs?\.?|horas?)?(?:\s*(am|pm))?\b)"
    r"|(?:\b(\d{1,2})\s*(am|pm)\b)"
    r"|(?:\b(\d{1,2})\s*(?:hs|h)\b)", re.I)
_REL_RE = re.compile(r"\ben\s+(\d{1,3}|una|un|media|dos|tres|cuatro|cinco)\s*(minutos?|min|horas?|hs|d[ií]as?|semanas?)\b", re.I)
_NUMWORDS = {"un": 1, "una": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5}


def _norm(text: str) -> str:
    return strip_accents((text or "").lower())


def _parse_time(norm: str) -> Optional[Tuple[int, int, bool]]:
    """(hora, minuto, explícita_am_pm) de la primera hora encontrada en el texto normalizado, o None."""
    for m in _TIME_RE.finditer(norm):
        g = m.groups()
        if g[0] is not None:
            hh, mm, ap, period = int(g[0]), int(g[1] or 0), g[2], g[3]
            strong = True
        elif g[4] is not None:
            hh, mm, ap, period = int(g[4]), int(g[5]), g[6], None
            strong = False
        elif g[7] is not None:
            hh, mm, ap, period = int(g[7]), 0, g[8], None
            strong = False
        else:
            hh, mm, ap, period = int(g[9]), 0, None, None
            strong = False
        if hh > 24 or mm > 59:
            continue
        ap = (ap or "").replace(".", "")
        if ap == "pm" and hh < 12:
            hh += 12
        elif ap == "am" and hh == 12:
            hh = 0
        elif not ap and period:
            if period in ("tarde", "noche") and hh < 12:
                hh += 12
            elif period == "noche" and hh == 12:
                hh = 0
            elif period == "mediodia" and hh < 6:
                hh += 12
        elif not ap and strong and 1 <= hh <= 7:
            hh += 12    # «a las 5» casi siempre es de la tarde; de 8 a 11 se deja como mañana
        if hh == 24:
            hh = 0
        return hh, mm, bool(ap or period)
    return None


def detect_datetime(text: str, ref_ts: Optional[int] = None) -> Optional[Dict]:
    """Detecta una fecha/hora concreta ("nos vemos el jueves a las 8", "el 15 de marzo", "mañana 18:30", "en 2 horas").
    ref_ts es la hora del mensaje (los «mañana» se cuentan desde ahí). Devuelve {'ts', 'label', 'has_time'} o None.
    Sin IA: pensado para correr sobre cada burbuja. El resultado puede estar en el pasado: el que llama decide."""
    raw = (text or "").strip()
    if len(raw) < 4 or len(raw) > 600:
        return None
    ref = datetime.fromtimestamp(ref_ts if ref_ts and ref_ts < 10 ** 12 else (ref_ts or 0) // 1000) if ref_ts \
        else datetime.now()
    norm = _norm(raw)

    # Relativos: «en 2 horas», «en 30 minutos», «en 3 días»
    rel = _REL_RE.search(norm)
    if rel:
        n_raw = rel.group(1)
        n = 0.5 if n_raw == "media" else float(_NUMWORDS.get(n_raw, n_raw) if not n_raw.isdigit() else n_raw)
        unit = rel.group(2)
        delta = (timedelta(minutes=n) if unit.startswith("min") else timedelta(hours=n) if unit.startswith(("hora", "hs"))
                 else timedelta(days=n) if unit.startswith("di") else timedelta(weeks=n))
        if unit.startswith(("hora", "hs", "min")) and n > 0 and n <= 72 or unit.startswith(("di", "sem")) and 0 < n <= 60:
            dt = ref + delta
            return {"ts": int(dt.timestamp()), "label": _label(dt, True), "has_time": True}

    # «de la mañana» no es el día de mañana
    clean = re.sub(r"\b(?:de|a|por|en|la)\s+la\s+manana\b|\bde\s+manana\b(?!\s+(?:a|temprano))", " ", norm)
    clean = re.sub(r"\bpor\s+la\s+manana\b|\ba\s+la\s+manana\b|\ben\s+la\s+manana\b", " ", clean)
    tm = _parse_time(norm)

    day: Optional[datetime] = None
    explicit_date = False
    m = re.search(r"\b(\d{1,2})\s+de\s+(" + "|".join(_MONTHS) + r")(?:\s+(?:de|del)\s+(\d{4}))?\b", clean)
    if m:
        d, mo = int(m.group(1)), _MONTHS[m.group(2)]
        y = int(m.group(3)) if m.group(3) else ref.year
        day = _safe_date(y, mo, d, ref, bool(m.group(3)))
        explicit_date = day is not None
    if day is None:
        m = re.search(r"\b(?:el|para el|dia|del|este|hasta el)\s+(\d{1,2})[/-](\d{1,2})(?:[/-](\d{2,4}))?\b", clean)
        if m:
            y = int(m.group(3)) if m.group(3) else ref.year
            y = y + 2000 if y < 100 else y
            day = _safe_date(y, int(m.group(2)), int(m.group(1)), ref, bool(m.group(3)))
            explicit_date = day is not None
    if day is None:
        if re.search(r"\bpasado\s+manana\b", clean):
            day = (ref + timedelta(days=2)).replace(hour=0, minute=0, second=0, microsecond=0)
        elif re.search(r"\bmanana\b", clean):
            day = (ref + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        elif re.search(r"\bhoy\b", clean):
            day = ref.replace(hour=0, minute=0, second=0, microsecond=0)
        else:
            wd = re.search(r"\b(?:el|este|proximo|pr[oó]ximo|del|para el|hasta el)?\s*(" + "|".join(_WEEKDAYS) + r")\b", clean)
            if wd:
                target = _WEEKDAYS[wd.group(1)]
                ahead = (target - ref.weekday()) % 7
                if ahead == 0 and tm and (tm[0], tm[1]) <= (ref.hour, ref.minute):
                    ahead = 7
                day = (ref + timedelta(days=ahead)).replace(hour=0, minute=0, second=0, microsecond=0)

    if day is None and tm is None:
        return None
    if day is None:
        # Solo hora («a las 8»): hoy si todavía no pasó, si no mañana. Exige «a las» (no un «8:30» suelto: puede ser un ratio).
        if not re.search(r"\ba la?s?\s+\d", norm):
            return None
        day = ref.replace(hour=0, minute=0, second=0, microsecond=0)
        if (tm[0], tm[1]) <= (ref.hour, ref.minute):
            day += timedelta(days=1)
    elif tm is None and not explicit_date:
        return None   # «mañana» o «el jueves» sin hora son demasiado comunes para ofrecer un recordatorio
    hh, mm = (tm[0], tm[1]) if tm else (9, 0)
    dt = day.replace(hour=hh, minute=mm)
    return {"ts": int(dt.timestamp()), "label": _label(dt, tm is not None), "has_time": tm is not None}


def _safe_date(y: int, mo: int, d: int, ref: datetime, year_given: bool) -> Optional[datetime]:
    try:
        dt = datetime(y, mo, d)
    except ValueError:
        return None
    if not year_given and dt.date() < ref.date():
        try:
            dt = datetime(y + 1, mo, d)
        except ValueError:
            return None
    return dt


def _label(dt: datetime, with_time: bool) -> str:
    s = f"{_WEEKDAY_NAMES[dt.weekday()]} {dt.day}/{dt.month}"
    return f"{s} {dt:%H:%M}" if with_time else s


def future_datetime(msg: Dict, now: Optional[float] = None) -> Optional[Dict]:
    """detect_datetime sobre un mensaje (texto o transcripción), solo si la fecha todavía no pasó."""
    text = _msg_text(msg)
    if not text:
        return None
    found = detect_datetime(text, msg.get("timestamp"))
    if found and found["ts"] > (now or datetime.now().timestamp()) + 30:
        return found
    return None


# ─── 2. Respuestas rápidas ───────────────────────────────────────────────────

QUICK_CONTEXT = 12
_quick_cache = LruCache(64)

_QUICK_RULES = (
    "Eres mi asistente de redacción. Te paso los últimos mensajes de una conversación de WhatsApp. Escribe 3 respuestas "
    "MUY cortas y distintas entre sí (por ejemplo una afirmativa, una negativa o postergando, y una que pregunte algo) al "
    "último mensaje de la otra persona, tal como las escribiría yo ('Yo').\n"
    "Reglas:\n"
    "- Cada una de 1 a 12 palabras, natural, imitando mi forma de escribir en la conversación (idioma, tratamiento "
    "tú/vos/usted, emojis).\n"
    "- No inventes datos, horarios, precios ni compromisos que no estén en la conversación.\n"
    "- Responde ÚNICAMENTE con las 3 respuestas, una por línea, sin numerar, sin comillas y sin explicaciones."
)


def build_quick_prompt(context: str, profile: str = "") -> str:
    parts = [_QUICK_RULES]
    if profile.strip():
        parts.append(reply_polisher._profile_block(profile))
    parts.append(f"=== ÚLTIMOS MENSAJES DEL CHAT ===\n{context.strip()}\n=== FIN ===")
    return "\n\n".join(parts)


def parse_quick_replies(raw: str, limit: int = 3) -> List[str]:
    """Líneas de la respuesta de la IA → hasta `limit` respuestas limpias y sin repetir."""
    text = reply_polisher.clean_reply(raw or "")
    out: List[str] = []
    for line in text.splitlines():
        s = re.sub(r"^\s*(?:[-*•>]+|\d+[.)])\s*", "", line).strip()
        s = reply_polisher.clean_reply(s).strip()
        if not s or len(s) > 160 or s.lower() in (x.lower() for x in out):
            continue
        out.append(s)
        if len(out) >= limit:
            break
    return out


def quick_cache_key(chat_jid: str, last_msg_id: str) -> Tuple[str, str]:
    return (chat_jid, last_msg_id)


def cached_quick_replies(chat_jid: str, last_msg_id: str) -> Optional[List[str]]:
    return _quick_cache.get(quick_cache_key(chat_jid, last_msg_id))


def should_suggest(last_msg: Optional[Dict]) -> bool:
    """Solo se sugiere cuando lo último del chat es un mensaje de la otra persona con algo para contestar."""
    return bool(last_msg) and not last_msg.get("from_me") and bool(_msg_text(last_msg))


def quick_replies(chat_jid: str, messages: List[Dict], contact_name: str, provider: str, model: str,
                  profile: str = "", force: bool = False) -> Tuple[List[str], str]:
    """(respuestas, error). Usa la caché por último mensaje salvo force. Bloqueante."""
    if not messages:
        return [], "No hay mensajes."
    last = messages[-1]
    key = quick_cache_key(chat_jid, last.get("id", ""))
    if not force:
        hit = _quick_cache.get(key)
        if hit is not None:
            return hit, ""
    context = reply_polisher.format_recent_context(messages[-QUICK_CONTEXT:], contact_name)
    if not context.strip():
        return [], "No hay mensajes de texto."
    out = pa.ask_llm(build_quick_prompt(context, profile), provider, model)
    if _is_error(out):
        return [], _error_text(out)
    replies = parse_quick_replies(out)
    if not replies:
        return [], "La IA no devolvió respuestas."
    _quick_cache.put(key, replies)
    return replies, ""


# ─── 1. «Lo que te perdiste» ─────────────────────────────────────────────────

_missed_cache = LruCache(24)

MISSED_SECTIONS = ["RESUMEN", "QUIÉN DIJO QUÉ", "DECISIONES", "PREGUNTAS PARA MÍ"]

_MISSED_RULES = (
    "Eres mi asistente. Te paso los mensajes de WhatsApp que NO leí de un chat (yo soy 'Yo'; si aparece 'Yo' es porque "
    "ya respondí algo). Hazme un resumen para ponerme al día rápido.\n"
    "Reglas:\n"
    "- Usa EXACTAMENTE estas secciones, cada título en su línea terminado en dos puntos, y omite las que estén vacías:\n"
    "RESUMEN: (2-4 líneas con lo esencial)\n"
    "QUIÉN DIJO QUÉ: (viñetas «- Nombre: lo más importante que dijo», una por persona relevante)\n"
    "DECISIONES: (viñetas con lo que se acordó o resolvió, con fechas/horas/lugares)\n"
    "PREGUNTAS PARA MÍ: (viñetas con lo que me preguntaron o esperan de mí, indicando quién)\n"
    "- Conserva datos concretos (nombres, números, fechas, horarios, direcciones). No inventes nada.\n"
    "- Idioma de la conversación, directo y sin relleno."
)


def build_missed_prompt(conversation: str, chat_name: str, is_group: bool) -> str:
    kind = "grupo" if is_group else "chat"
    return (f"{_MISSED_RULES}\n\n=== MENSAJES SIN LEER ({kind} «{chat_name}») ===\n{conversation.strip()}\n=== FIN ===")


def format_missed_conversation(messages: List[Dict], contact_name: str,
                               max_chars: int = MISSED_MAX_CHARS) -> str:
    """Los mensajes (cronológicos) como líneas «[fecha] Nombre: texto»; si pasa el tope, conserva los más recientes."""
    lines = []
    for m in messages:
        who = "Yo" if m.get("from_me") else (m.get("sender_name") or contact_name or "Contacto")
        line = pa._message_line(m, who)
        if line:
            lines.append(line[:600])
    while lines and sum(len(l) + 1 for l in lines) > max_chars:
        lines.pop(0)
    return "\n".join(lines)


def parse_missed(raw: str) -> List[Tuple[str, List[str]]]:
    """[(título, [líneas])] según MISSED_SECTIONS; si la IA no respetó el formato, todo va como RESUMEN."""
    t = reply_polisher.clean_reply(raw or "")
    if not t:
        return []
    labels = {strip_accents(s).upper(): s for s in MISSED_SECTIONS}
    sections: List[Tuple[str, List[str]]] = []
    current: Optional[Tuple[str, List[str]]] = None
    for line in t.splitlines():
        stripped = line.strip().strip("*#").strip()
        m = re.match(r"^([A-ZÁÉÍÓÚÑa-záéíóúñ ]+?)\s*:\s*(.*)$", stripped)
        key = strip_accents(m.group(1)).upper().strip() if m else ""
        if m and key in labels:
            current = (labels[key], [])
            sections.append(current)
            if m.group(2).strip():
                current[1].append(m.group(2).strip())
        elif stripped:
            if current is None:
                current = ("RESUMEN", [])
                sections.append(current)
            current[1].append(re.sub(r"^[-*•]\s*", "", stripped))
    return sections


def missed_cache_key(chat_jid: str, last_msg_id: str, n: int) -> Tuple[str, str, int]:
    return (chat_jid, last_msg_id, n)


def cached_missed(chat_jid: str, last_msg_id: str, n: int) -> Optional[str]:
    return _missed_cache.get(missed_cache_key(chat_jid, last_msg_id, n))


def missed_messages(chat_jid: str, unread: int) -> List[Dict]:
    """Los últimos `unread` mensajes recibidos del chat (con los míos intercalados que haya en el medio)."""
    msgs = db.get_messages(chat_jid, limit=min(max(unread, 1), MISSED_MAX_MESSAGES))
    return msgs


def summarize_missed(chat_jid: str, messages: List[Dict], chat_name: str, is_group: bool, unread: int,
                     provider: str, model: str, force: bool = False) -> Tuple[str, str]:
    """(texto crudo del resumen, error). Caché por (chat, último mensaje, cantidad). Bloqueante."""
    if not messages:
        return "", "No hay mensajes para resumir."
    key = missed_cache_key(chat_jid, messages[-1].get("id", ""), unread)
    if not force:
        hit = _missed_cache.get(key)
        if hit:
            return hit, ""
    conv = format_missed_conversation(messages, chat_name)
    if not conv.strip():
        return "", "No hay mensajes de texto para resumir."
    out = pa.ask_llm(build_missed_prompt(conv, chat_name, is_group), provider, model)
    if _is_error(out) or not parse_missed(out):
        return "", _error_text(out)
    _missed_cache.put(key, out.strip())
    return out.strip(), ""


# ─── 5. Traducción ───────────────────────────────────────────────────────────

LANGUAGES = ["español", "inglés", "portugués", "francés", "italiano", "alemán", "catalán", "japonés", "chino"]
DEFAULT_TRANSLATE_LANG = "español"
_translate_cache = LruCache(200)

_TRANSLATE_RULES = (
    "Traduce el texto al idioma indicado. Reglas:\n"
    "- Conserva el sentido, el tono (formal/informal), los emojis, los saltos de línea, los nombres propios, números, "
    "enlaces y menciones tal cual.\n"
    "- Si el texto ya está en ese idioma, devuélvelo igual.\n"
    "- Responde ÚNICAMENTE con la traducción: sin comillas, sin notas ni explicaciones."
)


def get_translate_lang() -> str:
    lang = load_app_config().get("GENERAL", "translate_lang", fallback=DEFAULT_TRANSLATE_LANG).strip()
    return lang or DEFAULT_TRANSLATE_LANG


def set_translate_lang(lang: str) -> bool:
    cfg = load_app_config()
    cfg.set("GENERAL", "translate_lang", (lang or DEFAULT_TRANSLATE_LANG).strip())
    return save_app_config(cfg)


def build_translate_prompt(text: str, target: str) -> str:
    return f"{_TRANSLATE_RULES}\n\n=== IDIOMA DE DESTINO ===\n{target}\n\n=== TEXTO ===\n{text.strip()}\n=== FIN ==="


def translate(text: str, target: str, provider: str, model: str) -> Tuple[str, str]:
    """(traducción, error). Con caché por (texto, idioma). Bloqueante."""
    text = (text or "").strip()
    if not text:
        return "", "No hay texto para traducir."
    key = (text, target.lower())
    hit = _translate_cache.get(key)
    if hit:
        return hit, ""
    out = pa.ask_llm(build_translate_prompt(text, target), provider, model)
    if _is_error(out):
        return "", _error_text(out)
    res = reply_polisher.clean_reply(out)
    if not res:
        return "", "La IA devolvió una respuesta vacía."
    _translate_cache.put(key, res)
    return res, ""


# ─── 3. Búsqueda por significado ─────────────────────────────────────────────

SEARCH_CANDIDATES = 40       # cuántos candidatos se le mandan a la IA para ordenar
SEARCH_RESULTS = 12
_search_cache = LruCache(30)

_STOPWORDS = set(strip_accents(w) for w in (
    "el la los las un una unos unas de del al y o u e en a ante con para por sobre sin que qué como cómo cuando cuándo donde "
    "dónde quien quién cual cuál cuanto cuánto es era fue son eran ser estar esta está estaba estaban hay había me te se lo le "
    "les nos mi tu su mis tus sus yo vos tú vos mensaje mensajes chat dijo dijeron hablaron habló hablamos hable busco buscar "
    "alguien algo todo toda ya muy más mas pero si sí no ni cosa cosas ese esa eso este esto").split())


def query_keywords(query: str, limit: int = 8) -> List[str]:
    """Palabras significativas de la consulta, sin acentos ni palabras vacías, sin repetir."""
    out: List[str] = []
    for w in re.findall(r"[\wñÑ]+", strip_accents(query or "").lower()):
        if len(w) < 3 or w in _STOPWORDS or w in out:
            continue
        out.append(w)
    return out[:limit]


def _stem(word: str) -> str:
    """Raíz tosca para el LIKE: quita 2 letras finales de palabras largas (cumpleaños→cumplean, viajamos→viajam)."""
    return word[:-2] if len(word) >= 7 else word[:-1] if len(word) == 6 else word


def build_expand_prompt(query: str) -> str:
    return ("Voy a buscar mensajes de WhatsApp (incluidas transcripciones de audios) por significado. Dame hasta 12 "
            "palabras sueltas, en español rioplatense, que probablemente aparezcan en el mensaje que busco: sinónimos, "
            "formas relacionadas y términos concretos que se usarían de verdad al escribirlo. Responde SOLO con las "
            "palabras separadas por comas, sin explicaciones.\n\n"
            f"=== BÚSQUEDA ===\n{query.strip()}\n=== FIN ===")


def parse_keywords(raw: str, limit: int = 14) -> List[str]:
    out: List[str] = []
    for w in re.split(r"[,\n;]+", reply_polisher.clean_reply(raw or "")):
        w = strip_accents(w.strip().strip("-*•\"'").strip().lower())
        if 3 <= len(w) <= 24 and re.fullmatch(r"[\w ñ]+", w) and w not in _STOPWORDS and w not in out:
            out.append(w)
        if len(out) >= limit:
            break
    return out


def search_candidates(terms: List[str], limit: int = SEARCH_CANDIDATES, scan: int = 600) -> List[Dict]:
    """Mensajes (texto o transcripción) que contienen alguna de las palabras, con los que matchean más palabras primero.
    Excluye chats bloqueados. Solo SQL + LIKE: barato."""
    stems = list(dict.fromkeys(_stem(t) for t in terms if t))
    if not stems:
        return []
    cond = " OR ".join("(m.text LIKE ? OR m.transcript LIKE ?)" for _ in stems)
    params: list = []
    for s in stems:
        like = f"%{s.replace('n', '_')}%"   # «_» deja pasar la ñ (el texto guardado conserva los acentos y las ñ)
        params += [like, like]
    with db.db_conn() as conn:
        rows = conn.execute(
            "SELECT m.*, c.name AS chat_name, c.custom_name AS chat_custom_name, c.is_group AS chat_is_group "
            "FROM messages m JOIN chats c ON c.jid = m.chat_jid "
            f"WHERE ({cond}) AND COALESCE(m.media_type,'text') != 'revoked' "
            "AND m.chat_jid NOT IN (SELECT chat_jid FROM chat_locks) ORDER BY m.timestamp DESC LIMIT ?",
            [*params, scan]).fetchall()
    scored = []
    for r in rows:
        d = dict(r)
        hay = strip_accents(f"{d.get('text') or ''} {d.get('transcript') or ''}").lower()
        score = sum(1 for s in stems if s in hay)
        if score:
            scored.append((score, d.get("timestamp") or 0, d))
    scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
    return [d for _, _, d in scored[:limit]]


def candidate_line(i: int, m: Dict, width: int = 220) -> str:
    text = " ".join(_msg_text(m).split())
    if len(text) > width:
        text = text[:width] + "…"
    who = "Yo" if m.get("from_me") else (m.get("sender_name") or m.get("chat_custom_name") or m.get("chat_name") or "Contacto")
    kind = " (audio transcripto)" if (m.get("media_type") or "") == "audio" else ""
    return f"{i}. [{who}]{kind} {text}"


def build_rerank_prompt(query: str, candidates: List[Dict]) -> str:
    lines = "\n".join(candidate_line(i + 1, m) for i, m in enumerate(candidates))
    return ("Busco un mensaje de WhatsApp por su significado. Te paso la búsqueda y una lista numerada de mensajes "
            "candidatos. Elige los que responden a lo que busco, del más relevante al menos relevante (máximo "
            f"{SEARCH_RESULTS}). Si ninguno sirve, responde NINGUNO.\n"
            "Responde SOLO con los números separados por comas, sin explicaciones.\n\n"
            f"=== BÚSQUEDA ===\n{query.strip()}\n=== FIN ===\n\n=== CANDIDATOS ===\n{lines}\n=== FIN ===")


def parse_rerank(raw: str, n: int) -> List[int]:
    """Índices (base 0) válidos y sin repetir, en el orden que dio la IA."""
    out: List[int] = []
    for tok in re.findall(r"\d+", raw or ""):
        i = int(tok) - 1
        if 0 <= i < n and i not in out:
            out.append(i)
    return out[:SEARCH_RESULTS]


def semantic_search(query: str, provider: str, model: str) -> Tuple[List[Dict], str]:
    """(mensajes ordenados por relevancia, aviso). Máximo 2 llamadas a la IA (sinónimos + reranking) y con caché por
    búsqueda; si la IA falla, devuelve los candidatos por coincidencia de palabras con un aviso. Bloqueante."""
    q = " ".join((query or "").split())
    if len(q) < 3:
        return [], "Escribí qué querés encontrar."
    key = q.lower()
    hit = _search_cache.get(key)
    if hit is not None:
        ids, note = hit
        found = [m for m in (db.get_message(i) for i in ids) if m]
        if found:
            return _with_chat_info(found), note

    terms = query_keywords(q)
    note = ""
    exp = pa.ask_llm(build_expand_prompt(q), provider, model)
    if _is_error(exp):
        note = "La IA no está disponible: resultados por palabras."
    else:
        terms = list(dict.fromkeys(terms + parse_keywords(exp)))
    cands = search_candidates(terms)
    if not cands:
        return [], note or "No encontré mensajes parecidos."
    ranked = cands[:SEARCH_RESULTS]
    if not note:
        out = pa.ask_llm(build_rerank_prompt(q, cands), provider, model)
        if _is_error(out):
            note = "No pude ordenar con IA: resultados por palabras."
        elif "ningun" in strip_accents(out).lower() and not re.search(r"\d", out):
            return [], "Ningún mensaje responde a eso."
        else:
            idx = parse_rerank(out, len(cands))
            if idx:
                ranked = [cands[i] for i in idx]
    if not note:
        _search_cache.put(key, ([m["id"] for m in ranked], note))
    return ranked, note


def _with_chat_info(msgs: List[Dict]) -> List[Dict]:
    for m in msgs:
        if "chat_name" not in m:
            chat = db.get_chat(m.get("chat_jid", "")) or {}
            m["chat_name"] = chat.get("name") or ""
            m["chat_custom_name"] = chat.get("custom_name") or ""
            m["chat_is_group"] = chat.get("is_group") or 0
    return msgs


# ─── Peticiones en segundo plano (hilo + callback) ───────────────────────────

def run_async(work: Callable[[], object], done: Callable[[object], None]) -> None:
    """Corre `work` en un hilo daemon y llama a `done(resultado)` desde ese hilo (conectarlo a una Signal)."""
    def target():
        try:
            res = work()
        except Exception as e:   # nunca dejar al llamador esperando
            res = e
        done(res)
    threading.Thread(target=target, name="advancedwhatsapp-ia-extra", daemon=True).start()
