"""Pruebas del webhook local (app/webhook_server.py): servidor real en el puerto 0, solo loopback, sin red externa."""
import json
import os
import sys
import threading
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest
from PySide6.QtCore import Qt

import app.db as db
from app import webhook_server as ws
from app.config import TOKEN_HEADER

TOKEN = "t" * 40
CHAT = "111@s.whatsapp.net"


@pytest.fixture()
def server(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setattr(ws, "_TOKEN", TOKEN)
    db.init_db()
    srv = ws._Server(("127.0.0.1", 0), ws.WebhookHandler)
    th = threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    th.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()
    srv.server_close()


def _post(base, path, payload=None, token=TOKEN, raw=None, headers=None):
    body = raw if raw is not None else json.dumps(payload if payload is not None else {}).encode("utf-8")
    h = {"Content-Type": "application/json", **(headers or {})}
    if token is not None:
        h[TOKEN_HEADER] = token
    req = urllib.request.Request(base + path, data=body, method="POST", headers=h)
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


@pytest.fixture()
def events():
    got = {}
    conns = []

    def watch(name):
        lst = got.setdefault(name, [])
        sig = getattr(ws.signals, name)
        slot = lambda *a: lst.append(a[0] if a else None)
        sig.connect(slot, Qt.DirectConnection)   # el servidor emite desde otro hilo; sin bucle de eventos
        conns.append((sig, slot))
        return lst

    yield watch
    for sig, slot in conns:
        sig.disconnect(slot)


def test_rejects_missing_or_wrong_token(server):
    assert _post(server, "/api/whatsapp/incoming", {}, token=None)[0] == 401
    assert _post(server, "/api/whatsapp/incoming", {}, token="otro")[0] == 401


def test_non_ascii_token_header_is_401_not_crash(server):
    # compare_digest con str no ASCII lanzaba TypeError y cortaba la conexión sin respuesta
    assert _post(server, "/api/whatsapp/incoming", {}, token="\u00f1and\u00fa")[0] == 401


def test_unknown_path_is_404(server):
    assert _post(server, "/api/nope", {})[0] == 404


def test_invalid_content_length_is_400(server):
    # Cuerpo vacío: si el cliente enviara datos que el servidor no lee, el cierre puede dar un RST (test intermitente)
    assert _post(server, "/api/whatsapp/incoming", raw=b"", headers={"Content-Length": "abc"})[0] == 400


def test_incoming_message_is_saved_and_emitted(server, events):
    got = events("message_received")
    payload = {"message_id": "w1", "chat_jid": CHAT, "push_name": "Ana", "phone": "111", "text": "hola",
               "timestamp": 1000}
    assert _post(server, "/api/whatsapp/incoming", payload)[0] == 200
    assert db.get_message("w1")["text"] == "hola"
    assert db.get_total_unread_count() == 1
    assert len(got) == 1 and got[0]["is_new"] is True


def test_incoming_duplicate_is_not_new_nor_double_counted(server, events):
    got = events("message_received")
    payload = {"message_id": "w1", "chat_jid": CHAT, "push_name": "Ana", "text": "hola", "timestamp": 1000}
    _post(server, "/api/whatsapp/incoming", payload)
    _post(server, "/api/whatsapp/incoming", payload)
    assert db.get_total_unread_count() == 1
    assert [g["is_new"] for g in got] == [True, False]


def test_incoming_without_timestamp_still_saves(server):
    payload = {"message_id": "w2", "chat_jid": CHAT, "push_name": "Ana", "text": "sin hora"}
    assert _post(server, "/api/whatsapp/incoming", payload)[0] == 200
    assert db.get_message("w2")["timestamp"] > 0


def test_incoming_without_ids_is_ignored_but_ok(server):
    assert _post(server, "/api/whatsapp/incoming", {"text": "x"})[0] == 200
    assert db.get_chats() == []


def test_incoming_empty_message_not_emitted(server, events):
    got = events("message_received")
    payload = {"message_id": "e1", "chat_jid": CHAT, "text": "   ", "timestamp": 1000}
    assert _post(server, "/api/whatsapp/incoming", payload)[0] == 200
    assert db.get_message("e1") is None and got == []


def test_incoming_revoke_and_edit_apply_on_original(server):
    base = {"message_id": "w3", "chat_jid": CHAT, "push_name": "Ana", "text": "original", "timestamp": 1000}
    _post(server, "/api/whatsapp/incoming", base)
    _post(server, "/api/whatsapp/incoming", {**base, "text": "editado", "edited": True})
    m = db.get_message("w3")
    assert m["text"] == "editado" and m["edited"] == 1
    _post(server, "/api/whatsapp/incoming", {**base, "revoked": True})
    assert db.get_message("w3")["media_type"] == "revoked"


def test_group_members_and_own_names_do_not_become_contacts(server):
    _post(server, "/api/whatsapp/incoming", {"message_id": "g1", "chat_jid": "9@g.us", "push_name": "Carlos",
                                              "text": "hola", "timestamp": 1000})
    _post(server, "/api/whatsapp/incoming", {"message_id": "o1", "chat_jid": CHAT, "push_name": "Yo",
                                              "text": "hola", "timestamp": 1000})
    with db.db_conn() as c:
        assert c.execute("SELECT COUNT(*) FROM contacts").fetchone()[0] == 0


def test_non_object_json_body_does_not_break(server):
    assert _post(server, "/api/whatsapp/incoming", raw=b"[1, 2, 3]")[0] == 200
    assert _post(server, "/api/app/activate", raw=b"null")[0] == 200
    assert _post(server, "/api/whatsapp/incoming", raw=b"esto no es json")[0] == 200


def test_invalid_utf8_body_does_not_break(server):
    assert _post(server, "/api/whatsapp/incoming", raw=b"\xff\xfe\x00")[0] == 200


def test_activate_emits_uri_with_default(server, events):
    got = events("app_activate")
    _post(server, "/api/app/activate", {"uri": "advancedwhatsapp://open?chat=1"})
    _post(server, "/api/app/activate", {})
    assert got == ["advancedwhatsapp://open?chat=1", "advancedwhatsapp://show"]


@pytest.mark.parametrize("path,signal", [
    ("/api/whatsapp/presence", "presence_changed"),
    ("/api/whatsapp/call", "call_event"),
    ("/api/whatsapp/contact_status", "contact_status"),
    ("/api/whatsapp/scheduled", "scheduled_changed"),
    ("/api/whatsapp/connection", "connection_changed"),
])
def test_passthrough_endpoints_emit_payload(server, events, path, signal):
    got = events(signal)
    assert _post(server, path, {"a": 1})[0] == 200
    assert got == [{"a": 1}]


def test_sync_batch_saves_and_signals(server, events):
    got = events("history_synced")
    payload = {"chats": [{"jid": CHAT, "name": "Ana"}], "contacts": [],
               "messages": [{"message_id": "s1", "chat_jid": CHAT, "text": "hola", "timestamp": 1000}]}
    status, body = _post(server, "/api/whatsapp/sync_batch", payload)
    assert status == 200 and json.loads(body) == {"status": "ok"}
    assert db.get_message("s1")["text"] == "hola"
    assert len(got) == 1


def test_status_endpoint_moves_own_ticks_forward(server, events):
    got = events("message_status")
    db.save_message("own", CHAT, "me", "Yo", True, "hola", 1000, status="sent")
    _post(server, "/api/whatsapp/status", {"message_id": "own", "status": "read"})
    _post(server, "/api/whatsapp/status", {"message_id": "own", "status": "delivered"})   # retrocede: ignorado
    assert db.get_message("own")["status"] == "read"
    assert got == [{"message_id": "own", "status": "read"}]


def test_reaction_endpoint(server, events):
    got = events("reaction_changed")
    db.save_message("r1", CHAT, CHAT, "Ana", False, "hola", 1000)
    _post(server, "/api/whatsapp/reaction", {"message_id": "r1", "reactor_jid": "me", "emoji": "\U0001F44D",
                                              "timestamp": 5, "chat_jid": CHAT})
    assert db.get_reactions(["r1"])["r1"] == [("\U0001F44D", "me")]
    assert got == [{"message_id": "r1", "chat_jid": CHAT}]
    _post(server, "/api/whatsapp/reaction", {"message_id": "r1", "reactor_jid": "me", "emoji": ""})
    assert db.get_reactions(["r1"]) == {}


def test_avatar_endpoint(server, events):
    got = events("avatar_updated")
    db.get_or_create_chat(CHAT, "Ana")
    _post(server, "/api/whatsapp/avatar", {"jid": CHAT, "avatar_path": "C:/x/a.jpg"})
    assert db.get_chat(CHAT)["avatar_path"] == "C:/x/a.jpg"
    assert len(got) == 1


def test_read_elsewhere_endpoint(server, events):
    got = events("read_elsewhere")
    db.save_message("a1", CHAT, CHAT, "Ana", False, "hola", 100)
    db.save_message("a2", CHAT, CHAT, "Ana", False, "hola2", 101)
    assert db.get_total_unread_count() == 2
    _post(server, "/api/whatsapp/read_elsewhere", {"chats": [{"chat_jid": CHAT, "all": True}]})
    assert db.get_total_unread_count() == 0
    assert got == [[CHAT]]
    _post(server, "/api/whatsapp/read_elsewhere", {"chats": [{"chat_jid": CHAT, "unread": True}]})
    assert db.get_total_unread_count() == 1


def test_internal_error_returns_500_so_gateway_retries(server, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("db caida")
    monkeypatch.setattr(ws, "save_message", boom)
    assert _post(server, "/api/whatsapp/incoming",
                 {"message_id": "x", "chat_jid": CHAT, "text": "hola", "timestamp": 1})[0] == 500


def test_webhook_server_start_stop_cycle(monkeypatch):
    monkeypatch.setattr(ws, "WEBHOOK_PORT", 0)
    srv = ws.WebhookServer()
    srv.start()
    try:
        assert srv.server is not None and srv.thread.is_alive()
        srv.start()   # idempotente
    finally:
        srv.stop()
    assert srv.server is None and srv.thread is None
