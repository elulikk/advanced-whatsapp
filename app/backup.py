"""Copias de seguridad (`.awbackup`) y base de la sincronización PC <-> Android.

Un dato propio de la app (tags, bloqueos, nombres de contactos, stickers...) vive en una **sección**: un diccionario
`clave -> valor` (valores JSON simples: texto, número, booleano, lista, objeto; sin decimales). Cada sección se registra con
`register_section()`; **para que una función nueva entre en la copia y en la sincronización alcanza con registrar su sección**
(ver docs/ARQUITECTURA.md, «Copias y sincronización»).

Documento (zip dentro del contenedor cifrado de `app/awcrypto.py`):

    manifest.json              {format, version, kind: backup|sync, created_at (ms), device{id, platform, app_version}, sections{}, ...}
    sections/<nombre>.json     {"items": {clave: {"v": valor, "t": ms}  |  {"t": ms, "d": 1}}}   (d = borrado / lápida)
    files/<sección>/<resto>    archivos que acompañan a una sección (p. ej. los .webp de los stickers)
    db/magsapp.db              solo en las copias: base SQLite (chats, contactos, mensajes y reacciones se unen al restaurar)

Resolución de conflictos: **gana el último cambio por clave** (`t` más grande). Los cambios locales no se registran uno por
uno: en cada lectura (`local_items`) se compara el valor actual con el último visto (tabla `sync_state`) y, si cambió, la
clave queda con `t = ahora`; una clave que desapareció queda como lápida. En empate gana el valor con hash canónico más grande.
"""
import hashlib
import io
import json
import os
import re
import shutil
import sqlite3
import tempfile
import threading
import time
import uuid
import zipfile
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Optional, Set, Tuple

from app import awcrypto
from app.awcrypto import BackupError
from app.config import APP_VERSION, DATA_DIR

FORMAT = "awbackup"
FORMAT_VERSION = 1
PLATFORM = "pc"
BACKUP_EXT = ".awbackup"
TOMBSTONE_TTL_MS = 90 * 24 * 3600 * 1000
BASELINE_T = 1                       # «t» de lo que ya existía antes de la primera lectura: cualquier cambio real le gana
MAX_ENTRY_BYTES = 64 * 1024 * 1024   # tope por archivo dentro del zip (anti zip-bomb)
MERGE_TABLES = ("chats", "contacts", "messages", "reactions")


# ── Secciones ──

@dataclass
class Section:
    name: str
    label: str
    collect: Callable[[], Dict[str, Any]]                         # estado actual: clave -> valor
    apply: Callable[[Dict[str, Any]], Optional[Set[str]]]         # clave -> valor nuevo (None = borrar); devuelve las claves aplicadas
    sync: bool = True                  # viaja en la sincronización con el celular
    secret: bool = False               # solo va en la copia si el usuario lo elige (claves de API)
    platforms: Optional[tuple] = None  # None = todas; ("pc",) = solo se restaura en la misma plataforma
    files: Optional[Callable[[Iterable[str]], List[Tuple[str, str]]]] = None   # claves -> [(nombre en files/<sección>/, ruta)]
    import_file: Optional[Callable[[str, bytes], None]] = None    # recibe (resto del nombre, contenido)
    order: int = 50                    # orden de aplicación (menor primero)


_sections: Dict[str, Section] = {}
_lock = threading.RLock()


def register_section(section: Section) -> None:
    _sections[section.name] = section


def get_sections(sync_only: bool = False, include_secret: bool = True) -> List[Section]:
    _load_builtin_sections()
    out = [s for s in _sections.values() if (s.sync or not sync_only) and (include_secret or not s.secret)]
    return sorted(out, key=lambda s: (s.order, s.name))


def _load_builtin_sections() -> None:
    from app import backup_sections  # noqa: F401  (registra las secciones propias)


# ── Valores canónicos, hash y estado de sincronización ──

def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def value_hash(value: Any) -> str:
    return hashlib.sha1(canonical(value).encode("utf-8")).hexdigest()


def item_sig(item: dict) -> str:
    """Firma para desempatar: un valor le gana a un borrado; entre valores, el hash canónico más grande."""
    return "" if item.get("d") else value_hash(item.get("v"))


def now_ms() -> int:
    return int(time.time() * 1000)


def _db_conn():
    from app.db import db_conn
    return db_conn()


def _ensure_state(conn) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS sync_state (
        section TEXT NOT NULL, key TEXT NOT NULL, h TEXT NOT NULL, t INTEGER NOT NULL, d INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (section, key))""")


def local_items(sec: Section, conn=None) -> Dict[str, dict]:
    """Ítems actuales de la sección con su `t`. Registra en `sync_state` lo que cambió desde la última lectura."""
    with _lock:
        current = sec.collect()
        if conn is None:
            with _db_conn() as c:
                return _track(sec, current, c)
        return _track(sec, current, conn)


def _track(sec: Section, current: Dict[str, Any], conn) -> Dict[str, dict]:
    _ensure_state(conn)
    rows = {r[0]: (r[1], r[2], r[3]) for r in
            conn.execute("SELECT key, h, t, d FROM sync_state WHERE section = ?", (sec.name,))}
    first_time = not rows
    now = now_ms()
    items: Dict[str, dict] = {}
    upserts = []
    for k, v in current.items():
        h = value_hash(v)
        st = rows.get(k)
        if st is None or st[0] != h or st[2]:
            t = BASELINE_T if first_time else now
            upserts.append((sec.name, k, h, t, 0))
        else:
            t = st[1]
        items[k] = {"v": v, "t": t}
    for k, (h, t, d) in rows.items():
        if k in current:
            continue
        if not d:
            t = now
            upserts.append((sec.name, k, "", t, 1))
        if now - t < TOMBSTONE_TTL_MS:
            items[k] = {"t": t, "d": 1}
    if upserts:
        conn.executemany("INSERT OR REPLACE INTO sync_state (section, key, h, t, d) VALUES (?, ?, ?, ?, ?)", upserts)
    conn.execute("DELETE FROM sync_state WHERE section = ? AND d = 1 AND t < ?", (sec.name, now - TOMBSTONE_TTL_MS))
    return items


def remote_wins(remote: dict, local: Optional[dict]) -> bool:
    """¿El ítem remoto le gana al local? Más nuevo gana; en empate, el de firma más grande (igual en ambas puntas)."""
    if local is None:
        return True
    if remote["t"] != local["t"]:
        return remote["t"] > local["t"]
    rs, ls = item_sig(remote), item_sig(local)
    return rs > ls


def merge_items(local: Dict[str, dict], remote: Dict[str, dict]) -> Tuple[Dict[str, dict], int]:
    """Une dos mapas de ítems. Devuelve (ítems remotos que ganan, cantidad de ítems locales que le ganan al remoto).
    Pura y sin efectos: es lo que prueban los tests y lo que repite Kotlin."""
    winners = {}
    for k, it in remote.items():
        if not isinstance(it, dict) or not isinstance(it.get("t"), int):
            continue
        if remote_wins(it, local.get(k)):
            winners[k] = it
    local_wins = sum(1 for k, it in local.items() if remote_wins(it, remote.get(k)))
    return winners, local_wins


def apply_remote(sec: Section, remote: Dict[str, dict], force: bool = False) -> dict:
    """Aplica a la app los ítems remotos que ganan. Con `force` (restauración total) el remoto gana siempre y lo local
    que no está en el remoto se borra. Devuelve {applied, local_wins}."""
    with _lock:
        local = local_items(sec)
        if force:
            winners = {k: it for k, it in remote.items() if isinstance(it, dict) and isinstance(it.get("t"), int)}
            for k, it in local.items():
                if k not in winners and not it.get("d"):
                    winners[k] = {"t": now_ms(), "d": 1}
            local_wins = 0
        else:
            winners, local_wins = merge_items(local, remote)
        changes = {k: (None if it.get("d") else it.get("v")) for k, it in winners.items()}
        done = sec.apply(changes) if changes else set()
        done = set(changes) if done is None else set(done)
        if done:
            current = sec.collect()
            with _db_conn() as conn:
                _ensure_state(conn)
                rows = []
                for k in done:
                    t = winners[k]["t"]
                    if k in current:
                        rows.append((sec.name, k, value_hash(current[k]), t, 0))
                    else:
                        rows.append((sec.name, k, "", t, 1))
                conn.executemany("INSERT OR REPLACE INTO sync_state (section, key, h, t, d) VALUES (?, ?, ?, ?, ?)", rows)
        return {"applied": len(done), "local_wins": local_wins}


def digest(all_items: Dict[str, Dict[str, dict]]) -> str:
    """Huella de qué cambió (secciones, claves, t y borrados), sin los valores: sirve para saber si hay algo nuevo que mandar."""
    h = hashlib.sha1()
    for name in sorted(all_items):
        for k in sorted(all_items[name]):
            it = all_items[name][k]
            h.update(f"{name}\x00{k}\x00{it['t']}\x00{1 if it.get('d') else 0}\n".encode("utf-8"))
    return h.hexdigest()


# ── Identidad del dispositivo ──

def device_id() -> str:
    path = os.path.join(DATA_DIR, "device_id")
    try:
        with open(path, "r", encoding="utf-8") as f:
            v = f.read().strip()
        if len(v) >= 16:
            return v
    except OSError:
        pass
    v = uuid.uuid4().hex
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(v)
    except OSError:
        pass
    return v


# ── Armado y lectura de documentos ──

def _manifest(kind: str, names: Dict[str, int], **extra) -> dict:
    """`sections`: {nombre: {count, sync}}. «sync» le dice a la otra app si una sección que no conoce puede viajar en la sincronización."""
    return {"format": FORMAT, "version": FORMAT_VERSION, "kind": kind, "created_at": now_ms(),
            "device": {"id": device_id(), "platform": PLATFORM, "app_version": APP_VERSION},
            "sections": {n: {"count": c, "sync": bool(_sections[n].sync) if n in _sections else False} for n, c in names.items()},
            **extra}


def build_zip(zip_path: str, kind: str, sections: List[Section], *, include_db: bool = False,
              file_filter: Optional[Callable[[Section, Dict[str, dict]], Optional[Iterable[str]]]] = None,
              max_files_bytes: Optional[int] = None) -> dict:
    """Escribe el zip (sin cifrar). Devuelve {manifest, items (por sección), files_sent (por sección: claves con archivo)}."""
    all_items: Dict[str, Dict[str, dict]] = {}
    sent_files: Dict[str, List[str]] = {}
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        budget = max_files_bytes
        for sec in sections:
            items = local_items(sec)
            all_items[sec.name] = items
            zf.writestr(f"sections/{sec.name}.json", json.dumps({"items": items}, ensure_ascii=False, separators=(",", ":")))
            if sec.files:
                keys = [k for k, it in items.items() if not it.get("d")]
                if file_filter:
                    keys = list(file_filter(sec, items) or [])
                used = []
                for key_group, (arc, path) in _expand_files(sec, keys):
                    try:
                        size = os.path.getsize(path)
                    except OSError:
                        continue
                    if budget is not None and size > budget:
                        continue
                    zf.write(path, f"files/{sec.name}/{arc}", compress_type=zipfile.ZIP_STORED)
                    if budget is not None:
                        budget -= size
                    used.append(key_group)
                sent_files[sec.name] = used
        if include_db:
            tmp = tempfile.mkdtemp(prefix="awb_")
            try:
                copy = os.path.join(tmp, "magsapp.db")
                _copy_db(copy)
                zf.write(copy, "db/magsapp.db")
            finally:
                shutil.rmtree(tmp, ignore_errors=True)
        manifest = _manifest(kind, {n: len(i) for n, i in all_items.items()}, has_db=include_db)
        zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False))
    return {"manifest": manifest, "items": all_items, "files_sent": sent_files}


def _expand_files(sec: Section, keys: List[str]):
    """Pide los archivos de la sección: devuelve (clave, (nombre, ruta))."""
    for key in keys:
        try:
            pairs = sec.files([key]) or []
        except Exception:
            pairs = []
        for pair in pairs:
            yield key, pair


def _copy_db(dest: str) -> None:
    from app import db as _db
    src = sqlite3.connect(_db.DB_PATH, timeout=30)
    try:
        dst = sqlite3.connect(dest)
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()


def read_zip(zip_path: str) -> zipfile.ZipFile:
    try:
        zf = zipfile.ZipFile(zip_path)
        manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
    except (zipfile.BadZipFile, KeyError, ValueError, OSError):
        raise BackupError("El contenido de la copia está dañado.") from None
    if manifest.get("format") != FORMAT:
        raise BackupError("No es una copia de Advanced WhatsApp.")
    if int(manifest.get("version", 0)) > FORMAT_VERSION:
        raise BackupError("La copia es de una versión más nueva de Advanced WhatsApp. Actualizá la app.")
    return zf


def _manifest_of(zf: zipfile.ZipFile) -> dict:
    return json.loads(zf.read("manifest.json").decode("utf-8"))


def _entry_bytes(zf: zipfile.ZipFile, name: str, limit: int = MAX_ENTRY_BYTES) -> bytes:
    info = zf.getinfo(name)
    if info.file_size > limit:
        raise BackupError("La copia contiene un archivo demasiado grande.")
    return zf.read(name)


_SAFE_NAME = re.compile(r"^[A-Za-z0-9_\-]+$")


def apply_document(zf: zipfile.ZipFile, *, force: bool = False, only_sync: bool = False,
                   include_secrets: bool = True) -> dict:
    """Importa los archivos y las secciones de un documento ya abierto. Devuelve el informe por sección."""
    _load_builtin_sections()
    manifest = _manifest_of(zf)
    src_platform = (manifest.get("device") or {}).get("platform")
    names = set(zf.namelist())
    report: Dict[str, dict] = {}
    secs = {s.name: s for s in get_sections(sync_only=only_sync, include_secret=include_secrets)}
    # 1) archivos primero: las secciones pueden necesitarlos (p. ej. el .webp de un sticker)
    for name, sec in secs.items():
        if not sec.import_file:
            continue
        prefix = f"files/{name}/"
        for entry in names:
            if entry.startswith(prefix) and not entry.endswith("/"):
                rest = entry[len(prefix):]
                if "/" in rest or "\\" in rest or rest.startswith("."):
                    continue
                try:
                    sec.import_file(rest, _entry_bytes(zf, entry, 8 * 1024 * 1024))
                except Exception:
                    continue
    # 2) secciones, en orden
    for sec in sorted(secs.values(), key=lambda s: (s.order, s.name)):
        entry = f"sections/{sec.name}.json"
        if entry not in names:
            continue
        if sec.platforms and src_platform not in sec.platforms:
            continue
        if sec.platforms and PLATFORM not in sec.platforms:
            continue
        try:
            doc = json.loads(_entry_bytes(zf, entry).decode("utf-8"))
            items = doc.get("items") or {}
            report[sec.name] = apply_remote(sec, items, force=force)
        except BackupError:
            raise
        except Exception as e:
            report[sec.name] = {"error": str(e)}
    return report


# ── Copias de seguridad ──

def create_backup(path: str, password: str, *, include_secrets: bool = False, include_db: bool = True) -> dict:
    """Escribe un `.awbackup` cifrado en `path` (de forma atómica). Devuelve {path, size, sections, items}."""
    if not password:
        raise BackupError("Falta la contraseña de la copia.")
    _load_builtin_sections()
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    tmpdir = tempfile.mkdtemp(prefix="awb_")
    out_tmp = path + ".tmp"
    try:
        zpath = os.path.join(tmpdir, "backup.zip")
        info = build_zip(zpath, "backup", get_sections(include_secret=include_secrets), include_db=include_db)
        with open(zpath, "rb") as src, open(out_tmp, "wb") as dst:
            awcrypto.encrypt_stream(src, dst, password)
        os.replace(out_tmp, path)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
        try:
            os.remove(out_tmp)
        except OSError:
            pass
    return {"path": path, "size": os.path.getsize(path),
            "sections": {n: len(i) for n, i in info["items"].items()}, "has_db": include_db}


def decrypt_to_zip(path: str, password: str, tmpdir: str) -> str:
    zpath = os.path.join(tmpdir, "contenido.zip")
    try:
        with open(path, "rb") as src, open(zpath, "wb") as dst:
            awcrypto.decrypt_stream(src, dst, password)
    except OSError as e:
        raise BackupError(f"No se pudo leer el archivo: {e}") from None
    return zpath


def inspect_backup(path: str, password: str) -> dict:
    """Abre la copia solo para mostrar qué trae (fecha, origen, secciones). No cambia nada."""
    tmpdir = tempfile.mkdtemp(prefix="awb_")
    try:
        with read_zip(decrypt_to_zip(path, password, tmpdir)) as zf:
            m = _manifest_of(zf)
            m["has_db"] = "db/magsapp.db" in zf.namelist()
            return m
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def restore_backup(path: str, password: str, *, mode: str = "merge", include_secrets: bool = True) -> dict:
    """Restaura una copia. `merge` conserva lo más nuevo de cada lado; `replace` deja la app igual a la copia."""
    if mode not in ("merge", "replace"):
        raise ValueError("modo inválido")
    tmpdir = tempfile.mkdtemp(prefix="awb_")
    try:
        with read_zip(decrypt_to_zip(path, password, tmpdir)) as zf:
            manifest = _manifest_of(zf)
            report: dict = {"mode": mode, "source": (manifest.get("device") or {}).get("platform"),
                            "created_at": manifest.get("created_at"), "db": None}
            if "db/magsapp.db" in zf.namelist():
                dbp = os.path.join(tmpdir, "bk.db")
                with zf.open("db/magsapp.db") as s, open(dbp, "wb") as d:
                    shutil.copyfileobj(s, d)
                report["db"] = merge_db(dbp, replace=(mode == "replace"))
            report["sections"] = apply_document(zf, force=(mode == "replace"), include_secrets=include_secrets)
            return report
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def merge_db(backup_db: str, *, replace: bool = False) -> dict:
    """Une chats, contactos, mensajes y reacciones de la base de la copia. Solo agrega lo que falta (nunca pisa un
    mensaje que ya está); con `replace` primero vacía esas tablas. Tolera esquemas distintos (PC/Android): usa las
    columnas en común. Los `media_path` que no existen en este equipo se vacían (el archivo se baja de nuevo al abrirlo)."""
    from app.db import get_db_connection, refresh_chat_previews, recompute_unread_counts
    conn = get_db_connection()
    stats = {t: 0 for t in MERGE_TABLES}
    try:
        conn.execute("ATTACH DATABASE ? AS bk", (backup_db,))
        try:
            bk_tables = {r[0] for r in conn.execute("SELECT name FROM bk.sqlite_master WHERE type = 'table'")}
            conn.execute("BEGIN IMMEDIATE")
            touched: List[str] = []
            if replace:
                for t in ("reactions", "messages", "contacts", "chats"):
                    if t in bk_tables:
                        conn.execute(f"DELETE FROM main.{t}")
            for t in ("chats", "contacts", "messages", "reactions"):
                if t not in bk_tables:
                    continue
                main_cols = [r[1] for r in conn.execute(f"PRAGMA main.table_info({t})")]
                bk_cols = {r[1] for r in conn.execute(f"PRAGMA bk.table_info({t})")}
                cols = [c for c in main_cols if c in bk_cols]
                if not cols:
                    continue
                col_sql = ", ".join(cols)
                if t == "messages":
                    conn.execute("DROP TABLE IF EXISTS temp.new_msg_ids")
                    conn.execute("CREATE TEMP TABLE new_msg_ids AS SELECT id FROM bk.messages "
                                 "WHERE id NOT IN (SELECT id FROM main.messages)")
                cur = conn.execute(f"INSERT OR IGNORE INTO main.{t} ({col_sql}) SELECT {col_sql} FROM bk.{t}")
                stats[t] = cur.rowcount if cur.rowcount is not None else 0
                if t == "messages":
                    touched = [r[0] for r in conn.execute(
                        "SELECT DISTINCT chat_jid FROM main.messages WHERE id IN (SELECT id FROM temp.new_msg_ids)")]
                    if "media_path" in cols:
                        rows = conn.execute(
                            "SELECT id, media_path FROM main.messages WHERE id IN (SELECT id FROM temp.new_msg_ids) "
                            "AND media_path IS NOT NULL AND media_path != ''").fetchall()
                        missing = [(r[0],) for r in rows if not os.path.exists(r[1])]
                        if missing:
                            conn.executemany("UPDATE main.messages SET media_path = NULL WHERE id = ?", missing)
            if replace:
                touched = [r[0] for r in conn.execute("SELECT jid FROM main.chats")]
            if touched:
                refresh_chat_previews(conn.cursor(), touched)
                recompute_unread_counts(conn, touched)
            conn.commit()
        finally:
            try:
                conn.rollback()
            except sqlite3.Error:
                pass
            try:
                conn.execute("DETACH DATABASE bk")
            except sqlite3.Error:
                pass
    finally:
        conn.close()
    return stats


# ── Copia automática con rotación ──

AUTO_PREFIX = "AdvancedWhatsapp-auto-"


def default_backup_dir() -> str:
    docs = os.path.join(os.path.expanduser("~"), "Documents")
    return os.path.join(docs if os.path.isdir(docs) else DATA_DIR, "AdvancedWhatsapp copias")


def rotate_backups(folder: str, keep: int) -> int:
    """Deja solo las `keep` copias automáticas más nuevas de la carpeta. Devuelve cuántas borró."""
    keep = max(1, int(keep))
    try:
        files = [os.path.join(folder, f) for f in os.listdir(folder)
                 if f.startswith(AUTO_PREFIX) and f.endswith(BACKUP_EXT)]
    except OSError:
        return 0
    files.sort(key=lambda p: (os.path.getmtime(p), p), reverse=True)
    removed = 0
    for old in files[keep:]:
        try:
            os.remove(old)
            removed += 1
        except OSError:
            pass
    return removed


def auto_backup_due(last_ts: float, every_days: int, now: Optional[float] = None) -> bool:
    now = time.time() if now is None else now
    return now - float(last_ts or 0) >= max(1, int(every_days)) * 86400 - 60


def run_auto_backup(password: str, folder: str, keep: int, include_secrets: bool = False) -> dict:
    name = f"{AUTO_PREFIX}{time.strftime('%Y%m%d-%H%M%S')}{BACKUP_EXT}"
    res = create_backup(os.path.join(folder, name), password, include_secrets=include_secrets)
    res["rotated"] = rotate_backups(folder, keep)
    return res


# ── Ajustes (config.ini, secciones [BACKUP] y [SYNC]) ──

def get_setting(section: str, key: str, default: str = "") -> str:
    from app.config import load_app_config
    return load_app_config().get(section, key, fallback=default)


def set_setting(section: str, key: str, value) -> bool:
    from app.config import load_app_config, save_app_config
    cfg = load_app_config()
    if not cfg.has_section(section):
        cfg.add_section(section)
    cfg.set(section, key, str(value))
    return save_app_config(cfg)
