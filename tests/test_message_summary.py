import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.ai import message_summary as ms


def test_message_text_uses_transcript_for_voice_notes():
    assert ms.message_text({"text": " hola ", "media_type": "text"}) == "hola"
    assert ms.message_text({"media_type": "audio", "transcript": "nos vemos a las 5"}) == "nos vemos a las 5"
    assert ms.message_text({"media_type": "audio", "transcript": "(sin voz detectable)"}) == ""
    assert not ms.can_summarize({"media_type": "revoked", "text": "x"})
    assert not ms.can_summarize({"media_type": "audio"})


def test_prompt_contains_message_author_and_context():
    p = ms.build_summary_prompt("  te paso el presupuesto mañana  ", "Ana", "Yo: ¿y el precio?")
    assert "=== MENSAJE A RESUMIR (de Ana) ===\nte paso el presupuesto mañana\n=== FIN ===" in p
    assert "Yo: ¿y el precio?" in p
    assert "RESUMEN:" in p and "INTERPRETACIÓN:" in p and "ESPERA:" in p
    assert "MENSAJES ANTERIORES" not in ms.build_summary_prompt("x", "Ana", "")


def test_parse_summary_sections_in_order():
    raw = ("**RESUMEN:** Llega el viernes a las 10.\n"
           "INTERPRETACION: Está molesta por la demora,\naunque lo dice con cortesía.\n"
           "ESPERA: Que confirmes si podés recibirlo.")
    assert ms.parse_summary(raw) == [
        ("En pocas palabras", "Llega el viernes a las 10."),
        ("Qué quiere decir", "Está molesta por la demora,\naunque lo dice con cortesía."),
        ("Qué espera", "Que confirmes si podés recibirlo."),
    ]


def test_parse_summary_without_format_falls_back_to_plain_text():
    assert ms.parse_summary("```\nSolo dice que sí.\n```") == [("En pocas palabras", "Solo dice que sí.")]
    assert ms.parse_summary("   ") == []
