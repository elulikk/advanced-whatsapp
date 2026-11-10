"""Cobertura adicional de app/tags.py: expiración, efectos, textos de vencimiento y casos límite."""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest

import app.db as db
from app import tags

CHAT = "111@s.whatsapp.net"


@pytest.fixture(autouse=True)
def tmpdb(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "t.db"))
    tags._invalidate()
    db.init_db()
    yield
    tags._invalidate()


@pytest.fixture()
def received():
    hits = []
    tags.tag_signals.changed.connect(lambda: hits.append(1))
    yield hits
    tags.tag_signals.changed.disconnect()


def test_expires_at_by_mode():
    assert tags.expires_at({"expire_mode": "none", "expire_value": 5}, 1000, 0) is None
    assert tags.expires_at({"expire_mode": "days", "expire_value": 0}, 1000, 0) is None
    assert tags.expires_at({"expire_mode": "desconocido", "expire_value": 3}, 1000, 0) is None
    assert tags.expires_at({"expire_mode": "hours", "expire_value": 2}, 1000, 0) == 1000 + 7200
    assert tags.expires_at({"expire_mode": "days", "expire_value": 2}, 1000, 0) == 1000 + 2 * 86400
    assert tags.expires_at({"expire_mode": "inactive", "expire_value": 1}, 1000, 5000) == 5000 + 86400
    # un último mensaje anterior a la asignación no acorta el plazo
    assert tags.expires_at({"expire_mode": "inactive", "expire_value": 1}, 5000, 1000) == 5000 + 86400
    assert tags.expires_at({"expire_mode": "inactive", "expire_value": 1}, 5000, None) == 5000 + 86400


def test_add_days_zero_business_days_is_identity():
    assert tags.add_days(1_700_000_000, 0, True) == 1_700_000_000


def test_assign_unknown_tag_or_empty_chat_is_rejected():
    assert tags.assign_tag(CHAT, 99999) is False
    tid = tags.create_tag({"name": "X"})
    assert tags.assign_tag("", tid) is False
    assert tags.count_chats_with_tag(tid) == 0


def test_reassign_restarts_expiry_clock():
    db.get_or_create_chat(CHAT, "Ana")
    tid = tags.create_tag({"expire_mode": "hours", "expire_value": 1})
    tags.assign_tag(CHAT, tid)
    with db.db_conn() as c:
        c.execute("UPDATE chat_tags SET assigned_at = ?", (int(time.time()) - 7200,))
    tags.assign_tag(CHAT, tid)        # reinicia el reloj
    assert tags.purge_expired() == 0
    assert tags.count_chats_with_tag(tid) == 1


def test_purge_removes_orphans_and_ignores_fixed_tags():
    db.get_or_create_chat(CHAT, "Ana")
    fixed = tags.create_tag({"name": "Fijo"})
    timed = tags.create_tag({"name": "Temporal", "expire_mode": "hours", "expire_value": 1})
    tags.assign_tag(CHAT, fixed)
    tags.assign_tag(CHAT, timed)
    # asignación huérfana (tag borrado por fuera, sin pasar por delete_tag)
    with db.db_conn() as c:
        c.execute("PRAGMA foreign_keys = OFF")
        c.execute("INSERT INTO chat_tags VALUES (?, 4242, ?)", (CHAT, int(time.time())))
    assert tags.purge_expired(time.time() + 3 * 3600) == 2
    assert [t["id"] for t in tags.tags_for_chat(CHAT)] == [fixed]


def test_purge_without_expiring_tags_does_nothing():
    for t in tags.list_tags():
        tags.delete_tag(t["id"])
    tid = tags.create_tag({"name": "Fijo"})
    db.get_or_create_chat(CHAT, "Ana")
    tags.assign_tag(CHAT, tid)
    assert tags.purge_expired(time.time() + 10 * 365 * 86400) == 0


def test_signal_emitted_on_changes_only(received):
    db.get_or_create_chat(CHAT, "Ana")
    tid = tags.create_tag({"name": "X"})
    tags.assign_tag(CHAT, tid)
    tags.update_tag(tid, {"name": "Y"})
    before = len(received)
    tags.update_tag(tid, {"campo_inexistente": 1})     # sin campos válidos: no hace nada
    tags.purge_expired()                               # nada vencido: no avisa
    assert len(received) == before
    tags.remove_tag(CHAT)
    assert len(received) == before + 1


def test_update_tag_ignores_unknown_fields_and_invalidates_cache():
    tid = tags.create_tag({"name": "A", "color": "#111111"})
    assert tags.get_tag(tid)["color"] == "#111111"
    tags.update_tag(tid, {"color": "#222222", "id": 777, "sort_order": 50})
    t = tags.get_tag(tid)
    assert t["color"] == "#222222" and t["id"] == tid


def test_list_tags_returns_copies():
    tags.create_tag({"name": "A"})
    first = tags.list_tags()
    first[0]["name"] = "mutado"
    first.clear()
    assert tags.list_tags()[0]["name"] != "mutado" and tags.list_tags()


def test_create_tag_appends_at_end_of_priority():
    a = tags.create_tag({"name": "A"})
    b = tags.create_tag({"name": "B"})
    ids = [t["id"] for t in tags.list_tags()]
    assert ids.index(a) < ids.index(b) and ids[-1] == b


def test_remove_tag_all_for_chat():
    db.get_or_create_chat(CHAT, "Ana")
    a, b = tags.create_tag({"name": "A"}), tags.create_tag({"name": "B"})
    tags.assign_tag(CHAT, a)
    tags.assign_tag(CHAT, b)
    tags.remove_tag(CHAT)
    assert tags.tags_for_chat(CHAT) == []


def test_tags_for_chats_many_jids_and_priority_order():
    for t in tags.list_tags():
        tags.delete_tag(t["id"])
    a, b = tags.create_tag({"name": "A"}), tags.create_tag({"name": "B"})
    jids = [f"{i}@s.whatsapp.net" for i in range(900)]      # más que un lote de consulta
    for j in (jids[0], jids[-1]):
        tags.assign_tag(j, b)
        tags.assign_tag(j, a)
    out = tags.tags_for_chats(jids + ["", None])
    assert set(out) == {jids[0], jids[-1]}
    assert [t["id"] for t in out[jids[-1]]] == [a, b]
    assert all("assigned_at" in t for t in out[jids[0]])
    assert tags.tags_for_chats([]) == {}


def test_chat_effects_defaults_and_strength():
    assert tags.chat_effects([]) == {"bg": None, "bg_strength": 0, "text": None, "header": None,
                                     "row": None, "name": None}
    fx = tags.chat_effects([{"fx_bg": "#fff", "fx_bg_strength": 0, "fx_header": "#000"}])
    assert fx["bg"] == "#fff" and fx["bg_strength"] == 18 and fx["header"] == "#000"


def test_short_and_describe_expiry():
    assert tags.short_expiry({"expire_mode": "none"}) == "fijo"
    assert tags.short_expiry({"expire_mode": "hours", "expire_value": 24}) == "24 h"
    assert tags.short_expiry({"expire_mode": "days", "expire_value": 10, "expire_business": 1}) == "10 d háb."
    assert tags.short_expiry({"expire_mode": "inactive", "expire_value": 30}) == "30 d sin msj"
    assert tags.short_expiry({"expire_mode": "hours", "expire_value": 0}) == "fijo"
    assert tags.describe_expiry({"expire_mode": "none"}) == "fijo"
    assert "24 h" in tags.describe_expiry({"expire_mode": "hours", "expire_value": 24})
    now = int(time.time())
    hours = {"expire_mode": "hours", "expire_value": 5}
    assert tags.describe_expiry(hours, now) == "vence en 4 h" or tags.describe_expiry(hours, now) == "vence en 5 h"
    assert tags.describe_expiry(hours, now - 10 * 3600) == "vencido"
    assert tags.describe_expiry(hours, now - 5 * 3600 + 600).startswith("vence en ") and \
        tags.describe_expiry(hours, now - 5 * 3600 + 600).endswith("min")
    inactive = {"expire_mode": "inactive", "expire_value": 10}
    assert tags.describe_expiry(inactive, now, now).endswith("si no hay mensajes")
    assert "días" in tags.describe_expiry(inactive, now, now)


def test_merging_lid_chat_moves_tags_to_phone_chat():
    db.save_message("p1", "5491155640505@s.whatsapp.net", "x", "Daniel", False, "hola", 1000, chat_name="Daniel F")
    tid = tags.create_tag({"name": "T"})
    db.get_or_create_chat("156134996467780@lid", name="Daniel F")
    tags.assign_tag("156134996467780@lid", tid)
    assert db.merge_lid_duplicates() == 1
    assert [t["id"] for t in tags.tags_for_chat("5491155640505@s.whatsapp.net")] == [tid]
    assert tags.tags_for_chat("156134996467780@lid") == []
