import os, sys, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest
import app.db as db
from app import chat_lock, organization as org

A = "111@s.whatsapp.net"
B = "222@s.whatsapp.net"
G = "333@g.us"
NOW = int(time.time())


@pytest.fixture(autouse=True)
def tmpdb(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "t.db"))
    chat_lock._invalidate()
    org.reset_cache()
    db.init_db()
    yield
    chat_lock._invalidate()
    org.reset_cache()


def add_chat(jid, ts=NOW, text="hola", from_me=False, mid=None):
    db.save_message(mid or f"m-{jid}-{ts}", jid, jid, "X", from_me, text, ts, chat_name="Chat " + jid[:3])


def jids(**kw):
    return {c["jid"] for c in db.get_chats(**kw)}


# ── Carpetas inteligentes ──

def folder_jids(rules):
    return jids(extra_where=org.rules_where(rules))


def test_empty_rules_match_nothing():
    add_chat(A)
    assert org.rules_are_empty({})
    assert folder_jids({}) == set()


def test_rules_kind_unread_muted():
    add_chat(A)
    add_chat(B, text="otro")
    add_chat(G)
    db.mark_chat_as_read(B)
    db.set_chat_muted(A, -1)
    assert folder_jids({"kind": "group"}) == {G}
    assert folder_jids({"kind": "individual"}) == {A, B}
    assert folder_jids({"unread": True}) == {A, G}
    assert folder_jids({"unread": True, "kind": "individual"}) == {A}
    assert folder_jids({"muted": "only"}) == {A}
    assert folder_jids({"muted": "none"}) == {B, G}
    # un silencio vencido no cuenta
    db.set_chat_muted(B, int(time.time()) - 10)
    assert B in folder_jids({"muted": "none"})


def test_rules_tags_and_picked_contacts():
    from app import tags
    add_chat(A); add_chat(B); add_chat(G)
    tid = tags.list_tags()[0]["id"]
    tags.assign_tag(A, tid)
    assert folder_jids({"tags": [tid]}) == {A}
    # tag Y no leído: A tiene el tag pero ya se leyó
    db.mark_chat_as_read(A)
    assert folder_jids({"tags": [tid], "unread": True}) == set()
    # los contactos elegidos suman aunque no cumplan el resto de las reglas
    assert folder_jids({"tags": [tid], "unread": True, "jids": [B]}) == {B}
    assert folder_jids({"jids": [B, G]}) == {B, G}


def test_folder_crud_and_order():
    f1 = org.create_folder("Trabajo", {"unread": True})
    f2 = org.create_folder("  ", {"kind": "group"})
    assert [f["name"] for f in org.list_folders()] == ["Trabajo", "Carpeta"]
    org.reorder_folders([f2, f1])
    assert [f["id"] for f in org.list_folders()] == [f2, f1]
    org.update_folder(f1, name="Laburo", rules={"kind": "individual", "tags": ["3", 3, "x"]})
    f = org.get_folder(f1)
    assert f["name"] == "Laburo" and f["rules"]["tags"] == [3] and f["rules"]["kind"] == "individual"
    org.delete_folder(f2)
    assert [x["id"] for x in org.list_folders()] == [f1]
    assert "no leídos" in org.describe_rules({"unread": True})


def test_folder_skips_archived_chats():
    add_chat(A); add_chat(B)
    org.set_archived(A, True)
    assert folder_jids({"kind": "individual"}) == {B}
    assert jids(archived="only") == {A}


# ── Archivado automático ──

def test_auto_archive_off_by_default_and_by_days():
    old = NOW - 40 * 86400
    add_chat(A, ts=old); add_chat(B, ts=NOW - 5 * 86400); add_chat(G, ts=old)
    assert org.auto_archive(0) == 0                     # apagado
    with db.db_conn() as c:
        c.execute("UPDATE chats SET is_pinned = 1 WHERE jid = ?", (G,))
    assert org.auto_archive(30, now=NOW) == 1           # solo A: B es reciente, G está fijado
    assert jids() == {B}
    assert jids(archived="only") == {A}
    assert A in jids(search="Chat 111")                 # la búsqueda sí encuentra archivados
    assert org.archived_count() == 1
    assert org.auto_archive(30, now=NOW) == 0           # idempotente


def test_message_unarchives_unless_muted():
    old = NOW - 40 * 86400
    add_chat(A, ts=old); add_chat(B, ts=old)
    org.auto_archive(30, now=NOW)
    db.set_chat_muted(B, -1)
    add_chat(A, ts=NOW, text="volví", mid="nuevo-a")
    add_chat(B, ts=NOW, text="volví", mid="nuevo-b")
    assert jids() == {A}
    assert jids(archived="only") == {B}                 # silenciado: sigue archivado


def test_old_history_does_not_unarchive():
    add_chat(A, ts=NOW - 40 * 86400)
    org.auto_archive(30, now=NOW)
    add_chat(A, ts=NOW - 39 * 86400, text="historial", mid="viejo")
    assert jids(archived="only") == {A}


# ── Chats ocultos ──

def test_hidden_requires_lock_and_secret():
    add_chat(A)
    chat_lock.set_password("1234")
    assert not chat_lock.set_hidden(A, True)            # sin bloquear
    chat_lock.lock_chat(A)
    assert not chat_lock.set_hidden(A, True)            # sin código secreto
    assert not chat_lock.set_secret_code("ab")          # muy corto
    assert chat_lock.set_secret_code("abrete")
    assert chat_lock.set_hidden(A, True)
    assert chat_lock.is_hidden(A) and chat_lock.hidden_now(A)


def test_hidden_not_listed_nor_searched_until_secret():
    add_chat(A); add_chat(B)
    chat_lock.set_password("1234")
    chat_lock.lock_chat(A)
    chat_lock.set_secret_code("abrete")
    chat_lock.set_hidden(A, True)
    assert jids() == {B}
    assert jids(search="Chat 111") == set()
    assert jids(filter_unread=True) == {B}
    assert db.get_total_unread_count() == 1             # el oculto no suma
    assert chat_lock.try_secret("nada") is None
    assert chat_lock.try_secret("abrete") == "revealed"
    assert chat_lock.is_revealed()
    assert jids(show_secret=True) == {A, B}
    assert chat_lock.try_secret("abrete") == "concealed"    # escribirlo de nuevo los esconde
    assert jids() == {B}


def test_lock_all_conceals_and_persistence():
    add_chat(A)
    chat_lock.set_password("1234"); chat_lock.lock_chat(A)
    chat_lock.set_secret_code("abrete"); chat_lock.set_hidden(A, True)
    chat_lock.reveal()
    chat_lock.lock_all()
    assert not chat_lock.is_revealed()
    chat_lock._invalidate()                              # reinicio: se relee de disco
    assert chat_lock.is_hidden(A) and chat_lock.has_secret_code() and chat_lock.verify_secret_code("abrete")
    with db.db_conn() as c:
        stored = " ".join(v for _, v in c.execute("SELECT * FROM lock_meta").fetchall())
    assert "abrete" not in stored.split()


def test_unlock_or_remove_clears_hidden():
    add_chat(A); add_chat(B)
    chat_lock.set_password("1234")
    chat_lock.set_secret_code("abrete")
    for j in (A, B):
        chat_lock.lock_chat(j); chat_lock.set_hidden(j, True)
    chat_lock.unlock_chat(A)
    assert not chat_lock.is_hidden(A) and chat_lock.is_hidden(B)
    chat_lock.remove_secret_code()
    assert not chat_lock.is_hidden(B) and not chat_lock.has_secret_code()
    assert jids() == {A, B}
    chat_lock.set_secret_code("otra"); chat_lock.set_hidden(B, True)
    chat_lock.remove_password()
    assert jids() == {A, B} and not chat_lock.has_secret_code()


def test_hidden_chat_sends_no_notification(monkeypatch):
    from app import notifications
    chat_lock.set_password("1234")
    chat_lock.set_secret_code("abrete")
    add_chat(A)
    chat_lock.lock_chat(A); chat_lock.set_hidden(A, True)
    shown = []
    monkeypatch.setattr(notifications, "get_pref", lambda k: True)
    center = notifications.NotificationCenter.__new__(notifications.NotificationCenter)
    monkeypatch.setattr(notifications, "get_chat", lambda j: shown.append(j) or None)   # solo se llega acá si no se corta antes
    center._on_message({"is_new": True, "from_me": False, "chat_jid": A, "timestamp": NOW})
    assert shown == []


# ── Lectura invisible por chat ──

def test_silent_read_per_chat():
    assert not org.is_silent_read(A)
    org.set_silent_read(A, True)
    assert org.is_silent_read(A) and not org.is_silent_read(B)
    org.reset_cache()                                    # persiste
    assert org.is_silent_read(A)
    org.set_silent_read(A, False)
    assert not org.is_silent_read(A)


def test_gateway_skips_receipts_for_silent_chats(monkeypatch):
    import threading
    from app.gateway_manager import GatewayManager
    calls = []
    gw = GatewayManager.__new__(GatewayManager)
    monkeypatch.setattr(GatewayManager, "_post_simple", lambda self, ep, payload: calls.append(payload))

    class Inline:
        def __init__(self, target, **kw): self.t = target
        def start(self): self.t()
    monkeypatch.setattr(threading, "Thread", Inline)
    org.set_silent_read(A, True)
    gw.send_read_receipts(A, ["1", "2"])
    assert calls == []
    gw.send_read_receipts(B, ["3"])
    assert calls == [{"to": B, "messages": ["3"]}]


# ── Bloqueo de la app ──

def test_app_lock_due(monkeypatch):
    from app import config
    chat_lock.set_password("1234")
    monkeypatch.setattr(config, "get_pref", lambda k: True)
    monkeypatch.setattr(org, "get_app_lock_minutes", lambda: 5)
    assert org.app_lock_due(None)                        # recién abierta: se pide
    assert not org.app_lock_due(1000, now=1000 + 299)
    assert org.app_lock_due(1000, now=1000 + 300)
    monkeypatch.setattr(config, "get_pref", lambda k: False)
    assert not org.app_lock_due(None)                    # apagado


def test_app_lock_needs_password(monkeypatch):
    from app import config
    monkeypatch.setattr(config, "get_pref", lambda k: True)
    assert not org.app_lock_enabled()                    # sin contraseña no hay nada que pedir


# ── Galería ──

def test_gallery_pages():
    for i in range(5):
        db.save_message(f"i{i}", A, A, "X", False, "foto", NOW - i, media_type="image", media_path=f"/x/{i}.jpg", chat_name="C")
    db.save_message("d1", A, A, "X", False, "contrato.pdf", NOW, media_type="document", chat_name="C")
    db.save_message("a1", A, A, "X", False, "", NOW, media_type="audio", chat_name="C")
    db.save_message("l1", A, A, "X", False, "mirá https://ejemplo.com/a y http://b.org.", NOW, chat_name="C")
    items, n = org.gallery_page(A, "media", limit=3)
    assert [m["id"] for m in items] == ["i0", "i1", "i2"] and n == 3
    items, n = org.gallery_page(A, "media", limit=3, offset=3)
    assert [m["id"] for m in items] == ["i3", "i4"] and n == 2
    assert [m["id"] for m in org.gallery_page(A, "docs")[0]] == ["d1"]
    assert [m["id"] for m in org.gallery_page(A, "audio")[0]] == ["a1"]
    links, _ = org.gallery_page(A, "links")
    assert [l["url"] for l in links] == ["https://ejemplo.com/a", "http://b.org"]
    assert org.gallery_page(A, "otra") == ([], 0)
