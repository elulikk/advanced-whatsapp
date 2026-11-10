"""Ramas de AIService sin cubrir en test_anthropic: errores de red, parseo, fallbacks, claves en mensajes."""
import configparser
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest
import requests

from app.ai import ai_service, psych_analysis as pa
from app.ai.ai_service import AIService


class _Resp:
    def __init__(self, status=200, body=None, text=""):
        self.status_code, self._body, self.ok, self.reason, self.text = status, body, status < 400, "x", text

    def json(self):
        if self._body is None:
            raise ValueError("no json")
        return self._body

    def raise_for_status(self):
        if not self.ok:
            raise requests.HTTPError(f"{self.status_code} Client Error: x for url: https://api.example.com")


def _cfg(**sections):
    cfg = configparser.ConfigParser()
    base = {"IA": {"enabled": "true", "provider": "Groq", "model": ""}}
    base.update(sections)
    cfg.read_dict(base)
    return cfg


def _svc(**sections):
    return AIService(_cfg(**sections))


def _chat(content):
    return {"choices": [{"message": {"content": content}}]}


# ─── is_error_response ───────────────────────────────────────────────────────

@pytest.mark.parametrize("text", [None, "", "   ", "Error: algo", "error de red", "⚠️ Cuota excedida en X",
                                  "❌ Error de autenticación", "⏳ Tiempo de espera", "Error conectando con Groq (m):\nx"])
def test_is_error_response_true(text):
    assert AIService.is_error_response(text)


def test_is_error_response_ignores_markers_inside_valid_answers():
    assert not AIService.is_error_response("Ojo con el precio ⚠️ sube mañana")
    assert not AIService.is_error_response("Listo ✅ y no ❌ nada ⏳ más")
    assert not AIService.is_error_response("Hola, te confirmo la cita")


# ─── _format_error ───────────────────────────────────────────────────────────

def test_format_error_categories():
    f = _svc()._format_error
    assert "Cuota excedida" in f("Groq", "429 Too Many Requests", "m")
    assert "Cuota excedida" in f("Groq", "Rate limit reached", "m")
    assert "autenticación" in f("OpenAI", "401 Unauthorized", "m")
    assert "autenticación" in f("OpenAI", "Incorrect API key provided", "m")
    assert "no encontrado" in f("Gemini", "404 model not found", "m")
    assert "Tiempo de espera" in f("Groq", requests.Timeout("Read timed out. (read timeout=30)"), "m")
    other = f("Groq", "boom" * 200, "m")
    assert other.startswith("Error conectando con Groq (m)") and len(other) < 300


def test_format_error_retry_hint_and_no_hint():
    f = _svc()._format_error
    assert "Reintenta en 12 segundos" in f("Gemini", "429 quota. Please retry after 12.4s", "m")
    assert "Reintenta" not in f("Gemini", "429 quota exceeded, retry later", "m")


def test_format_error_never_leaks_api_keys():
    secrets = ["sk-proj-ABCDEF1234567890", "gsk_ABCDEF1234567890xyz", "AIzaSyA-1234567890abcdefghij_KLM"]
    for s in secrets:
        out = _svc()._format_error("X", RuntimeError(f"fallo con la clave {s} en la petición"), "m")
        assert s not in out and "[clave oculta]" in out
    assert ai_service._redact("hola sin claves") == "hola sin claves"


def test_model_listing_errors_do_not_print_keys(monkeypatch, capsys):
    def boom(*a, **k):
        raise requests.ConnectionError("fallo con sk-secretisimo1234567890 en la url")
    monkeypatch.setattr(ai_service.requests, "get", boom)
    svc = _svc(GROQ={"api_key": "k"}, OPENAI={"api_key": "k"}, ANTHROPIC={"api_key": "k"})
    svc.get_groq_models(), svc.get_openai_models(), svc.get_anthropic_models()
    out = capsys.readouterr().out
    assert "sk-secretisimo" not in out and out.count("[clave oculta]") == 3


# ─── Groq / OpenAI ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("method,section,url_part", [("get_groq_response", "GROQ", "groq.com"),
                                                      ("get_openai_response", "OPENAI", "openai.com")])
def test_chat_providers_request_and_parse(monkeypatch, method, section, url_part):
    sent = {}

    def fake_post(url, headers, json, timeout):
        sent.update(url=url, headers=headers, body=json, timeout=timeout)
        return _Resp(200, _chat("Hola ñandú"))

    monkeypatch.setattr(ai_service.requests, "post", fake_post)
    svc = _svc(**{section: {"api_key": " k1 ", "model": "modelo-x"}})
    assert getattr(svc, method)("hola", timeout=7) == "Hola ñandú"
    assert url_part in sent["url"] and sent["headers"]["Authorization"] == "Bearer k1"
    assert sent["body"]["model"] == "modelo-x" and sent["timeout"] == 7
    assert sent["body"]["messages"] == [{"role": "user", "content": "hola"}]


@pytest.mark.parametrize("method,section", [("get_groq_response", "GROQ"), ("get_openai_response", "OPENAI")])
def test_chat_providers_empty_model_in_config_uses_default(monkeypatch, method, section):
    seen = {}
    monkeypatch.setattr(ai_service.requests, "post",
                        lambda url, headers, json, timeout: seen.update(m=json["model"]) or _Resp(200, _chat("ok")))
    assert getattr(_svc(**{section: {"api_key": "k", "model": ""}}), method)("x") == "ok"
    assert seen["m"]


@pytest.mark.parametrize("method,section", [("get_groq_response", "GROQ"), ("get_openai_response", "OPENAI")])
def test_chat_providers_failures_become_error_text(monkeypatch, method, section):
    call = getattr(_svc(**{section: {"api_key": "k"}}), method)
    assert AIService.is_error_response(getattr(_svc(**{section: {"api_key": ""}}), method)("x"))

    def timeout(*a, **k):
        raise requests.Timeout("Read timed out. (read timeout=30)")
    monkeypatch.setattr(ai_service.requests, "post", timeout)
    assert "Tiempo de espera" in call("x")

    monkeypatch.setattr(ai_service.requests, "post", lambda *a, **k: _Resp(429, {}))
    assert "Cuota excedida" in call("x")
    monkeypatch.setattr(ai_service.requests, "post", lambda *a, **k: _Resp(401, {}))
    assert "autenticación" in call("x")
    # JSON inválido / sin «choices»
    monkeypatch.setattr(ai_service.requests, "post", lambda *a, **k: _Resp(200, None))
    assert AIService.is_error_response(call("x"))
    monkeypatch.setattr(ai_service.requests, "post", lambda *a, **k: _Resp(200, {"error": "x"}))
    assert AIService.is_error_response(call("x"))
    # contenido null: texto vacío (error), nunca None
    monkeypatch.setattr(ai_service.requests, "post", lambda *a, **k: _Resp(200, _chat(None)))
    assert call("x") == ""


# ─── Anthropic: ramas de error ───────────────────────────────────────────────

def test_anthropic_error_without_json_body_and_non_text_blocks(monkeypatch):
    svc = _svc(ANTHROPIC={"api_key": "k"})
    monkeypatch.setattr(ai_service.requests, "post", lambda *a, **k: _Resp(500, None))
    assert "Error conectando con Anthropic" in svc.get_anthropic_response("x")
    monkeypatch.setattr(ai_service.requests, "post", lambda *a, **k: _Resp(429, {"error": {"message": "rate limit"}}))
    assert "Cuota excedida" in svc.get_anthropic_response("x")
    monkeypatch.setattr(ai_service.requests, "post", lambda *a, **k: _Resp(200, {"content": [
        {"type": "thinking", "thinking": "..."}, {"type": "text", "text": "Hola"}]}))
    assert svc.get_anthropic_response("x") == "Hola"
    monkeypatch.setattr(ai_service.requests, "post", lambda *a, **k: _Resp(200, {"content": []}))
    assert AIService.is_error_response(svc.get_anthropic_response("x"))


def test_anthropic_empty_configured_model_uses_default(monkeypatch):
    seen = {}
    monkeypatch.setattr(ai_service.requests, "post",
                        lambda url, headers, json, timeout: seen.update(m=json["model"]) or
                        _Resp(200, {"content": [{"type": "text", "text": "ok"}]}))
    _svc(ANTHROPIC={"api_key": "k", "model": ""}).get_anthropic_response("x")
    assert seen["m"] == "claude-sonnet-5-5"


# ─── Listas de modelos ───────────────────────────────────────────────────────

def test_groq_models_filtered_and_ordered(monkeypatch):
    ids = ["whisper-large-v3", "zeta", "llama-3.3-70b-versatile", "llama-guard-4", "openai/gpt-oss-20b", "orpheus-tts"]
    monkeypatch.setattr(ai_service.requests, "get", lambda *a, **k: _Resp(200, {"data": [{"id": i} for i in ids]}))
    assert _svc(GROQ={"api_key": "k"}).get_groq_models() == ["openai/gpt-oss-20b", "llama-3.3-70b-versatile", "zeta"]


def test_openai_models_filtered_and_ordered(monkeypatch):
    ids = ["text-embedding-3", "gpt-4o", "whisper-1", "gpt-4o-mini", "gpt-4o-realtime-preview", "o1-pro", "dall-e-3"]
    monkeypatch.setattr(ai_service.requests, "get", lambda *a, **k: _Resp(200, {"data": [{"id": i} for i in ids]}))
    assert _svc(OPENAI={"api_key": "k"}).get_openai_models() == ["gpt-4o-mini", "gpt-4o", "o1-pro"]


@pytest.mark.parametrize("method,section", [("get_groq_models", "GROQ"), ("get_openai_models", "OPENAI"),
                                            ("get_anthropic_models", "ANTHROPIC")])
def test_model_lists_fall_back_on_network_error_empty_data_or_no_key(monkeypatch, method, section):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    no_key = getattr(_svc(), method)()
    assert no_key
    svc = _svc(**{section: {"api_key": "k"}})

    def boom(*a, **k):
        raise requests.ConnectionError("sin red")
    monkeypatch.setattr(ai_service.requests, "get", boom)
    assert getattr(svc, method)() == no_key
    monkeypatch.setattr(ai_service.requests, "get", lambda *a, **k: _Resp(200, {"data": []}))
    assert getattr(svc, method)() == no_key
    monkeypatch.setattr(ai_service.requests, "get", lambda *a, **k: _Resp(500, {}))
    assert getattr(svc, method)() == no_key


def test_gemini_without_key_returns_fallback_and_error():
    svc = _svc(GEMINI={"api_key": ""})
    assert "gemini-2.5-flash" in svc.get_gemini_models()
    assert AIService.is_error_response(svc.get_gemini_response("x"))


# ─── generate_ai_completion / test_connection ────────────────────────────────

def _stub(monkeypatch, svc, **results):
    calls = []
    for name in ("gemini", "groq", "openai", "anthropic"):
        def make(n):
            def fn(prompt, *a, **k):
                calls.append(n)
                return results.get(n, "Error: sin clave")
            return fn
        monkeypatch.setattr(svc, f"get_{name}_response", make(name))
    monkeypatch.setattr(svc, "_ensure_gemini", lambda key=None: True)
    return calls


def test_completion_disabled():
    svc = _svc(IA={"enabled": "false", "provider": "Groq"})
    assert "desactivada" in svc.generate_ai_completion("x")


@pytest.mark.parametrize("provider,order", [
    ("Claude", ["anthropic", "gemini", "openai"]),
    ("Groq", ["groq", "gemini", "openai"]),
    ("OpenAI", ["openai", "gemini", "groq"]),
    ("Gemini", ["gemini", "groq", "openai"]),
])
def test_completion_fallback_order_and_final_error(monkeypatch, provider, order):
    svc = _svc()
    calls = _stub(monkeypatch, svc)
    out = svc.generate_ai_completion("x", provider=provider)
    assert calls == order and AIService.is_error_response(out)


def test_completion_stops_at_first_success(monkeypatch):
    svc = _svc()
    calls = _stub(monkeypatch, svc, gemini="respuesta de gemini")
    assert svc.generate_ai_completion("x", provider="Groq") == "respuesta de gemini"
    assert calls == ["groq", "gemini"]


def test_completion_never_returns_none(monkeypatch):
    svc = _svc()
    _stub(monkeypatch, svc, groq=None, gemini=None, openai=None)
    out = svc.generate_ai_completion("x", provider="Groq")
    assert isinstance(out, str) and AIService.is_error_response(out)


def test_test_connection_results(monkeypatch):
    svc = _svc()
    _stub(monkeypatch, svc, groq=" OK ", anthropic="❌ Error de autenticación")
    ok = svc.test_connection("Groq Cloud", model_name="m")
    assert ok["success"] and ok["response"] == "OK" and ok["model"] == "m" and ok["error"] == ""
    bad = svc.test_connection("claude")
    assert not bad["success"] and "autenticación" in bad["error"] and bad["provider"].startswith("Anthropic")
    unknown = svc.test_connection("mistral")
    assert not unknown["success"] and "no soportado" in unknown["error"]


# ─── ask_llm (psych_analysis) ────────────────────────────────────────────────

def test_ask_llm_routes_by_provider_case_insensitive(monkeypatch):
    calls = []
    for name in ("groq", "openai", "anthropic", "gemini"):
        monkeypatch.setattr(AIService, f"get_{name}_response",
                            lambda self, prompt, _n=name, **k: calls.append((_n, k)) or _n)
    monkeypatch.setattr(pa, "AIService", lambda: AIService(_cfg()))
    assert pa.ask_llm("p", "Groq", "m") == "groq" and pa.ask_llm("p", "groq", "m") == "groq"
    assert pa.ask_llm("p", "OpenAI", "") == "openai"
    assert pa.ask_llm("p", "Claude", "m") == "anthropic" and pa.ask_llm("p", "anthropic") == "anthropic"
    assert pa.ask_llm("p", None, None) == "gemini" and pa.ask_llm("p", "Gemini", "f") == "gemini"
    assert calls[2] == ("openai", {"model": None, "timeout": pa.AI_TIMEOUT})  # modelo vacío → None
