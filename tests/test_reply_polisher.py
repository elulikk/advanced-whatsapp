import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.ai import psych_analysis as pa
from app.ai import reply_polisher as rp


def test_context_labels_senders_and_skips_empty():
    msgs = [
        {"text": "hola, ¿cuánto sale?", "from_me": 0, "sender_name": "Ana", "timestamp": 0},
        {"text": "", "from_me": 0, "media_type": "text", "timestamp": 0},
        {"text": "$500", "from_me": 1, "timestamp": 0},
    ]
    ctx = rp.format_recent_context(msgs, "Contacto X")
    assert ctx.splitlines() == ["Ana: hola, ¿cuánto sale?", "Yo: $500"]


def test_prompt_contains_draft_and_context():
    p = rp.build_polish_prompt("  si dale mañana 10  ", "Ana: ¿nos vemos?")
    assert "=== MI BORRADOR ===\nsi dale mañana 10\n=== FIN ===" in p
    assert "Ana: ¿nos vemos?" in p
    assert "ÚNICAMENTE con el texto final" in p
    assert "ÚLTIMOS MENSAJES" not in rp.build_polish_prompt("x", "")


def test_prompt_uses_mode_and_extra_instruction():
    ordenar = rp.build_polish_prompt("x", "", "ordenar")
    suavizar = rp.build_polish_prompt("x", "", "suavizar", "no menciones el precio")
    assert "NO es corregir ortografía" in ordenar and "Reorganiza las ideas" in ordenar
    assert "INDICACIÓN ADICIONAL" not in ordenar
    assert "hiriente" in suavizar and "no menciones el precio" in suavizar
    assert "Reorganiza las ideas" not in suavizar
    assert "Reorganiza las ideas" in rp.build_polish_prompt("x", "", "no-existe")  # cae al modo por defecto
    assert set(rp.MODES) >= {"ordenar", "suavizar", "firme", "breve", "libre"}


def test_suggest_prompt_tone_extra_and_notes():
    ctx = "Ana: ¿podés venir el lunes?"
    p = rp.build_suggest_prompt(ctx, "suavizar", "dile que el lunes no puedo", "mejor martes")
    assert "todavía NO tengo escrita ninguna respuesta" in p
    assert "TONO DESEADO" in p and "hiriente" in p
    assert "dile que el lunes no puedo" in p and "mejor martes" in p and ctx in p
    # «ordenar» y «libre» no son tonos: no se agregan
    assert "TONO DESEADO" not in rp.build_suggest_prompt(ctx, "ordenar")
    assert "TONO DESEADO" not in rp.build_suggest_prompt(ctx, "libre")
    assert "INDICACIÓN ADICIONAL" not in rp.build_suggest_prompt(ctx)


def test_harsh_and_sarcastic_modes_apply_to_suggest_and_polish():
    assert {"hiriente", "sarcastico", "pasivo_agresivo", "frio", "humor"} <= set(rp.MODES)
    assert set(rp.TONE_MODES) <= set(rp.MODES)
    s = rp.build_suggest_prompt("Ana: llegué tarde, perdón", "sarcastico")
    assert "TONO DESEADO" in s and "sarcasmo" in s
    h = rp.build_polish_prompt("x", "", "hiriente")
    assert "hiriente" in h and "Sin amenazas" in h


def test_suggest_needs_messages_and_calls_llm(monkeypatch):
    called = []
    monkeypatch.setattr(pa, "ask_llm", lambda prompt, prov, model: called.append(prompt) or '"Dale, nos vemos."')
    assert rp.suggest([], "Ana", "Gemini", "m").startswith("Error") and not called
    msgs = [{"text": "¿nos vemos?", "from_me": 0, "sender_name": "Ana", "timestamp": 0}]
    assert rp.suggest(msgs, "Ana", "Gemini", "m", "breve") == "Dale, nos vemos."
    assert "Ana: ¿nos vemos?" in called[0]


def test_clean_reply_strips_quotes_and_fences():
    assert rp.clean_reply('  "Hola, ¿cómo estás?"  ') == "Hola, ¿cómo estás?"
    assert rp.clean_reply("```\nHola\n```") == "Hola"
    assert rp.clean_reply("Dijo \"hola\" ayer") == "Dijo \"hola\" ayer"


def test_polish_passes_error_through_and_cleans_ok(monkeypatch):
    monkeypatch.setattr(pa, "ask_llm", lambda prompt, prov, model: '"Listo, nos vemos mañana a las 10."')
    assert rp.polish("dale mañana 10", [], "Ana", "Gemini", "m") == "Listo, nos vemos mañana a las 10."
    monkeypatch.setattr(pa, "ask_llm", lambda prompt, prov, model: "Error: No se ha configurado la API Key")
    assert rp.polish("x", [], "Ana", "Gemini", "m").startswith("Error")
