import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from unittest.mock import MagicMock

import app.gateway_manager as gm


class _Resp:
    status_code = 200
    text = ""

    def __init__(self, data):
        self._data = data

    def json(self):
        return self._data


def _manager(monkeypatch, items=()):
    http = MagicMock()
    http.post.return_value = _Resp({"success": True, "message_id": "M1"})
    http.request.side_effect = lambda method, url, **kw: _Resp({"items": list(items), "id": "abc"})
    monkeypatch.setattr(gm, "_http", http)
    return gm.GatewayManager.__new__(gm.GatewayManager), http


def test_without_at_sends_right_away(monkeypatch):
    mgr, http = _manager(monkeypatch)
    assert mgr.send_message(to="1@s.whatsapp.net", text="hola")["message_id"] == "M1"
    assert http.post.call_args.args[0].endswith("/send")
    http.request.assert_not_called()


def test_with_at_goes_to_the_queue(monkeypatch):
    mgr, http = _manager(monkeypatch)
    res = mgr.send_message(to="1@s.whatsapp.net", text="hola", at=2000000000.7, chat_name="Ana",
                           quoted_text="cita", quoted_sender="Yo")
    assert res["success"] and res["id"] == "abc"
    http.post.assert_not_called()
    method, url = http.request.call_args.args
    body = http.request.call_args.kwargs["json"]
    assert (method, url.rsplit("/", 1)[1]) == ("POST", "message-schedule")
    assert body["at"] == 2000000000 and body["chat_name"] == "Ana"
    assert body["quoted_text"] == "cita" and body["consume"] is False


def test_voice_note_keeps_duration_and_wave_when_scheduled(monkeypatch):
    mgr, http = _manager(monkeypatch)
    mgr.send_message(to="1@s.whatsapp.net", audio_path="x.ogg", audio_seconds=3.5, audio_waveform=[1, 2],
                     at=2000000000, consume=True)
    body = http.request.call_args.kwargs["json"]
    assert body["audio_path"] == "x.ogg" and body["audio_seconds"] == 3.5
    assert body["audio_waveform"] == [1, 2] and body["consume"] is True


def test_pending_count_adds_statuses_and_messages(monkeypatch):
    items = [{"state": "pending"}, {"state": "sending"}, {"state": "sent"}, {"state": "failed"}]
    mgr, _ = _manager(monkeypatch, items)
    assert mgr.pending_scheduled_count() == 4   # 2 de la cola de estados + 2 de la de mensajes
