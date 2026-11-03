import os, sys, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest
import app.db as db
from app import tags


@pytest.fixture(autouse=True)
def tmpdb(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "t.db"))
    tags._invalidate()
    db.init_db()
    yield
    tags._invalidate()


def _chat(jid="111@s.whatsapp.net", last=None):
    db.get_or_create_chat(jid, "Ana")
    if last is not None:
        with db.db_conn() as c:
            c.execute("UPDATE chats SET last_message_time = ? WHERE jid = ?", (last, jid))
    return jid


def test_defaults_installed_once():
    names = [t["name"] for t in tags.list_tags()]
    assert names == [t["name"] for t in tags.DEFAULT_TAGS]
    for t in tags.list_tags():
        tags.delete_tag(t["id"])
    db.init_db()
    assert tags.list_tags() == []


def test_upgrade_resets_all_tags():
    """Base de una versión anterior: se borran todos los tags y asignaciones, quedan los predeterminados."""
    with db.db_conn() as c:
        c.execute("UPDATE tag_meta SET value = '2' WHERE key = 'defaults_version'")
        mine = tags._insert(c, {"name": "cliente romphuevos", "shape": "hexagon", "color": "#e5534b"}, 99)
        c.execute("INSERT INTO chat_tags VALUES ('1@s.whatsapp.net', ?, 0)", (mine,))
    tags._invalidate()
    db.init_db()
    assert [t["name"] for t in tags.list_tags()] == [t["name"] for t in tags.DEFAULT_TAGS]
    with db.db_conn() as c:
        assert c.execute("SELECT COUNT(*) FROM chat_tags").fetchone()[0] == 0


def test_business_days():
    from datetime import datetime
    fri = int(datetime(2026, 10, 30, 15, 0).timestamp())       # viernes
    assert datetime.fromtimestamp(tags.add_days(fri, 1, True)) == datetime(2026, 11, 2, 15, 0)    # lunes
    assert datetime.fromtimestamp(tags.add_days(fri, 10, True)) == datetime(2026, 11, 13, 15, 0)
    sat = int(datetime(2026, 10, 31, 9, 0).timestamp())
    assert datetime.fromtimestamp(tags.add_days(sat, 1, True)) == datetime(2026, 11, 2, 9, 0)


def test_business_day_tag_expires():
    jid = _chat()
    tid = tags.create_tag({"name": "Hábil", "expire_mode": "days", "expire_value": 10, "expire_business": 1})
    tags.assign_tag(jid, tid)
    now = time.time()
    assert tags.purge_expired(now + 12 * 86400) == 0      # 10 hábiles son al menos 13 días corridos
    assert tags.purge_expired(now + 15 * 86400) == 1


def test_urgent_default_expires_in_24_hours():
    jid = _chat()
    urgente = [t for t in tags.list_tags() if t["name"] == "Urgente"][0]
    tags.assign_tag(jid, urgente["id"])
    now = time.time()
    assert tags.purge_expired(now + 23 * 3600) == 0
    assert tags.purge_expired(now + 25 * 3600) == 1


def test_row_effects_priority():
    jid = _chat()
    a = tags.create_tag({"name": "A", "fx_row": "#ff0000"})
    b = tags.create_tag({"name": "B", "fx_row": "#00ff00", "fx_name": "#ffff00"})
    tags.assign_tag(jid, b)
    tags.assign_tag(jid, a)
    fx = tags.chat_effects(tags.tags_for_chat(jid))
    assert fx["row"] == "#ff0000" and fx["name"] == "#ffff00"


def test_assign_and_remove():
    jid = _chat()
    tid = tags.create_tag({"name": "X"})
    tags.assign_tag(jid, tid)
    assert [t["id"] for t in tags.tags_for_chat(jid)] == [tid]
    tags.remove_tag(jid, tid)
    assert tags.tags_for_chat(jid) == []


def test_priority_follows_order_and_effects():
    jid = _chat()
    for t in tags.list_tags():
        tags.delete_tag(t["id"])
    a = tags.create_tag({"name": "A", "fx_bg": "#ff0000"})
    b = tags.create_tag({"name": "B", "fx_bg": "#00ff00", "fx_text": "#ffff00"})
    tags.assign_tag(jid, b)
    tags.assign_tag(jid, a)
    fx = tags.chat_effects(tags.tags_for_chat(jid))
    assert fx["bg"] == "#ff0000" and fx["text"] == "#ffff00"
    tags.reorder_tags([b, a])
    fx = tags.chat_effects(tags.tags_for_chat(jid))
    assert fx["bg"] == "#00ff00"


def test_expire_hours():
    jid = _chat()
    tid = tags.create_tag({"expire_mode": "hours", "expire_value": 24})
    tags.assign_tag(jid, tid)
    assert tags.purge_expired(time.time() + 23 * 3600) == 0
    assert tags.purge_expired(time.time() + 25 * 3600) == 1
    assert tags.tags_for_chat(jid) == []


def test_expire_inactive_counts_from_last_message():
    now = int(time.time())
    jid = _chat(last=now)
    tid = tags.create_tag({"expire_mode": "inactive", "expire_value": 30})
    tags.assign_tag(jid, tid)
    assert tags.purge_expired(now + 29 * 86400) == 0
    # Un mensaje nuevo estira el plazo
    with db.db_conn() as c:
        c.execute("UPDATE chats SET last_message_time = ? WHERE jid = ?", (now + 20 * 86400, jid))
    assert tags.purge_expired(now + 31 * 86400) == 0
    assert tags.purge_expired(now + 51 * 86400) == 1


def test_delete_tag_and_chat_cleanup():
    jid = _chat()
    tid = tags.create_tag()
    tags.assign_tag(jid, tid)
    db.delete_chat(jid)
    with db.db_conn() as c:
        assert c.execute("SELECT COUNT(*) FROM chat_tags").fetchone()[0] == 0
    jid = _chat()
    tags.assign_tag(jid, tid)
    tags.delete_tag(tid)
    assert tags.tags_for_chat(jid) == []
