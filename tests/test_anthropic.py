import configparser
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.ai import ai_service, psych_analysis as pa
from app.ai.ai_service import AIService


class _Resp:
    def __init__(self, status=200, body=None):
        self.status_code, self._body, self.ok, self.reason = status, body or {}, status < 400, "x"

    def json(self):
        return self._body

    def raise_for_status(self):
        if not self.ok:
            raise RuntimeError(str(self.status_code))


def _svc(key="clave-de-prueba", model="claude-haiku-5-5"):
    cfg = configparser.ConfigParser()
    cfg.read_dict({"IA": {"enabled": "true", "provider": "Claude", "model": model}, "ANTHROPIC": {"api_key": key}})
    return AIService(cfg)


def test_messages_request_shape(monkeypatch):
    sent = {}

    def fake_post(url, headers, json, timeout):
        sent.update(url=url, headers=headers, body=json)
        return _Resp(200, {"content": [{"type": "text", "text": "Hola"}, {"type": "text", "text": " mundo"}]})

    monkeypatch.setattr(ai_service.requests, "post", fake_post)
    out = _svc().get_anthropic_response("hi", model="claude-opus-5-5")
    assert out == "Hola mundo"
    assert sent["url"].endswith("/v1/messages")
    assert sent["headers"]["x-api-key"] == "clave-de-prueba" and "anthropic-version" in sent["headers"]
    assert sent["body"]["model"] == "claude-opus-5-5" and sent["body"]["max_tokens"] > 0


def test_uses_configured_model_and_env_key(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "desde-env")
    seen = {}
    monkeypatch.setattr(ai_service.requests, "post",
                        lambda url, headers, json, timeout: seen.update(h=headers, m=json["model"]) or
                        _Resp(200, {"content": [{"type": "text", "text": "ok"}]}))
    assert _svc(key="").generate_ai_completion("hi") == "ok"
    assert seen["m"] == "claude-haiku-5-5" and seen["h"]["x-api-key"] == "desde-env"


def test_auth_error_is_reported(monkeypatch):
    monkeypatch.setattr(ai_service.requests, "post",
                        lambda *a, **k: _Resp(401, {"error": {"message": "invalid x-api-key"}}))
    out = _svc().get_anthropic_response("hi")
    assert AIService.is_error_response(out) and "autenticación" in out


def test_missing_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert AIService.is_error_response(_svc(key="").get_anthropic_response("hi"))


def test_model_list_falls_back_without_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    models = _svc(key="").get_anthropic_models()
    assert {"claude-haiku-5-5", "claude-sonnet-5-5", "claude-opus-5-5"} <= set(models)


def test_model_list_from_api(monkeypatch):
    monkeypatch.setattr(ai_service.requests, "get", lambda *a, **k: _Resp(200, {"data": [
        {"id": "claude-opus-5-5"}, {"id": "claude-haiku-5-5"}, {"id": "otro"}, {"id": "claude-nuevo-9"}]}))
    assert _svc().get_anthropic_models() == ["claude-opus-5-5", "claude-haiku-5-5", "claude-nuevo-9"]


def test_claude_is_a_selectable_provider():
    assert "Claude" in pa.PROVIDERS and pa.CHAR_BUDGET["Claude"] > 0
