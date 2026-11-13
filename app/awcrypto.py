"""Contenedor cifrado de Advanced WhatsApp (`.awbackup` y documentos de sincronización).

Formato v1 (idéntico en PC y Android; ver docs/ARQUITECTURA.md):

    encabezado (34 bytes)
        "AWBK"                 4   magia
        versión                1   = 1
        kdf                    1   = 1 -> PBKDF2-HMAC-SHA256
        iteraciones            4   u32 big-endian
        sal                   16
        prefijo de nonce       8
    trozos, uno detrás de otro
        largo del cifrado      4   u32 big-endian (incluye la etiqueta GCM de 16 bytes)
        cifrado + etiqueta         AES-256-GCM

- Clave: PBKDF2-HMAC-SHA256(contraseña UTF-8, sal, iteraciones) -> 32 bytes.
- Texto plano partido en trozos de 1 MiB (el último puede ser más corto; un contenido vacío es un solo trozo vacío).
- Nonce del trozo i = prefijo (8 bytes) + i como u32 big-endian.
- AAD del trozo = encabezado completo + 1 byte (1 si es el último trozo, 0 si no): así no se puede cortar, reordenar ni
  empalmar un archivo sin que falle la autenticación.

Se procesa por trozos para que una base de datos grande no obligue a tenerla entera en memoria.
"""
import hashlib
import os
import struct
from typing import BinaryIO, Optional

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

MAGIC = b"AWBK"
VERSION = 1
KDF_PBKDF2_SHA256 = 1
HEADER_LEN = 34
CHUNK = 1 << 20
TAG_LEN = 16
DEFAULT_ITERATIONS = 210_000
MIN_ITERATIONS = 1_000
MAX_ITERATIONS = 5_000_000


class BackupError(Exception):
    """Error base de copias y sincronización (el mensaje se puede mostrar al usuario)."""


class BadFormatError(BackupError):
    """El archivo no es un contenedor válido (o está truncado)."""


class WrongPasswordError(BackupError):
    """Contraseña incorrecta o archivo dañado (con GCM no se pueden distinguir)."""


def derive_key(password: str, salt: bytes, iterations: int) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", (password or "").encode("utf-8"), salt, iterations, 32)


def make_header(iterations: int, salt: bytes, nonce_prefix: bytes) -> bytes:
    return MAGIC + bytes([VERSION, KDF_PBKDF2_SHA256]) + struct.pack(">I", iterations) + salt + nonce_prefix


def parse_header(header: bytes) -> tuple:
    """(iteraciones, sal, prefijo de nonce). Lanza BadFormatError si no es un contenedor soportado."""
    if len(header) != HEADER_LEN or header[:4] != MAGIC:
        raise BadFormatError("No es un archivo de copia de Advanced WhatsApp.")
    if header[4] != VERSION or header[5] != KDF_PBKDF2_SHA256:
        raise BadFormatError("El archivo es de una versión más nueva de Advanced WhatsApp.")
    iterations = struct.unpack(">I", header[6:10])[0]
    if not MIN_ITERATIONS <= iterations <= MAX_ITERATIONS:
        raise BadFormatError("El archivo está dañado (parámetros de cifrado inválidos).")
    return iterations, header[10:26], header[26:34]


def _nonce(prefix: bytes, index: int) -> bytes:
    return prefix + struct.pack(">I", index)


def _read_full(src: BinaryIO, n: int) -> bytes:
    out = bytearray()
    while len(out) < n:
        part = src.read(n - len(out))
        if not part:
            break
        out += part
    return bytes(out)


def encrypt_stream(src: BinaryIO, dst: BinaryIO, password: str, *, iterations: int = DEFAULT_ITERATIONS,
                   salt: Optional[bytes] = None, nonce_prefix: Optional[bytes] = None) -> None:
    """Cifra `src` en `dst`. `salt` y `nonce_prefix` solo se fijan en las pruebas (vectores de test)."""
    salt = salt if salt is not None else os.urandom(16)
    nonce_prefix = nonce_prefix if nonce_prefix is not None else os.urandom(8)
    if len(salt) != 16 or len(nonce_prefix) != 8:
        raise ValueError("sal o prefijo de nonce con largo incorrecto")
    header = make_header(iterations, salt, nonce_prefix)
    aes = AESGCM(derive_key(password, salt, iterations))
    dst.write(header)
    index = 0
    current = _read_full(src, CHUNK)
    while True:
        nxt = _read_full(src, CHUNK) if len(current) == CHUNK else b""
        last = not nxt
        ct = aes.encrypt(_nonce(nonce_prefix, index), current, header + (b"\x01" if last else b"\x00"))
        dst.write(struct.pack(">I", len(ct)))
        dst.write(ct)
        if last:
            return
        current = nxt
        index += 1


def decrypt_stream(src: BinaryIO, dst: BinaryIO, password: str) -> None:
    """Descifra `src` en `dst`. Lanza BadFormatError o WrongPasswordError; `dst` puede quedar con datos parciales."""
    header = _read_full(src, HEADER_LEN)
    iterations, salt, prefix = parse_header(header)
    aes = AESGCM(derive_key(password, salt, iterations))
    index = 0
    lead = _read_full(src, 4)
    while True:
        if len(lead) != 4:
            raise BadFormatError("El archivo está incompleto.")
        size = struct.unpack(">I", lead)[0]
        if size < TAG_LEN or size > CHUNK + TAG_LEN:
            raise BadFormatError("El archivo está dañado.")
        ct = _read_full(src, size)
        if len(ct) != size:
            raise BadFormatError("El archivo está incompleto.")
        lead = _read_full(src, 4)
        last = not lead
        try:
            plain = aes.decrypt(_nonce(prefix, index), ct, header + (b"\x01" if last else b"\x00"))
        except InvalidTag:
            raise WrongPasswordError("Contraseña incorrecta o archivo dañado.") from None
        dst.write(plain)
        if last:
            return
        index += 1


def encrypt_bytes(data: bytes, password: str, **kw) -> bytes:
    import io
    out = io.BytesIO()
    encrypt_stream(io.BytesIO(data), out, password, **kw)
    return out.getvalue()


def decrypt_bytes(blob: bytes, password: str) -> bytes:
    import io
    out = io.BytesIO()
    decrypt_stream(io.BytesIO(blob), out, password)
    return out.getvalue()


def is_container(path: str) -> bool:
    """¿El archivo empieza con la magia del contenedor? (no valida la contraseña)"""
    try:
        with open(path, "rb") as f:
            return f.read(4) == MAGIC
    except OSError:
        return False
