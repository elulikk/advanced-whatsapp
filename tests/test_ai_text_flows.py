"""Ramas extra de message_summary, reply_polisher y psych_analysis (sin red)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest

from app.ai import message_summary as ms
from app.ai import psych_analysis as pa
from app.ai import reply_polisher as rp


# ─── message_summary ─────────────────────────────────────────────────────────

def test_parse_summary_partial_and_unknown_sections():
    raw = "RESUMEN: Llega tarde.\nESPERA: Nada en particular"
    assert ms.parse_summary(raw) == [("En pocas palabras", "Llega tarde."), ("Qué espera", "Nada en particular")]
    assert ms.parse_summary(None) == []


def test_summarize_rejects_message_without_text_and_does_not_call_llm(monkeypatch):
    monkeypatch.setattr(pa, "ask_llm", lambda *a, **k: pytest.fail("no debe llamar a la IA"))
    assert ms.summarize({"media_type": "sticker"}, [], "Ana", "Gemini", "m").startswith("Error")


def test_summarize_builds_context_and_returns_stripped_output(monkeypatch):
    seen = []
    monkeypatch.setattr(pa, "ask_llm", lambda prompt, prov, model: seen.append((prompt, prov, model)) or
                        "  RESUMEN: a\nINTERPRETACIÓN: b\nESPERA: c  ")
    previous = [{"text": f"m{i}", "from_me": i % 2, "sender_name": "Ana", "timestamp": 0} for i in range(20)]
    previous.append({"text": "", "from_me": 0, "timestamp": 0})   # vacío: se omite
    out = ms.summarize({"text": "¿vamos?", "from_me": 0, "sender_name": "Ana"}, previous, "X", "Claude", "haiku")
    assert out == "RESUMEN: a\nINTERPRETACIÓN: b\nESPERA: c"
    prompt = seen[0][0]
    assert seen[0][1:] == ("Claude", "haiku") and "(de Ana)" in prompt
    assert "m19" in prompt and "m12" not in prompt          # solo los últimos CONTEXT_MESSAGES con texto


def test_summarize_author_when_from_me(monkeypatch):
    monkeypatch.setattr(pa, "ask_llm", lambda prompt, prov, model: "algo" if "(de mí (Yo))" in prompt else "no")
    assert ms.summarize({"text": "hola", "from_me": 1}, [], "Ana", "Gemini", "m") == "algo"


@pytest.mark.parametrize("llm_out", [None, "", "   "])
def test_summarize_empty_or_none_answer_is_an_error_string(monkeypatch, llm_out):
    monkeypatch.setattr(pa, "ask_llm", lambda *a: llm_out)
    out = ms.summarize({"text": "hola"}, [], "Ana", "Gemini", "m")
    assert isinstance(out, str) and out.startswith("Error")


def test_summarize_passes_error_through(monkeypatch):
    monkeypatch.setattr(pa, "ask_llm", lambda *a: "⚠️ Cuota excedida en Groq")
    assert ms.summarize({"text": "hola"}, [], "Ana", "Groq", "m").startswith("⚠️")


def _run_request(monkeypatch, llm, db_fail=False, msg=None):
    """Ejecuta request_summary con el hilo en línea y la base simulada."""
    saved, emitted = {}, []

    class _Inline:
        def __init__(self, target, **k):
            self.target = target

        def start(self):
            self.target()

    monkeypatch.setattr(ms.threading, "Thread", _Inline)
    monkeypatch.setattr(ms.db, "get_messages", lambda *a, **k: [])

    def set_summary(mid, out):
        if db_fail:
            raise RuntimeError("database is locked")
        saved[mid] = out
    monkeypatch.setattr(ms.db, "set_message_summary", set_summary)
    monkeypatch.setattr(pa, "ask_llm", llm)
    monkeypatch.setattr(ms.summary_signals, "summary_ready", type("S", (), {"emit": lambda self, m: emitted.append(m)})())
    msg = msg or {"id": "m1", "chat_jid": "1@s", "text": "hola", "timestamp": 5}
    return ms.request_summary(msg, "Ana", "Gemini", "m"), saved, emitted


def test_request_summary_success_saves_and_clears_pending(monkeypatch):
    ms._errors.clear(); ms._pending.clear()
    started, saved, emitted = _run_request(monkeypatch, lambda *a: "RESUMEN: x\nINTERPRETACIÓN: y\nESPERA: z")
    assert started and saved == {"m1": "RESUMEN: x\nINTERPRETACIÓN: y\nESPERA: z"}
    assert emitted == ["m1"] and not ms.is_pending("m1") and ms.get_error("m1") is None


def test_request_summary_llm_error_is_kept_not_saved(monkeypatch):
    ms._errors.clear(); ms._pending.clear()
    started, saved, emitted = _run_request(monkeypatch, lambda *a: "Error: sin clave")
    assert started and saved == {} and ms.get_error("m1") == "Error: sin clave" and emitted == ["m1"]


def test_request_summary_exception_in_llm_is_reported(monkeypatch):
    ms._errors.clear(); ms._pending.clear()

    def boom(*a):
        raise ValueError("kaboom")
    _, saved, emitted = _run_request(monkeypatch, boom)
    assert "kaboom" in ms.get_error("m1") and not ms.is_pending("m1") and emitted == ["m1"]


def test_request_summary_db_failure_does_not_leave_message_pending(monkeypatch):
    ms._errors.clear(); ms._pending.clear()
    _, saved, emitted = _run_request(monkeypatch, lambda *a: "RESUMEN: x", db_fail=True)
    assert not ms.is_pending("m1") and "locked" in ms.get_error("m1") and emitted == ["m1"]


def test_request_summary_refuses_without_id_text_or_when_pending(monkeypatch):
    ms._errors.clear(); ms._pending.clear()
    assert ms.request_summary({"text": "hola"}, "A", "G", "m") is False
    assert ms.request_summary({"id": "z", "media_type": "sticker"}, "A", "G", "m") is False
    ms._pending.add("busy")
    assert ms.request_summary({"id": "busy", "text": "hola"}, "A", "G", "m") is False
    ms._pending.clear()


# ─── reply_polisher ──────────────────────────────────────────────────────────

def test_clean_reply_quote_edge_cases():
    assert rp.clean_reply('"Hola" y "chau"') == '"Hola" y "chau"'      # no son comillas envolventes
    assert rp.clean_reply("«Hola»") == "Hola" and rp.clean_reply("“Hola”") == "Hola"
    assert rp.clean_reply('"') == '"' and rp.clean_reply("") == "" and rp.clean_reply(None) == ""
    assert rp.clean_reply("```text\n\"Dale\"\n```") == "Dale"
    assert rp.clean_reply('  "  espacios  "  ') == "espacios"


def test_polish_and_suggest_accept_none_for_optional_texts(monkeypatch):
    seen = []
    monkeypatch.setattr(pa, "ask_llm", lambda prompt, prov, model: seen.append(prompt) or "ok")
    assert rp.polish("borrador", [], "Ana", "Gemini", "m", extra=None, profile=None) == "ok"
    msgs = [{"text": "hola", "from_me": 0, "sender_name": "Ana", "timestamp": 0}]
    assert rp.suggest(msgs, "Ana", "Gemini", "m", extra=None, notes=None, profile=None) == "ok"
    assert len(seen) == 2


def test_polish_error_and_empty_answers(monkeypatch):
    monkeypatch.setattr(pa, "ask_llm", lambda *a: "❌ Error de autenticación en Groq.")
    assert rp.polish("x", [], "A", "Groq", "m").startswith("❌")
    monkeypatch.setattr(pa, "ask_llm", lambda *a: '""')
    assert rp.polish("x", [], "A", "Groq", "m").startswith("Error")
    monkeypatch.setattr(pa, "ask_llm", lambda *a: None)
    assert rp.polish("x", [], "A", "Groq", "m").startswith("Error")


def test_polish_does_not_treat_valid_text_with_emoji_as_error(monkeypatch):
    monkeypatch.setattr(pa, "ask_llm", lambda *a: "Te aviso ⚠️ que mañana cerramos")
    assert rp.polish("x", [], "A", "Groq", "m") == "Te aviso ⚠️ que mañana cerramos"


def test_private_instruction_present_only_with_extra_and_context_optional():
    with_extra = rp.build_polish_prompt("b", "", "ordenar", "decile que no")
    assert "INDICACIÓN PRIVADA" in with_extra and "decile que no" in with_extra
    assert "INDICACIÓN PRIVADA" not in rp.build_polish_prompt("b", "", "ordenar", "   ")
    assert rp.build_suggest_prompt("Ana: hola").count("=== ÚLTIMOS MENSAJES DEL CHAT ===") == 1


# ─── psych_analysis ──────────────────────────────────────────────────────────

@pytest.fixture
def tmp_analysis(tmp_path, monkeypatch):
    d = tmp_path / "analisis"
    d.mkdir()
    monkeypatch.setattr(pa, "ANALYSIS_DIR", str(d))
    return d


def test_message_line_timestamps_media_and_bad_values():
    assert pa._message_line({"text": ""}, "Ana") == ""
    assert pa._message_line({"text": "hola", "timestamp": 0}, "Ana") == "Ana: hola"
    assert pa._message_line({"text": "hola", "timestamp": "abc"}, "Ana") == "Ana: hola"
    assert pa._message_line({"text": "hola", "timestamp": 10**30}, "Ana") == "Ana: hola"
    ms_line = pa._message_line({"text": "hola", "timestamp": 1_700_000_000_000}, "Ana")   # milisegundos
    s_line = pa._message_line({"text": "hola", "timestamp": 1_700_000_000}, "Ana")
    assert ms_line == s_line and ms_line.startswith("[") and ms_line.endswith("Ana: hola")
    assert pa._message_line({"media_type": "image", "text": "mirá"}, "Ana") == "Ana: [imagen] mirá"
    assert pa._message_line({"media_type": "audio", "transcript": "hola"}, "Ana") == \
        "Ana: [nota de voz] (transcripción) hola"
    assert pa._message_line({"media_type": "document"}, "Ana") == "Ana: [documento]"


def test_depth_rules_by_amount_written():
    mat = "Yo: hola\nAna: gracias"
    assert pa.subject_stats(mat, "normal", "Ana") == (1, 1)
    assert pa._depth_rule("normal", 1, 1)[0] == "minimal"
    assert pa._depth_rule("normal", 5, 60)[0] == "moderate"
    assert pa._depth_rule("normal", 30, 500)[0] == "rich"
    p = pa.build_prompt(mat, "normal", "full", "Ana", 2)
    assert "escribió muy poco" in p and "Perfil de personalidad" not in p


def test_subject_stats_multiline_messages_and_media_tags():
    mat = "[01/01/26 10:00] Ana: primera línea\nsegunda línea\n[01/01/26 10:01] Yo: hola\nignorada\nAna: [imagen] fin"
    assert pa.subject_stats(mat, "normal", "Ana") == (2, 5)


def test_empty_material_and_chats():
    assert pa.format_conversation([], "Ana") == ""
    assert pa.format_self_material([], 1000) == ("", 0, 0)
    assert pa.format_self_material([{"name": "A", "messages": []}], 1000) == ("", 0, 0)
    assert pa.format_self_material([{"name": "A", "messages": [{"text": "hola", "from_me": 1}]}], 0) == ("", 0, 0)
    assert pa.subject_stats("", "normal", "Ana") == (0, 0)


def test_self_material_keeps_most_recent_lines():
    msgs = [{"text": f"mensaje{i}", "from_me": 1, "timestamp": 0} for i in range(10)]
    text, n_mine, n_chats = pa.format_self_material([{"name": "Ana", "messages": msgs}], 70)
    assert "mensaje9" in text and "mensaje0" not in text and n_chats == 1 and 0 < n_mine < 10


def test_safe_jid_cannot_escape_analysis_dir(tmp_analysis):
    assert pa._safe("..") == "desconocido" and pa._safe("") == "desconocido" and pa._safe(None) == "desconocido"
    assert "/" not in pa._safe("../../etc@x") and "\\" not in pa._safe("a/b\\c@x")
    assert pa._safe("123-456@g.us") == "123-456"
    assert os.path.dirname(pa.subject_dir("..")) == str(tmp_analysis)


def test_notes_with_invalid_utf8_do_not_crash(tmp_analysis):
    jid = "5@s.whatsapp.net"
    pa.save_notes(jid, "ok")
    with open(pa.notes_path(jid), "wb") as f:
        f.write("Mi nota ñ".encode("cp1252") + b"\n")        # guardada como ANSI por un editor
    assert "Mi nota" in pa.load_notes(jid)
    with open(pa.history_path(jid), "wb") as f:
        f.write(b"\xff\xfe historial")
    assert "historial" in pa.load_history(jid)
    with open(pa.analysis_path(jid, "normal"), "wb") as f:
        f.write(b"Fecha: x\n" + pa._SEP.encode() + b"\ntexto \xe9\n")
    assert pa.load_analysis(jid, "normal")["text"].startswith("texto")
    assert "Mi nota" in pa.reply_profile(jid)


def test_corrupt_prefs_are_ignored(tmp_analysis):
    path = os.path.join(pa.ANALYSIS_DIR, "preferencias.json")
    for raw in (b"{no es json", b"[1, 2]", b"\xff\xfe\x00", b""):
        with open(path, "wb") as f:
            f.write(raw)
        assert pa.load_prefs() == {}
    pa.save_prefs(length="full")                      # se recupera
    assert pa.load_prefs() == {"length": "full"}
    pa.record_model_result("Groq", "", True)           # sin modelo: no guarda nada
    assert pa.model_results("Groq") == {}
    pa.record_model_result("Groq", "m", False, "x" * 1000)
    assert len(pa.model_results("Groq")["m"]["error"]) == 300


def test_default_provider_model_normalizes_names(monkeypatch):
    import configparser

    def cfg(provider, ia_model="", **sections):
        c = configparser.ConfigParser()
        c.read_dict({"IA": {"provider": provider, "model": ia_model}, **sections})
        return c
    monkeypatch.setattr(pa, "load_app_config", lambda: cfg(" anthropic ", "", ANTHROPIC={"model": "claude-x"}))
    assert pa.default_provider_model() == ("Claude", "claude-x")
    monkeypatch.setattr(pa, "load_app_config", lambda: cfg("groq", "qwen"))
    assert pa.default_provider_model() == ("Groq", "qwen")
    monkeypatch.setattr(pa, "load_app_config", lambda: cfg("Desconocido"))
    assert pa.default_provider_model() == ("Gemini", "")
    monkeypatch.setattr(pa, "load_app_config", lambda: cfg("OpenAI", OPENAI={"model": "gpt-4o"}))
    assert pa.default_provider_model() == ("OpenAI", "gpt-4o")


def test_followup_prompt_and_export_variants():
    p = pa.build_followup_prompt("Ana: hola", "normal", "Ana", "es amable", [("¿y?", "nada")], "¿Está enojada?")
    assert "PREGUNTAS ANTERIORES" in p and "Pregunta: ¿y?" in p and "¿Está enojada?" in p and "es amable" in p
    assert "PREGUNTAS ANTERIORES" not in pa.build_followup_prompt("m", "interno", "yo", "a", [], "q")
    e = pa.build_export("normal", "Ana", " análisis ", [])
    assert "(Ana)" in e and "PREGUNTAS Y RESPUESTAS" not in e and "MENSAJES ANALIZADOS" not in e
