import os, sys, time
from datetime import datetime
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest
import app.db as db
from app import reminders
from app.ai import smart_features as sf

ME_JID = "111@s.whatsapp.net"


@pytest.fixture
def tmpdb(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "t.db"))
    db.init_db()


def ts(y, mo, d, h=12, mi=0):
    return int(datetime(y, mo, d, h, mi).timestamp())


# Lunes 2 de marzo de 2026, 10:00
REF = ts(2026, 3, 2, 10)


# ─── Fechas ───────────────────────────────────────────────────────────────────

def test_detect_weekday_with_time():
    r = sf.detect_datetime("nos vemos el jueves a las 8", REF)
    dt = datetime.fromtimestamp(r["ts"])
    assert (dt.month, dt.day, dt.hour, dt.minute) == (3, 5, 8, 0)
    assert r["has_time"] and "jueves" in r["label"]


def test_detect_afternoon_hours_and_qualifiers():
    assert datetime.fromtimestamp(sf.detect_datetime("mañana a las 5", REF)["ts"]).hour == 17
    assert datetime.fromtimestamp(sf.detect_datetime("mañana a las 9 de la noche", REF)["ts"]).hour == 21
    r = sf.detect_datetime("mañana a las 10 de la mañana", REF)
    dt = datetime.fromtimestamp(r["ts"])
    assert (dt.day, dt.hour) == (3, 10)
    assert datetime.fromtimestamp(sf.detect_datetime("pasado mañana 18:30", REF)["ts"]).day == 4
    assert datetime.fromtimestamp(sf.detect_datetime("viernes 8pm", REF)["ts"]).hour == 20


def test_detect_explicit_dates():
    r = sf.detect_datetime("la reunión es el 15 de abril", REF)
    dt = datetime.fromtimestamp(r["ts"])
    assert (dt.month, dt.day, dt.hour) == (4, 15, 9) and not r["has_time"]
    r = sf.detect_datetime("entrega para el 20/3 a las 14:00", REF)
    dt = datetime.fromtimestamp(r["ts"])
    assert (dt.month, dt.day, dt.hour) == (3, 20, 14)
    # una fecha sin año que ya pasó se asume del año siguiente
    assert datetime.fromtimestamp(sf.detect_datetime("el 1 de febrero", REF)["ts"]).year == 2027


def test_detect_relative_and_time_only():
    r = sf.detect_datetime("llego en 2 horas", REF)
    assert r["ts"] == REF + 2 * 3600
    r = sf.detect_datetime("pasame a buscar a las 8:30", REF)
    dt = datetime.fromtimestamp(r["ts"])
    assert (dt.day, dt.hour, dt.minute) == (2, 8, 30) or (dt.day, dt.hour) == (3, 8)   # 8:30 ya pasó a las 10:00 → mañana
    assert dt.day == 3


def test_detect_ignores_vague_or_unrelated_text():
    for t in ["buen día, hasta mañana", "nos vemos el jueves", "hola cómo estás", "son 3.50 por kilo",
              "me quedé 1/2 hora", "ok", ""]:
        assert sf.detect_datetime(t, REF) is None, t


def test_future_datetime_skips_past_messages():
    old = {"id": "a", "text": "mañana a las 8", "timestamp": ts(2020, 1, 1)}
    assert sf.future_datetime(old) is None
    soon = {"id": "b", "text": "mañana a las 8", "timestamp": int(time.time())}
    assert sf.future_datetime(soon) is not None
    voice = {"id": "c", "media_type": "audio", "transcript": "el jueves a las 7 en casa",
             "timestamp": int(time.time())}
    assert sf.future_datetime(voice) is not None


# ─── Requiere respuesta ──────────────────────────────────────────────────────

def m(i, text, from_me=False, **kw):
    return {"id": i, "text": text, "from_me": from_me, "timestamp": 1000 + int(i[1:]), **kw}


def test_looks_like_request():
    assert sf.looks_like_request("¿venís mañana?")
    assert sf.looks_like_request("avisame cuando llegues")
    assert sf.looks_like_request("Necesito que me pases el archivo")
    assert not sf.looks_like_request("gracias!")
    assert not sf.looks_like_request("jajaja")
    assert not sf.looks_like_request("ok")
    assert not sf.looks_like_request("")


def test_needs_reply_only_if_question_is_unanswered():
    assert sf.needs_reply_from_messages([m("m1", "hola"), m("m2", "¿podés mañana?")])
    # lo contesté después
    assert not sf.needs_reply_from_messages([m("m1", "¿podés mañana?"), m("m2", "sí", from_me=True)])
    # la pregunta quedó antes de un mensaje sin pedido, pero sigue sin respuesta mía
    assert sf.needs_reply_from_messages([m("m1", "¿podés mañana?"), m("m2", "avisame")])
    assert not sf.needs_reply_from_messages([m("m1", "dale"), m("m2", "gracias")])
    assert not sf.needs_reply_from_messages([])
    # mensaje eliminado no cuenta
    assert not sf.needs_reply_from_messages([m("m1", "listo", from_me=True), m("m2", "¿?", media_type="revoked")])
    # nota de voz transcripta con pregunta
    assert sf.needs_reply_from_messages([m("m1", "", media_type="audio", transcript="che, ¿cuándo llegás?")])


def _save(i, jid, text, from_me, when, name="Ana"):
    db.save_message(i, jid, jid, name, from_me, text, when)


def test_unanswered_chats_filters_and_ignores_groups(tmpdb):
    now = int(time.time())
    _save("a1", "111@s.whatsapp.net", "¿me pasás el link?", False, now - 60)
    _save("b1", "222@s.whatsapp.net", "¿venís?", False, now - 120)
    _save("b2", "222@s.whatsapp.net", "voy", True, now - 100)
    _save("c1", "333@s.whatsapp.net", "¿viste?", False, now - 30 * 86400)   # muy viejo
    _save("g1", "999@g.us", "¿alguien sabe?", False, now - 10)
    res = sf.unanswered_chats()
    assert [c["jid"] for c in res] == ["111@s.whatsapp.net"]
    assert res[0]["needs_reply"]
    assert sf.needs_reply_jids(["111@s.whatsapp.net", "222@s.whatsapp.net", "999@g.us"]) == {"111@s.whatsapp.net"}


# ─── Caché y respuestas rápidas ──────────────────────────────────────────────

def test_lru_cache_evicts_oldest():
    c = sf.LruCache(2)
    c.put("a", 1); c.put("b", 2); c.get("a"); c.put("c", 3)
    assert "a" in c and "c" in c and "b" not in c


def test_parse_quick_replies():
    raw = '1. Dale, voy\n- "No puedo mañana"\n• ¿A qué hora?\nDale, voy\nextra'
    assert sf.parse_quick_replies(raw) == ["Dale, voy", "No puedo mañana", "¿A qué hora?"]
    assert sf.parse_quick_replies("") == []


def test_quick_replies_use_cache_per_last_message(monkeypatch):
    calls = []

    def fake_llm(prompt, provider=None, model=None):
        calls.append(prompt)
        return "Sí\nNo\n¿Cuándo?"

    monkeypatch.setattr(sf.pa, "ask_llm", fake_llm)
    sf._quick_cache.clear()
    msgs = [m("m1", "¿venís?")]
    assert sf.quick_replies("j", msgs, "Ana", "Gemini", "x") == (["Sí", "No", "¿Cuándo?"], "")
    assert sf.quick_replies("j", msgs, "Ana", "Gemini", "x")[0] == ["Sí", "No", "¿Cuándo?"]
    assert len(calls) == 1
    assert sf.cached_quick_replies("j", "m1") == ["Sí", "No", "¿Cuándo?"]
    # llegó otro mensaje: es otra clave, se vuelve a pedir
    sf.quick_replies("j", msgs + [m("m2", "¿y?")], "Ana", "Gemini", "x")
    assert len(calls) == 2
    # un error de la IA no se cachea
    monkeypatch.setattr(sf.pa, "ask_llm", lambda *a, **k: "Error: sin clave")
    assert sf.quick_replies("j", [m("m9", "hola?")], "Ana", "Gemini", "x")[1]
    assert sf.cached_quick_replies("j", "m9") is None


def test_should_suggest():
    assert sf.should_suggest(m("m1", "hola"))
    assert not sf.should_suggest(m("m1", "hola", from_me=True))
    assert not sf.should_suggest(None)


# ─── Lo que te perdiste ──────────────────────────────────────────────────────

def test_parse_missed_sections():
    raw = ("RESUMEN: Organizaron la cena.\nDECISIONES:\n- Sábado 21:00 en lo de Marta\n"
           "PREGUNTAS PARA MI:\n- Ana: ¿llevás vino?")
    assert sf.parse_missed(raw) == [
        ("RESUMEN", ["Organizaron la cena."]),
        ("DECISIONES", ["Sábado 21:00 en lo de Marta"]),
        ("PREGUNTAS PARA MÍ", ["Ana: ¿llevás vino?"]),
    ]
    assert sf.parse_missed("Todo tranquilo") == [("RESUMEN", ["Todo tranquilo"])]


def test_summarize_missed_caches_by_last_message(monkeypatch):
    calls = []
    monkeypatch.setattr(sf.pa, "ask_llm", lambda p, *a, **k: calls.append(p) or "RESUMEN: ok")
    sf._missed_cache.clear()
    msgs = [m("m1", "hola"), m("m2", "¿vamos?")]
    assert sf.summarize_missed("g", msgs, "Grupo", True, 20, "Gemini", "x") == ("RESUMEN: ok", "")
    sf.summarize_missed("g", msgs, "Grupo", True, 20, "Gemini", "x")
    assert len(calls) == 1 and "Grupo" in calls[0]
    assert sf.cached_missed("g", "m2", 20) == "RESUMEN: ok"
    sf.summarize_missed("g", msgs, "Grupo", True, 20, "Gemini", "x", force=True)
    assert len(calls) == 2


def test_format_missed_conversation_keeps_latest_when_over_limit():
    msgs = [m(f"m{i}", "x" * 50) for i in range(1, 40)]
    out = sf.format_missed_conversation(msgs, "Ana", max_chars=500)
    assert len(out) <= 500 and out.count("\n") < 12


# ─── Traducción ──────────────────────────────────────────────────────────────

def test_translate_caches_and_reports_errors(monkeypatch):
    calls = []
    monkeypatch.setattr(sf.pa, "ask_llm", lambda p, *a, **k: calls.append(p) or '"Hello"')
    sf._translate_cache.clear()
    assert sf.translate("Hola", "inglés", "Gemini", "x") == ("Hello", "")
    assert sf.translate("Hola", "inglés", "Gemini", "x") == ("Hello", "")
    assert len(calls) == 1 and "inglés" in calls[0]
    monkeypatch.setattr(sf.pa, "ask_llm", lambda *a, **k: "Error: cuota")
    assert sf.translate("Chau", "inglés", "Gemini", "x")[1].startswith("Error")
    assert sf.translate("  ", "inglés", "Gemini", "x")[1]


# ─── Búsqueda por significado ────────────────────────────────────────────────

def test_query_keywords_and_parsers():
    assert sf.query_keywords("¿Cuándo era el cumpleaños de Marta?") == ["cumpleanos", "marta"]
    assert sf.parse_keywords("cumpleaños, fiesta; torta\n- festejo") == ["cumpleanos", "fiesta", "torta", "festejo"]
    assert sf.parse_rerank("3, 1, 3, 99, 2", 5) == [2, 0, 1]


def test_search_finds_voice_transcripts_and_excludes_locked(tmpdb, monkeypatch):
    now = int(time.time())
    _save("a1", "111@s.whatsapp.net", "Mañana es el cumpleaños de Marta", False, now - 100)
    db.save_message("a2", "222@s.whatsapp.net", "222@s.whatsapp.net", "Beto", False, "", now - 50, media_type="audio",
                    media_path="x.ogg")
    db.set_message_transcript("a2", "no te olvides de la torta de cumpleaños")
    _save("a3", "333@s.whatsapp.net", "hablamos del auto", False, now - 10)
    with db.db_conn() as conn:
        conn.execute("INSERT INTO chat_locks (chat_jid, salt, hash) VALUES ('333@s.whatsapp.net', 'x', 'y')") \
            if False else None
    res = sf.search_candidates(sf.query_keywords("cumpleaños torta"))
    assert {r["id"] for r in res} == {"a1", "a2"}
    assert res[0]["id"] == "a2"   # el que matchea más palabras va primero


def test_semantic_search_uses_two_calls_and_caches(tmpdb, monkeypatch):
    now = int(time.time())
    _save("a1", "111@s.whatsapp.net", "El sábado cenamos en lo de Marta", False, now - 100)
    _save("a2", "111@s.whatsapp.net", "cenamos pizza ayer", False, now - 50)
    answers = iter(["cena, cenamos, sábado", "1"])
    calls = []
    monkeypatch.setattr(sf.pa, "ask_llm", lambda p, *a, **k: calls.append(p) or next(answers))
    sf._search_cache.clear()
    res, note = sf.semantic_search("¿cuándo es la cena con Marta?", "Gemini", "x")
    assert note == "" and len(calls) == 2
    assert len(res) >= 1
    again, _ = sf.semantic_search("¿cuándo es la cena con Marta?", "Gemini", "x")
    assert len(calls) == 2 and [r["id"] for r in again] == [r["id"] for r in res]


def test_semantic_search_falls_back_when_ai_fails(tmpdb, monkeypatch):
    _save("a1", "111@s.whatsapp.net", "El sábado cenamos en lo de Marta", False, int(time.time()))
    monkeypatch.setattr(sf.pa, "ask_llm", lambda *a, **k: "Error: sin clave")
    sf._search_cache.clear()
    res, note = sf.semantic_search("cenamos con Marta", "Gemini", "x")
    assert res and note


# ─── Recordatorios locales ───────────────────────────────────────────────────

def test_reminders_store_and_pop_due(tmp_path):
    p = str(tmp_path / "r.json")
    now = 1_000_000
    reminders.add_reminder(now - 60, "Cena con Ana", "111@s.whatsapp.net", "Ana", path=p)
    reminders.add_reminder(now + 3600, "Más tarde", path=p)
    reminders.add_reminder(now - 13 * 3600, "Muy viejo", path=p)
    due = reminders.pop_due(now, path=p)
    assert [r["text"] for r in due] == ["Cena con Ana"]      # el de hace 13 h se descarta sin avisar
    left = reminders.list_reminders(p)
    assert [r["text"] for r in left] == ["Más tarde"]
    reminders.remove_reminder(left[0]["id"], path=p)
    assert reminders.list_reminders(p) == []
