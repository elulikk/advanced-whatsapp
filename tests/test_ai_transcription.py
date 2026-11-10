"""transcription.transcribe_audio: proveedores, respaldo entre ellos y errores (sin red)."""
import configparser
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest
import requests

from app.ai import transcription as tr


class _Resp:
    def __init__(self, status=200, body=None, text=""):
        self.status_code, self._body, self.text = status, body, text

    def json(self):
        if self._body is None:
            raise ValueError("no json")
        return self._body


def _cfg(groq="", openai=""):
    cfg = configparser.ConfigParser()
    cfg.read_dict({"GROQ": {"api_key": groq}, "OPENAI": {"api_key": openai}})
    return cfg


@pytest.fixture
def audio(tmp_path):
    p = tmp_path / "nota.ogg"
    p.write_bytes(b"OggS-datos")
    return str(p)


def test_no_keys_raises_with_hint(audio):
    with pytest.raises(RuntimeError, match="API key"):
        tr.transcribe_audio(audio, _cfg())


def test_missing_file_raises_clear_error(tmp_path):
    with pytest.raises(RuntimeError, match="No se encontró"):
        tr.transcribe_audio(str(tmp_path / "no-existe.ogg"), _cfg(groq="k"))


def test_groq_first_request_shape_and_strip(monkeypatch, audio):
    seen = []

    def fake_post(url, headers, files, data, timeout):
        seen.append((url, headers, files["file"][0], data, timeout))
        return _Resp(200, {"text": "  nos vemos a las 5 ñ  "})

    monkeypatch.setattr(tr.requests, "post", fake_post)
    assert tr.transcribe_audio(audio, _cfg(groq=" kg ", openai="ko")) == "nos vemos a las 5 ñ"
    assert len(seen) == 1
    url, headers, fname, data, timeout = seen[0]
    assert url == tr.GROQ_TRANSCRIPTION_URL and headers == {"Authorization": "Bearer kg"}
    assert fname == "nota.ogg" and data["model"] == "whisper-large-v3-turbo" and timeout > 0


def test_falls_back_to_openai_on_http_error_exception_or_bad_json(monkeypatch, audio):
    for first in (lambda: _Resp(429, text="rate"), lambda: (_ for _ in ()).throw(requests.Timeout("t")),
                  lambda: _Resp(200, None)):
        urls = []

        def fake_post(url, **kw):
            urls.append(url)
            return first() if url == tr.GROQ_TRANSCRIPTION_URL else _Resp(200, {"text": "hola"})

        monkeypatch.setattr(tr.requests, "post", fake_post)
        assert tr.transcribe_audio(audio, _cfg(groq="kg", openai="ko")) == "hola"
        assert urls == [tr.GROQ_TRANSCRIPTION_URL, tr.OPENAI_TRANSCRIPTION_URL]


def test_openai_only(monkeypatch, audio):
    seen = []
    monkeypatch.setattr(tr.requests, "post", lambda url, **kw: seen.append((url, kw["data"]["model"])) or
                        _Resp(200, {"text": "ok"}))
    assert tr.transcribe_audio(audio, _cfg(openai="ko")) == "ok"
    assert seen == [(tr.OPENAI_TRANSCRIPTION_URL, "whisper-1")]


def test_all_providers_fail_reports_last_error_truncated(monkeypatch, audio):
    monkeypatch.setattr(tr.requests, "post", lambda url, **kw: _Resp(500, text="x" * 1000))
    with pytest.raises(RuntimeError) as e:
        tr.transcribe_audio(audio, _cfg(groq="kg", openai="ko"))
    msg = str(e.value)
    assert "OpenAI" in msg and "HTTP 500" in msg and len(msg) < 300


def test_silent_audio_returns_empty_string(monkeypatch, audio):
    monkeypatch.setattr(tr.requests, "post", lambda url, **kw: _Resp(200, {"text": None}))
    assert tr.transcribe_audio(audio, _cfg(groq="kg")) == ""


def test_error_does_not_leak_key(monkeypatch, audio):
    monkeypatch.setattr(tr.requests, "post", lambda url, **kw: _Resp(401, text="bad"))
    with pytest.raises(RuntimeError) as e:
        tr.transcribe_audio(audio, _cfg(groq="gsk_SECRETO123456"))
    assert "gsk_SECRETO123456" not in str(e.value)
