"""
Advanced WhatsApp - Biblioteca local de stickers (sin Qt)
=========================================================
Los stickers viven en ~/.magsapp/media/stickers/<id>.webp (512x512) con un índice JSON
(`index.json`, escrito de forma atómica) que guarda paquetes, favoritos, uso reciente y los
emojis recientes del selector. El id es el hash del contenido: un sticker repetido no se duplica.

Se alimenta de: un sticker recibido ("Agregar a mis stickers"), archivos .webp/.png/.jpg/.gif/.wastickers
del disco, y el historial de chats (`import_from_chats`, que baja bajo demanda los que faltan).
"""

import hashlib
import io
import json
import os
import threading
import time
import zipfile
from typing import Callable, Dict, List, Optional

from app.config import MEDIA_DIR

STICKERS_DIR = os.path.join(MEDIA_DIR, "stickers")
INDEX_NAME = "index.json"
DEFAULT_PACK = "Mis stickers"
CHATS_PACK = "De mis chats"
SIZE = 512                       # WhatsApp pide 512x512
MAX_STATIC_BYTES = 100 * 1024    # límite de WhatsApp para un sticker quieto
MAX_ANIMATED_BYTES = 500 * 1024
MAX_FRAMES = 60
IMAGE_EXTS = (".webp", ".png", ".jpg", ".jpeg", ".gif", ".bmp")
MAX_ARCHIVE_ENTRIES = 300
MAX_ENTRY_BYTES = 4 * 1024 * 1024

_lock = threading.RLock()
_cache: Dict[str, object] = {"key": None, "data": None}


# ── Índice ──
def _index_path() -> str:
    return os.path.join(STICKERS_DIR, INDEX_NAME)


def _empty() -> dict:
    return {"version": 1, "packs": [DEFAULT_PACK], "stickers": {}, "recent_emojis": [], "imported_msgs": []}


def _load() -> dict:
    """Lee el índice (con caché por fecha de modificación). Un índice roto se trata como vacío."""
    path = _index_path()
    try:
        key = (path, os.stat(path).st_mtime_ns)
    except OSError:
        return _empty()
    if _cache["key"] == key and _cache["data"] is not None:
        return _cache["data"]    # type: ignore[return-value]
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict) or not isinstance(data.get("stickers"), dict):
            raise ValueError("índice inválido")
    except (OSError, ValueError):
        return _empty()
    base = _empty()
    base.update(data)
    if DEFAULT_PACK not in base["packs"]:
        base["packs"].insert(0, DEFAULT_PACK)
    _cache["key"], _cache["data"] = key, base
    return base


def _save(data: dict):
    os.makedirs(STICKERS_DIR, exist_ok=True)
    path = _index_path()
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(tmp, path)
    try:
        _cache["key"], _cache["data"] = (path, os.stat(path).st_mtime_ns), data
    except OSError:
        _cache["key"] = None


def _write_atomic(path: str, content: bytes):
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(content)
    os.replace(tmp, path)


# ── Conversión ──
def to_webp512(data: bytes) -> tuple:
    """Convierte imagen (webp/png/jpg/gif...) a webp 512x512 con fondo transparente.
    Devuelve (bytes, animado). Un webp que ya es 512x512 se deja tal cual (conserva la animación)."""
    from PIL import Image, ImageSequence

    with Image.open(io.BytesIO(data)) as img:
        animated = bool(getattr(img, "is_animated", False)) and getattr(img, "n_frames", 1) > 1
        if img.format == "WEBP" and img.size == (SIZE, SIZE) and len(data) <= (MAX_ANIMATED_BYTES if animated else 4 * MAX_STATIC_BYTES):
            return data, animated

        def fit(frame):
            frame = frame.convert("RGBA")
            frame.thumbnail((SIZE, SIZE), Image.LANCZOS)
            canvas = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
            canvas.paste(frame, ((SIZE - frame.width) // 2, (SIZE - frame.height) // 2), frame)
            return canvas

        if animated:
            frames, durations = [], []
            for i, fr in enumerate(ImageSequence.Iterator(img)):
                if i >= MAX_FRAMES:
                    break
                durations.append(int(fr.info.get("duration", 80) or 80))
                frames.append(fit(fr))
            quality_steps = (70, 50, 30)
            out = b""
            for q in quality_steps:
                buf = io.BytesIO()
                frames[0].save(buf, "WEBP", save_all=True, append_images=frames[1:], duration=durations,
                               loop=0, quality=q, method=4)
                out = buf.getvalue()
                if len(out) <= MAX_ANIMATED_BYTES:
                    break
            return out, True

        canvas = fit(img)
        out = b""
        for q in (85, 65, 45, 30):
            buf = io.BytesIO()
            canvas.save(buf, "WEBP", quality=q, method=4)
            out = buf.getvalue()
            if len(out) <= MAX_STATIC_BYTES:
                break
        return out, False


# ── Altas ──
def add_bytes(data: bytes, pack: str = DEFAULT_PACK, fav: bool = False) -> Optional[dict]:
    """Agrega una imagen a la biblioteca. Devuelve el registro (existente si ya estaba) o None si no se pudo leer."""
    try:
        webp, animated = to_webp512(data)
    except Exception:
        return None
    sid = hashlib.sha1(webp).hexdigest()[:16]
    with _lock:
        idx = _load()
        rec = idx["stickers"].get(sid)
        if rec and os.path.exists(os.path.join(STICKERS_DIR, rec["file"])):
            rec = dict(rec, existing=True)
            return rec
        os.makedirs(STICKERS_DIR, exist_ok=True)
        fname = f"{sid}.webp"
        _write_atomic(os.path.join(STICKERS_DIR, fname), webp)
        pack = (pack or DEFAULT_PACK).strip() or DEFAULT_PACK
        if pack not in idx["packs"]:
            idx["packs"].append(pack)
        rec = {"id": sid, "file": fname, "pack": pack, "fav": bool(fav), "animated": animated,
               "added": time.time(), "used": 0, "uses": 0}
        idx["stickers"][sid] = rec
        _save(idx)
        return dict(rec)


def add_file(path: str, pack: str = DEFAULT_PACK, fav: bool = False) -> Optional[dict]:
    try:
        if os.path.getsize(path) > 32 * 1024 * 1024:
            return None
        with open(path, "rb") as f:
            return add_bytes(f.read(), pack, fav)
    except OSError:
        return None


def _pack_from_archive(zf: zipfile.ZipFile, fallback: str) -> str:
    for name in zf.namelist():
        if os.path.basename(name).lower() == "title.txt":
            try:
                title = zf.read(name).decode("utf-8", "ignore").strip().splitlines()[0].strip()
                if title:
                    return title[:60]
            except (KeyError, IndexError, RuntimeError):
                pass
    return fallback


def add_archive(path: str, pack: Optional[str] = None) -> dict:
    """Importa un .wastickers (zip con webp + title.txt + tray). Ignora el icono de bandeja."""
    res = {"added": 0, "duplicates": 0, "failed": 0}
    try:
        zf = zipfile.ZipFile(path)
    except (OSError, zipfile.BadZipFile):
        res["failed"] += 1
        return res
    with zf:
        name = pack or _pack_from_archive(zf, os.path.splitext(os.path.basename(path))[0])
        infos = [i for i in zf.infolist() if not i.is_dir()][:MAX_ARCHIVE_ENTRIES]
        for info in infos:
            base = os.path.basename(info.filename).lower()
            if not base.endswith(IMAGE_EXTS) or base.startswith("tray") or info.file_size > MAX_ENTRY_BYTES:
                continue
            try:
                rec = add_bytes(zf.read(info), name)
            except (OSError, RuntimeError, zipfile.BadZipFile):
                rec = None
            _tally(res, rec)
    return res


def _tally(res: dict, rec: Optional[dict]):
    if rec is None:
        res["failed"] += 1
    elif rec.get("existing"):
        res["duplicates"] += 1
    else:
        res["added"] += 1


def add_files(paths: List[str], pack: Optional[str] = None,
              progress: Optional[Callable[[int, int], None]] = None) -> dict:
    """Importa archivos sueltos, carpetas (sus imágenes, un nivel) y .wastickers.
    Devuelve {"added", "duplicates", "failed"}."""
    res = {"added": 0, "duplicates": 0, "failed": 0}
    files: List[tuple] = []     # (ruta, paquete)
    for p in paths:
        if os.path.isdir(p):
            folder_pack = pack or os.path.basename(os.path.normpath(p)) or DEFAULT_PACK
            try:
                names = sorted(os.listdir(p))
            except OSError:
                continue
            files += [(os.path.join(p, n), folder_pack) for n in names if n.lower().endswith(IMAGE_EXTS)]
        else:
            files.append((p, pack or DEFAULT_PACK))
    for i, (p, pk) in enumerate(files):
        low = p.lower()
        if low.endswith((".wastickers", ".zip")):
            sub = add_archive(p, pack)
            for k in res:
                res[k] += sub[k]
        elif low.endswith(IMAGE_EXTS):
            _tally(res, add_file(p, pk))
        else:
            res["failed"] += 1
        if progress:
            progress(i + 1, len(files))
    return res


def add_from_message(msg: dict, pack: str = DEFAULT_PACK) -> Optional[dict]:
    """«Agregar a mis stickers» sobre un mensaje ya descargado."""
    path = msg.get("media_path")
    if not path or not os.path.exists(path):
        return None
    return add_file(path, pack)


def import_from_chats(download: bool = True, gateway=None, pack: str = CHATS_PACK,
                      progress: Optional[Callable[[int, int], None]] = None,
                      cancelled: Optional[Callable[[], bool]] = None) -> dict:
    """Junta los stickers del historial (recibidos y enviados). Los que no están en disco se bajan bajo
    demanda con su `media_raw`. Devuelve {"added", "duplicates", "failed", "unavailable"}."""
    from app import db

    res = {"added": 0, "duplicates": 0, "failed": 0, "unavailable": 0}
    with db.db_conn() as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT id, media_path, media_raw FROM messages WHERE media_type = 'sticker' ORDER BY timestamp DESC")]
    with _lock:
        done = set(_load().get("imported_msgs", []))
    rows = [r for r in rows if r["id"] not in done]
    newly_done: List[str] = []
    for i, row in enumerate(rows):
        if cancelled and cancelled():
            break
        path = row.get("media_path")
        if not (path and os.path.exists(path)):
            path = None
            if download and row.get("media_raw"):
                if gateway is None:
                    from app.gateway_manager import GatewayManager
                    gateway = GatewayManager.get_instance()
                got = gateway.download_media(row["id"], "sticker", row["media_raw"])
                if got.get("success") and got.get("media_path") and os.path.exists(got["media_path"]):
                    path = got["media_path"]
                    try:
                        db.set_message_media_path(row["id"], path)
                    except Exception:
                        pass
            if path is None:
                res["unavailable"] += 1
                if progress:
                    progress(i + 1, len(rows))
                continue
        rec = add_file(path, pack)
        _tally(res, rec)
        if rec is not None:
            newly_done.append(row["id"])
        if progress:
            progress(i + 1, len(rows))
    if newly_done:
        with _lock:
            idx = _load()
            idx["imported_msgs"] = list(idx.get("imported_msgs", [])) + newly_done
            _save(idx)
    return res


# ── Consulta ──
def sticker_path(rec: dict) -> str:
    return os.path.join(STICKERS_DIR, rec["file"])


def _present(recs) -> List[dict]:
    return [r for r in recs if os.path.exists(sticker_path(r))]


def list_stickers(pack: Optional[str] = None) -> List[dict]:
    """Stickers (el más nuevo primero), de un paquete o de todos."""
    with _lock:
        recs = [r for r in _load()["stickers"].values() if pack is None or r.get("pack") == pack]
    return _present(sorted(recs, key=lambda r: r.get("added", 0), reverse=True))


def get(sid: str) -> Optional[dict]:
    with _lock:
        rec = _load()["stickers"].get(sid)
        return dict(rec) if rec else None


def favorites() -> List[dict]:
    with _lock:
        recs = [r for r in _load()["stickers"].values() if r.get("fav")]
    return _present(sorted(recs, key=lambda r: r.get("added", 0), reverse=True))


def recents(limit: int = 30) -> List[dict]:
    with _lock:
        recs = [r for r in _load()["stickers"].values() if r.get("used")]
    return _present(sorted(recs, key=lambda r: r["used"], reverse=True))[:limit]


def packs() -> List[dict]:
    """[{name, count, cover}] en el orden del índice (los vacíos incluidos)."""
    with _lock:
        idx = _load()
        out = []
        for name in idx["packs"]:
            recs = sorted((r for r in idx["stickers"].values() if r.get("pack") == name),
                          key=lambda r: r.get("added", 0), reverse=True)
            recs = _present(recs)
            out.append({"name": name, "count": len(recs), "cover": recs[0] if recs else None})
        return out


def count() -> int:
    with _lock:
        return len(_load()["stickers"])


# ── Cambios ──
def _update(sid: str, **fields) -> bool:
    with _lock:
        idx = _load()
        rec = idx["stickers"].get(sid)
        if not rec:
            return False
        rec.update(fields)
        _save(idx)
        return True


def mark_used(sid: str) -> bool:
    with _lock:
        rec = _load()["stickers"].get(sid)
        uses = (rec or {}).get("uses", 0) + 1
    return _update(sid, used=time.time(), uses=uses)


def toggle_fav(sid: str) -> bool:
    """Alterna favorito; devuelve el estado nuevo."""
    with _lock:
        rec = _load()["stickers"].get(sid)
        if not rec:
            return False
        new = not rec.get("fav")
        _update(sid, fav=new)
        return new


def move_to_pack(sid: str, pack: str) -> bool:
    pack = (pack or "").strip()
    if not pack:
        return False
    with _lock:
        idx = _load()
        if pack not in idx["packs"]:
            idx["packs"].append(pack)
            _save(idx)
    return _update(sid, pack=pack)


def create_pack(name: str) -> bool:
    name = (name or "").strip()[:60]
    if not name:
        return False
    with _lock:
        idx = _load()
        if name in idx["packs"]:
            return False
        idx["packs"].append(name)
        _save(idx)
        return True


def delete_pack(name: str) -> int:
    """Borra el paquete y sus stickers. El paquete por defecto no se elimina (solo se vacía). Devuelve cuántos borró."""
    removed = 0
    with _lock:
        idx = _load()
        for sid in [s for s, r in idx["stickers"].items() if r.get("pack") == name]:
            rec = idx["stickers"].pop(sid)
            try:
                os.remove(sticker_path(rec))
            except OSError:
                pass
            removed += 1
        if name != DEFAULT_PACK and name in idx["packs"]:
            idx["packs"].remove(name)
        _save(idx)
    return removed


def delete_sticker(sid: str) -> bool:
    with _lock:
        idx = _load()
        rec = idx["stickers"].pop(sid, None)
        if not rec:
            return False
        try:
            os.remove(sticker_path(rec))
        except OSError:
            pass
        _save(idx)
        return True


# ── Emojis recientes (comparten el índice) ──
def recent_emojis() -> List[str]:
    with _lock:
        return list(_load().get("recent_emojis", []))


def add_recent_emoji(emoji: str) -> List[str]:
    from app.emojis import push_recent
    with _lock:
        idx = _load()
        idx["recent_emojis"] = push_recent(idx.get("recent_emojis", []), emoji)
        _save(idx)
        return list(idx["recent_emojis"])
