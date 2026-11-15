"""Mensajería extra: eliminados/ediciones conservados, destacados, encuestas, plantillas, repetición y respuestas automáticas."""
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest

import app.db as db
from app import auto_reply, config, polls, quick_replies, tags
from app.recurrence import describe_repeat, next_repeat_at, normalize_repeat

CHAT = "111@s.whatsapp.net"


@pytest.fixture(autouse=True)
def tmpdb(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setattr(db, "_load_lid_phone_map", lambda: {})
    db._early_status.clear()
    tags._invalidate()
    monkeypatch.setitem(config._pref_cache, "keep_deleted", True)
    db.init_db()
    yield
    db._early_status.clear()
    tags._invalidate()


def _msg(i, text="hola", ts=100, **kw):
    return db.save_message(i, CHAT, CHAT, "Ana", False, text, ts, **kw)


# ── eliminados y ediciones ──
def test_revoke_keeps_original():
    _msg("m1", "dato secreto")
    assert db.revoke_message_local("m1")
    m = db.get_message("m1")
    assert m["media_type"] == "revoked" and m["text"] == ""
    orig = db.get_revoked_original("m1")
    assert orig["text"] == "dato secreto" and orig["media_type"] == "text"


def test_revoke_without_setting_drops_content(monkeypatch):
    monkeypatch.setitem(config._pref_cache, "keep_deleted", False)
    _msg("m1", "dato secreto")
    db.revoke_message_local("m1")
    assert db.get_revoked_original("m1") is None


def test_revoke_twice_does_not_lose_original():
    _msg("m1", "uno")
    db.revoke_message_local("m1")
    db.revoke_message_local("m1")
    assert db.get_revoked_original("m1")["text"] == "uno"


def test_edit_history_in_order():
    _msg("m1", "v1")
    assert db.apply_message_edit("m1", "v2")
    assert db.apply_message_edit("m1", "v3")
    assert [e["text"] for e in db.get_message_edits("m1")] == ["v1", "v2"]
    m = db.get_message("m1")
    assert m["text"] == "v3" and m["edited"] == 1


def test_edit_same_text_no_version(monkeypatch):
    _msg("m1", "igual")
    db.apply_message_edit("m1", "igual")
    assert db.get_message_edits("m1") == []


def test_edit_history_off(monkeypatch):
    monkeypatch.setitem(config._pref_cache, "keep_deleted", False)
    _msg("m1", "v1")
    db.apply_message_edit("m1", "v2")
    assert db.get_message_edits("m1") == []


def test_keep_deleted_default_on():
    assert config.PREF_DEFAULTS["keep_deleted"] is True


# ── destacados ──
def test_star_and_search():
    _msg("m1", "La clave del wifi es 1234", ts=100)
    _msg("m2", "otra cosa", ts=101)
    assert db.set_message_starred("m1", True)
    assert db.set_message_starred("m2", True)
    assert [m["id"] for m in db.get_starred_messages()] == ["m2", "m1"]   # el último destacado primero
    assert [m["id"] for m in db.get_starred_messages("WIFI")] == ["m1"]
    db.set_message_starred("m1", False)
    assert [m["id"] for m in db.get_starred_messages()] == ["m2"]


def test_starred_includes_chat_name():
    _msg("m1", "hola")
    db.set_message_starred("m1", True)
    assert db.get_starred_messages()[0]["chat_name"]


def test_set_star_unknown_message():
    assert db.set_message_starred("nope", True) is False


# ── encuestas ──
POLL = {"id": "p1", "name": "¿Pizza?", "multi": False, "voters": 3,
        "options": [{"name": "Sí", "votes": 2, "mine": True}, {"name": "No", "votes": 1, "mine": False},
                    {"name": "Tal vez", "votes": 0, "mine": False}]}


def test_poll_roundtrip_and_update():
    _msg("p1", "📊 Encuesta: ¿Pizza?", media_type="poll")
    assert db.set_message_poll("p1", POLL)
    assert db.parse_poll(db.get_message("p1"))["options"][0]["votes"] == 2
    db.set_message_poll("p1", {**POLL, "options": [{"name": "Sí", "votes": 5}]})
    assert db.parse_poll(db.get_message("p1"))["options"][0]["votes"] == 5


def test_parse_poll_garbage():
    assert db.parse_poll({"poll_json": "no es json"}) is None
    assert db.parse_poll({}) is None


def test_poll_lines_and_bars():
    lines = polls.poll_lines(POLL)
    assert [ln["votes"] for ln in lines] == [2, 1, 0]
    assert lines[0]["bar"] == "█" * polls.BAR_WIDTH and lines[2]["bar"] == "░" * polls.BAR_WIDTH
    assert lines[0]["mine"] and polls.totals(POLL) == 3
    assert "¿Pizza?" in polls.poll_html(POLL, "#0f0", "#999")
    assert polls.poll_text(POLL).splitlines()[1] == "- Sí: 2"


# ── plantillas ──
def test_templates_save_load_dedup(tmp_path):
    path = str(tmp_path / "plantillas.json")
    assert quick_replies.save_templates(
        [{"shortcut": "/Saludo", "text": " Hola! "}, {"shortcut": "saludo", "text": "otro"}, {"shortcut": "x", "text": ""}], path)
    assert quick_replies.load_templates(path) == [{"shortcut": "saludo", "text": "Hola!"}]
    assert not os.path.exists(path + ".tmp")


def test_templates_corrupt_file(tmp_path):
    path = tmp_path / "p.json"
    path.write_text("{no", encoding="utf-8")
    assert quick_replies.load_templates(str(path)) == []


def test_expand_trailing_shortcut():
    items = [{"shortcut": "dir", "text": "Calle Falsa 123"}, {"shortcut": "hola", "text": "Hola, ¿cómo estás?"}]
    assert quick_replies.expand_trailing("/dir", items) == "Calle Falsa 123"
    assert quick_replies.expand_trailing("Mi dirección: /dir", items) == "Mi dirección: Calle Falsa 123"
    assert quick_replies.expand_trailing("/nada", items) is None
    assert quick_replies.expand_trailing("http://sitio/dir", items) is None   # una URL no es un atajo
    assert quick_replies.expand_trailing("sin barra", items) is None
    assert [s["shortcut"] for s in quick_replies.suggestions("/h", items)] == ["hola"]


# ── repetición de programados ──
def _ts(*a):
    return int(datetime(*a).timestamp())


def test_daily_weekly_monthly_yearly():
    at = _ts(2026, 1, 31, 9, 0)
    assert next_repeat_at(at, {"type": "daily"}, at) == _ts(2026, 2, 1, 9, 0)
    assert next_repeat_at(at, {"type": "monthly"}, at) == _ts(2026, 2, 28, 9, 0)      # febrero no tiene 31
    assert next_repeat_at(at, {"type": "yearly"}, at) == _ts(2027, 1, 31, 9, 0)
    # lunes y miércoles; el 31/01/2026 es sábado
    assert next_repeat_at(at, {"type": "weekly", "days": [1, 3]}, at) == _ts(2026, 2, 2, 9, 0)
    assert next_repeat_at(at, {"type": "weekly", "days": [1], "every": 2}, at) == _ts(2026, 2, 9, 9, 0)


def test_monthly_does_not_drift_from_anchor():
    anchor = _ts(2026, 1, 31, 9, 0)
    assert next_repeat_at(anchor, {"type": "monthly"}, _ts(2026, 3, 1)) == _ts(2026, 3, 31, 9, 0)


def test_yearly_leap_day():
    at = _ts(2024, 2, 29, 8, 0)
    assert next_repeat_at(at, {"type": "yearly"}, at) == _ts(2025, 2, 28, 8, 0)
    assert next_repeat_at(at, {"type": "yearly"}, _ts(2025, 6, 1)) == _ts(2026, 2, 28, 8, 0)


def test_next_repeat_skips_missed_occurrences():
    at = _ts(2026, 1, 1, 9, 0)
    assert next_repeat_at(at, {"type": "daily"}, _ts(2026, 1, 10, 12, 0)) == _ts(2026, 1, 11, 9, 0)


def test_repeat_validation():
    assert normalize_repeat(None) is None and normalize_repeat({"type": "x"}) is None
    assert normalize_repeat({"type": "weekly", "days": [9, 2, 2, "1"]}) == {"type": "weekly", "every": 1, "days": [1, 2]}
    assert next_repeat_at(100, {"type": "nada"}, 0) is None
    assert describe_repeat({"type": "weekly", "days": [1, 3]}) == "Cada semana (lun, mié)"
    assert describe_repeat({"type": "yearly"}) == "Todos los años"
    assert describe_repeat({"type": "daily", "every": 3}) == "Cada 3 días"


# ── respuestas automáticas (config) ──
def test_auto_reply_rules():
    r = auto_reply.new_rule("schedule")
    assert auto_reply.validate_rule(r) == ""
    r["text"] = "  "
    assert auto_reply.validate_rule(r)
    r["text"] = "ok"
    r["from"] = "25:00"
    assert auto_reply.validate_rule(r)
    assert auto_reply.new_rule("manual")["kind"] == "manual"
    d = auto_reply.describe_rule({**auto_reply.new_rule("schedule"), "days": [1, 2], "exclude": ["a"]})
    assert "Lun, Mar" in d and "1 excepciones" in d and "12 h" in d
    assert auto_reply.active_rules({"rules": [{"enabled": False}, {"id": "b"}]}) == [{"id": "b"}]
