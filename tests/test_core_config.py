"""Pruebas de app/config.py (preferencias, token, escritura de config.ini) y utilidades de windows_integration."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest

from app import config


@pytest.fixture(autouse=True)
def sandbox(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONFIG_PATH", str(tmp_path / "config.ini"))
    monkeypatch.setattr(config, "MAGNETO_CONFIG_PATH", str(tmp_path / "magneto.ini"))
    monkeypatch.setattr(config, "TOKEN_PATH", str(tmp_path / "gateway.token"))
    config._pref_cache.clear()
    yield
    config._pref_cache.clear()


# ── Tamaño de fuente ──

@pytest.mark.parametrize("value,expected", [
    (100, 100), ("120", 120), (10, config.FONT_SCALE_MIN), (999, config.FONT_SCALE_MAX),
    (None, config.FONT_SCALE_DEFAULT), ("abc", config.FONT_SCALE_DEFAULT), (85.9, 85),
])
def test_clamp_font_scale(value, expected):
    assert config.clamp_font_scale(value) == expected


def test_font_scale_roundtrip_and_clamped_on_save():
    assert config.get_chat_font_scale() == config.FONT_SCALE_DEFAULT
    assert config.set_chat_font_scale(130) is True
    assert config.get_chat_font_scale() == 130
    config.set_chat_font_scale(5000)
    assert config.get_chat_font_scale() == config.FONT_SCALE_MAX


# ── Carga de configuración ──

def test_load_defaults_without_files():
    cfg = config.load_app_config()
    assert cfg.get("IA", "enabled") == "true"
    assert cfg.get("IA", "provider") == "Gemini"
    for section in ("GEMINI", "GROQ", "OPENAI", "ANTHROPIC", "GENERAL"):
        assert cfg.has_section(section)


def test_percent_signs_are_not_interpolated(tmp_path):
    (tmp_path / "config.ini").write_text("[GEMINI]\napi_key = abc%def\n", encoding="utf-8")
    assert config.load_app_config().get("GEMINI", "api_key") == "abc%def"


def test_latin1_config_file_still_loads(tmp_path):
    (tmp_path / "config.ini").write_bytes("[GENERAL]\nnombre = Martín\n".encode("latin-1"))
    assert config.load_app_config().get("GENERAL", "nombre") == "Martín"


def test_corrupt_config_file_falls_back_to_defaults(tmp_path):
    (tmp_path / "config.ini").write_text("esto no es ini\n[sin cerrar", encoding="utf-8")
    assert config.load_app_config().get("IA", "provider") == "Gemini"


def test_magneto_fallback_fills_missing_keys_only(tmp_path):
    (tmp_path / "magneto.ini").write_text(
        "[GEMINI]\napi_key = KEY_M\nmodel = gem-m\n[OPENAI]\napi_key = OAI_M\n[ERIK]\nprovider = OpenAI\n",
        encoding="utf-8")
    (tmp_path / "config.ini").write_text("[OPENAI]\napi_key = MIA\n", encoding="utf-8")
    cfg = config.load_app_config()
    assert cfg.get("GEMINI", "api_key") == "KEY_M" and cfg.get("GEMINI", "model") == "gem-m"
    assert cfg.get("OPENAI", "api_key") == "MIA"          # la propia manda
    assert cfg.get("IA", "provider") == "OpenAI"


def test_own_ia_settings_win_over_magneto(tmp_path):
    (tmp_path / "magneto.ini").write_text("[ERIK]\nprovider = OpenAI\n", encoding="utf-8")
    (tmp_path / "config.ini").write_text("[IA]\nprovider = Groq\n", encoding="utf-8")
    assert config.load_app_config().get("IA", "provider") == "Groq"


def test_save_is_atomic_and_leaves_no_temp_file(tmp_path):
    cfg = config.load_app_config()
    cfg.set("GENERAL", "x", "1")
    assert config.save_app_config(cfg) is True
    assert config.load_app_config().get("GENERAL", "x") == "1"
    assert not (tmp_path / "config.ini.tmp").exists()


def test_save_failure_returns_false(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONFIG_PATH", str(tmp_path / "no_existe" / "config.ini"))
    assert config.save_app_config(config.load_app_config()) is False


# ── Preferencias booleanas ──

def test_pref_defaults_and_cache():
    assert config.get_pref("notify_enabled") is True
    assert config.get_pref("clave_desconocida") is False


def test_set_pref_persists_and_updates_cache():
    assert config.set_pref("notify_sound", False) is True
    assert config.get_pref("notify_sound") is False
    config._pref_cache.clear()          # como un reinicio: se lee del archivo
    assert config.get_pref("notify_sound") is False
    config.set_pref("notify_sound", True)
    config._pref_cache.clear()
    assert config.get_pref("notify_sound") is True


def test_garbled_boolean_falls_back_to_default(tmp_path):
    (tmp_path / "config.ini").write_text("[GENERAL]\nnotify_sound = quizas\npolish_use_profile = nose\n",
                                         encoding="utf-8")
    assert config.get_pref("notify_sound") is True       # el predeterminado, no una excepción
    assert config.get_polish_profile() is True


def test_polish_settings_roundtrip():
    assert config.get_polish_ai() == ("", "")
    config.set_polish_ai("Gemini", " gemini-x ")
    assert config.get_polish_ai() == ("Gemini", "gemini-x")
    config.set_polish_ai(None, None)
    assert config.get_polish_ai() == ("", "")
    config.set_polish_mode("formal")
    assert config.get_polish_mode() == "formal"
    config.set_polish_profile(False)
    assert config.get_polish_profile() is False


def test_tag_palette_view_validates_values():
    assert config.get_tag_palette_view() == "grid"
    config.set_tag_palette_view("list")
    assert config.get_tag_palette_view() == "list"
    config.set_tag_palette_view("otra")
    assert config.get_tag_palette_view() == "grid"


# ── Token del gateway ──

def test_gateway_token_is_generated_once_and_reused(tmp_path):
    t1 = config.get_gateway_token()
    assert len(t1) >= 32
    assert config.get_gateway_token() == t1
    assert (tmp_path / "gateway.token").read_text(encoding="utf-8").strip() == t1


def test_short_or_empty_token_file_is_regenerated(tmp_path):
    (tmp_path / "gateway.token").write_text("corto", encoding="utf-8")
    t = config.get_gateway_token()
    assert len(t) >= 32 and t != "corto"


# ── windows_integration: utilidades puras ──

def test_toast_xml_escaping_and_control_characters():
    from app.windows_integration import _xml
    assert _xml('a<b>&"c') == "a&lt;b&gt;&amp;&quot;c"
    assert _xml("hola\x00\x08\x0b\x1bmundo\n\t") == "holamundo\n\t"
    assert _xml(None) == ""


def test_make_uri_and_short_id():
    from app.windows_integration import make_uri, _short_id, PROTOCOL
    assert make_uri("show") == f"{PROTOCOL}://show"
    assert make_uri("open", "1@s.whatsapp.net") == f"{PROTOCOL}://open?chat=1%40s.whatsapp.net"
    assert len(_short_id("x" * 500)) == 16 and _short_id("a") == _short_id("a") != _short_id("b")


def test_thumbnail_from_media_raw_edge_cases(tmp_path):
    import base64
    import json
    from app.windows_integration import thumbnail_from_media_raw
    assert thumbnail_from_media_raw(None, "id") is None
    assert thumbnail_from_media_raw("no es json", "id") is None
    assert thumbnail_from_media_raw(json.dumps({"message": {"imageMessage": {}}}), "id") is None
    raw = json.dumps({"message": {"ephemeralMessage": {"message": {"imageMessage": {
        "jpegThumbnail": base64.b64encode(b"\xff\xd8jpg").decode()}}}}})
    path = thumbnail_from_media_raw(raw, "thumb-test-id")
    assert path and open(path, "rb").read() == b"\xff\xd8jpg"
    os.remove(path)
