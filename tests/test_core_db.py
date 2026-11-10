"""Cobertura adicional de app/db.py: previews, fusión LID/teléfono, prefijos de JID, estados tempranos, borrados."""
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
    monkeypatch.setattr(db, "_load_lid_phone_map", lambda: {})
    db._early_status.clear()
    tags._invalidate()
    db.init_db()
    yield
    db._early_status.clear()
    tags._invalidate()


def _save(i, text="hola", ts=100, jid=CHAT, from_me=False, **kw):
    return db.save_message(i, jid, jid if not from_me else "me", "Ana", from_me, text, ts, **kw)


# ── Conexión / init ──

def test_init_db_is_idempotent_and_keeps_data():
    _save("m1")
    db.init_db()
    db.init_db()
    assert db.get_message("m1")["text"] == "hola"
    with db.db_conn() as c:
        assert c.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_db_conn_rolls_back_on_error():
    with pytest.raises(RuntimeError):
        with db.db_conn() as c:
            c.execute("INSERT INTO chats (jid, name) VALUES ('x@s.whatsapp.net', 'X')")
            raise RuntimeError("falla")
    assert db.get_chat("x@s.whatsapp.net") is None


def test_init_db_removes_ghost_messages_and_chats_without_jid():
    with db.db_conn() as c:
        c.execute("INSERT INTO chats (jid, name) VALUES (?, 'Ana')", (CHAT,))
        c.execute("INSERT INTO messages (id, chat_jid, text, media_type, timestamp) VALUES ('g', ?, '  ', 'text', 1)",
                  (CHAT,))
        c.execute("INSERT INTO messages (id, chat_jid, text, media_type, timestamp) VALUES ('ok', ?, 'hola', 'text', 2)",
                  (CHAT,))
    db.init_db()
    assert db.get_message("g") is None and db.get_message("ok") is not None


# ── Chats ──

def test_get_or_create_chat_naming():
    c = db.get_or_create_chat("5491155@s.whatsapp.net", phone="5491155")
    assert c["name"] == "+5491155"
    db.delete_chat("5491155@s.whatsapp.net")
    assert db.get_or_create_chat("5491155@s.whatsapp.net")["name"] == "5491155"
    c = db.get_or_create_chat("777@g.us")
    assert c["is_group"] == 1 and c["name"] == "777"
    # un nombre real reemplaza al placeholder numérico, pero no a otro nombre real
    db.get_or_create_chat("5491155@s.whatsapp.net", name="Ana")
    assert db.get_chat("5491155@s.whatsapp.net")["name"] == "Ana"
    db.get_or_create_chat("5491155@s.whatsapp.net", name="Otra")
    assert db.get_chat("5491155@s.whatsapp.net")["name"] == "Ana"


def test_get_chat_handles_empty_jid():
    assert db.get_chat("") is None and db.get_chat(None) is None


def test_chats_sorted_by_pin_then_recency_and_paginated():
    for i, ts in enumerate((100, 300, 200)):
        _save(f"m{i}", ts=ts, jid=f"{i}00@s.whatsapp.net")
    with db.db_conn() as c:
        c.execute("UPDATE chats SET is_pinned = 1 WHERE jid = '000@s.whatsapp.net'")
    order = [x["jid"] for x in db.get_chats()]
    assert order == ["000@s.whatsapp.net", "100@s.whatsapp.net", "200@s.whatsapp.net"]
    assert [x["jid"] for x in db.get_chats(limit=1, offset=1)] == ["100@s.whatsapp.net"]


def test_search_matches_name_phone_and_preview():
    db.save_message("s1", CHAT, CHAT, "Ana", False, "pedido de pizza", 100, chat_name="Ana Lopez")
    db.save_sync_batch([{"jid": "222@s.whatsapp.net", "name": "Beto", "phone": "5492944"}], [], [])
    assert {c["jid"] for c in db.get_chats(search="Lopez")} == {CHAT}
    assert {c["jid"] for c in db.get_chats(search="pizza")} == {CHAT}
    assert {c["jid"] for c in db.get_chats(search="2944")} == {"222@s.whatsapp.net"}
    assert db.get_chats(search="   ") != []   # búsqueda en blanco = sin filtro


def test_muted_chats():
    _save("m1")
    assert not db.is_chat_muted(db.get_chat(CHAT))
    db.set_chat_muted(CHAT, int(time.time()) + 3600)
    assert db.is_chat_muted(db.get_chat(CHAT))
    db.set_chat_muted(CHAT, int(time.time()) - 1)
    assert not db.is_chat_muted(db.get_chat(CHAT))
    db.set_chat_muted(CHAT, -1)
    assert db.is_chat_muted(db.get_chat(CHAT))
    assert not db.is_chat_muted(None)


def test_contact_notes_roundtrip_and_blank_clears():
    _save("m1")
    assert db.get_contact_notes(CHAT) == ""
    db.set_contact_notes(CHAT, "  cliente VIP ")
    assert db.get_contact_notes(CHAT) == "cliente VIP"
    db.set_contact_notes(CHAT, "   ")
    assert db.get_contact_notes(CHAT) == ""
    assert db.get_contact_notes("nadie@s.whatsapp.net") == ""


# ── Prefijos de JID: un número que es prefijo de otro no debe tocar al otro chat ──

def test_contact_update_does_not_touch_chat_with_longer_number():
    db.get_or_create_chat("54911@s.whatsapp.net", name="Corto")
    db.get_or_create_chat("5491122334455@s.whatsapp.net", name="Largo")
    db.save_or_update_contact("54911@s.whatsapp.net", "Renombrado", "54911")
    assert db.get_chat("54911@s.whatsapp.net")["name"] == "Renombrado"
    assert db.get_chat("5491122334455@s.whatsapp.net")["name"] == "Largo"


def test_contact_update_still_reaches_lid_twin():
    db.get_or_create_chat("784305@lid", name="Viejo")
    db.save_or_update_contact("784305@s.whatsapp.net", "Nuevo", "784305")
    assert db.get_chat("784305@lid")["name"] == "Nuevo"


def test_avatar_update_does_not_touch_chat_with_longer_number():
    db.get_or_create_chat("54911@s.whatsapp.net", name="Corto")
    db.get_or_create_chat("5491122334455@s.whatsapp.net", name="Largo")
    db.update_chat_avatar("54911@s.whatsapp.net", "/tmp/a.jpg")
    assert db.get_chat("54911@s.whatsapp.net")["avatar_path"] == "/tmp/a.jpg"
    assert db.get_chat("5491122334455@s.whatsapp.net")["avatar_path"] is None


def test_resolve_phone_sources(monkeypatch):
    assert db.resolve_phone({"phone": "+54 9 11-22"}) == "5491122"
    assert db.resolve_phone({"jid": "5491100@s.whatsapp.net"}) == "5491100"
    assert db.resolve_phone({"jid": "1203@g.us"}) == ""
    monkeypatch.setattr(db, "_load_lid_phone_map", lambda: {"999": "5493300@s.whatsapp.net"})
    assert db.resolve_phone({"jid": "999@lid"}) == "5493300"
    monkeypatch.setattr(db, "_load_lid_phone_map", lambda: {})
    db.save_contact("888@lid", name="Eva", phone="5494400")
    assert db.resolve_phone({"jid": "888@lid"}) == "5494400"
    # el prefijo de otro número no cuenta como coincidencia
    db.save_contact("7771234@s.whatsapp.net", name="Otro", phone="7771234")
    assert db.resolve_phone({"jid": "777@lid"}) == ""


def test_find_chat_avatar_requires_existing_file(tmp_path):
    img = tmp_path / "a.jpg"
    img.write_bytes(b"x")
    db.get_or_create_chat(CHAT, name="Ana")
    assert db.find_chat_avatar(CHAT) is None
    db.update_chat_avatar(CHAT, str(img))
    assert db.find_chat_avatar(CHAT) == str(img)
    assert db.find_chat_avatar("111@lid") == str(img)       # mismo número, otro sufijo
    img.unlink()
    assert db.find_chat_avatar(CHAT) is None
    assert db.find_chat_avatar("") is None


# ── Mensajes / previews ──

@pytest.mark.parametrize("mtype,text,expected", [
    ("text", "hola", "hola"),
    ("image", "", "\U0001F4F7 Foto"),
    ("image", "mira", "\U0001F4F7 Foto: mira"),
    ("video", "", "\U0001F3A5 Video"),
    ("audio", "x", "\U0001F3B5 Nota de voz"),
    ("sticker", "", "\U0001F3A8 Sticker"),
    ("revoked", "", "\U0001F6AB Mensaje eliminado"),
    ("location", "", "\U0001F4CD Ubicación"),
    ("contact", "Juan", "\U0001F464 Juan"),
    ("document", "a.pdf", "\U0001F4C4 Archivo: a.pdf"),
])
def test_preview_for(mtype, text, expected):
    assert db._preview_for(mtype, text) == expected


def test_empty_text_message_is_not_stored():
    assert _save("e1", text="   ") is False
    assert db.get_message("e1") is None
    assert db.get_chat(CHAT) is None


def test_media_message_without_text_is_stored():
    assert _save("i1", text="", media_type="image", media_path="/tmp/i.jpg") is True
    assert db.get_chat(CHAT)["last_message_text"].endswith("Foto")


def test_old_message_arriving_late_does_not_replace_preview():
    _save("new", text="reciente", ts=500)
    _save("old", text="viejo", ts=100)
    chat = db.get_chat(CHAT)
    assert chat["last_message_text"] == "reciente" and chat["last_message_time"] == 500


def test_get_messages_is_chronological_and_paginates_by_timestamp():
    for i in range(5):
        _save(f"m{i}", text=f"t{i}", ts=100 + i)
    assert [m["id"] for m in db.get_messages(CHAT, limit=3)] == ["m2", "m3", "m4"]
    assert [m["id"] for m in db.get_messages(CHAT, limit=10, before_timestamp=102)] == ["m0", "m1"]
    assert db.count_messages_since(CHAT, 103) == 2


def test_chat_media_lists_files_and_links():
    _save("f1", text="", media_type="image", media_path="/tmp/a.jpg", ts=100)
    _save("l1", text="mira https://ejemplo.com/x. y http://otro.org)", ts=200)
    out = db.get_chat_media(CHAT)
    assert [f["id"] for f in out["files"]] == ["f1"]
    assert [l["url"] for l in out["links"]] == ["https://ejemplo.com/x", "http://otro.org"]


def test_edit_marks_edited_and_clears_summary():
    _save("m1")
    db.set_message_summary("m1", "resumen")
    assert db.apply_message_edit("m1", " nuevo ") is True
    m = db.get_message("m1")
    assert m["text"] == "nuevo" and m["edited"] == 1 and m["summary"] is None
    assert db.get_chat(CHAT)["last_message_text"] == "nuevo"
    assert db.apply_message_edit("m1", "  ") is False
    assert db.apply_message_edit("nope", "x") is False


def test_sync_batch_revoke_and_edit_only_apply_to_existing():
    _save("m1", text="original")
    db.save_sync_batch([], [], [
        {"message_id": "m1", "chat_jid": CHAT, "edit_of": "m1", "text": "editado", "timestamp": 1},
        {"message_id": "ghost", "chat_jid": CHAT, "revoke_of": "ghost", "timestamp": 1},
    ])
    assert db.get_message("m1")["text"] == "editado"
    assert db.get_message("ghost") is None
    db.save_sync_batch([], [], [{"message_id": "m1", "chat_jid": CHAT, "revoke_of": "m1", "timestamp": 1}])
    m = db.get_message("m1")
    assert m["media_type"] == "revoked" and m["text"] == ""


def test_sync_batch_skips_entries_without_ids_and_empty_messages():
    db.save_sync_batch([{"name": "sin jid"}, {"jid": CHAT, "name": "Ana"}], [{"name": "x"}],
                       [{"chat_jid": CHAT, "text": "sin id"}, {"message_id": "v", "chat_jid": CHAT, "text": " "}])
    assert [c["jid"] for c in db.get_chats()] == [CHAT]
    assert db.get_messages(CHAT) == []


def test_delete_messages_handles_empty_and_updates_empty_chat_preview():
    db.delete_messages([])
    db.delete_messages([None, ""])
    _save("m1")
    db.delete_messages(["m1", "inexistente"])
    assert db.get_chat(CHAT)["last_message_text"] == "Sin mensajes"


def test_clear_and_delete_chat_clean_everything():
    _save("m1")
    db.set_reaction("m1", "me", "x")
    tid = tags.create_tag({"name": "T"})
    tags.assign_tag(CHAT, tid)
    db.clear_chat_messages(CHAT)
    assert db.get_messages(CHAT) == [] and db.get_chat(CHAT)["last_message_text"] == "Sin mensajes"
    _save("m2")
    db.delete_chat(CHAT)
    assert db.get_chat(CHAT) is None and db.get_message("m2") is None
    assert tags.count_chats_with_tag(tid) == 0


def test_revoked_message_clears_reactions_and_pin():
    _save("m1")
    db.set_reaction("m1", "me", "x")
    db.set_message_pinned("m1", True)
    assert db.revoke_message_local("m1") is True
    assert db.get_reactions(["m1"]) == {} and db.get_pinned_messages(CHAT) == []
    assert db.revoke_message_local("nope") is False
    assert db.message_exists("m1") and not db.message_exists("") and not db.message_exists("nope")


def test_pin_unknown_message_returns_false():
    assert db.set_message_pinned("nope", True) is False


def test_reactions_ignore_empty_ids_and_chunk_large_queries():
    db.set_reaction("", "me", "x")
    db.set_reaction("m1", "", "x")
    assert db.get_reactions([]) == {}
    for i in range(3):
        db.set_reaction(f"m{i}", "me", "❤", i + 1)
    ids = [f"m{i}" for i in range(1200)]
    assert set(db.get_reactions(ids)) == {"m0", "m1", "m2"}


# ── Leídos / estados tempranos ──

def test_mark_read_elsewhere_with_many_ids_is_chunked():
    ids = [f"x{i}" for i in range(1100)]
    assert db.mark_messages_read_elsewhere(ids) == []
    assert db.mark_messages_read_elsewhere([None, ""]) == []
    assert len(db._early_status) == 1100


def test_early_status_store_is_bounded_and_keeps_best_rank(monkeypatch):
    monkeypatch.setattr(db, "_EARLY_STATUS_MAX", 3)
    for i in range(6):
        db._stash_early_status(f"id{i}", "delivered")
    assert len(db._early_status) == 3 and "id5" in db._early_status and "id0" not in db._early_status
    db._stash_early_status("id5", "read")
    db._stash_early_status("id5", "delivered")
    assert db._early_status["id5"] == "read"


def test_update_status_rejects_unknown_status():
    _save("own", from_me=True, status="sent")
    assert db.update_message_status("own", "borrado") is False
    assert db.get_message("own")["status"] == "sent"


def test_mark_chat_read_elsewhere_unknown_chat():
    assert db.mark_chat_read_elsewhere("nadie@s.whatsapp.net") is False


# ── Fusión LID / teléfono ──

def test_merge_by_lid_map_moves_messages_unread_and_tags(monkeypatch):
    monkeypatch.setattr(db, "_load_lid_phone_map", lambda: {"555": "5491100"})
    _save("p1", ts=100, jid="5491100@s.whatsapp.net")
    _save("l1", text="por lid", ts=200, jid="555@lid")
    tid = tags.create_tag({"name": "T"})
    # el tag estaba en el chat '@lid' (el mensaje anterior ya intentó fusionar, así que lo ponemos de nuevo)
    db.get_or_create_chat("555@lid", name="Eva")
    tags.assign_tag("555@lid", tid)
    assert db.merge_lid_duplicates() == 1
    assert db.get_chat("555@lid") is None
    assert len(db.get_messages("5491100@s.whatsapp.net")) == 2
    assert [t["id"] for t in tags.tags_for_chat("5491100@s.whatsapp.net")] == [tid]
    assert db.get_chat("5491100@s.whatsapp.net")["last_message_time"] == 200


def test_merge_ignores_placeholder_names():
    db.save_message("a", "a11@s.whatsapp.net", "a", "Luis", False, "x", 1, chat_name="Contacto (1234)")
    db.get_or_create_chat("999@lid", name="Contacto (1234)")
    assert db.merge_lid_duplicates() == 0


def test_is_real_name():
    assert db._is_real_name("Ana")
    for bad in ("", "12345", "+5491", "Contacto (1)", "Usuario (2)"):
        assert not db._is_real_name(bad)


# ── Análisis interno ──

def test_self_analysis_only_includes_chats_where_i_wrote():
    _save("a", text="hola", ts=100)
    _save("b", text="te respondo", ts=101, from_me=True)
    _save("c", text="solo recibido", ts=100, jid="222@s.whatsapp.net")
    out = db.get_chats_for_self_analysis()
    assert [c["jid"] for c in out] == [CHAT]
    assert [m["text"] for m in out[0]["messages"]] == ["hola", "te respondo"]
    mine = db.get_chats_for_self_analysis(include_others=False)
    assert [m["text"] for m in mine[0]["messages"]] == ["te respondo"]
    assert len(db.get_chats_for_self_analysis(per_chat_limit=1)[0]["messages"]) == 1


def test_contacts_picker_filters_placeholders_and_searches():
    db.save_contact("1@s.whatsapp.net", name="Ana", phone="1")
    db.save_contact("2@s.whatsapp.net", name="Usuario (2)", phone="2")
    db.save_contact("3@s.whatsapp.net", name="Beto", phone="3")
    assert [c["name"] for c in db.get_all_contacts()] == ["Ana", "Beto"]
    assert [c["name"] for c in db.get_all_contacts("bet")] == ["Beto"]
