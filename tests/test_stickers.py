import io
import os
import sys
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from PIL import Image

from app import emojis, stickers


@pytest.fixture(autouse=True)
def lib(tmp_path, monkeypatch):
    monkeypatch.setattr(stickers, "STICKERS_DIR", str(tmp_path / "stickers"))
    stickers._cache.update(key=None, data=None)
    yield tmp_path


def _img(fmt="PNG", size=(100, 60), color=(200, 30, 30, 255)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGBA", size, color).save(buf, fmt)
    return buf.getvalue()


def _gif() -> bytes:
    frames = [Image.new("RGB", (80, 80), c) for c in ("red", "green", "blue")]
    buf = io.BytesIO()
    frames[0].save(buf, "GIF", save_all=True, append_images=frames[1:], duration=100, loop=0)
    return buf.getvalue()


# ── Conversión ──
def test_convert_png_to_webp_512():
    out, animated = stickers.to_webp512(_img())
    img = Image.open(io.BytesIO(out))
    assert img.format == "WEBP" and img.size == (512, 512) and not animated
    assert img.convert("RGBA").getpixel((0, 0))[3] == 0          # fondo transparente
    assert img.convert("RGBA").getpixel((256, 256))[3] == 255    # la imagen quedó centrada


def test_convert_keeps_512_webp_untouched():
    buf = io.BytesIO()
    Image.new("RGBA", (512, 512), "blue").save(buf, "WEBP")
    assert stickers.to_webp512(buf.getvalue())[0] == buf.getvalue()


def test_convert_animated_gif_stays_animated():
    out, animated = stickers.to_webp512(_gif())
    img = Image.open(io.BytesIO(out))
    assert animated and img.size == (512, 512) and img.n_frames == 3


def test_invalid_image_is_rejected():
    assert stickers.add_bytes(b"no soy una imagen") is None


# ── Biblioteca ──
def test_add_dedup_and_index_persists():
    a = stickers.add_bytes(_img())
    b = stickers.add_bytes(_img())
    assert a["id"] == b["id"] and b["existing"] and not a.get("existing")
    assert stickers.count() == 1
    assert os.path.exists(stickers.sticker_path(a))
    stickers._cache.update(key=None, data=None)       # relee del disco
    assert [r["id"] for r in stickers.list_stickers()] == [a["id"]]
    assert not os.path.exists(stickers._index_path() + ".tmp")


def test_corrupt_index_is_treated_as_empty(lib):
    os.makedirs(stickers.STICKERS_DIR)
    with open(stickers._index_path(), "w") as f:
        f.write("{no es json")
    assert stickers.list_stickers() == []
    assert stickers.add_bytes(_img()) is not None


def test_favorites_recents_and_packs():
    a = stickers.add_bytes(_img(color=(1, 2, 3, 255)), pack="Gatos")
    b = stickers.add_bytes(_img(color=(9, 9, 9, 255)))
    assert stickers.toggle_fav(a["id"]) is True
    assert [r["id"] for r in stickers.favorites()] == [a["id"]]
    assert stickers.toggle_fav(a["id"]) is False
    assert stickers.recents() == []
    stickers.mark_used(a["id"])
    stickers.mark_used(b["id"])
    assert [r["id"] for r in stickers.recents()][0] == b["id"]
    names = {p["name"]: p["count"] for p in stickers.packs()}
    assert names == {stickers.DEFAULT_PACK: 1, "Gatos": 1}
    assert stickers.move_to_pack(b["id"], "Gatos")
    assert {p["name"]: p["count"] for p in stickers.packs()}["Gatos"] == 2


def test_delete_sticker_and_pack():
    a = stickers.add_bytes(_img(), pack="Temporal")
    path = stickers.sticker_path(a)
    assert stickers.delete_pack("Temporal") == 1
    assert not os.path.exists(path)
    assert "Temporal" not in [p["name"] for p in stickers.packs()]
    b = stickers.add_bytes(_img(color=(5, 5, 5, 255)))
    assert stickers.delete_sticker(b["id"]) and not stickers.delete_sticker(b["id"])


def test_missing_file_is_hidden():
    a = stickers.add_bytes(_img())
    os.remove(stickers.sticker_path(a))
    assert stickers.list_stickers() == []


# ── Importación ──
def test_add_files_folder_and_wastickers(lib):
    src = lib / "origen"
    src.mkdir()
    (src / "a.png").write_bytes(_img(color=(10, 20, 30, 255)))
    (src / "b.png").write_bytes(_img(color=(40, 50, 60, 255)))
    (src / "nota.txt").write_text("x")
    res = stickers.add_files([str(src)])
    assert res == {"added": 2, "duplicates": 0, "failed": 0}
    assert [p["name"] for p in stickers.packs()][-1] == "origen"

    zpath = lib / "perritos.wastickers"
    with zipfile.ZipFile(zpath, "w") as z:
        z.writestr("title.txt", "Perritos\n")
        z.writestr("tray.png", _img())
        z.writestr("1.webp", _img("WEBP", color=(70, 80, 90, 255)))
        z.writestr("2.png", _img(color=(10, 20, 30, 255)))     # repetido de la carpeta
    res = stickers.add_files([str(zpath)])
    assert res["added"] == 1 and res["duplicates"] == 1 and res["failed"] == 0
    assert "Perritos" in [p["name"] for p in stickers.packs()]


def test_add_files_reports_failures(lib):
    bad = lib / "roto.png"
    bad.write_bytes(b"basura")
    other = lib / "doc.pdf"
    other.write_bytes(b"x")
    assert stickers.add_files([str(bad), str(other)])["failed"] == 2


def test_add_from_message(lib):
    p = lib / "media_x.webp"
    p.write_bytes(_img("WEBP", size=(512, 512)))
    assert stickers.add_from_message({"media_path": str(p)}) is not None
    assert stickers.add_from_message({"media_path": str(lib / "no_existe.webp")}) is None


class _FakeGateway:
    def __init__(self, folder):
        self.folder, self.calls = folder, []

    def download_media(self, mid, mtype, raw):
        self.calls.append(mid)
        if raw == "caduco":
            return {"success": False, "error": "ya no está"}
        path = os.path.join(self.folder, f"media_{mid}.webp")
        with open(path, "wb") as f:
            f.write(_img("WEBP", size=(512, 512), color=(len(mid), 7, 7, 255)))
        return {"success": True, "media_path": path}


def test_import_from_chats(lib, monkeypatch):
    from app import db
    monkeypatch.setattr(db, "DB_PATH", str(lib / "t.db"))
    db.init_db()
    local = lib / "local.webp"
    local.write_bytes(_img("WEBP", size=(512, 512), color=(1, 1, 1, 255)))
    db.save_message("S1", "1@s.whatsapp.net", "1@s.whatsapp.net", "Ana", False, "🎨 Sticker", 100,
                    media_type="sticker", media_path=str(local))
    db.save_message("S22", "1@s.whatsapp.net", "1@s.whatsapp.net", "Ana", False, "🎨 Sticker", 101,
                    media_type="sticker", media_raw="{}")
    db.save_message("S333", "1@s.whatsapp.net", "1@s.whatsapp.net", "Ana", False, "🎨 Sticker", 102,
                    media_type="sticker", media_raw="caduco")
    db.save_message("T1", "1@s.whatsapp.net", "1@s.whatsapp.net", "Ana", False, "hola", 103)
    gw = _FakeGateway(str(lib))
    res = stickers.import_from_chats(gateway=gw)
    assert res == {"added": 2, "duplicates": 0, "failed": 0, "unavailable": 1}
    assert sorted(gw.calls) == ["S22", "S333"]
    assert db.get_message("S22")["media_path"]
    # Segunda pasada: solo reintenta el que no estaba disponible
    gw.calls.clear()
    res = stickers.import_from_chats(gateway=gw)
    assert gw.calls == ["S333"] and res["added"] == 0


# ── Emojis ──
def test_emoji_categories_and_search():
    cats = emojis.categories()
    assert len(cats) >= 8 and all(items for _n, _i, items in cats)
    assert "❤️" in emojis.search("corazón rojo")           # con acento
    assert "🍕" in emojis.search("PIZZA")
    assert emojis.search("   ") == []
    assert emojis.search("xyzxyz") == []
    assert "🐶" in emojis.search("animales")                 # por nombre de categoría


def test_recent_emojis_order_and_limit():
    assert emojis.push_recent(["a", "b", "c"], "b") == ["b", "a", "c"]
    assert len(emojis.push_recent([str(i) for i in range(40)], "z")) == emojis.MAX_RECENTS
    stickers.add_recent_emoji("😀")
    assert stickers.add_recent_emoji("🔥") == ["🔥", "😀"]
    assert stickers.recent_emojis() == ["🔥", "😀"]
