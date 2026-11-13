"""Calidad de vida: estadísticas, limpieza de multimedia, caché de miniaturas, previews de links y audios en cadena."""
import base64
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest

import app.db as db
from app import audio_queue, link_preview, media_cleanup, stats, tags, thumb_cache

A = "111@s.whatsapp.net"
B = "222@s.whatsapp.net"
G = "999@g.us"
DAY = 86400


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


def _save(i, ts, jid=A, from_me=False, text="hola", **kw):
    return db.save_message(i, jid, "me" if from_me else jid, "Ana", from_me, text, ts, **kw)


# ── Estadísticas ─────────────────────────────────────────────────────────────

def test_top_chats_and_totals():
    for i in range(5):
        _save(f"a{i}", 1000 + i, A, from_me=i % 2 == 0)
    for i in range(2):
        _save(f"b{i}", 2000 + i, B)
    top = stats.top_chats(0)
    assert [c["jid"] for c in top] == [A, B]
    assert top[0]["total"] == 5 and top[0]["sent"] == 3 and top[0]["received"] == 2
    t = stats.totals(0)
    assert t == {"total": 7, "sent": 3, "received": 4, "chats": 2}


def test_period_filter_and_revoked_not_counted():
    now = int(time.time())
    _save("old", now - 100 * DAY)
    _save("new", now - DAY)
    assert stats.totals(stats.period_start("month", now))["total"] == 1
    assert stats.totals(stats.period_start("all", now))["total"] == 2
    db.revoke_message_local("new")
    assert stats.totals(0)["total"] == 1


def test_messages_by_hour_has_24_slots():
    _save("x", 1_700_000_000)
    hours = stats.messages_by_hour(0)
    assert len(hours) == 24 and sum(h["received"] for h in hours) == 1


def test_messages_by_day_groups_sent_and_received():
    base = 1_700_000_000
    _save("d1", base, from_me=True)
    _save("d2", base + 10)
    _save("d3", base + 3 * DAY)
    days = stats.messages_by_day(0)
    assert len(days) == 2 and days[0]["sent"] == 1 and days[0]["received"] == 1


def test_response_times_mine_and_theirs():
    _save("1", 1000)                    # ellos
    _save("2", 1060, from_me=True)      # yo contesto en 60 s
    _save("3", 1200)                    # ellos contestan en 140 s
    _save("4", 1500, from_me=True)      # yo contesto en 300 s
    _save("5", 1510, from_me=True)      # segundo mensaje seguido mío: no es una respuesta
    _save("6", 1000 + 3 * DAY, jid=A)   # más de 24 h: se descarta
    _save("g1", 1000, jid=G)            # los grupos no cuentan
    _save("g2", 1010, jid=G, from_me=True)
    r = stats.response_times(0)
    assert r["mine"] == 180 and r["mine_count"] == 2
    assert r["theirs"] == 140 and r["theirs_count"] == 1


def test_response_times_without_data_is_none():
    r = stats.response_times(0)
    assert r["mine"] is None and r["theirs"] is None


def test_fastest_and_slowest_need_minimum_replies():
    t = 1000
    for i in range(3):                  # en A contesto siempre en 10 s
        _save(f"a{i}o", t, A)
        _save(f"a{i}m", t + 10, A, from_me=True)
        t += 100
    t = 1000
    for i in range(3):                  # en B siempre en 500 s
        _save(f"b{i}o", t, B)
        _save(f"b{i}m", t + 500, B, from_me=True)
        t += 1000
    sp = stats.slowest_and_fastest(0, min_replies=3)
    assert sp["fastest"][0]["jid"] == A and sp["slowest"][0]["jid"] == B
    assert stats.slowest_and_fastest(0, min_replies=4)["fastest"] == []


def test_format_duration_and_index_idempotent():
    assert stats.format_duration(None) == "—"
    assert stats.format_duration(45) == "45 s"
    assert stats.format_duration(180) == "3 min"
    assert stats.format_duration(3 * 3600 + 900) == "3 h 15 min"
    stats.ensure_indexes()
    stats.ensure_indexes()
    with db.db_conn() as c:
        names = [r[1] for r in c.execute("PRAGMA index_list(messages)")]
    assert "idx_messages_ts" in names


def test_stats_use_the_index():
    with db.db_conn() as c:
        stats.ensure_indexes(c)
        plan = " ".join(str(r[3]) for r in c.execute(
            "EXPLAIN QUERY PLAN SELECT COUNT(*) FROM messages WHERE timestamp >= 5"))
    assert "idx_messages_ts" in plan


def test_report_runs_end_to_end():
    _save("r1", int(time.time()) - 60)
    rep = stats.report("week")
    assert rep["totals"]["total"] == 1 and len(rep["by_hour"]) == 24 and rep["top_chats"]


# ── Limpieza de multimedia ───────────────────────────────────────────────────

def _media(tmp_path, name, size, sub=""):
    folder = tmp_path / "media" / sub if sub else tmp_path / "media"
    folder.mkdir(parents=True, exist_ok=True)
    p = folder / name
    p.write_bytes(b"x" * size)
    return str(p)


def _media_msg(tmp_path, i, age_days, size=1000, mtype="image", raw="{}", sub="", jid=A, from_me=False):
    now = int(time.time())
    return _save(i, now - age_days * DAY, jid=jid, from_me=from_me, text="", media_type=mtype,
                 media_path=_media(tmp_path, f"{i}.bin", size, sub), media_raw=raw)


def test_usage_by_type_and_chat(tmp_path):
    _media_msg(tmp_path, "i1", 1, 1000, "image")
    _media_msg(tmp_path, "i2", 1, 500, "audio", jid=B)
    u = media_cleanup.usage(str(tmp_path / "media"))
    assert u["total_bytes"] == 1500 and u["total_files"] == 2
    assert u["by_type"]["image"] == {"bytes": 1000, "count": 1}
    assert u["by_chat"][0]["jid"] == A


def test_cleanup_by_age_keeps_recent_and_protected(tmp_path):
    media = str(tmp_path / "media")
    _media_msg(tmp_path, "viejo", 90)
    _media_msg(tmp_path, "nuevo", 2)
    _media_msg(tmp_path, "propio", 90, sub="enviados", from_me=True)      # carpeta protegida
    _media_msg(tmp_path, "sinraw", 90, raw=None)                          # no se puede volver a bajar
    res = media_cleanup.cleanup(older_than_days=30, media_dir=media)
    assert res["files"] == 1 and res["bytes"] == 1000
    assert db.get_message("viejo")["media_path"] is None
    assert not os.path.exists(str(tmp_path / "media" / "viejo.bin"))
    assert db.get_message("nuevo")["media_path"]
    assert db.get_message("propio")["media_path"] and db.get_message("sinraw")["media_path"]


def test_cleanup_by_size_removes_oldest_first(tmp_path):
    media = str(tmp_path / "media")
    _media_msg(tmp_path, "m1", 30, 1000)
    _media_msg(tmp_path, "m2", 20, 1000)
    _media_msg(tmp_path, "m3", 10, 1000)
    limit_gb = 1500 / 1024 ** 3       # entra 1 solo archivo
    res = media_cleanup.cleanup(max_gb=limit_gb, media_dir=media)
    assert res["files"] == 2
    assert db.get_message("m1")["media_path"] is None and db.get_message("m2")["media_path"] is None
    assert db.get_message("m3")["media_path"]


def test_cleanup_without_criteria_does_nothing(tmp_path):
    _media_msg(tmp_path, "m1", 400)
    assert media_cleanup.plan(0, 0, media_dir=str(tmp_path / "media")) == []
    assert db.get_message("m1")["media_path"]


def test_plan_does_not_delete_and_ignores_missing_files(tmp_path):
    media = str(tmp_path / "media")
    _media_msg(tmp_path, "m1", 90)
    _media_msg(tmp_path, "m2", 90)
    os.remove(str(tmp_path / "media" / "m2.bin"))
    cands = media_cleanup.plan(30, media_dir=media)
    assert [c["id"] for c in cands] == ["m1"] and os.path.exists(str(tmp_path / "media" / "m1.bin"))


def test_auto_cleanup_runs_once_a_day(tmp_path, monkeypatch):
    store = {"media_auto_days": "30", "media_auto_gb": "0"}
    monkeypatch.setattr(media_cleanup.config, "get_setting", lambda k, d="": store.get(k, d))
    monkeypatch.setattr(media_cleanup.config, "set_setting", lambda k, v: store.__setitem__(k, str(v)) or True)
    monkeypatch.setattr(media_cleanup, "cleanup", lambda *a, **k: {"files": 3, "bytes": 9, "errors": 0})
    now = int(time.time())
    assert media_cleanup.run_auto_if_due(now)["files"] == 3
    assert media_cleanup.run_auto_if_due(now + 3600) is None
    assert media_cleanup.run_auto_if_due(now + 25 * 3600)["files"] == 3
    store["media_auto_days"] = "0"
    store["media_auto_last"] = "0"
    assert media_cleanup.run_auto_if_due(now) is None      # apagada


def test_format_size():
    assert media_cleanup.format_size(500) == "500 B"
    assert media_cleanup.format_size(1536) == "1.5 KB"
    assert media_cleanup.format_size(5 * 1024 ** 3) == "5.0 GB"


# ── Caché de miniaturas ──────────────────────────────────────────────────────

def _make_jpeg(path, w, h):
    from PySide6.QtGui import QColor, QImage
    img = QImage(w, h, QImage.Format_RGB32)
    img.fill(QColor("#3070a0"))
    assert img.save(str(path), "JPG", 90)


def test_lru_evicts_least_recent():
    c = thumb_cache.LruCache(2)
    c.put("a", 1)
    c.put("b", 2)
    assert c.get("a") == 1          # a pasa a ser la más reciente
    c.put("c", 3)
    assert c.get("b") is None and c.get("a") == 1 and c.get("c") == 3
    assert len(c) == 2 and c.hits == 3 and c.misses == 1


def test_lru_get_or_create_calls_factory_once():
    c = thumb_cache.LruCache(4)
    calls = []
    f = lambda: calls.append(1) or "v"
    assert c.get_or_create("k", f) == "v" and c.get_or_create("k", f) == "v"
    assert len(calls) == 1


def test_thumb_is_smaller_and_reused(tmp_path):
    src = tmp_path / "foto.jpg"
    _make_jpeg(src, 3000, 2000)
    d = str(tmp_path / "thumbs")
    thumb, w, h = thumb_cache.thumb_for(str(src), 640, d)
    assert thumb != str(src) and os.path.exists(thumb)
    assert max(w, h) == 640 and w > h
    assert os.path.getsize(thumb) < os.path.getsize(src)
    mtime = os.path.getmtime(thumb)
    thumb2, w2, h2 = thumb_cache.thumb_for(str(src), 640, d)
    assert thumb2 == thumb and (w2, h2) == (w, h) and os.path.getmtime(thumb) >= mtime


def test_thumb_regenerates_when_file_changes(tmp_path):
    src = tmp_path / "foto.jpg"
    d = str(tmp_path / "thumbs")
    _make_jpeg(src, 1200, 800)
    t1, *_ = thumb_cache.thumb_for(str(src), 640, d)
    time.sleep(0.01)
    _make_jpeg(src, 1000, 1000)
    os.utime(src, (time.time() + 5, time.time() + 5))
    t2, w, h = thumb_cache.thumb_for(str(src), 640, d)
    assert t1 != t2 and (w, h) == (640, 640)


def test_small_image_is_not_enlarged_and_bad_files_fall_back(tmp_path):
    src = tmp_path / "chica.jpg"
    _make_jpeg(src, 100, 80)
    _t, w, h = thumb_cache.thumb_for(str(src), 640, str(tmp_path / "thumbs"))
    assert (w, h) == (100, 80)
    bad = tmp_path / "roto.jpg"
    bad.write_bytes(b"no es una imagen")
    assert thumb_cache.thumb_for(str(bad), 640, str(tmp_path / "thumbs")) == (str(bad), 0, 0)
    assert thumb_cache.thumb_for(str(tmp_path / "noexiste.jpg"), 640, str(tmp_path / "thumbs"))[1:] == (0, 0)


def test_fit_size_keeps_ratio():
    assert thumb_cache.fit_size(640, 480, 300, 240) == (300, 225)
    assert thumb_cache.fit_size(100, 80, 300, 240) == (100, 80)
    assert thumb_cache.fit_size(0, 0) == (300, 240)


def test_prune_disk_drops_oldest(tmp_path):
    d = tmp_path / "thumbs" / "ab"
    d.mkdir(parents=True)
    for i in range(5):
        p = d / f"{i}.jpg"
        p.write_bytes(b"x" * 1000)
        os.utime(p, (1000 + i, 1000 + i))
    removed = thumb_cache.prune_disk(3000, str(tmp_path / "thumbs"))
    left = sorted(os.listdir(d))
    assert removed >= 2 and "4.jpg" in left and "0.jpg" not in left
    assert thumb_cache.disk_usage(str(tmp_path / "thumbs")) <= 3000


# ── Preview de links ─────────────────────────────────────────────────────────

@pytest.fixture
def lp(tmp_path, monkeypatch):
    monkeypatch.setattr(link_preview, "PREVIEWS_DIR", str(tmp_path / "previews"))
    monkeypatch.setattr(link_preview, "get_pref", lambda k: True)
    monkeypatch.setattr(link_preview, "_table_ready", False)
    link_preview._mem.clear()
    link_preview._failed_at.clear()
    link_preview._queued.clear()
    yield link_preview
    link_preview._mem.clear()
    link_preview._failed_at.clear()
    monkeypatch.setattr(link_preview, "_fetcher", None)


def test_first_url_strips_punctuation():
    assert link_preview.first_url("mirá https://ejemplo.com/a?b=1, che") == "https://ejemplo.com/a?b=1"
    assert link_preview.first_url("(https://x.org/p).") == "https://x.org/p"
    assert link_preview.first_url("sin links") is None and link_preview.first_url("") is None


def test_store_event_and_lookup_use_db_cache(lp):
    img = base64.b64encode(b"\xff\xd8\xff fake").decode()
    lp.store_event({"text": "mirá https://ej.com/nota", "link_preview": {
        "url": "https://ej.com/nota", "title": "Una nota", "description": "Resumen", "image_b64": img}})
    prev = lp.lookup("https://ej.com/nota", fetch=False)
    assert prev["title"] == "Una nota" and os.path.exists(prev["image_path"])
    lp._mem.clear()                                    # simula reiniciar la app: sale de la base
    again = lp.lookup("https://ej.com/nota", fetch=False)
    assert again["description"] == "Resumen"


def test_lookup_fetches_in_background_and_notifies(lp, monkeypatch):
    got = []
    lp.signals.preview_ready.connect(got.append)
    calls = []

    def fetcher(url):
        calls.append(url)
        return {"success": True, "preview": {"title": "Hola", "description": "d", "image_b64": None}}
    monkeypatch.setattr(lp, "_fetcher", fetcher)
    from PySide6.QtCore import QCoreApplication
    app = QCoreApplication.instance() or QCoreApplication([])
    assert lp.lookup("https://nuevo.com/x") is None     # no bloquea: devuelve ya y deja el trabajo en cola
    for _ in range(100):
        app.processEvents()                              # la señal viene de otro hilo: hace falta el bucle de eventos
        if got:
            break
        time.sleep(0.05)
    assert got == ["https://nuevo.com/x"] and calls == ["https://nuevo.com/x"]
    assert lp.lookup("https://nuevo.com/x")["title"] == "Hola"
    lp.signals.preview_ready.disconnect(got.append)


def test_failed_fetch_is_not_retried_right_away(lp, monkeypatch):
    calls = []
    monkeypatch.setattr(lp, "_fetcher", lambda u: calls.append(u))     # devuelve None: sin preview
    lp.lookup("https://roto.com/")
    for _ in range(100):
        if "https://roto.com/" in lp._failed_at:
            break
        time.sleep(0.05)
    assert lp.lookup("https://roto.com/") is None
    time.sleep(0.2)
    assert calls == ["https://roto.com/"]


def test_disabled_pref_skips_everything(lp, monkeypatch):
    monkeypatch.setattr(lp, "get_pref", lambda k: False)
    monkeypatch.setattr(lp, "_fetcher", lambda u: pytest.fail("no debería pedir nada"))
    assert lp.lookup("https://x.com/") is None


def test_card_html_escapes_content(lp):
    colors = {"text": "#fff", "text_2": "#999", "accent": "#0f0"}
    prev = {"url": "https://ej.com/?a=1&b=2", "title": "<b>Hack</b>", "description": "x" * 300, "image_path": None}
    out = lp.card_html(prev, lambda inner, accent=None: f"[{inner}]", colors)
    assert "&lt;b&gt;Hack" in out and "ej.com" in out and "…" in out and "a=1&amp;b=2" in out


# ── Audios en cadena ─────────────────────────────────────────────────────────

def test_next_audio_only_when_consecutive():
    _save("v1", 100, text="", media_type="audio", media_path="/tmp/a")
    _save("v2", 110, text="", media_type="audio", media_path="/tmp/b")
    _save("t1", 120, text="hola")
    _save("v3", 130, text="", media_type="audio", media_path="/tmp/c")
    assert audio_queue.next_audio(A, "v1")["id"] == "v2"
    assert audio_queue.next_audio(A, "v2") is None          # le sigue un texto
    assert audio_queue.next_audio(A, "v3") is None          # es el último
    assert audio_queue.next_audio(A, "nope") is None
    assert audio_queue.next_audio(B, "v1") is None          # otro chat


def test_next_audio_same_second_uses_insertion_order():
    _save("s1", 100, text="", media_type="audio", media_path="/tmp/a")
    _save("s2", 100, text="", media_type="audio", media_path="/tmp/b")
    assert audio_queue.next_audio(A, "s1")["id"] == "s2"
