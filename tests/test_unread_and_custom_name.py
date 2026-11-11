"""Contador de no leídos recalculado desde los mensajes reales y nombre propio de contactos."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest

import app.db as db
from app import tags

CHAT = "111@s.whatsapp.net"
OTRO = "222@s.whatsapp.net"


@pytest.fixture(autouse=True)
def tmpdb(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setattr(db, "_load_lid_phone_map", lambda: {})
    db._early_status.clear()
    tags._invalidate()
    db.init_db()
    yield
    db._early_status.clear()
    tags._invalidate()


def _in(i, jid=CHAT, ts=100):
    return db.save_message(i, jid, jid, "Ana", False, "hola", ts)


def _unread(jid=CHAT):
    return db.get_chat(jid)["unread_count"]


def test_recompute_caps_inflated_counter():
    _in("m1"); _in("m2", ts=101)
    with db.db_conn() as c:
        c.execute("UPDATE chats SET unread_count = 9 WHERE jid = ?", (CHAT,))
    assert db.recompute_unread_counts() == 1
    assert _unread() == 2
    assert db.get_total_unread_count() == 2


def test_recompute_zeroes_chat_without_pending_messages():
    _in("m1")
    db.mark_chat_as_read(CHAT)
    with db.db_conn() as c:
        c.execute("UPDATE chats SET unread_count = 5 WHERE jid = ?", (CHAT,))
    db.recompute_unread_counts()
    assert _unread() == 0


def test_recompute_is_idempotent_and_can_target_chats():
    _in("m1"); _in("a1", OTRO)
    with db.db_conn() as c:
        c.execute("UPDATE chats SET unread_count = 7")
    assert db.recompute_unread_counts(jids=[CHAT]) == 1
    assert _unread() == 1 and _unread(OTRO) == 7
    db.recompute_unread_counts()
    assert db.recompute_unread_counts() == 0


def test_manual_unread_survives_recompute_until_chat_is_read():
    _in("m1")
    db.mark_chat_as_read(CHAT)
    assert db.mark_chat_unread_elsewhere(CHAT)
    db.recompute_unread_counts()
    assert _unread() == 1
    db.mark_chat_as_read(CHAT)
    db.recompute_unread_counts()
    assert _unread() == 0


def test_read_elsewhere_lowers_badge():
    _in("m1"); _in("m2", ts=101); _in("a1", OTRO)
    assert db.get_total_unread_count() == 3
    db.mark_messages_read_elsewhere(["m1", "m2"])
    assert db.get_total_unread_count() == 1
    db.mark_chat_read_elsewhere(OTRO)
    assert db.get_total_unread_count() == 0


def test_muted_chats_do_not_count_in_app_badge():
    _in("m1"); _in("a1", OTRO); _in("a2", OTRO, ts=101)
    db.set_chat_muted(OTRO, -1)
    assert db.get_total_unread_count() == 3
    assert db.get_total_unread_count(include_muted=False) == 1
    db.set_chat_muted(OTRO, 1)  # silencio vencido: vuelve a contar
    assert db.get_total_unread_count(include_muted=False) == 3


def test_groups_can_be_left_out_of_app_badge():
    grupo = "120363000000000000@g.us"
    _in("m1"); _in("g1", grupo); _in("g2", grupo, ts=101)
    assert db.get_total_unread_count() == 3
    assert db.get_total_unread_count(include_groups=False) == 1


def test_init_db_recomputes_on_start():
    _in("m1")
    with db.db_conn() as c:
        c.execute("UPDATE chats SET unread_count = 12 WHERE jid = ?", (CHAT,))
    db.init_db()
    assert _unread() == 1


def test_custom_name_survives_whatsapp_rename():
    _in("m1")
    assert db.save_or_update_contact(CHAT, "Seba del laburo", "111")
    db.save_sync_batch([{"jid": CHAT, "name": "Sebastián"}], [{"jid": CHAT, "name": "Sebastián"}], [],
                       rename_chats=True)
    chat = db.get_chat(CHAT)
    assert chat["custom_name"] == "Seba del laburo"
    from app.ui.chat_list import resolve_display_name
    assert resolve_display_name(chat) == "Seba del laburo"


def test_set_and_clear_custom_name_and_search():
    _in("m1")
    db.set_chat_custom_name(CHAT, "  Jefe  ")
    assert db.get_chat_custom_name(CHAT) == "Jefe"
    assert [c["jid"] for c in db.get_chats(search="Jefe")] == [CHAT]
    db.set_chat_custom_name(CHAT, "")
    assert db.get_chat_custom_name(CHAT) == ""
