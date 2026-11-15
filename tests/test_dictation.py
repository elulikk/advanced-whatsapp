"""Dictado a texto: helpers de transcripción (WAV, MIME, unión con el borrador) y emprolijado (sin red)."""
import os
import sys
import wave

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.ai import reply_polisher as rp
from app.ai import transcription as tr


def test_merge_dictation_vacio_y_espacios():
    assert tr.merge_dictation("", "  hola mundo ") == "hola mundo"
    assert tr.merge_dictation("   ", "hola") == "hola"
    assert tr.merge_dictation("Hola", "") == "Hola"
    assert tr.merge_dictation("Hola", "   ") == "Hola"
    assert tr.merge_dictation(None, "hola") == "hola"


def test_merge_dictation_agrega_sin_tocar_lo_escrito():
    assert tr.merge_dictation("Buenas", "cómo estás") == "Buenas cómo estás"
    assert tr.merge_dictation("Buenas ", "cómo estás") == "Buenas cómo estás"
    assert tr.merge_dictation("Línea 1\n", "línea 2") == "Línea 1\nlínea 2"


def test_audio_mime_por_extension():
    assert tr.audio_mime("a.wav") == "audio/wav"
    assert tr.audio_mime("A.OGG") == "audio/ogg"
    assert tr.audio_mime("a.mp3") == "audio/mpeg"
    assert tr.audio_mime("sin_extension") == "audio/ogg"


def test_pcm_to_wav_es_un_wav_valido(tmp_path):
    pcm = (b"\x01\x00" * 4800)   # 0,1 s a 48 kHz
    dest = str(tmp_path / "d.wav")
    tr.pcm_to_wav(pcm, dest)
    with wave.open(dest, "rb") as w:
        assert (w.getnchannels(), w.getsampwidth(), w.getframerate()) == (1, 2, 48000)
        assert w.getnframes() == 4800


def test_prompt_de_dictado_incluye_el_texto_y_no_reformula():
    p = rp.build_dictation_prompt("  eh hola che mmm cómo va  ")
    assert "eh hola che mmm cómo va" in p
    assert "NO cambies lo que digo" in p


def test_polish_dictation_usa_la_ia_y_limpia_la_respuesta(monkeypatch):
    calls = {}

    def fake(prompt, provider, model):
        calls["args"] = (prompt, provider, model)
        return '"Hola, ¿cómo va?"'
    monkeypatch.setattr(rp.pa, "ask_llm", fake)
    assert rp.polish_dictation("hola como va", "Groq", "m") == "Hola, ¿cómo va?"
    assert calls["args"][1:] == ("Groq", "m")


def test_polish_dictation_vacio_es_error():
    out = rp.polish_dictation("   ", "Groq", "m")
    assert rp.AIService.is_error_response(out)
