"""
Advanced WhatsApp - Actualizaciones automáticas
===============================================
Busca versiones nuevas en GitHub Releases, baja el instalador y lo lanza en silencio.

Convención de publicación (la usa tools/publicar_release.py):
  - Un release por plataforma, con tag propio: ``pc-v1.1.0`` (PC) y ``android-v0.5.0`` (Android).
  - PC:      asset ``AdvancedWhatsapp-Setup-<versión>.exe`` (+ opcional ``AdvancedWhatsapp-Setup-<versión>.exe.sha256``).
  - Android: asset ``AdvancedWhatsapp-Android-<versión>.apk`` (+ opcional ``.apk.sha256``).
Esta parte no usa Qt: es la lógica pura (comparar versiones, elegir asset, parsear la respuesta, bajar y verificar).
"""

import hashlib
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Callable, List, Optional, Tuple

DEFAULT_REPO = "elulikk/advanced-whatsapp"
TAG_PREFIX = "pc-v"
ASSET_RE = re.compile(r"^AdvancedWhatsapp-Setup-(\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.\-+]*)?)\.exe$")
CHECK_INTERVAL_S = 12 * 3600
STARTUP_DELAY_MS = 20000

# Silencioso: sin ventanas ni mensajes, sin reiniciar Windows. El [Run] del .iss reabre la app al terminar.
INSTALLER_ARGS = ["/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"]


class UpdateError(Exception):
    """Error con mensaje listo para mostrar al usuario."""


@dataclass
class UpdateInfo:
    version: str
    tag: str
    notes: str
    page_url: str
    asset_name: str
    asset_url: str
    asset_size: int
    sha256_url: str = ""


def parse_version(text: str) -> Optional[Tuple[int, int, int, tuple]]:
    """'pc-v1.2.3' o 'v1.2.3-beta.1' -> tupla comparable. Sin pre-release gana sobre cualquier pre-release."""
    if not text:
        return None
    m = re.search(r"(\d+)\.(\d+)(?:\.(\d+))?(?:-([0-9A-Za-z.\-]+))?", str(text))
    if not m:
        return None
    pre = m.group(4)
    if pre:
        parts = tuple((0, int(p), "") if p.isdigit() else (1, 0, p) for p in pre.split("."))
    else:
        parts = ((2, 0, ""),)
    return int(m.group(1)), int(m.group(2)), int(m.group(3) or 0), parts


def is_newer(candidate: str, current: str) -> bool:
    a, b = parse_version(candidate), parse_version(current)
    if a is None or b is None:
        return False
    return a > b


def pick_asset(assets: List[dict], pattern=ASSET_RE) -> Tuple[Optional[dict], Optional[dict]]:
    """Devuelve (asset del instalador, asset .sha256 o None) buscando por nombre."""
    main = next((a for a in assets if pattern.match(str(a.get("name", "")))), None)
    if not main:
        return None, None
    want = main["name"].lower() + ".sha256"
    sha = next((a for a in assets if str(a.get("name", "")).lower() == want), None)
    return main, sha


def select_release(releases: List[dict], current: str, prefix: str = TAG_PREFIX,
                   pattern=ASSET_RE) -> Optional[UpdateInfo]:
    """
    De la lista de releases de GitHub elige la versión más nueva (tag que empieza con `prefix` y con el asset
    esperado) y la devuelve solo si es mayor que `current`. Ignora borradores y pre-releases.
    """
    best: Optional[UpdateInfo] = None
    for rel in releases or []:
        if not isinstance(rel, dict) or rel.get("draft") or rel.get("prerelease"):
            continue
        tag = str(rel.get("tag_name", ""))
        if prefix and not tag.startswith(prefix):
            continue
        main, sha = pick_asset(rel.get("assets") or [], pattern)
        if not main or not main.get("browser_download_url"):
            continue
        version = pattern.match(main["name"]).group(1)
        if parse_version(version) is None:
            continue
        if best is None or is_newer(version, best.version):
            best = UpdateInfo(
                version=version, tag=tag, notes=str(rel.get("body") or "").strip(),
                page_url=str(rel.get("html_url") or ""), asset_name=main["name"],
                asset_url=main["browser_download_url"], asset_size=int(main.get("size") or 0),
                sha256_url=(sha or {}).get("browser_download_url", ""))
    if best and is_newer(best.version, current):
        return best
    return None


def parse_sha256(text: str) -> str:
    """Primer hash de 64 caracteres hex de un .sha256 (con o sin nombre de archivo al lado)."""
    m = re.search(r"\b([0-9a-fA-F]{64})\b", text or "")
    return m.group(1).lower() if m else ""


def normalize_repo(repo: str) -> str:
    """Acepta 'dueño/repo' o la URL de GitHub; devuelve 'dueño/repo' o '' si no sirve."""
    r = (repo or "").strip()
    r = re.sub(r"^https?://github\.com/", "", r).strip("/")
    r = re.sub(r"\.git$", "", r)
    return r if re.fullmatch(r"[\w.\-]+/[\w.\-]+", r) else ""


def _get(url: str, timeout: int = 15) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "AdvancedWhatsapp-Updater",
                                               "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def fetch_releases(repo: str) -> List[dict]:
    repo = normalize_repo(repo)
    if not repo:
        raise UpdateError("El repositorio de actualizaciones no es válido (formato dueño/repo).")
    try:
        data = json.loads(_get(f"https://api.github.com/repos/{repo}/releases?per_page=30").decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise UpdateError("No se encontraron releases: para buscar actualizaciones hace falta que el repositorio "
                              f"{repo} sea público (o corregí el dueño/repo en config.ini).")
        if e.code in (403, 429):
            raise UpdateError("GitHub limitó las consultas por ahora. Probá de nuevo en un rato.")
        raise UpdateError(f"GitHub respondió con el error {e.code}.")
    except (urllib.error.URLError, TimeoutError, OSError):
        raise UpdateError("No se pudo conectar con GitHub. Revisá tu conexión a internet.")
    except ValueError:
        raise UpdateError("La respuesta de GitHub no se pudo leer.")
    return data if isinstance(data, list) else []


def check_for_update(repo: str, current: str) -> Optional[UpdateInfo]:
    return select_release(fetch_releases(repo), current)


def updates_dir() -> str:
    from app.config import DATA_DIR
    d = os.path.join(DATA_DIR, "updates")
    os.makedirs(d, exist_ok=True)
    return d


def sha256_of(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _silent_remove(path: str):
    try:
        os.remove(path)
    except OSError:
        pass


def download_installer(info: UpdateInfo, progress: Callable[[int, int], None] = None,
                       cancelled: Callable[[], bool] = None) -> str:
    """Baja el Setup a ~/.magsapp/updates/, verifica tamaño y SHA-256 (si hay) y devuelve la ruta."""
    dest = os.path.join(updates_dir(), info.asset_name)
    part = dest + ".part"
    expected_sha = ""
    if info.sha256_url:
        try:
            expected_sha = parse_sha256(_get(info.sha256_url).decode("utf-8", "replace"))
        except Exception:
            expected_sha = ""
        if not expected_sha:
            raise UpdateError("No se pudo leer el archivo de verificación (.sha256) del release.")
    try:
        req = urllib.request.Request(info.asset_url, headers={"User-Agent": "AdvancedWhatsapp-Updater"})
        with urllib.request.urlopen(req, timeout=30) as r, open(part, "wb") as out:
            total = int(r.headers.get("Content-Length") or info.asset_size or 0)
            done = 0
            while True:
                if cancelled and cancelled():
                    raise UpdateError("Descarga cancelada.")
                chunk = r.read(1 << 17)
                if not chunk:
                    break
                out.write(chunk)
                done += len(chunk)
                if progress:
                    progress(done, total)
        size = os.path.getsize(part)
        if info.asset_size and size != info.asset_size:
            raise UpdateError(f"La descarga quedó incompleta ({size} de {info.asset_size} bytes).")
        if expected_sha and sha256_of(part) != expected_sha:
            raise UpdateError("La descarga está dañada: no coincide el SHA-256 del release.")
        os.replace(part, dest)
        return dest
    except UpdateError:
        _silent_remove(part)
        raise
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        _silent_remove(part)
        raise UpdateError(f"No se pudo bajar la actualización: {e}")


def cleanup_old_installers(keep: str = ""):
    """Borra instaladores viejos de ~/.magsapp/updates/ (salvo `keep`)."""
    try:
        d = updates_dir()
        for n in os.listdir(d):
            p = os.path.join(d, n)
            if p != keep and (n.endswith(".exe") or n.endswith(".part")):
                _silent_remove(p)
    except OSError:
        pass


def launch_installer(path: str):
    """Lanza el Setup en silencio, desligado de esta app (el .iss reabre Advanced WhatsApp al terminar)."""
    flags = 0
    if sys.platform == "win32":
        flags = 0x00000008 | 0x00000200   # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    subprocess.Popen([path] + INSTALLER_ARGS, close_fds=True, creationflags=flags)
