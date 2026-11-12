"""Copias .awbackup y sincronización: cifrado (con vector de prueba compartido con Kotlin), merge por clave y restauración."""
import io
import json
import os
import sqlite3
import sys
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest

import app.db as db
from app import awcrypto, backup, tags

# ── Vector de prueba: lo verifica igual android/.../BackupFormatTest.kt ──
VECTOR_PASSWORD = "contraseña-ñ-123"
VECTOR_PLAINTEXT = "Advanced WhatsApp ✓ backup".encode("utf-8")
VECTOR_SALT = bytes(range(16))
VECTOR_NONCE = bytes(range(16, 24))
VECTOR_HEX = ("4157424b0101000003e8000102030405060708090a0b0c0d0e0f10111213141516170000002cbcda0fae90a062ee1f27a2c91a10f1ff"
              "e11eb2b8a0bd594fb1375ba44afff445cf0db05d1b0ecd043614bce6")
VECTOR_VALUE = {"b": [1, True, None], "a": "ñ\"\n\x01é"}
VECTOR_CANONICAL = '{"a":"ñ\\"\\n\\u0001é","b":[1,true,null]}'
VECTOR_VALUE_HASH = "669c877d6c75f59b6676e7084af5ade4de111389"


def test_vector_cifrado_determinista():
    blob = awcrypto.encrypt_bytes(VECTOR_PLAINTEXT, VECTOR_PASSWORD, iterations=1000, salt=VECTOR_SALT,
                                  nonce_prefix=VECTOR_NONCE)
    assert blob.hex() == VECTOR_HEX
    assert awcrypto.decrypt_bytes(bytes.fromhex(VECTOR_HEX), VECTOR_PASSWORD) == VECTOR_PLAINTEXT


def test_vector_canonico_y_hash():
    assert backup.canonical(VECTOR_VALUE) == VECTOR_CANONICAL
    assert backup.value_hash(VECTOR_VALUE) == VECTOR_VALUE_HASH


def test_cifrado_ida_y_vuelta_varios_trozos():
    data = os.urandom(awcrypto.CHUNK * 2 + 12345)
    blob = awcrypto.encrypt_bytes(data, "clave", iterations=1000)
    assert awcrypto.decrypt_bytes(blob, "clave") == data
    assert awcrypto.decrypt_bytes(awcrypto.encrypt_bytes(b"", "clave", iterations=1000), "clave") == b""


def test_contrasenia_incorrecta_y_manipulacion():
    blob = awcrypto.encrypt_bytes(b"secreto" * 100, "buena", iterations=1000)
    with pytest.raises(awcrypto.WrongPasswordError):
        awcrypto.decrypt_bytes(blob, "mala")
    tampered = bytearray(blob)
    tampered[-5] ^= 1
    with pytest.raises(awcrypto.WrongPasswordError):
        awcrypto.decrypt_bytes(bytes(tampered), "buena")
    with pytest.raises(awcrypto.BadFormatError):
        awcrypto.decrypt_bytes(b"no es un backup", "buena")


def test_truncado_se_detecta():
    data = os.urandom(awcrypto.CHUNK + 10)
    blob = awcrypto.encrypt_bytes(data, "k", iterations=1000)
    # cortar el último trozo completo: el anterior ya no figura como «último» y falla la autenticación
    first_len = 4 + int.from_bytes(blob[awcrypto.HEADER_LEN:awcrypto.HEADER_LEN + 4], "big")
    cut = blob[:awcrypto.HEADER_LEN + first_len]
    with pytest.raises(awcrypto.BackupError):
        awcrypto.decrypt_bytes(cut, "k")
    with pytest.raises(awcrypto.BackupError):
        awcrypto.decrypt_bytes(blob[:-3], "k")


# ── Merge por clave (puro) ──

def it(v, t):
    return {"v": v, "t": t}


def tomb(t):
    return {"t": t, "d": 1}


def test_gana_el_ultimo_cambio():
    local = {"a": it("x", 10), "b": it("y", 50), "c": it("z", 10)}
    remote = {"a": it("nuevo", 20), "b": it("viejo", 40), "d": it("solo-remoto", 5)}
    winners, local_wins = backup.merge_items(local, remote)
    assert set(winners) == {"a", "d"}
    assert local_wins == 2          # b (más nuevo acá) y c (solo acá)


def test_borrado_mas_nuevo_gana_y_valor_mas_nuevo_revive():
    assert "a" in backup.merge_items({"a": it("x", 10)}, {"a": tomb(20)})[0]
    assert "a" not in backup.merge_items({"a": tomb(30)}, {"a": it("x", 20)})[0]
    assert "a" in backup.merge_items({"a": tomb(10)}, {"a": it("x", 20)})[0]


def test_empate_converge_en_ambos_lados():
    a, b = {"k": it("uno", 7)}, {"k": it("dos", 7)}
    win_a, _ = backup.merge_items(a, b)      # lo que A toma de B
    win_b, _ = backup.merge_items(b, a)      # lo que B toma de A
    # exactamente uno de los dos cambia: ambos quedan con el mismo valor
    assert len(win_a) + len(win_b) == 1
    # un valor le gana a un borrado en empate
    assert "k" in backup.merge_items({"k": tomb(7)}, {"k": it("x", 7)})[0]
    assert "k" not in backup.merge_items({"k": it("x", 7)}, {"k": tomb(7)})[0]
    # iguales: nadie cambia
    assert backup.merge_items({"k": it("x", 7)}, {"k": it("x", 7)}) == ({}, 0)


# ── Con base de datos ──

@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "a.db"))
    import app.backup as b
    monkeypatch.setattr(b, "DATA_DIR", str(tmp_path))
    from app import stickers
    monkeypatch.setattr(stickers, "STICKERS_DIR", str(tmp_path / "stk"))
    stickers._cache["key"] = None
    tags._invalidate()
    db.init_db()
    yield tmp_path
    tags._invalidate()


def _set_name(jid, name, notes=None):
    with db.db_conn() as conn:
        conn.execute("INSERT OR IGNORE INTO chats (jid, name) VALUES (?, 'x')", (jid,))
        conn.execute("UPDATE chats SET custom_name = ?, notes = ? WHERE jid = ?", (name, notes, jid))


def test_local_items_detecta_cambios_y_borrados(env):
    sec = backup.get_sections()[0]
    names = next(s for s in backup.get_sections() if s.name == "chat_names")
    _set_name("1@s.whatsapp.net", "Ana")
    first = backup.local_items(names)
    assert first["1@s.whatsapp.net"]["v"] == "Ana" and first["1@s.whatsapp.net"]["t"] == backup.BASELINE_T
    again = backup.local_items(names)
    assert again == first                      # sin cambios no se mueve el t
    _set_name("1@s.whatsapp.net", "Ana María")
    changed = backup.local_items(names)["1@s.whatsapp.net"]
    assert changed["v"] == "Ana María" and changed["t"] > backup.BASELINE_T
    _set_name("1@s.whatsapp.net", None)
    gone = backup.local_items(names)["1@s.whatsapp.net"]
    assert gone.get("d") == 1 and gone["t"] >= changed["t"]
    assert sec is not None


def test_apply_remote_respeta_lo_mas_nuevo(env):
    names = next(s for s in backup.get_sections() if s.name == "chat_names")
    _set_name("1@s.whatsapp.net", "Local")
    _set_name("2@s.whatsapp.net", "Dos")
    backup.local_items(names)
    _set_name("1@s.whatsapp.net", "Local editado")
    local_t = backup.local_items(names)["1@s.whatsapp.net"]["t"]
    remote = {"1@s.whatsapp.net": it("Remoto viejo", local_t - 1000), "2@s.whatsapp.net": it("Dos nuevo", local_t + 1000),
              "9@s.whatsapp.net": it("de un chat que no existe", local_t + 1000)}
    rep = backup.apply_remote(names, remote)
    assert rep["applied"] == 1 and rep["local_wins"] >= 1
    assert db.get_chat_custom_name("1@s.whatsapp.net") == "Local editado"
    assert db.get_chat_custom_name("2@s.whatsapp.net") == "Dos nuevo"
    # lo aplicado no cuenta como cambio local (si no, rebotaría de vuelta)
    assert backup.local_items(names)["2@s.whatsapp.net"]["t"] == local_t + 1000


def test_tags_y_asignaciones_por_nombre(env):
    sec = next(s for s in backup.get_sections() if s.name == "tags")
    tid = tags.create_tag({"name": "VIP", "color": "#112233"})
    _set_name("1@s.whatsapp.net", None)
    tags.assign_tag("1@s.whatsapp.net", tid)
    items = backup.local_items(sec)
    assert items["tag:vip"]["v"]["color"] == "#112233"
    assert "assign:1@s.whatsapp.net|vip" in items
    # otro equipo: la misma foto aplicada sobre una base vacía
    other = {k: dict(v, t=v["t"] + 5000) for k, v in items.items()}
    other["tag:vip"]["v"] = dict(other["tag:vip"]["v"], color="#445566")
    backup.apply_remote(sec, other)
    vip = next(t for t in tags.list_tags() if t["name"] == "VIP")
    assert vip["color"] == "#445566"
    assert len([t for t in tags.list_tags() if t["name"].lower() == "vip"]) == 1


def test_backup_ida_y_vuelta_merge_y_reemplazo(env, tmp_path):
    db.save_message("m1", "5@s.whatsapp.net", "5@s.whatsapp.net", "Eva", False, "hola", 1000)
    _set_name("5@s.whatsapp.net", "Eva querida", "nota")
    path = str(tmp_path / "copia.awbackup")
    res = backup.create_backup(path, "pw", include_secrets=False)
    assert res["size"] > 0 and awcrypto.is_container(path)
    info = backup.inspect_backup(path, "pw")
    assert info["format"] == "awbackup" and info["device"]["platform"] == "pc" and info["has_db"]
    assert "secrets" not in info["sections"]          # las claves de IA no van salvo que se elijan
    with pytest.raises(awcrypto.WrongPasswordError):
        backup.inspect_backup(path, "otra")

    # después de la copia: mensaje nuevo y nombre editado más nuevo (no deben pisarse al restaurar con merge)
    db.save_message("m2", "5@s.whatsapp.net", "5@s.whatsapp.net", "Eva", False, "más nuevo", 2000)
    _set_name("5@s.whatsapp.net", "Eva editada después", "nota")
    rep = backup.restore_backup(path, "pw", mode="merge")
    assert rep["db"]["messages"] == 0
    assert {m["id"] for m in db.get_messages("5@s.whatsapp.net")} == {"m1", "m2"}
    assert db.get_chat_custom_name("5@s.whatsapp.net") == "Eva editada después"

    # mensajes borrados y reinstalación: el merge los vuelve a traer
    with db.db_conn() as conn:
        conn.execute("DELETE FROM messages WHERE id = 'm1'")
    rep = backup.restore_backup(path, "pw", mode="merge")
    assert rep["db"]["messages"] == 1
    assert {m["id"] for m in db.get_messages("5@s.whatsapp.net")} == {"m1", "m2"}

    # reemplazo total: queda igual a la copia (m2 desaparece, el nombre vuelve al de la copia)
    backup.restore_backup(path, "pw", mode="replace")
    assert {m["id"] for m in db.get_messages("5@s.whatsapp.net")} == {"m1"}
    assert db.get_chat_custom_name("5@s.whatsapp.net") == "Eva querida"


def test_backup_con_claves_de_ia_solo_si_se_pide(env, tmp_path, monkeypatch):
    from app import config as appconfig
    cfg_path = str(tmp_path / "config.ini")
    with open(cfg_path, "w", encoding="utf-8") as f:
        f.write("[GEMINI]\napi_key = AIza-secreta\nmodel = gemini-2.5-flash\n[GENERAL]\nnotify_sound = false\n")
    import app.backup_sections as bs
    monkeypatch.setattr(bs, "CONFIG_PATH", cfg_path)
    p1, p2 = str(tmp_path / "sin.awbackup"), str(tmp_path / "con.awbackup")
    backup.create_backup(p1, "pw", include_secrets=False, include_db=False)
    backup.create_backup(p2, "pw", include_secrets=True, include_db=False)

    def raw(path):
        buf = io.BytesIO()
        with open(path, "rb") as f:
            awcrypto.decrypt_stream(f, buf, "pw")
        return zipfile.ZipFile(io.BytesIO(buf.getvalue()))

    z1, z2 = raw(p1), raw(p2)
    assert "sections/secrets.json" not in z1.namelist()
    assert b"AIza-secreta" not in b"".join(z1.read(n) for n in z1.namelist())
    assert json.loads(z2.read("sections/secrets.json"))["items"]["ai.gemini.key"]["v"] == "AIza-secreta"
    cfgs = json.loads(z1.read("sections/config.json"))["items"]
    assert "GEMINI.api_key" not in cfgs and cfgs["GENERAL.notify_sound"]["v"] == "false"
    assert appconfig is not None


def test_stickers_con_archivo_se_aplican_y_sin_archivo_esperan(env):
    import hashlib
    from app import stickers
    sec = next(s for s in backup.get_sections() if s.name == "stickers")
    data = b"RIFFxxxxWEBPfake-contenido"
    sid = hashlib.sha1(data).hexdigest()[:16]
    remote = {f"sticker:{sid}": it({"pack": "Mis stickers", "fav": True, "animated": False, "added": 5}, 99)}
    assert backup.apply_remote(sec, remote)["applied"] == 0       # el archivo todavía no llegó
    sec.import_file(f"{sid}.webp", b"otro contenido")             # hash que no coincide: se descarta
    assert not os.path.exists(os.path.join(stickers.STICKERS_DIR, f"{sid}.webp"))
    sec.import_file(f"{sid}.webp", data)
    assert backup.apply_remote(sec, remote)["applied"] == 1
    assert stickers.get(sid)["fav"] is True
    assert sec.files([f"sticker:{sid}"])[0][0] == f"{sid}.webp"


def test_rotacion_y_vencimiento(tmp_path):
    for i in range(5):
        p = tmp_path / f"{backup.AUTO_PREFIX}2026010{i}-000000.awbackup"
        p.write_bytes(b"x")
        os.utime(p, (1000 + i, 1000 + i))
    (tmp_path / "manual.awbackup").write_bytes(b"x")            # las manuales no se tocan
    assert backup.rotate_backups(str(tmp_path), 2) == 3
    left = sorted(os.listdir(tmp_path))
    assert left == ["AdvancedWhatsapp-auto-20260103-000000.awbackup", "AdvancedWhatsapp-auto-20260104-000000.awbackup",
                    "manual.awbackup"]
    assert backup.auto_backup_due(0, 7) and not backup.auto_backup_due(1_000_000, 7, now=1_000_000 + 3 * 86400)
    assert backup.auto_backup_due(1_000_000, 7, now=1_000_000 + 7 * 86400)


def test_documentos_de_sync_no_se_guardan_como_mensajes(env):
    assert db.is_sync_doc("document", "awsync-ab12cd34-1700000000.awsync")
    assert not db.is_sync_doc("document", "informe.pdf")
    assert db.save_message("s1", "1@s.whatsapp.net", "me", "Yo", True, "awsync-x-1.awsync", 1, media_type="document") is False
    assert db.get_message("s1") is None
