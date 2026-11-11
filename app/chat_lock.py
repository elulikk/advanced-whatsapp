"""Bloqueo de chats con contraseña (PIN) global de la app.

- La contraseña se guarda solo como hash PBKDF2-HMAC-SHA256 con sal aleatoria (tabla `lock_meta`); nunca en texto plano.
- Los chats bloqueados viven en la tabla `chat_locks` y se cachean en memoria (la lista de chats consulta
  `is_locked` por cada fila, así que no puede ir a disco cada vez).
- "Desbloqueado" es un estado de sesión (solo en memoria): el chat queda abierto mientras se lo mira y se vuelve a
  bloquear al salir de él o cuando la app pasa un rato en segundo plano (`lock_all`).
"""
import hashlib
import hmac
import os
import time
from typing import List, Optional, Set

from app.db import db_conn

MIN_PASSWORD_LEN = 4
PBKDF2_ITERATIONS = 200_000
LOCKED_PREVIEW = "Chat bloqueado"
LOCKED_NOTIFICATION = "Nuevo mensaje"

_locked: Optional[Set[str]] = None      # chats bloqueados (cache de la tabla)
_unlocked: Set[str] = set()             # bloqueados que se abrieron en esta sesión
_has_password: Optional[bool] = None


def ensure_tables(conn) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS chat_locks (
            chat_jid TEXT PRIMARY KEY,
            locked_at INTEGER NOT NULL DEFAULT 0
        )
    """)
    conn.execute("CREATE TABLE IF NOT EXISTS lock_meta (key TEXT PRIMARY KEY, value TEXT)")


def _invalidate() -> None:
    global _locked, _has_password
    _locked = None
    _has_password = None
    _unlocked.clear()


# ── Contraseña ──

def hash_password(password: str, salt: Optional[bytes] = None, iterations: int = PBKDF2_ITERATIONS) -> tuple:
    """Devuelve (hash_hex, sal_hex, iteraciones)."""
    salt = salt if salt is not None else os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", (password or "").encode("utf-8"), salt, iterations)
    return digest.hex(), salt.hex(), iterations


def check_hash(password: str, hash_hex: str, salt_hex: str, iterations: int) -> bool:
    try:
        digest = hashlib.pbkdf2_hmac("sha256", (password or "").encode("utf-8"), bytes.fromhex(salt_hex), int(iterations))
        return hmac.compare_digest(digest.hex(), hash_hex)
    except (ValueError, TypeError):
        return False


def has_password() -> bool:
    global _has_password
    if _has_password is None:
        with db_conn() as conn:
            row = conn.execute("SELECT 1 FROM lock_meta WHERE key = 'pw_hash'").fetchone()
        _has_password = bool(row)
    return _has_password


def set_password(password: str) -> bool:
    """Crea o cambia la contraseña. False si es demasiado corta."""
    global _has_password
    if len((password or "").strip()) < MIN_PASSWORD_LEN:
        return False
    h, s, it = hash_password(password)
    with db_conn() as conn:
        conn.executemany("INSERT OR REPLACE INTO lock_meta (key, value) VALUES (?, ?)",
                         [("pw_hash", h), ("pw_salt", s), ("pw_iters", str(it))])
    _has_password = True
    return True


def verify_password(password: str) -> bool:
    with db_conn() as conn:
        rows = dict(conn.execute("SELECT key, value FROM lock_meta WHERE key IN ('pw_hash','pw_salt','pw_iters')").fetchall())
    if "pw_hash" not in rows:
        return False
    return check_hash(password, rows["pw_hash"], rows.get("pw_salt", ""), int(rows.get("pw_iters") or PBKDF2_ITERATIONS))


def remove_password() -> None:
    """Quita la contraseña y desbloquea todos los chats (sin contraseña no hay nada que proteger)."""
    global _has_password
    with db_conn() as conn:
        conn.execute("DELETE FROM lock_meta WHERE key IN ('pw_hash','pw_salt','pw_iters')")
        conn.execute("DELETE FROM chat_locks")
    _has_password = False
    _invalidate()


# ── Chats bloqueados ──

def _load() -> Set[str]:
    global _locked
    if _locked is None:
        with db_conn() as conn:
            _locked = {r[0] for r in conn.execute("SELECT chat_jid FROM chat_locks").fetchall()}
    return _locked


def locked_jids() -> List[str]:
    return sorted(_load())


def is_locked(jid: Optional[str]) -> bool:
    """¿El chat está bloqueado? (aunque esté abierto en esta sesión: sirve para ocultar vistas previas)."""
    return bool(jid) and jid in _load()


def is_open(jid: Optional[str]) -> bool:
    """¿Se puede ver el contenido? Verdadero si no está bloqueado o ya se desbloqueó en esta sesión."""
    return not is_locked(jid) or jid in _unlocked


def lock_chat(jid: str) -> bool:
    """Bloquea el chat. Requiere que ya exista la contraseña."""
    if not jid or not has_password():
        return False
    with db_conn() as conn:
        conn.execute("INSERT OR REPLACE INTO chat_locks (chat_jid, locked_at) VALUES (?, ?)", (jid, int(time.time())))
    _load().add(jid)
    _unlocked.discard(jid)
    return True


def unlock_chat(jid: str) -> None:
    """Saca el bloqueo del chat definitivamente."""
    with db_conn() as conn:
        conn.execute("DELETE FROM chat_locks WHERE chat_jid = ?", (jid,))
    _load().discard(jid)
    _unlocked.discard(jid)


def mark_open(jid: str) -> None:
    """Deja el chat abierto en esta sesión (después de verificar la contraseña)."""
    if is_locked(jid):
        _unlocked.add(jid)


def relock(jid: Optional[str] = None, keep: Optional[str] = None) -> None:
    """Vuelve a cerrar un chat (o todos, menos `keep`)."""
    if jid:
        _unlocked.discard(jid)
    else:
        for j in list(_unlocked):
            if j != keep:
                _unlocked.discard(j)


def lock_all() -> None:
    _unlocked.clear()
