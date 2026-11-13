"""Secciones propias de la app para las copias y la sincronización (ver `app/backup.py`).

Para sumar datos nuevos (plantillas de texto, carpetas inteligentes, chats ocultos...) basta con definir acá, o en el módulo
de la función, `collect()` y `apply()` y llamar a `register_section(Section(...))`. Claves y valores deben ser estables
entre PC y Android (identificá por jid / nombre, nunca por ids autoincrementales).
"""
import configparser
import hashlib
import os
import re
import time
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from app.backup import Section, register_section
from app.config import CONFIG_PATH
from app.db import db_conn

PLATFORM_PC = ("pc",)


# ── Nombres y notas de contactos ──

def _collect_chat_column(column: str) -> Dict[str, Any]:
    with db_conn() as conn:
        rows = conn.execute(f"SELECT jid, {column} FROM chats WHERE {column} IS NOT NULL AND trim({column}) != ''").fetchall()
    return {r[0]: r[1] for r in rows}


def _apply_chat_column(column: str, changes: Dict[str, Any]) -> Set[str]:
    done: Set[str] = set()
    with db_conn() as conn:
        for jid, value in changes.items():
            clean = value.strip() if isinstance(value, str) else ""
            cur = conn.execute(f"UPDATE chats SET {column} = ? WHERE jid = ?", (clean or None, jid))
            if cur.rowcount:
                done.add(jid)   # si el chat todavía no existe acá, queda pendiente para la próxima vez
    return done


# ── Tags ──

def _collect_tags() -> Dict[str, Any]:
    from app.tags import list_tags, TAG_FIELDS
    out: Dict[str, Any] = {}
    names: Dict[int, str] = {}
    for i, t in enumerate(list_tags()):
        low = (t["name"] or "").strip().lower()
        if not low:
            continue
        names[t["id"]] = low
        out[f"tag:{low}"] = {**{f: t.get(f) for f in TAG_FIELDS}, "order": i}
    with db_conn() as conn:
        rows = conn.execute("SELECT chat_jid, tag_id, assigned_at FROM chat_tags").fetchall()
    for jid, tag_id, at in rows:
        if tag_id in names:
            out[f"assign:{jid}|{names[tag_id]}"] = {"at": int(at or 0)}
    return out


def _apply_tags(changes: Dict[str, Any]) -> Set[str]:
    from app import tags
    done: Set[str] = set()
    existing = {(t["name"] or "").strip().lower(): t["id"] for t in tags.list_tags()}
    orders: List[Tuple[int, int]] = []
    for key, value in changes.items():
        if not key.startswith("tag:"):
            continue
        low = key[4:]
        if value is None:
            if low in existing:
                tags.delete_tag(existing.pop(low))
            done.add(key)
            continue
        if not isinstance(value, dict):
            continue
        data = {f: value[f] for f in tags.TAG_FIELDS if f in value}
        if low in existing:
            tags.update_tag(existing[low], data)
        else:
            existing[low] = tags.create_tag(data)
        if isinstance(value.get("order"), int):
            orders.append((value["order"], existing[low]))
        done.add(key)
    if orders:
        with db_conn() as conn:
            for order, tid in orders:
                conn.execute("UPDATE tags SET sort_order = ? WHERE id = ?", (order, tid))
        tags.notify_changed()
    changed = False
    with db_conn() as conn:
        for key, value in changes.items():
            if not key.startswith("assign:") or "|" not in key:
                continue
            jid, _, low = key[7:].rpartition("|")
            tid = existing.get(low)
            if value is None:
                if tid is not None:
                    conn.execute("DELETE FROM chat_tags WHERE chat_jid = ? AND tag_id = ?", (jid, tid))
                done.add(key)
                changed = True
            elif tid is not None and isinstance(value, dict):
                conn.execute("INSERT OR REPLACE INTO chat_tags (chat_jid, tag_id, assigned_at) VALUES (?, ?, ?)",
                             (jid, tid, int(value.get("at") or time.time())))
                done.add(key)
                changed = True
    if changed:
        tags.notify_changed()
    return done


# ── Chats bloqueados ──

def _collect_locks() -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    with db_conn() as conn:
        for (jid,) in conn.execute("SELECT chat_jid FROM chat_locks"):
            out[f"lock:{jid}"] = True
        meta = dict(conn.execute("SELECT key, value FROM lock_meta WHERE key IN ('pw_hash','pw_salt','pw_iters')").fetchall())
    if "pw_hash" in meta:
        out["pw"] = {"hash": meta["pw_hash"], "salt": meta.get("pw_salt", ""), "iters": int(meta.get("pw_iters") or 0)}
    return out


def _apply_locks(changes: Dict[str, Any]) -> Set[str]:
    from app import chat_lock
    done: Set[str] = set()
    with db_conn() as conn:
        if "pw" in changes:
            v = changes["pw"]
            if isinstance(v, dict) and v.get("hash"):
                conn.executemany("INSERT OR REPLACE INTO lock_meta (key, value) VALUES (?, ?)",
                                 [("pw_hash", str(v["hash"])), ("pw_salt", str(v.get("salt", ""))),
                                  ("pw_iters", str(int(v.get("iters") or chat_lock.PBKDF2_ITERATIONS)))])
            else:
                conn.execute("DELETE FROM lock_meta WHERE key IN ('pw_hash','pw_salt','pw_iters')")
            done.add("pw")
        for key, value in changes.items():
            if not key.startswith("lock:"):
                continue
            jid = key[5:]
            if value:
                conn.execute("INSERT OR REPLACE INTO chat_locks (chat_jid, locked_at) VALUES (?, ?)", (jid, int(time.time())))
            else:
                conn.execute("DELETE FROM chat_locks WHERE chat_jid = ?", (jid,))
            done.add(key)
    chat_lock._invalidate()
    return done


# ── Stickers ──

_STICKER_FILE = re.compile(r"^[0-9a-f]{16}\.webp$")


def _collect_stickers() -> Dict[str, Any]:
    from app import stickers
    out: Dict[str, Any] = {}
    with stickers._lock:
        idx = stickers._load()
        for name in idx.get("packs", []):
            out[f"pack:{name}"] = True
        for sid, rec in idx.get("stickers", {}).items():
            if os.path.exists(stickers.sticker_path(rec)):
                out[f"sticker:{sid}"] = {"pack": rec.get("pack") or stickers.DEFAULT_PACK, "fav": bool(rec.get("fav")),
                                         "animated": bool(rec.get("animated")), "added": int(rec.get("added") or 0)}
    return out


def _apply_stickers(changes: Dict[str, Any]) -> Set[str]:
    from app import stickers
    done: Set[str] = set()
    with stickers._lock:
        idx = stickers._load()
        for key, value in changes.items():
            if key.startswith("pack:"):
                name = key[5:]
                if value and name not in idx["packs"]:
                    idx["packs"].append(name)
                elif not value and name in idx["packs"] and name != stickers.DEFAULT_PACK \
                        and not any(r.get("pack") == name for r in idx["stickers"].values()):
                    idx["packs"].remove(name)
                done.add(key)
        for key, value in changes.items():
            if not key.startswith("sticker:"):
                continue
            sid = key[8:]
            if value is None:
                rec = idx["stickers"].pop(sid, None)
                if rec:
                    try:
                        os.remove(stickers.sticker_path(rec))
                    except OSError:
                        pass
                done.add(key)
            elif isinstance(value, dict):
                path = os.path.join(stickers.STICKERS_DIR, f"{sid}.webp")
                if not _STICKER_FILE.match(f"{sid}.webp") or not os.path.exists(path):
                    continue   # el archivo todavía no llegó: se reintenta con la próxima sincronización
                pack = (value.get("pack") or stickers.DEFAULT_PACK).strip() or stickers.DEFAULT_PACK
                if pack not in idx["packs"]:
                    idx["packs"].append(pack)
                rec = idx["stickers"].get(sid) or {"id": sid, "file": f"{sid}.webp", "used": 0, "uses": 0}
                rec.update(pack=pack, fav=bool(value.get("fav")), animated=bool(value.get("animated")),
                           added=rec.get("added") or int(value.get("added") or 0))
                idx["stickers"][sid] = rec
                done.add(key)
        stickers._save(idx)
    return done


def _sticker_files(keys: Iterable[str]) -> List[Tuple[str, str]]:
    from app import stickers
    out = []
    for k in keys:
        if k.startswith("sticker:"):
            path = os.path.join(stickers.STICKERS_DIR, f"{k[8:]}.webp")
            if os.path.exists(path):
                out.append((f"{k[8:]}.webp", path))
    return out


def _import_sticker_file(rest: str, data: bytes) -> None:
    from app import stickers
    if not _STICKER_FILE.match(rest) or len(data) > 4 * 1024 * 1024:
        return
    # El id es el hash del contenido: un archivo cuyo hash no coincide es basura o está adulterado
    if hashlib.sha1(data).hexdigest()[:16] != rest[:-5]:
        return
    path = os.path.join(stickers.STICKERS_DIR, rest)
    if os.path.exists(path):
        return
    os.makedirs(stickers.STICKERS_DIR, exist_ok=True)
    stickers._write_atomic(path, data)


# ── Preferencias compartidas con Android ──

_SHARED_PREFS = ("auto_transcribe", "status_receipts", "notify_groups")


def _collect_prefs() -> Dict[str, Any]:
    from app.config import get_pref, get_polish_profile
    out = {k: bool(get_pref(k)) for k in _SHARED_PREFS}
    out["use_profile"] = bool(get_polish_profile())
    return out


def _apply_prefs(changes: Dict[str, Any]) -> Set[str]:
    from app.config import set_pref, set_polish_profile
    done: Set[str] = set()
    for k, v in changes.items():
        if v is None or not isinstance(v, bool):
            continue
        if k in _SHARED_PREFS:
            set_pref(k, v)
        elif k == "use_profile":
            set_polish_profile(v)
        else:
            continue
        done.add(k)
    return done


# ── Configuración propia de la PC (no viaja a Android) y claves de IA (solo si se elige) ──

_SECRET_WORDS = ("key", "token", "secret", "password", "pass")
_AI_SECTIONS = {"gemini": "GEMINI", "groq": "GROQ", "openai": "OPENAI", "anthropic": "ANTHROPIC"}


def _read_raw_config() -> configparser.ConfigParser:
    cfg = configparser.ConfigParser(interpolation=None)
    try:
        cfg.read(CONFIG_PATH, encoding="utf-8")
    except Exception:
        pass
    return cfg


def _is_secret_option(opt: str) -> bool:
    o = opt.lower()
    return any(w in o for w in _SECRET_WORDS)


def _collect_config() -> Dict[str, Any]:
    cfg = _read_raw_config()
    out: Dict[str, Any] = {}
    for sec in cfg.sections():
        for opt, val in cfg.items(sec):
            if not _is_secret_option(opt):
                out[f"{sec}.{opt}"] = val
    return out


def _apply_config(changes: Dict[str, Any]) -> Set[str]:
    from app import config as appconfig
    cfg = _read_raw_config()
    done: Set[str] = set()
    for key, val in changes.items():
        sec, _, opt = key.partition(".")
        if not sec or not opt or _is_secret_option(opt):
            continue
        if val is None:
            if cfg.has_section(sec):
                cfg.remove_option(sec, opt)
        else:
            if not cfg.has_section(sec):
                cfg.add_section(sec)
            cfg.set(sec, opt, str(val))
        done.add(key)
    if done:
        appconfig.save_app_config(cfg)
        appconfig._pref_cache.clear()
    return done


def _collect_secrets() -> Dict[str, Any]:
    cfg = _read_raw_config()
    out: Dict[str, Any] = {}
    for prov, sec in _AI_SECTIONS.items():
        key = cfg.get(sec, "api_key", fallback="").strip()
        if key:
            out[f"ai.{prov}.key"] = key
            model = cfg.get(sec, "model", fallback="").strip()
            if model:
                out[f"ai.{prov}.model"] = model
    return out


def _apply_secrets(changes: Dict[str, Any]) -> Set[str]:
    from app import config as appconfig
    cfg = _read_raw_config()
    done: Set[str] = set()
    for key, val in changes.items():
        m = re.match(r"^ai\.(gemini|groq|openai|anthropic)\.(key|model)$", key)
        if not m or not isinstance(val, str):
            continue
        sec = _AI_SECTIONS[m.group(1)]
        if not cfg.has_section(sec):
            cfg.add_section(sec)
        cfg.set(sec, "api_key" if m.group(2) == "key" else "model", val)
        done.add(key)
    if done:
        appconfig.save_app_config(cfg)
    return done


# ── Mensajes programados (solo copias: cada gateway envía los suyos) ──

def _sched_key(it: dict) -> str:
    return f"msg:{it.get('to')}|{it.get('at')}|{hashlib.sha1((it.get('text') or '').encode('utf-8')).hexdigest()[:8]}"


def _collect_scheduled() -> Dict[str, Any]:
    try:
        from app.gateway_manager import GatewayManager
        items = GatewayManager.get_instance().list_scheduled_messages().get("items") or []
    except Exception:
        return {}
    now = time.time()
    out: Dict[str, Any] = {}
    for it in items:
        if it.get("state") == "pending" and it.get("type") == "text" and (it.get("at") or 0) > now and it.get("text"):
            out[_sched_key(it)] = {"to": it["to"], "text": it["text"], "at": int(it["at"]), "chat_name": it.get("chat_name") or ""}
    return out


def _apply_scheduled(changes: Dict[str, Any]) -> Set[str]:
    done: Set[str] = set()
    try:
        from app.gateway_manager import GatewayManager
        gw = GatewayManager.get_instance()
        have = set(_collect_scheduled())
    except Exception:
        return done
    for key, v in changes.items():
        if not isinstance(v, dict) or key in have or int(v.get("at") or 0) <= time.time() + 30:
            continue   # los borrados no se deshacen: un programado que ya salió no vuelve
        res = gw.send_message(to=v["to"], text=v["text"], at=int(v["at"]), chat_name=v.get("chat_name") or None)
        if res.get("success") or res.get("id"):
            done.add(key)
    return done


def register_builtin() -> None:
    register_section(Section("chat_names", "Nombres de contactos", lambda: _collect_chat_column("custom_name"),
                             lambda c: _apply_chat_column("custom_name", c), order=10))
    register_section(Section("chat_notes", "Notas de contactos", lambda: _collect_chat_column("notes"),
                             lambda c: _apply_chat_column("notes", c), order=11))
    register_section(Section("tags", "Tags de chats", _collect_tags, _apply_tags, order=20))
    register_section(Section("chat_locks", "Chats bloqueados", _collect_locks, _apply_locks, order=30))
    register_section(Section("stickers", "Stickers", _collect_stickers, _apply_stickers, files=_sticker_files,
                             import_file=_import_sticker_file, order=40))
    register_section(Section("prefs", "Preferencias compartidas", _collect_prefs, _apply_prefs, order=50))
    register_section(Section("config", "Configuración de la PC", _collect_config, _apply_config, sync=False,
                             platforms=PLATFORM_PC, order=60))
    register_section(Section("secrets", "Claves de IA", _collect_secrets, _apply_secrets, sync=False, secret=True, order=61))
    register_section(Section("scheduled_messages", "Mensajes programados", _collect_scheduled, _apply_scheduled,
                             sync=False, order=70))


register_builtin()
