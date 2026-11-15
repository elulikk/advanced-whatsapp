"""Leer imágenes con IA (PC): guardado, búsqueda por contenido, cola automática y proveedores con visión."""
import configparser
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest

import app.db as db
import app.media_service as ms
from app.ai import image_reader, smart_features
from app.ai.ai_service import AIService

CHAT = "111@s.whatsapp.net"


@pytest.fixture(autouse=True)
def entorno(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setattr(db, "_load_lid_phone_map", lambda: {})
    db._early_status.clear()
    db.init_db()
    monkeypatch.setattr(ms, "_ensure_worker", lambda: None)
    monkeypatch.setattr(ms, "_ensure_img_workers", lambda: None)
    ms._pending.clear()
    ms._errors.clear()
    for q in (ms._jobs, ms._img_jobs):
        while not q.empty():
            q.get()
    prefs = {"auto_read_images": True}
    monkeypatch.setattr(ms, "get_pref", lambda k: prefs.get(k, False))
    monkeypatch.setattr(ms.image_reader, "has_vision_key", lambda service=None: True)
    yield prefs
    ms._pending.clear()
    ms._errors.clear()


def _foto(tmp_path, i, ts=1000, en_disco=True, media_type="image"):
    path = None
    if en_disco:
        path = str(tmp_path / f"{i}.jpg")
        with open(path, "wb") as f:
            f.write(b"x")
    db.save_message(i, CHAT, CHAT, "Ana", False, "", ts, media_type=media_type, media_path=path, media_raw="{}")
    return {"id": i, "media_type": media_type, "media_path": path}


# ─── base de datos ───────────────────────────────────────────────────────────

def test_default_apagado():
    from app.config import PREF_DEFAULTS
    assert PREF_DEFAULTS["auto_read_images"] is False


def test_guarda_lectura(tmp_path):
    _foto(tmp_path, "f1")
    db.set_message_image_read("f1", " Un ticket de compra ", " Total $1200 ")
    m = db.get_message("f1")
    assert m["image_desc"] == "Un ticket de compra" and m["image_ocr"] == "Total $1200"
    assert not db.is_message_image_failed("f1")


def test_fallo_y_reintento(tmp_path):
    _foto(tmp_path, "f1")
    db.set_message_image_failed("f1")
    assert db.is_message_image_failed("f1")
    db.set_message_image_read("f1", "algo", "")
    assert not db.is_message_image_failed("f1")


def test_migracion_idempotente():
    db.init_db()   # una segunda vez no rompe (columnas ya existentes)
    with db.db_conn() as conn:
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(messages)")}
    assert {"image_desc", "image_ocr", "image_failed"} <= cols


def test_eliminar_mensaje_borra_lectura(tmp_path):
    _foto(tmp_path, "f1")
    db.set_message_image_read("f1", "desc", "texto")
    db.revoke_message_local("f1")
    m = db.get_message("f1")
    assert not m["image_desc"] and not m["image_ocr"]


def test_pendientes_solo_en_disco(tmp_path):
    _foto(tmp_path, "en_disco", ts=2000)
    _foto(tmp_path, "sin_bajar", ts=2000, en_disco=False)
    _foto(tmp_path, "leida", ts=2000)
    db.set_message_image_read("leida", "ya", "")
    _foto(tmp_path, "fallida", ts=2000)
    db.set_message_image_failed("fallida")
    _foto(tmp_path, "vieja", ts=10)
    ids = [r["id"] for r in db.get_images_pending_read(1000)]
    assert ids == ["en_disco"]


# ─── búsqueda por contenido ──────────────────────────────────────────────────

def test_buscar_encuentra_por_descripcion_y_texto(tmp_path):
    _foto(tmp_path, "f1")
    _foto(tmp_path, "f2")
    db.set_message_image_read("f1", "Captura de una factura de electricidad", "")
    db.set_message_image_read("f2", "Un perro en la playa", "Cartel: prohibido estacionar")
    assert [m["id"] for m in smart_features.search_candidates(["factura"])] == ["f1"]
    assert [m["id"] for m in smart_features.search_candidates(["estacionar"])] == ["f2"]


def test_candidato_muestra_lo_leido(tmp_path):
    _foto(tmp_path, "f1")
    db.set_message_image_read("f1", "Un gato", "Miau")
    m = db.get_message("f1")
    assert "Un gato" in smart_features.candidate_line(1, m) and "(imagen)" in smart_features.candidate_line(1, m)


def test_destacados_busca_en_imagenes(tmp_path):
    _foto(tmp_path, "f1")
    db.set_message_image_read("f1", "Un mapa del subte", "")
    db.set_message_starred("f1", True)
    assert [m["id"] for m in db.get_starred_messages("subte")] == ["f1"]


# ─── respuesta de la IA ──────────────────────────────────────────────────────

def test_parse_reading():
    assert image_reader.parse_reading("DESCRIPCION: Un menú\nTEXTO: Pizza $10\nCafé $2") == ("Un menú", "Pizza $10\nCafé $2")
    assert image_reader.parse_reading("DESCRIPCIÓN: Un paisaje\nTEXTO: NINGUNO") == ("Un paisaje", "")
    assert image_reader.parse_reading("Una foto de un auto") == ("Una foto de un auto", "")


# ─── cola automática ─────────────────────────────────────────────────────────

def test_encola_foto_descargada(tmp_path):
    assert ms.auto_read_image(_foto(tmp_path, "f1")) is True
    assert ms.is_pending("readimg", "f1")
    assert ms._img_jobs.qsize() == 1 and ms._jobs.qsize() == 0   # cola aparte de las descargas


def test_no_encola_apagado_sin_clave_ni_sin_archivo(tmp_path, entorno, monkeypatch):
    entorno["auto_read_images"] = False
    assert ms.auto_read_image(_foto(tmp_path, "f1")) is False
    assert ms.auto_read_images_backlog(now=1000) == 0
    entorno["auto_read_images"] = True
    monkeypatch.setattr(ms.image_reader, "has_vision_key", lambda service=None: False)
    assert ms.auto_read_image(_foto(tmp_path, "f2")) is False
    monkeypatch.setattr(ms.image_reader, "has_vision_key", lambda service=None: True)
    assert ms.auto_read_image(_foto(tmp_path, "f3", en_disco=False)) is False   # nunca se baja para analizarla
    assert ms._img_jobs.qsize() == 0


def test_no_duplica_ni_reintenta_fallidas(tmp_path):
    msg = _foto(tmp_path, "f1")
    assert ms.auto_read_image(msg) is True
    assert ms.auto_read_image(msg) is False
    other = _foto(tmp_path, "f2")
    db.set_message_image_failed("f2")
    assert ms.auto_read_image(other) is False
    assert ms._img_jobs.qsize() == 1


def test_stickers_y_ya_leidas_no_se_leen_solas(tmp_path):
    assert ms.auto_read_image(_foto(tmp_path, "s1", media_type="sticker")) is False
    msg = _foto(tmp_path, "f1")
    assert ms.auto_read_image({**msg, "image_desc": "ya está"}) is False


def test_backlog_solo_fotos_en_disco(tmp_path):
    _foto(tmp_path, "f1", ts=2000)
    _foto(tmp_path, "f2", ts=2000, en_disco=False)
    assert ms.auto_read_images_backlog(now=2000) == 1


def test_lectura_manual_exige_archivo(tmp_path):
    ms.request_image_read(_foto(tmp_path, "f1", en_disco=False))
    assert ms._img_jobs.qsize() == 0
    ms.request_image_read(_foto(tmp_path, "f2"))
    assert ms._img_jobs.qsize() == 1


def test_trabajador_guarda_y_marca_fallo(tmp_path, monkeypatch):
    msgs = [_foto(tmp_path, "ok"), _foto(tmp_path, "mal")]

    def fake_read(path):
        if "mal" in path:
            raise RuntimeError("sin cuota")
        return "Una foto", "Hola"
    monkeypatch.setattr(ms.image_reader, "read_image", fake_read)
    for m in msgs:
        ms.auto_read_image(m)
    # el hilo corre un trabajo por vez: se lo simula sacando de la cola
    import threading
    t = threading.Thread(target=ms._run_images, daemon=True)
    t.start()
    for _ in range(100):
        if not ms.is_pending("readimg", "ok") and not ms.is_pending("readimg", "mal"):
            break
        import time
        time.sleep(0.05)
    assert db.get_message("ok")["image_ocr"] == "Hola"
    assert db.is_message_image_failed("mal") and "sin cuota" in ms.get_error("readimg", "mal")


# ─── proveedores con visión ──────────────────────────────────────────────────

def _svc(**keys):
    cfg = configparser.ConfigParser()
    cfg.read_dict({"IA": {"provider": keys.pop("provider", "Gemini"), "enabled": "true"},
                   **{k.upper(): {"api_key": v} for k, v in keys.items()}})
    return AIService(cfg)


def test_proveedores_saltan_los_sin_clave():
    assert _svc().vision_providers() == []
    assert _svc(groq="gsk_x", openai="sk-x").vision_providers() == ["openai", "groq"]
    assert _svc(provider="Groq", groq="gsk_x", gemini="k").vision_providers()[0] == "groq"


def test_modelo_con_vision_por_defecto():
    s = _svc(groq="k", openai="k")
    assert s._vision_model("openai") == "gpt-4o-mini"
    assert "llama-4" in s._vision_model("groq")


def test_fallback_entre_proveedores(monkeypatch):
    s = _svc(gemini="k", openai="k")
    calls = []

    def fake(provider, prompt, data, mime, timeout):
        calls.append(provider)
        return "Error: cuota" if provider == "gemini" else "DESCRIPCION: ok\nTEXTO: NINGUNO"
    monkeypatch.setattr(s, "_vision_request", fake)
    assert s.generate_vision_completion("p", b"x").startswith("DESCRIPCION")
    assert calls == ["gemini", "openai"]


def test_sin_clave_devuelve_error():
    out = _svc().generate_vision_completion("p", b"x")
    assert AIService.is_error_response(out)
