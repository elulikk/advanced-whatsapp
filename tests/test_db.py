import os, sys, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest
import app.db as db


@pytest.fixture(autouse=True)
def tmpdb(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "t.db"))
    db.init_db()


def _msg(i="m1", from_me=False, **kw):
    return db.save_message(i, "111@s.whatsapp.net", "111@s.whatsapp.net", "Ana",
                           from_me, "hola", int(time.time()), **kw)


def test_duplicate_message_does_not_inflate_unread():
    _msg(); _msg()
    assert db.get_total_unread_count() == 1


def test_resend_keeps_status():
    _msg(status="read"); _msg()
    assert db.get_messages("111@s.whatsapp.net")[0]["status"] == "read"


def test_save_contact_creates_chat_without_lock():
    t = time.time()
    assert db.save_or_update_contact("222@s.whatsapp.net", "Beto", "222")
    assert time.time() - t < 5
    assert any(c["jid"] == "222@s.whatsapp.net" for c in db.get_chats())


def test_legacy_migration_runs_once():
    with db.db_conn() as c:
        assert c.execute("PRAGMA user_version").fetchone()[0] == 1


def test_group_chat_not_named_after_sender():
    db.save_message("g1", "999@g.us", "5@s.whatsapp.net", "Carlos", False, "hola", int(time.time()),
                    chat_name="Familia")
    db.save_message("g2", "999@g.us", "6@s.whatsapp.net", "Lucia", False, "chau", int(time.time()))
    chat = [c for c in db.get_chats(include_hidden=True) if c["jid"] == "999@g.us"][0]
    assert chat["name"] == "Familia"


def test_group_without_name_never_takes_sender_name():
    db.save_message("g3", "888@g.us", "5@s.whatsapp.net", "Carlos", False, "hola", int(time.time()))
    chat = [c for c in db.get_chats(include_hidden=True) if c["jid"] == "888@g.us"][0]
    assert chat["name"] != "Carlos"


def _chat_jids(**kw):
    return {c["jid"] for c in db.get_chats(**kw)}


def test_groups_stay_out_of_all_and_appear_in_their_tab():
    _msg()
    db.save_message("g4", "777@g.us", "5@s.whatsapp.net", "Carlos", False, "hola", int(time.time()), chat_name="Familia")
    assert _chat_jids() == {"111@s.whatsapp.net"}
    assert _chat_jids(groups_only=True) == {"777@g.us"}


def test_unread_and_search_include_groups():
    _msg()
    db.save_message("g5", "777@g.us", "5@s.whatsapp.net", "Carlos", False, "hola", int(time.time()), chat_name="Familia")
    assert _chat_jids(filter_unread=True) == {"111@s.whatsapp.net", "777@g.us"}
    assert _chat_jids(search="Familia") == {"777@g.us"}
    assert db.get_total_unread_count() == 2


def test_channels_stay_hidden():
    db.save_message("n1", "123@newsletter", "123@newsletter", "Canal", False, "hola", int(time.time()))
    assert "123@newsletter" not in _chat_jids(filter_unread=True)


def test_placeholder_does_not_replace_group_name():
    db.save_sync_batch([{"jid": "777@g.us", "name": "Familia", "phone": None, "last_message_time": 0, "is_group": 1}], [], [])
    db.save_sync_batch([{"jid": "777@g.us", "name": "Grupo", "phone": None, "last_message_time": 0, "is_group": 1}], [], [])
    assert [c["name"] for c in db.get_chats(groups_only=True)] == ["Familia"]


def test_sync_batch_without_unread_keeps_counter():
    _msg(); _msg("m2")
    db.save_sync_batch([{"jid": "111@s.whatsapp.net", "name": "Ana"}], [], [])
    assert db.get_total_unread_count() == 2


def test_sync_batch_keeps_media_raw_for_later_download():
    db.save_sync_batch(
        [{"jid": "111@s.whatsapp.net", "name": "Ana"}], [],
        [{"message_id": "a1", "chat_jid": "111@s.whatsapp.net", "text": "", "media_type": "audio",
          "media_raw": '{"key":{"id":"a1"}}', "timestamp": 1000}]
    )
    m = db.get_message("a1")
    assert m["media_type"] == "audio"
    assert m["media_raw"] == '{"key":{"id":"a1"}}'
    assert m["media_path"] is None


def test_set_media_path_and_transcript():
    db.save_sync_batch([{"jid": "111@s.whatsapp.net", "name": "Ana"}], [],
                       [{"message_id": "a2", "chat_jid": "111@s.whatsapp.net", "text": "",
                         "media_type": "audio", "media_raw": "{}", "timestamp": 1000}])
    db.set_message_media_path("a2", "/tmp/a2.ogg")
    db.set_message_transcript("a2", "hola mundo")
    m = db.get_message("a2")
    assert m["media_path"] == "/tmp/a2.ogg"
    assert m["transcript"] == "hola mundo"


def test_resync_does_not_erase_downloaded_media_path():
    batch_msg = {"message_id": "a3", "chat_jid": "111@s.whatsapp.net", "text": "",
                 "media_type": "image", "media_raw": "{}", "timestamp": 1000}
    db.save_sync_batch([{"jid": "111@s.whatsapp.net", "name": "Ana"}], [], [batch_msg])
    db.set_message_media_path("a3", "/tmp/a3.jpg")
    db.save_sync_batch([{"jid": "111@s.whatsapp.net", "name": "Ana"}], [], [batch_msg])
    assert db.get_message("a3")["media_path"] == "/tmp/a3.jpg"


def test_merge_lid_chat_into_phone_chat_by_name():
    db.save_message("p1", "5491155640505@s.whatsapp.net", "5491155640505@s.whatsapp.net", "Daniel", False,
                    "hola", 1000, chat_name="Daniel F")
    db.save_sync_batch([{"jid": "156134996467780@lid", "name": "Daniel F"}], [], [])
    jids = [c["jid"] for c in db.get_chats()]
    assert "156134996467780@lid" not in jids
    assert len(db.get_messages("5491155640505@s.whatsapp.net")) == 1


def test_merge_keeps_ambiguous_names_separate():
    for n in ("a", "b"):
        db.save_message(n, f"{n}11@s.whatsapp.net", f"{n}11@s.whatsapp.net", "Luis", False, "x", 1000, chat_name="Luis P")
    db.save_sync_batch([{"jid": "999@lid", "name": "Luis P"}], [], [])
    assert any(c["jid"] == "999@lid" for c in db.get_chats())


def test_message_status_only_moves_forward():
    db.save_message("own1", "111@s.whatsapp.net", "me", "Yo", True, "hola", int(time.time()), status="sent")
    assert db.update_message_status("own1", "delivered")
    assert db.update_message_status("own1", "read")
    assert not db.update_message_status("own1", "delivered")  # no retrocede
    assert db.get_message("own1")["status"] == "read"


def test_message_status_ignores_incoming_messages():
    _msg("in1")
    assert not db.update_message_status("in1", "read")


def test_status_arriving_before_save_is_applied():
    assert not db.update_message_status("early1", "delivered")   # aún no existe el mensaje
    assert not db.update_message_status("early1", "read")
    db.save_message("early1", "111@s.whatsapp.net", "me", "Yo", True, "hola", int(time.time()), status="sent")
    assert db.get_message("early1")["status"] == "read"


def test_sync_batch_updates_own_status_but_never_lowers_it():
    def batch(status):
        db.save_sync_batch([{"jid": "111@s.whatsapp.net", "name": "Ana"}], [], [{
            "message_id": "h1", "chat_jid": "111@s.whatsapp.net", "sender_jid": "me", "from_me": True,
            "text": "hola", "media_type": "text", "timestamp": 1000, "status": status}])
    batch("sent")
    batch("read")
    assert db.get_message("h1")["status"] == "read"
    batch("sent")
    assert db.get_message("h1")["status"] == "read"


def test_mark_chat_as_read_returns_only_unread_incoming():
    _msg("in1"); _msg("in2", status="read")
    db.save_message("own1", "111@s.whatsapp.net", "me", "Yo", True, "x", int(time.time()))
    assert db.mark_chat_as_read("111@s.whatsapp.net") == [("in1", "111@s.whatsapp.net")]
    assert db.mark_chat_as_read("111@s.whatsapp.net") == []


def test_reactions_set_replace_remove():
    _msg()
    db.set_reaction("m1", "me", "👍", 1)
    db.set_reaction("m1", "111@s.whatsapp.net", "❤️", 2)
    assert db.get_reactions(["m1"])["m1"] == [("👍", "me"), ("❤️", "111@s.whatsapp.net")]
    db.set_reaction("m1", "me", "😂", 3)
    assert ("😂", "me") in db.get_reactions(["m1"])["m1"]
    assert len(db.get_reactions(["m1"])["m1"]) == 2
    db.set_reaction("m1", "me", "", 4)
    assert db.get_reactions(["m1"])["m1"] == [("❤️", "111@s.whatsapp.net")]


def test_clear_chat_removes_reactions():
    _msg()
    db.set_reaction("m1", "me", "👍")
    db.clear_chat_messages("111@s.whatsapp.net")
    assert db.get_reactions(["m1"]) == {}


def test_pin_keeps_max_three_and_unpins_oldest():
    for i in range(4):
        db.save_message(f"p{i}", "111@s.whatsapp.net", "111@s.whatsapp.net", "Ana", False, f"t{i}", 100 + i)
        db.set_message_pinned(f"p{i}", True)
        time.sleep(0.005)
    assert [m["id"] for m in db.get_pinned_messages("111@s.whatsapp.net")] == ["p3", "p2", "p1"]
    db.set_message_pinned("p2", False)
    assert [m["id"] for m in db.get_pinned_messages("111@s.whatsapp.net")] == ["p3", "p1"]


def test_delete_for_me_removes_message_and_fixes_preview():
    db.save_message("a", "111@s.whatsapp.net", "111@s.whatsapp.net", "Ana", False, "viejo", 100)
    db.save_message("b", "111@s.whatsapp.net", "111@s.whatsapp.net", "Ana", False, "nuevo", 200)
    db.set_reaction("b", "me", "👍")
    db.delete_messages(["b"])
    assert db.get_message("b") is None and db.get_reactions(["b"]) == {}
    assert db.get_chats()[0]["last_message_text"] == "viejo"


def test_revoke_keeps_notice_without_content():
    db.save_message("a", "111@s.whatsapp.net", "me", "Yo", True, "secreto", 100)
    db.set_message_pinned("a", True)
    db.revoke_message_local("a")
    m = db.get_message("a")
    assert m["media_type"] == "revoked" and m["text"] == "" and m["pinned_at"] is None


def test_agenda_rename_updates_existing_chat_only():
    # Dos números distintos con el mismo nombre: el cambio de agenda ("Martin 1") llega al chat existente
    db.save_sync_batch([{"jid": "201@s.whatsapp.net", "name": "Martin"},
                        {"jid": "809@s.whatsapp.net", "name": "Martin"}], [], [])
    db.save_sync_batch([], [{"name": "Martin 1", "phone": "201", "lid": "144"},
                            {"name": "Ana", "phone": "333"}], [], rename_chats=True)
    names = {c["jid"]: c["name"] for c in db.get_chats()}
    assert names["201@s.whatsapp.net"] == "Martin 1"
    assert names["809@s.whatsapp.net"] == "Martin"
    assert "333@s.whatsapp.net" not in names   # un renombre nunca crea chats


def test_full_contact_sync_does_not_rename_chats():
    db.save_sync_batch([{"jid": "201@s.whatsapp.net", "name": "Martin 1"}], [], [])
    db.save_sync_batch([], [{"name": "Martin", "phone": "201"}], [])
    assert db.get_chats()[0]["name"] == "Martin 1"


def test_rename_ignores_placeholder_names():
    db.save_sync_batch([{"jid": "201@s.whatsapp.net", "name": "Martin 1"}], [], [])
    db.save_sync_batch([], [{"name": "+201", "phone": "201"}], [], rename_chats=True)
    assert db.get_chats()[0]["name"] == "Martin 1"


def test_summary_kept_on_resync_and_cleared_when_text_changes():
    _msg()
    db.set_message_summary("m1", "RESUMEN: saluda")
    _msg()
    assert db.get_message("m1")["summary"] == "RESUMEN: saluda"
    db.save_message("m1", "111@s.whatsapp.net", "111@s.whatsapp.net", "Ana", False, "hola, editado", int(time.time()))
    assert db.get_message("m1")["summary"] is None


# ── Leídos en el celular ──

CHAT = "111@s.whatsapp.net"


def _unread(jid=CHAT):
    return db.get_chat(jid)["unread_count"]


def _in(i, ts, **kw):
    return db.save_message(i, CHAT, CHAT, "Ana", False, "hola", ts, **kw)


def test_already_read_message_does_not_count():
    _in("a1", 100, already_read=True)
    assert _unread() == 0
    assert db.get_messages(CHAT)[0]["status"] == "read"


def test_read_on_phone_receipts_clear_counter():
    _in("a1", 100); _in("a2", 101); _in("a3", 102)
    assert _unread() == 3
    assert db.mark_messages_read_elsewhere(["a1", "a2", "a3"]) == [CHAT]
    assert _unread() == 0


def test_read_on_phone_latest_message_reads_whole_chat():
    # El contador vino del historial (5) pero acá solo hay 1 mensaje: leer el último deja el chat en 0
    db.save_sync_batch([{"jid": CHAT, "unread_count": 5}], [], [])
    _in("a1", 100)
    db.mark_messages_read_elsewhere(["a1"])
    assert _unread() == 0


def test_read_on_phone_partial_keeps_newer_unread():
    _in("a1", 100); _in("a2", 101)
    db.mark_messages_read_elsewhere(["a1"])
    assert _unread() == 1


def test_receipt_before_message_is_applied_on_arrival():
    assert db.mark_messages_read_elsewhere(["early1"]) == []
    _in("early1", 100)
    assert _unread() == 0


def test_chat_read_and_unread_on_phone():
    _in("a1", 100); _in("a2", 101)
    assert db.mark_chat_read_elsewhere(CHAT)
    assert _unread() == 0
    assert not db.mark_chat_read_elsewhere(CHAT)   # ya estaba: nada que recargar
    assert db.mark_chat_unread_elsewhere(CHAT)
    assert _unread() == 1


def test_mark_all_chats_as_read():
    _in("a1", 100)
    db.save_message("b1", "222@s.whatsapp.net", "222@s.whatsapp.net", "Beto", False, "hey", 100)
    result = db.mark_all_chats_as_read()
    assert set(result) == {CHAT, "222@s.whatsapp.net"}
    assert result[CHAT] == ["a1"]
    assert db.get_total_unread_count() == 0
