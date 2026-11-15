"""Exportar chat: armado de TXT/HTML/PDF, filtros de fechas, paginación y opciones."""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest

import app.db as db
from app import chat_export as ce

A = "111@s.whatsapp.net"
DAY = 86400
T0 = 1_700_000_000  # 14/11/2023


@pytest.fixture(autouse=True)
def tmpdb(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setattr(db, "_load_lid_phone_map", lambda: {})
    db._early_status.clear()
    db.init_db()
    db.get_or_create_chat(A, "Ana")
    yield
    db._early_status.clear()


def _save(i, ts, text="hola", from_me=False, **kw):
    return db.save_message(i, A, "me" if from_me else A, "Ana", from_me, text, ts, **kw)


def _read(path):
    with open(path, encoding="utf-8", newline="") as f:
        return f.read()


def test_range_for():
    assert ce.range_for("all") == (None, None)
    since, until = ce.range_for("month", now=1_000_000)
    assert since == 1_000_000 - 30 * DAY and until is None
    since, until = ce.range_for("custom", desde=T0, hasta=T0 + DAY)
    assert since == T0 and until == T0 + 2 * DAY - 1


def test_iter_pages_orden_y_filtro():
    for i in range(25):
        _save(f"m{i}", T0 + i * 60, f"t{i}")
    _save("otro", T0, "x")  # mismo timestamp que m0: no se pierde ni se repite
    pages = list(ce.iter_pages(A, page_size=10))
    ids = [m["id"] for p in pages for m in p]
    assert len(pages) == 3 and len(ids) == 26 and len(set(ids)) == 26
    stamps = [m["timestamp"] for p in pages for m in p]
    assert stamps == sorted(stamps)
    assert ce.count_messages(A, T0 + 10 * 60, T0 + 12 * 60) == 3
    got = [m["id"] for p in ce.iter_pages(A, T0 + 10 * 60, T0 + 12 * 60) for m in p]
    assert got == ["m10", "m11", "m12"]


def test_txt_formato_whatsapp(tmp_path):
    _save("a", T0, "Hola\nsegunda línea")
    _save("b", T0 + 60, "Chau", from_me=True)
    dest = str(tmp_path / "chat.txt")
    res = ce.export_chat(A, dest, ce.ExportOptions(fmt="txt"))
    txt = _read(dest)
    assert res.messages == 2 and not os.path.exists(dest + ".part")
    d = time.strftime("%d/%m/%y %H:%M", time.localtime(T0))
    assert f"{d} - Ana: Hola\r\nsegunda línea\r\n" in txt
    assert " - Yo: Chau" in txt


def test_txt_multimedia_adjunta_u_omitida(tmp_path):
    img = tmp_path / "foto.jpg"
    img.write_bytes(b"\xff\xd8x")
    _save("i1", T0, "mirá", media_type="image", media_path=str(img))
    _save("i2", T0 + 1, "", media_type="image")
    dest = str(tmp_path / "c.txt")
    ce.export_chat(A, dest, ce.ExportOptions(fmt="txt"))
    txt = _read(dest)
    assert "foto.jpg (archivo adjunto)" in txt and "<Multimedia omitida>" in txt
    ce.export_chat(A, dest, ce.ExportOptions(fmt="txt", include_media=False))
    assert "(archivo adjunto)" not in _read(dest)


def test_transcripciones_y_resumenes_opcionales(tmp_path):
    _save("v", T0, "", media_type="audio")
    db.set_message_transcript("v", "texto del audio")
    db.set_message_summary("v", "resumen del audio")
    dest = str(tmp_path / "c.txt")
    ce.export_chat(A, dest, ce.ExportOptions(fmt="txt"))
    t = _read(dest)
    assert "Transcripción: texto del audio" in t and "Resumen IA" not in t
    ce.export_chat(A, dest, ce.ExportOptions(fmt="txt", include_transcripts=False, include_summaries=True))
    t = _read(dest)
    assert "Transcripción" not in t and "Resumen IA: resumen del audio" in t


def test_eliminados_y_ediciones(tmp_path):
    _save("d", T0, "secreto")
    _save("e", T0 + 1, "versión 1")
    db.apply_message_edit("e", "versión 2")
    db.revoke_message_local("d")
    dest = str(tmp_path / "c.txt")
    ce.export_chat(A, dest, ce.ExportOptions(fmt="txt"))
    t = _read(dest)
    assert "secreto" in t and "Eliminado por el remitente" in t
    assert "Versión anterior 1: versión 1" in t and "versión 2" in t
    ce.export_chat(A, dest, ce.ExportOptions(fmt="txt", include_deleted=False))
    t = _read(dest)
    assert "secreto" not in t and "Mensaje eliminado" in t and "Versión anterior" not in t


def test_html_escapa_y_agrupa_por_dia(tmp_path):
    _save("a", T0, "<script>alert(1)</script> & más")
    _save("b", T0 + 2 * DAY, "otro día", from_me=True)
    dest = str(tmp_path / "c.html")
    ce.export_chat(A, dest, ce.ExportOptions(fmt="html"))
    h = _read(dest)
    assert "<script>alert" not in h and "&lt;script&gt;" in h
    assert h.count('class="day"') == 2 and 'class="b me"' in h
    assert h.rstrip().endswith("</html>")


def test_html_imagen_incrustada_o_en_carpeta(tmp_path):
    img = tmp_path / "f.png"
    img.write_bytes(b"\x89PNG-demo")
    _save("i", T0, "foto", media_type="image", media_path=str(img))
    dest = str(tmp_path / "c.html")
    ce.export_chat(A, dest, ce.ExportOptions(fmt="html", embed_images=True))
    assert "data:image/png;base64," in _read(dest) and not os.path.isdir(str(tmp_path / "c_archivos"))
    ce.export_chat(A, dest, ce.ExportOptions(fmt="html", embed_images=False))
    assert "c_archivos/" in _read(dest)
    assert len(os.listdir(tmp_path / "c_archivos")) == 1


def test_descargar_lo_que_falta_solo_si_se_pide(tmp_path):
    real = tmp_path / "bajado.jpg"
    real.write_bytes(b"x")
    _save("i", T0, "", media_type="image", media_raw="{}")
    calls = []

    def dl(msg):
        calls.append(msg["id"])
        return str(real)

    dest = str(tmp_path / "c.txt")
    res = ce.export_chat(A, dest, ce.ExportOptions(fmt="txt"), downloader=dl)
    assert calls == [] and res.media_missing == 1
    res = ce.export_chat(A, dest, ce.ExportOptions(fmt="txt", download_missing=True), downloader=dl)
    assert calls == ["i"] and res.media_files == 1 and "bajado.jpg" in _read(dest)


def test_progreso_y_cancelacion(tmp_path):
    for i in range(1200):
        _save(f"m{i}", T0 + i, "x")
    seen = []
    dest = str(tmp_path / "c.txt")
    res = ce.export_chat(A, dest, ce.ExportOptions(fmt="txt"), progress=lambda d, t: seen.append((d, t)))
    assert seen[-1] == (1200, 1200) and len(seen) == 3 and res.messages == 1200
    os.remove(dest)
    n = {"c": 0}

    def stop():
        n["c"] += 1
        return n["c"] > 1

    res = ce.export_chat(A, dest, ce.ExportOptions(fmt="txt"), cancelled=stop)
    assert res.cancelled and not os.path.exists(dest) and not os.path.exists(dest + ".part")


def test_rango_de_fechas_en_el_archivo(tmp_path):
    for i in range(5):
        _save(f"m{i}", T0 + i * DAY, f"dia{i}")
    dest = str(tmp_path / "c.txt")
    res = ce.export_chat(A, dest, ce.ExportOptions(fmt="txt", since=T0 + DAY, until=T0 + 2 * DAY))
    t = _read(dest)
    assert res.messages == 2 and "dia1" in t and "dia2" in t and "dia0" not in t and "dia3" not in t


def test_pdf_basico(tmp_path):
    pytest.importorskip("PySide6")
    from PySide6.QtCore import QCoreApplication
    from PySide6.QtGui import QGuiApplication
    inst = QCoreApplication.instance()
    if inst is not None and not isinstance(inst, QGuiApplication):
        pytest.skip("otro test dejó una QCoreApplication sin GUI; el PDF necesita fuentes")
    app = inst or QGuiApplication([])
    for i in range(80):
        _save(f"m{i}", T0 + i * 3600, ("mensaje largo " * 40) if i % 7 == 0 else f"texto {i}", from_me=i % 2 == 0)
    dest = str(tmp_path / "c.pdf")
    res = ce.export_chat(A, dest, ce.ExportOptions(fmt="pdf"))
    data = open(dest, "rb").read()
    assert res.messages == 80 and data.startswith(b"%PDF") and len(data) > 2000
    assert not os.path.exists(dest + ".part")
    del app


def test_formato_invalido(tmp_path):
    with pytest.raises(ValueError):
        ce.export_chat(A, str(tmp_path / "x"), ce.ExportOptions(fmt="docx"))
