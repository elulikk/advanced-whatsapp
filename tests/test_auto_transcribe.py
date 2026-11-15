"""Transcripción automática de audios (PC): ajuste, sin duplicados, sin reintentos de los fallidos y sin API key."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest

import app.db as db
import app.media_service as ms

CHAT = "111@s.whatsapp.net"


@pytest.fixture(autouse=True)
def entorno(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setattr(db, "_load_lid_phone_map", lambda: {})
    db._early_status.clear()
    db.init_db()
    # sin hilo real: se inspecciona la cola
    monkeypatch.setattr(ms, "_ensure_worker", lambda: None)
    ms._pending.clear()
    ms._errors.clear()
    ms._unavailable_cache.clear()
    while not ms._jobs.empty():
        ms._jobs.get()
    prefs = {"auto_transcribe": True}
    monkeypatch.setattr(ms, "get_pref", lambda k: prefs.get(k, False))
    monkeypatch.setattr(ms, "has_transcription_key", lambda config=None: True)
    yield prefs
    ms._pending.clear()
    ms._errors.clear()


def _audio(i, ts=1000, raw='{"message":{}}'):
    db.save_message(i, CHAT, CHAT, "Ana", False, "", ts, media_type="audio", media_raw=raw)
    return {"id": i, "media_type": "audio", "media_raw": raw}


def test_default_activado():
    from app.config import PREF_DEFAULTS
    assert PREF_DEFAULTS["auto_transcribe"] is True


def test_encola_audio_nuevo():
    assert ms.auto_transcribe(_audio("a1")) is True
    assert ms.is_pending("transcribe", "a1")


def test_no_encola_si_esta_apagado(entorno):
    entorno["auto_transcribe"] = False
    assert ms.auto_transcribe(_audio("a1")) is False
    assert ms.auto_transcribe_backlog(now=1000) == 0


def test_no_encola_sin_api_key(monkeypatch):
    monkeypatch.setattr(ms, "has_transcription_key", lambda config=None: False)
    assert ms.auto_transcribe(_audio("a1")) is False
    assert ms.auto_transcribe_backlog(now=1000) == 0


def test_no_duplica():
    msg = _audio("a1")
    assert ms.auto_transcribe(msg) is True
    assert ms.auto_transcribe(msg) is False
    assert ms._jobs.qsize() == 1


def test_ignora_no_audio_y_ya_transcriptos():
    assert ms.auto_transcribe({"id": "x", "media_type": "image", "media_raw": "{}"}) is False
    assert ms.auto_transcribe({**_audio("a1"), "transcript": "hola"}) is False


def test_no_reintenta_fallidos():
    msg = _audio("a1")
    db.set_message_transcript_failed("a1")
    assert ms.auto_transcribe(msg) is False
    assert ms.auto_transcribe_backlog(now=1000) == 0
    # el manual sigue andando
    ms.request_transcript(msg)
    assert ms.is_pending("transcribe", "a1")


def test_falla_en_worker_marca_error_y_no_reintenta(monkeypatch):
    msg = _audio("a1")
    ms.auto_transcribe(msg)

    def boom(*a, **k):
        raise RuntimeError("sin red")
    monkeypatch.setattr(ms, "_download", boom)
    # corre un solo trabajo de la cola
    monkeypatch.setattr(ms._jobs, "get", lambda real=ms._jobs.get: _una_vez(real))
    with pytest.raises(StopIteration):
        ms._run()
    assert db.is_message_transcript_failed("a1")
    assert ms.get_error("transcribe", "a1")
    assert ms.auto_transcribe(msg) is False


_vez = {"n": 0}


def _una_vez(real):
    _vez["n"] += 1
    if _vez["n"] > 1:
        _vez["n"] = 0
        raise StopIteration
    return real()


def test_backlog_solo_recientes_y_sin_transcripcion():
    _audio("viejo", ts=1000)
    _audio("nuevo", ts=1000 + 10 * 86400)
    _audio("hecho", ts=1000 + 10 * 86400)
    db.set_message_transcript("hecho", "ya está")
    n = ms.auto_transcribe_backlog(now=1000 + 10 * 86400 + 60)
    assert n == 1
    assert ms.is_pending("transcribe", "nuevo")
    assert not ms.is_pending("transcribe", "viejo")


def test_prioridad_manual_antes_que_automatica():
    ms.auto_transcribe(_audio("auto"))
    ms.request_transcript(_audio("manual", ts=1001))
    first = ms._jobs.get()
    assert first[3]["id"] == "manual"


def test_transcript_exitosa_limpia_fallo():
    _audio("a1")
    db.set_message_transcript_failed("a1")
    db.set_message_transcript("a1", "hola")
    assert not db.is_message_transcript_failed("a1")
