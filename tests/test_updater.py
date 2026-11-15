"""Actualizaciones automáticas: comparación semver, elección del asset y parseo de la respuesta de GitHub (sin red)."""
import hashlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest

from app import updater


def _asset(name, size=100):
    return {"name": name, "size": size, "browser_download_url": f"https://github.com/o/r/releases/download/x/{name}"}


def _release(tag, assets, body="Notas", draft=False, prerelease=False):
    return {"tag_name": tag, "body": body, "html_url": f"https://github.com/o/r/releases/tag/{tag}",
            "draft": draft, "prerelease": prerelease, "assets": assets}


def test_comparacion_de_versiones():
    assert updater.is_newer("1.1.0", "1.0.0")
    assert updater.is_newer("1.10.0", "1.9.9")        # no es comparación de texto
    assert updater.is_newer("pc-v2.0.0", "1.9.9")
    assert not updater.is_newer("1.0.0", "1.0.0")
    assert not updater.is_newer("0.9.9", "1.0.0")
    assert updater.is_newer("1.0.0", "1.0.0-beta.2")   # la final gana a la pre-release
    assert updater.is_newer("1.0.0-beta.2", "1.0.0-beta.1")
    assert updater.is_newer("1.0.1", "1.0")
    assert not updater.is_newer("basura", "1.0.0")


def test_eleccion_de_asset_y_sha():
    assets = [_asset("AdvancedWhatsapp-Android-0.5.0.apk"), _asset("AdvancedWhatsapp-Setup-1.1.0.exe"),
              _asset("AdvancedWhatsapp-Setup-1.1.0.exe.sha256")]
    main, sha = updater.pick_asset(assets)
    assert main["name"] == "AdvancedWhatsapp-Setup-1.1.0.exe"
    assert sha["name"].endswith(".sha256")
    assert updater.pick_asset([_asset("otra-cosa.zip")]) == (None, None)


def test_select_release_prefiere_la_mas_nueva_y_filtra_por_tag():
    rels = [
        _release("android-v0.5.0", [_asset("AdvancedWhatsapp-Android-0.5.0.apk")]),
        _release("pc-v1.1.0", [_asset("AdvancedWhatsapp-Setup-1.1.0.exe", 555),
                               _asset("AdvancedWhatsapp-Setup-1.1.0.exe.sha256")], body="Novedades"),
        _release("pc-v1.2.0", [_asset("AdvancedWhatsapp-Setup-1.2.0.exe")], draft=True),
        _release("pc-v1.3.0", [_asset("AdvancedWhatsapp-Setup-1.3.0.exe")], prerelease=True),
        _release("pc-v1.0.0", [_asset("AdvancedWhatsapp-Setup-1.0.0.exe")]),
        _release("pc-v1.0.5", []),   # sin asset: se ignora
    ]
    info = updater.select_release(rels, "1.0.0")
    assert info.version == "1.1.0" and info.tag == "pc-v1.1.0"
    assert info.asset_size == 555 and info.notes == "Novedades"
    assert info.sha256_url.endswith(".sha256")
    assert updater.select_release(rels, "1.1.0") is None
    assert updater.select_release([], "1.0.0") is None
    assert updater.select_release(None, "1.0.0") is None


def test_select_release_para_android():
    import re
    pat = re.compile(r"^AdvancedWhatsapp-Android-(\d+\.\d+\.\d+)\.apk$")
    rels = [_release("android-v0.5.0", [_asset("AdvancedWhatsapp-Android-0.5.0.apk")]),
            _release("pc-v9.0.0", [_asset("AdvancedWhatsapp-Setup-9.0.0.exe")])]
    info = updater.select_release(rels, "0.4.0", prefix="android-v", pattern=pat)
    assert info.version == "0.5.0"


def test_parse_sha256_y_repo():
    h = hashlib.sha256(b"x").hexdigest()
    assert updater.parse_sha256(f"{h.upper()}  AdvancedWhatsapp-Setup-1.1.0.exe\n") == h
    assert updater.parse_sha256("nada") == ""
    assert updater.normalize_repo("https://github.com/elulikk/advanced-whatsapp.git") == "elulikk/advanced-whatsapp"
    assert updater.normalize_repo("a b") == ""


def test_descarga_verifica_tamano_y_sha(tmp_path, monkeypatch):
    data = b"instalador" * 100
    monkeypatch.setattr(updater, "updates_dir", lambda: str(tmp_path))

    class Resp:
        def __init__(self, b): self.b, self.headers = b, {"Content-Length": str(len(b))}
        def read(self, n=-1):
            out, self.b = self.b[:n], self.b[n:]
            return out
        def __enter__(self): return self
        def __exit__(self, *a): pass

    sha = hashlib.sha256(data).hexdigest()
    def fake_open(req, timeout=0):
        return Resp(data)
    monkeypatch.setattr(updater.urllib.request, "urlopen", fake_open)
    monkeypatch.setattr(updater, "_get", lambda url, timeout=15: f"{sha}  x.exe".encode())

    info = updater.UpdateInfo("1.1.0", "pc-v1.1.0", "", "", "AdvancedWhatsapp-Setup-1.1.0.exe", "http://x", len(data), "http://x.sha256")
    path = updater.download_installer(info)
    assert os.path.getsize(path) == len(data)

    # SHA que no coincide -> error y no queda el .part
    monkeypatch.setattr(updater, "_get", lambda url, timeout=15: b"0" * 64)
    os.remove(path)
    with pytest.raises(updater.UpdateError):
        updater.download_installer(info)
    assert not os.listdir(tmp_path)

    # Tamaño distinto
    info.sha256_url = ""
    info.asset_size = len(data) + 1
    with pytest.raises(updater.UpdateError):
        updater.download_installer(info)
