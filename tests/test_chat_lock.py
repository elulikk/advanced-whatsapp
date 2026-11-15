import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest
import app.db as db
from app import chat_lock


@pytest.fixture(autouse=True)
def tmpdb(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "t.db"))
    chat_lock._invalidate()
    db.init_db()
    yield
    chat_lock._invalidate()


JID = "111@s.whatsapp.net"


def test_hash_has_salt_and_is_not_plaintext():
    h1, s1, it = chat_lock.hash_password("secreto123")
    h2, s2, _ = chat_lock.hash_password("secreto123")
    assert s1 != s2 and h1 != h2            # sal aleatoria
    assert "secreto123" not in h1
    assert chat_lock.check_hash("secreto123", h1, s1, it)
    assert not chat_lock.check_hash("otra", h1, s1, it)
    assert not chat_lock.check_hash("x", "zz", "no-hex", it)   # datos corruptos: False, sin excepción


def test_password_lifecycle_and_storage():
    assert not chat_lock.has_password()
    assert not chat_lock.verify_password("1234")
    assert not chat_lock.set_password("abc")            # muy corta
    assert chat_lock.set_password("1234")
    assert chat_lock.has_password()
    assert chat_lock.verify_password("1234") and not chat_lock.verify_password("4321")
    with db.db_conn() as c:
        stored = " ".join(v for _, v in c.execute("SELECT * FROM lock_meta").fetchall())
    assert "1234" not in stored.split()                  # solo hash + sal + iteraciones
    assert chat_lock.set_password("nueva-clave")
    assert chat_lock.verify_password("nueva-clave") and not chat_lock.verify_password("1234")


def test_lock_requires_password_and_persists():
    assert not chat_lock.lock_chat(JID)                  # sin contraseña no se puede bloquear
    chat_lock.set_password("1234")
    assert chat_lock.lock_chat(JID)
    assert chat_lock.is_locked(JID) and not chat_lock.is_open(JID)
    chat_lock._invalidate()                              # simula reiniciar la app: se relee de disco
    assert chat_lock.is_locked(JID) and chat_lock.has_password() and chat_lock.verify_password("1234")
    chat_lock.unlock_chat(JID)
    chat_lock._invalidate()
    assert not chat_lock.is_locked(JID)


def test_session_open_and_relock():
    chat_lock.set_password("1234")
    chat_lock.lock_chat(JID)
    chat_lock.lock_chat("222@s.whatsapp.net")
    chat_lock.mark_open(JID)
    chat_lock.mark_open("222@s.whatsapp.net")
    assert chat_lock.is_open(JID) and chat_lock.is_locked(JID)   # abierto pero sigue marcado como bloqueado
    chat_lock.relock(keep=JID)
    assert chat_lock.is_open(JID) and not chat_lock.is_open("222@s.whatsapp.net")
    chat_lock.lock_all()
    assert not chat_lock.is_open(JID)
    assert chat_lock.is_open("999@s.whatsapp.net")               # un chat común siempre se abre


def test_remove_password_unlocks_everything():
    chat_lock.set_password("1234")
    chat_lock.lock_chat(JID)
    chat_lock.remove_password()
    assert not chat_lock.has_password() and not chat_lock.is_locked(JID)
    chat_lock._invalidate()
    assert chat_lock.locked_jids() == []


def test_search_does_not_leak_locked_preview():
    db.get_or_create_chat(JID, "Ana")
    db.get_or_create_chat("222@s.whatsapp.net", "Beto")
    with db.db_conn() as c:
        c.execute("UPDATE chats SET last_message_text = 'clave del banco' WHERE jid IN (?, ?)", (JID, "222@s.whatsapp.net"))
    chat_lock.set_password("1234")
    chat_lock.lock_chat(JID)
    found = [c["jid"] for c in db.get_chats(search="banco")]
    assert found == ["222@s.whatsapp.net"]
    assert JID in [c["jid"] for c in db.get_chats(search="Ana")]     # por nombre sí aparece


def test_delete_chat_drops_lock():
    db.get_or_create_chat(JID, "Ana")
    chat_lock.set_password("1234")
    chat_lock.lock_chat(JID)
    db.delete_chat(JID)
    chat_lock._invalidate()
    assert not chat_lock.is_locked(JID)
