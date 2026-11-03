import os, sys, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest
import app.db as db
from app.ai import psych_analysis as pa


@pytest.fixture(autouse=True)
def tmpdirs(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setattr(pa, "ANALYSIS_DIR", str(tmp_path / "analisis"))
    os.makedirs(tmp_path / "analisis")
    db.init_db()


def test_self_analysis_chats_include_others_as_context():
    now = int(time.time())
    db.save_message("a1", "111@s.whatsapp.net", "me", "Yo", True, "hola Ana", now)
    db.save_message("a2", "111@s.whatsapp.net", "111@s.whatsapp.net", "Ana", False, "hola", now + 1)
    db.save_message("b1", "222@s.whatsapp.net", "222@s.whatsapp.net", "Spam", False, "promo", now)
    db.save_message("g1", "999@g.us", "me", "Yo", True, "hola grupo", now)
    chats = db.get_chats_for_self_analysis()
    assert [c["jid"] for c in chats] == ["111@s.whatsapp.net"]  # sin grupos ni chats donde no escribí
    assert [m["text"] for m in chats[0]["messages"]] == ["hola Ana", "hola"]
    only_mine = db.get_chats_for_self_analysis(include_others=False)
    assert [m["text"] for m in only_mine[0]["messages"]] == ["hola Ana"]
    text, n_mine, n_chats = pa.format_self_material(chats, 10_000)
    assert "Yo: hola Ana" in text and ": hola" in text and (n_mine, n_chats) == (1, 1)


def test_self_material_respects_budget_and_covers_all_chats():
    chats = [{"name": f"C{i}", "messages": [{"text": "x" * 50, "timestamp": 0, "from_me": 1}] * 100} for i in range(3)]
    text, n_msgs, n_chats = pa.format_self_material(chats, 2000)
    assert len(text) <= 2000 and n_chats == 3 and n_msgs > 3


def test_save_analysis_appends_notes_and_reloads():
    pa.save_analysis("111@s.whatsapp.net", "Ana", "Es empática.", "normal", "short", "Gemini", "m", 10)
    pa.save_question("111@s.whatsapp.net", "normal", "¿Y?", "Nada más.")
    notes = pa.load_notes("111@s.whatsapp.net")
    assert "Es empática." in notes and "P: ¿Y?" in notes
    assert pa.load_analysis("111@s.whatsapp.net", "normal")["text"] == "Es empática."
    assert pa.load_analysis("111@s.whatsapp.net", "comerciante") is None


def test_legacy_file_migrates_to_comerciante():
    legacy = os.path.join(pa.ANALYSIS_DIR, "111.txt")
    with open(legacy, "w", encoding="utf-8") as f:
        f.write(f"Cliente: Ana\nFecha: 2026-01-01 10:00\nModo: short\n{pa._SEP}\nViejo análisis\n")
    assert pa.load_analysis("111@s.whatsapp.net", "comerciante")["text"] == "Viejo análisis"
    assert not os.path.exists(legacy)
    assert "Viejo análisis" in pa.load_notes("111@s.whatsapp.net")


def test_prompts_include_notes_and_material():
    p = pa.build_prompt("Yo: hola", "interno", "full", "yo", 1, 1, notes="nota vieja")
    assert "nota vieja" in p and "MIS CONVERSACIONES" in p and "no analices a los demás" in p
    assert "MIS MENSAJES" in pa.build_prompt("Yo: hola", "interno", "full", "yo", 1, 1, with_context=False)
    e = pa.build_export("interno", "yo", "análisis", [("q", "a")], "Yo: hola")
    assert "de mí mismo" in e and "Pregunta: q" in e and "Yo: hola" in e


def test_prefs_merge_and_model_results():
    pa.save_prefs(length="full", provider="Groq", models={"Groq": "qwen"})
    pa.save_prefs(models={"Gemini": "flash"})
    pa.record_model_result("Groq", "qwen", False, "Error: 429")
    pa.record_model_result("Groq", "qwen", True)
    prefs = pa.load_prefs()
    assert prefs["length"] == "full" and prefs["models"] == {"Groq": "qwen", "Gemini": "flash"}
    assert pa.model_results("Groq")["qwen"]["ok"] is True and pa.model_results("OpenAI") == {}


def test_relation_kind_and_searches_in_prompt():
    mat = "Ana: " + "necesito plata urgente confía en mí " * 8
    general = pa.build_prompt(mat, "normal", "short", "Ana", 1)
    assert "AMIGO" not in general and "TRABAJO" not in general
    for bad in ("vendedor", "cliente", "compra", "venta"):
        assert bad not in general.lower()
    amigo = pa.build_prompt(mat, "normal", "short", "Ana", 1, relation="Amigo")
    assert "AMIGO" in amigo
    custom = pa.build_prompt(mat, "normal", "short", "Ana", 1, relation="Vecino")
    assert "Vecino" in custom
    q = pa.build_prompt(mat, "normal", "short", "Ana", 1, relation="Trabajo",
                        searches=["¿Me quiere estafar?", "¿Está enojado/a conmigo?"])
    assert "1. ¿Me quiere estafar?" in q and "2. ¿Está enojado/a conmigo?" in q
    assert "Sin evidencia suficiente" in q and "TRABAJO" in q


def test_kinds_and_custom_searches_persist():
    assert pa.load_kinds()[:2] == ["General", "Amigo"] and "Familia" in pa.load_kinds() and "Trabajo" in pa.load_kinds()
    pa.remember_kind("Vecino")
    pa.remember_kind("amigo")          # ya existe: no se duplica
    assert pa.load_kinds()[-1] == "Vecino" and len(pa.load_kinds()) == len(pa.KIND_HINTS) + 1
    assert [q for q, own in pa.load_searches() if not own] == pa.DEFAULT_SEARCHES
    pa.add_search("¿Me debe plata?")
    pa.add_search("¿Me debe plata?")
    assert [q for q, own in pa.load_searches() if own] == ["¿Me debe plata?"]
    pa.remove_search("¿Me debe plata?")
    assert [q for q, own in pa.load_searches() if own] == []


def test_save_analysis_records_relation_and_searches():
    path = pa.save_analysis("111@s.whatsapp.net", "Ana", "Resultado", "normal", "short", "Claude", "m", 5,
                            "Amigo", ["¿Me quiere estafar?"])
    head = open(path, encoding="utf-8").read()
    assert "Relación: Amigo" in head and "Búsquedas: ¿Me quiere estafar?" in head
    assert pa.load_analysis("111@s.whatsapp.net", "normal")["text"] == "Resultado"
    assert "Preguntas de la búsqueda" in pa.load_notes("111@s.whatsapp.net")


def test_redo_ignores_previous_ai_output():
    jid = "111@s.whatsapp.net"
    pa.save_notes(jid, "Es mi primo, ojo con la plata.\n")
    pa.save_analysis(jid, "Ana", "CONCLUSION_VIEJA_ESTAFADOR", "normal", "short", "Claude", "m", 5)
    pa.save_question(jid, "normal", "¿Y?", "RESPUESTA_VIEJA")
    notes = pa.load_notes(jid)
    assert "CONCLUSION_VIEJA_ESTAFADOR" in notes            # sigue guardado para consultarlo
    mat = "Ana: " + "hola cómo estás todo bien por acá " * 6
    p = pa.build_prompt(mat, "normal", "short", "Ana", 1, notes=notes)
    assert "CONCLUSION_VIEJA_ESTAFADOR" not in p and "RESPUESTA_VIEJA" not in p
    assert "Es mi primo, ojo con la plata." in p and "desde cero" in p
    assert pa.user_notes(pa.load_notes("sin-notas")) == ""
