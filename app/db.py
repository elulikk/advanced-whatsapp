import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from typing import List, Dict, Optional, Any
from app.config import DB_PATH


def get_db_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=15.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _migrate_legacy_v1(cursor):
    """Consolidación única de duplicados LID/teléfono de la cuenta original."""
    # 1. Chat propio
    cursor.execute("""
        UPDATE chats
        SET name = 'Mag Tecnologia (Tú)', phone = '5492944940567'
        WHERE jid LIKE '784305%@lid' OR jid LIKE '5492944940567%@s.whatsapp.net'
    """)

    # 2. Consolidar mensajes huérfanos de LIDs hacia el chat telefónico de Marithza
    try:
        # Migrar mensajes del chat 5467627647112@lid hacia 5492944802167@s.whatsapp.net
        cursor.execute("""
            UPDATE messages
            SET chat_jid = '5492944802167@s.whatsapp.net'
            WHERE chat_jid LIKE '5467627647112@lid'
        """)
        # Eliminar el chat duplicado vacío de Marithza
        cursor.execute("DELETE FROM chats WHERE jid = '5467627647112@lid'")
    except Exception:
        pass



def _load_lid_phone_map() -> Dict[str, str]:
    """Mapa LID -> teléfono que mantiene el gateway (puede no existir todavía)."""
    try:
        import json
        with open(os.path.join(os.path.dirname(DB_PATH), "lid_phone_map.json"), "r", encoding="utf-8") as f:
            data = json.load(f)
        return {str(k): str(v) for k, v in data.items() if isinstance(v, (str, int))}
    except Exception:
        return {}


def _is_real_name(name: str) -> bool:
    """Nombre de persona (no un número ni un marcador como 'Contacto (1234)' o '+549...')."""
    return bool(name) and not name.isdigit() and not name.startswith(("Contacto (", "Usuario (", "+"))


def merge_lid_duplicates(conn: Optional[sqlite3.Connection] = None, only_jid: Optional[str] = None) -> int:
    """
    Une los chats '@lid' con su chat de teléfono equivalente (mismo contacto, dos identificadores).
    Primero por el mapa LID->teléfono; si no está, por nombre real único. Devuelve cuántos fusionó.
    """
    if conn is None:
        with db_conn() as own:
            return merge_lid_duplicates(own, only_jid)

    lid_map = _load_lid_phone_map()
    if only_jid:
        lids = conn.execute("SELECT jid, name FROM chats WHERE jid = ? AND jid LIKE '%@lid'", (only_jid,)).fetchall()
    else:
        lids = conn.execute("SELECT jid, name FROM chats WHERE jid LIKE '%@lid'").fetchall()
    merged = 0
    for row in lids:
        lid_jid, name = row["jid"], (row["name"] or "").strip()
        target = None

        phone = lid_map.get(lid_jid.split("@")[0])
        if phone:
            cand = f"{phone.split('@')[0]}@s.whatsapp.net"
            if conn.execute("SELECT 1 FROM chats WHERE jid = ?", (cand,)).fetchone():
                target = cand

        if not target and _is_real_name(name):
            cands = conn.execute(
                "SELECT jid FROM chats WHERE name = ? AND jid LIKE '%@s.whatsapp.net'", (name,)
            ).fetchall()
            if len(cands) == 1:
                target = cands[0]["jid"]

        if not target:
            continue

        conn.execute("UPDATE messages SET chat_jid = ? WHERE chat_jid = ?", (target, lid_jid))
        conn.execute("""
            UPDATE chats SET
                unread_count = unread_count + COALESCE((SELECT unread_count FROM chats WHERE jid = ?), 0),
                last_message_time = MAX(last_message_time, COALESCE((SELECT last_message_time FROM chats WHERE jid = ?), 0)),
                avatar_path = COALESCE(avatar_path, (SELECT avatar_path FROM chats WHERE jid = ?))
            WHERE jid = ?
        """, (lid_jid, lid_jid, lid_jid, target))
        # Los tags del chat '@lid' pasan al chat de teléfono (si la tabla ya existe)
        try:
            conn.execute("UPDATE OR IGNORE chat_tags SET chat_jid = ? WHERE chat_jid = ?", (target, lid_jid))
            conn.execute("DELETE FROM chat_tags WHERE chat_jid = ?", (lid_jid,))
            conn.execute("UPDATE OR IGNORE chat_locks SET chat_jid = ? WHERE chat_jid = ?", (target, lid_jid))
            conn.execute("DELETE FROM chat_locks WHERE chat_jid = ?", (lid_jid,))
        except sqlite3.OperationalError:
            pass
        conn.execute("DELETE FROM chats WHERE jid = ?", (lid_jid,))
        merged += 1

    if merged:
        # Recomponer la vista previa del chat destino con su mensaje más reciente
        conn.execute("""
            UPDATE chats SET last_message_time = COALESCE((
                SELECT MAX(timestamp) FROM messages WHERE chat_jid = chats.jid), last_message_time)
            WHERE jid LIKE '%@s.whatsapp.net'
        """)
    return merged


@contextmanager
def db_conn():
    """Conexión con commit/rollback automático y cierre garantizado."""
    conn = get_db_connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    """Inicializa las tablas, índices y consolida duplicados."""
    with db_conn() as conn:
        cursor = conn.cursor()

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS chats (
                jid TEXT PRIMARY KEY,
                name TEXT,
                phone TEXT,
                unread_count INTEGER DEFAULT 0,
                last_message_text TEXT,
                last_message_time INTEGER DEFAULT 0,
                is_group INTEGER DEFAULT 0,
                is_pinned INTEGER DEFAULT 0,
                is_archived INTEGER DEFAULT 0,
                avatar_url TEXT,
                avatar_path TEXT,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id TEXT PRIMARY KEY,
                chat_jid TEXT NOT NULL,
                sender_jid TEXT,
                sender_name TEXT,
                from_me INTEGER DEFAULT 0,
                text TEXT,
                media_type TEXT DEFAULT 'text',
                media_path TEXT,
                quoted_text TEXT,
                quoted_sender TEXT,
                timestamp INTEGER NOT NULL,
                status TEXT DEFAULT 'sent',
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (chat_jid) REFERENCES chats(jid) ON DELETE CASCADE
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS contacts (
                jid TEXT PRIMARY KEY,
                name TEXT,
                phone TEXT,
                push_name TEXT,
                avatar_path TEXT,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Sin FK a messages: una reacción puede llegar antes que el mensaje (historial)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS reactions (
                message_id TEXT NOT NULL,
                reactor_jid TEXT NOT NULL,
                emoji TEXT NOT NULL,
                timestamp INTEGER DEFAULT 0,
                PRIMARY KEY (message_id, reactor_jid)
            )
        """)

        # Versiones anteriores de un mensaje editado (la más vieja primero), sin FK: se limpian con el mensaje
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS message_edits (
                message_id TEXT NOT NULL,
                text TEXT,
                edited_at INTEGER DEFAULT 0
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_message_edits_msg ON message_edits(message_id)")

        cursor.execute("CREATE INDEX IF NOT EXISTS idx_messages_chat_jid ON messages(chat_jid, timestamp)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_chats_last_time ON chats(last_message_time DESC)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_contacts_name ON contacts(name)")

        # Migraciones seguras
        try:
            cursor.execute("ALTER TABLE messages ADD COLUMN quoted_text TEXT")
        except Exception:
            pass
        try:
            cursor.execute("ALTER TABLE messages ADD COLUMN quoted_sender TEXT")
        except Exception:
            pass
        try:
            cursor.execute("ALTER TABLE chats ADD COLUMN avatar_path TEXT")
        except Exception:
            pass
        try:
            cursor.execute("ALTER TABLE contacts ADD COLUMN notes TEXT")
        except Exception:
            pass
        try:
            cursor.execute("ALTER TABLE chats ADD COLUMN notes TEXT")
        except Exception:
            pass
        # media_raw: mensaje serializado para poder descargar el archivo después (historial)
        # transcript: transcripción de notas de voz (Whisper)
        # pinned_at: momento (epoch) en que se fijó el mensaje en su chat; NULL = no fijado
        # summary: resumen e interpretación del mensaje hecho por la IA (modo resumen)
        # edited: el remitente editó el texto después de enviarlo
        # transcript_failed: la transcripción automática falló; no se reintenta sola (queda el «Transcribir» manual)
        # starred_at: mensaje destacado (epoch ms); revoked_orig: JSON con el contenido de un mensaje eliminado por el
        # remitente (se conserva salvo que se apague el ajuste); poll_json: encuesta con las opciones y el conteo de votos
        for column in ("media_raw TEXT", "transcript TEXT", "pinned_at INTEGER", "summary TEXT",
                       "edited INTEGER DEFAULT 0", "transcript_failed INTEGER DEFAULT 0",
                       "starred_at INTEGER", "revoked_orig TEXT", "poll_json TEXT"):
            try:
                cursor.execute(f"ALTER TABLE messages ADD COLUMN {column}")
            except Exception:
                pass
        # custom_name: nombre que puso el usuario; prevalece sobre el que manda WhatsApp o la agenda
        # manual_unread: chat marcado como no leído a mano (sin mensajes sin leer reales)
        for column in ("custom_name TEXT", "manual_unread INTEGER DEFAULT 0"):
            try:
                cursor.execute(f"ALTER TABLE chats ADD COLUMN {column}")
            except Exception:
                pass
        # muted_until: notificaciones silenciadas hasta ese momento (epoch); -1 = para siempre
        try:
            cursor.execute("ALTER TABLE chats ADD COLUMN muted_until INTEGER DEFAULT 0")
        except Exception:
            pass

        # Tags visuales de los chats (definiciones y asignaciones)
        from app.tags import ensure_tables as _ensure_tag_tables
        _ensure_tag_tables(conn)

        # Bloqueo de chats con contraseña (chat_locks / lock_meta)
        from app.chat_lock import ensure_tables as _ensure_lock_tables
        _ensure_lock_tables(conn)

        # Migración puntual de datos heredados (antes se ejecutaba en cada arranque)
        if cursor.execute("PRAGMA user_version").fetchone()[0] < 1:
            _migrate_legacy_v1(cursor)
            cursor.execute("PRAGMA user_version = 1")

        # 3. Eliminar chats sin JID válido
        cursor.execute("DELETE FROM chats WHERE jid IS NULL OR jid = ''")

        # 3b. Unir chats duplicados LID / teléfono
        merge_lid_duplicates(conn)

        # 3b2. El contador de no leídos se recalcula desde los mensajes reales (evita que se acumule)
        recompute_unread_counts(conn)

        # 3c. Vistas previas y horas desde el mensaje más reciente de cada chat
        refresh_chat_previews(cursor, [r[0] for r in cursor.execute("SELECT jid FROM chats").fetchall()])

        # 4. Limpieza de mensajes fantasma/vacíos (desencriptación fallida o stubs vacíos)
        cursor.execute("""
            DELETE FROM messages 
            WHERE (text IS NULL OR trim(text) = '') 
              AND (media_type IS NULL OR media_type = 'text') 
              AND (media_path IS NULL OR media_path = '') 
              AND (quoted_text IS NULL OR trim(quoted_text) = '')
        """)

        # 5. Recomponer preview y último timestamp para chats afectados por mensajes fantasma
        cursor.execute("""
            UPDATE chats 
            SET 
                last_message_text = COALESCE((
                    SELECT CASE 
                        WHEN m.media_type = 'image' THEN COALESCE('📷 Foto: ' || NULLIF(m.text, ''), '📷 Foto')
                        WHEN m.media_type = 'video' THEN COALESCE('🎥 Video: ' || NULLIF(m.text, ''), '🎥 Video')
                        WHEN m.media_type = 'audio' THEN '🎵 Nota de voz'
                        WHEN m.media_type = 'document' THEN COALESCE('📄 ' || NULLIF(m.text, ''), '📄 Documento')
                        WHEN m.media_type = 'sticker' THEN '🎨 Sticker'
                        WHEN m.media_type = 'location' THEN '📍 Ubicación'
                        WHEN m.media_type = 'contact' THEN '👤 Contacto'
                        WHEN m.media_type = 'revoked' THEN '🚫 Mensaje eliminado'
                        ELSE NULLIF(m.text, '')
                    END
                    FROM messages m 
                    WHERE m.chat_jid = chats.jid 
                    ORDER BY m.timestamp DESC, m.created_at DESC 
                    LIMIT 1
                ), CASE WHEN last_message_text = 'Mensaje' THEN 'Sin mensajes' ELSE last_message_text END),
                last_message_time = COALESCE((
                    SELECT m.timestamp 
                    FROM messages m 
                    WHERE m.chat_jid = chats.jid 
                    ORDER BY m.timestamp DESC, m.created_at DESC 
                    LIMIT 1
                ), last_message_time)
            WHERE last_message_text = 'Mensaje' OR last_message_text IS NULL OR last_message_text = ''
        """)

        # 6. Corregir nombres 'Usuario (...)' a número si se conoce o 'Contacto (...)'
        cursor.execute("""
            UPDATE chats
            SET name = '+' || phone
            WHERE name LIKE 'Usuario%' AND phone IS NOT NULL AND length(phone) >= 7
        """)
        cursor.execute("""
            UPDATE chats
            SET name = REPLACE(name, 'Usuario (', 'Contacto (')
            WHERE name LIKE 'Usuario (%'
        """)
        conn.commit()


def get_or_create_chat(jid: str, name: Optional[str] = None, phone: Optional[str] = None,
                       conn: Optional[sqlite3.Connection] = None) -> Dict[str, Any]:
    if conn is None:
        with db_conn() as own:
            return get_or_create_chat(jid, name, phone, conn=own)
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM chats WHERE jid = ?", (jid,))
    row = cursor.fetchone()
    
    is_group = 1 if jid.endswith("@g.us") else 0
    clean = jid.split("@")[0].split(":")[0]
    display_name = name or (phone and f"+{phone}") or clean

    if row:
        current_name = row["name"] or ""
        if name and (current_name == clean or not current_name or current_name.isdigit()):
            cursor.execute("UPDATE chats SET name = ?, phone = COALESCE(?, phone) WHERE jid = ?", (display_name, phone, jid))
            conn.commit()
        return dict(row)

    now = int(time.time())
    cursor.execute("""
        INSERT INTO chats (jid, name, phone, unread_count, last_message_text, last_message_time, is_group)
        VALUES (?, ?, ?, 0, '', ?, ?)
    """, (jid, display_name, phone, now, is_group))
    conn.commit()

    cursor.execute("SELECT * FROM chats WHERE jid = ?", (jid,))
    return dict(cursor.fetchone())


def _preview_for(media_type: str, text: str) -> str:
    """Texto de la vista previa de un chat según el tipo de mensaje."""
    t = text or ""
    if media_type == "image":
        return f"📷 Foto: {t}" if t else "📷 Foto"
    if media_type == "video":
        return f"🎥 Video: {t}" if t else "🎥 Video"
    if media_type == "document":
        return f"📄 Archivo: {t}" if t else "📄 Archivo"
    if media_type == "audio":
        return "🎵 Nota de voz"
    if media_type == "sticker":
        return "🎨 Sticker"
    if media_type == "location":
        return f"📍 Ubicación: {t}" if t else "📍 Ubicación"
    if media_type == "contact":
        return f"👤 {t}" if t else "👤 Contacto"
    if media_type == "revoked":
        return "🚫 Mensaje eliminado"
    return t


def refresh_chat_previews(cursor, chat_jids) -> None:
    """Recalcula vista previa y hora de cada chat desde su mensaje MÁS RECIENTE (no el último que llegó)."""
    for jid in set(chat_jids):
        row = cursor.execute(
            "SELECT text, media_type, timestamp FROM messages WHERE chat_jid = ? "
            "ORDER BY timestamp DESC, created_at DESC LIMIT 1", (jid,)
        ).fetchone()
        if row:
            cursor.execute(
                "UPDATE chats SET last_message_text = ?, last_message_time = ? WHERE jid = ?",
                (_preview_for(row["media_type"] or "text", row["text"]), row["timestamp"], jid),
            )


def save_message(
    msg_id: str,
    chat_jid: str,
    sender_jid: str,
    sender_name: str,
    from_me: bool,
    text: str,
    timestamp: int,
    media_type: str = "text",
    media_path: Optional[str] = None,
    quoted_text: Optional[str] = None,
    quoted_sender: Optional[str] = None,
    status: str = "sent",
    chat_name: Optional[str] = None,
    media_raw: Optional[str] = None,
    already_read: bool = False
) -> bool:
    """Guarda un mensaje con soporte para respuestas citadas y filtrado de mensajes vacíos.
    already_read: mensaje recibido que ya leíste en otro dispositivo (no suma a "sin leer")."""
    clean_text = (text or "").strip()
    # Descartar mensajes fantasma completamente vacíos
    if not clean_text and (media_type == "text" or not media_type) and not media_path and not quoted_text:
        return False

    with db_conn() as conn:
        cursor = conn.cursor()

        # Determinar nombre legítimo del remitente
        valid_sender = None
        if not from_me and sender_name and sender_name.strip() and sender_name.lower() not in ["yo", "null", "none", "mag tecnologia"]:
            valid_sender = sender_name.strip()

        # Nombre del chat: en grupos nunca debe ser el del remitente
        chat_label = chat_name or (None if chat_jid.endswith("@g.us") else valid_sender)

        # Asegurar chat
        get_or_create_chat(chat_jid, name=chat_label, conn=conn)

        from_me_val = 1 if from_me else 0

        is_new = cursor.execute("SELECT 1 FROM messages WHERE id = ?", (msg_id,)).fetchone() is None

        if is_new:
            # Si WhatsApp ya avisó (entregado/leído) antes de que guardáramos el mensaje, respetarlo
            with _early_status_lock:
                early = _early_status.pop(msg_id, None)
            if from_me and early and _STATUS_RANK.get(early, 0) > _STATUS_RANK.get(status, 0):
                status = early
            elif not from_me and early == "read":
                already_read = True   # lo leíste en el celular antes de que llegara acá

        if not from_me and already_read:
            status = "read"

        cursor.execute("""
            INSERT INTO messages (
                id, chat_jid, sender_jid, sender_name, from_me, text, media_type, media_path,
                quoted_text, quoted_sender, timestamp, status, media_raw
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                summary = CASE WHEN excluded.text IS messages.text THEN messages.summary ELSE NULL END,
                text = excluded.text,
                media_type = excluded.media_type,
                media_path = COALESCE(excluded.media_path, messages.media_path),
                quoted_text = COALESCE(excluded.quoted_text, messages.quoted_text),
                quoted_sender = COALESCE(excluded.quoted_sender, messages.quoted_sender),
                media_raw = COALESCE(excluded.media_raw, messages.media_raw)
        """, (
            msg_id, chat_jid, sender_jid, sender_name, from_me_val, clean_text, media_type, media_path,
            quoted_text, quoted_sender, timestamp, status, media_raw
        ))

        # Determinar snippet para la lista
        snippet = clean_text
        if media_type == "image":
            snippet = f"📷 Foto: {clean_text}" if clean_text else "📷 Foto"
        elif media_type == "video":
            snippet = f"🎥 Video: {clean_text}" if clean_text else "🎥 Video"
        elif media_type == "document":
            snippet = f"📄 Archivo: {clean_text}" if clean_text else "📄 Archivo"
        elif media_type == "audio":
            snippet = "🎵 Nota de voz"
        elif media_type == "sticker":
            snippet = "🎨 Sticker"
        elif media_type == "location":
            snippet = f"📍 Ubicación: {clean_text}" if clean_text else "📍 Ubicación"
        elif media_type == "contact":
            snippet = f"👤 {clean_text}" if clean_text else "👤 Contacto"
        elif media_type == "revoked":
            snippet = "🚫 Mensaje eliminado"

        if not snippet and not clean_text:
            snippet = "Mensaje multimedia"

        unread_increment = 1 if (is_new and not from_me and not already_read) else 0

        cursor.execute("""
            UPDATE chats 
            SET last_message_text = ?, 
                last_message_time = ?,
                unread_count = unread_count + ?,
                name = CASE 
                    WHEN ? IS NOT NULL AND (name IS NULL OR name = '' OR name GLOB '[0-9]*' OR name LIKE 'Usuario%' OR name LIKE 'Contacto%' OR name = 'Grupo') THEN ?
                    ELSE name 
                END,
                updated_at = CURRENT_TIMESTAMP
            WHERE jid = ?
        """, (snippet, timestamp, unread_increment, chat_label, chat_label, chat_jid))
        refresh_chat_previews(cursor, [chat_jid])

        # Si el mensaje llegó por LID y ya existe el chat del teléfono, unirlos ahora (no esperar al próximo arranque)
        if chat_jid.endswith("@lid"):
            merge_lid_duplicates(conn, only_jid=chat_jid)

        conn.commit()
        return True


def save_sync_batch(chats: List[Dict[str, Any]], contacts: List[Dict[str, Any]], messages: List[Dict[str, Any]],
                    rename_chats: bool = False):
    """Guarda en lote sincronizado los chats, contactos y mensajes en una única transacción ultra-rápida.
    rename_chats=True: los contactos son renombres (p. ej. cambio en la agenda del teléfono) y su nombre
    pasa a los chats que ya existen; nunca crea chats nuevos."""
    with db_conn() as conn:
        cursor = conn.cursor()

        # 1. Contactos
        for c in contacts:
            jid = c.get("jid") or (c.get("phone") and f"{c['phone']}@s.whatsapp.net") or (c.get("lid") and f"{c['lid']}@lid")
            if not jid:
                continue
            cursor.execute("""
                INSERT INTO contacts (jid, name, phone, push_name, avatar_path, updated_at)
                VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(jid) DO UPDATE SET
                    name = COALESCE(NULLIF(excluded.name, ''), contacts.name),
                    phone = COALESCE(NULLIF(excluded.phone, ''), contacts.phone),
                    push_name = COALESCE(NULLIF(excluded.push_name, ''), contacts.push_name),
                    avatar_path = COALESCE(excluded.avatar_path, contacts.avatar_path),
                    updated_at = CURRENT_TIMESTAMP
            """, (jid, c.get("name"), c.get("phone"), c.get("pushName"), c.get("avatar_path")))

            name = (c.get("name") or "").strip()
            if rename_chats and _is_real_name(name):
                for chat_jid in {jid, c.get("lid") and f"{c['lid']}@lid"} - {None, ""}:
                    cursor.execute("UPDATE chats SET name = ? WHERE jid = ? AND name IS NOT ?", (name, chat_jid, name))

        # 2. Chats
        for ch in chats:
            jid = ch.get("jid")
            if not jid:
                continue
            name = ch.get("name")
            phone = ch.get("phone")
            unread = ch.get("unread_count")  # None = no tocar el contador
            time_val = ch.get("last_message_time", 0)
            is_group = ch.get("is_group", 0)
            avatar_path = ch.get("avatar_path")

            cursor.execute("""
                INSERT INTO chats (jid, name, phone, unread_count, last_message_text, last_message_time, is_group, avatar_path)
                VALUES (?, ?, ?, COALESCE(?, 0), '', ?, ?, ?)
                ON CONFLICT(jid) DO UPDATE SET
                    name = CASE 
                        WHEN excluded.name IS NOT NULL AND excluded.name != '' AND excluded.name != 'Grupo' AND NOT excluded.name GLOB '[0-9]*' AND NOT excluded.name LIKE 'Usuario%' THEN excluded.name
                        ELSE chats.name 
                    END,
                    phone = COALESCE(excluded.phone, chats.phone),
                    unread_count = COALESCE(?, chats.unread_count),
                    avatar_path = COALESCE(excluded.avatar_path, chats.avatar_path),
                    last_message_time = CASE 
                        WHEN excluded.last_message_time > chats.last_message_time THEN excluded.last_message_time 
                        ELSE chats.last_message_time 
                    END
            """, (jid, name, phone, unread, time_val, is_group, avatar_path, unread))

        # 3. Mensajes
        touched_chats = []
        for m in messages:
            msg_id = m.get("message_id")
            chat_jid = m.get("chat_jid")
            if not msg_id or not chat_jid:
                continue

            text_val = (m.get("text") or "").strip()
            media_type = m.get("media_type", "text")
            media_path = m.get("media_path")
            quoted_text = m.get("quoted_text")
            quoted_sender = m.get("quoted_sender")

            # Borrado / edición del historial: solo se aplican si el mensaje original existe
            if m.get("revoke_of") or m.get("edit_of"):
                exists = cursor.execute("SELECT 1 FROM messages WHERE id = ?", (msg_id,)).fetchone()
                if exists and m.get("revoke_of"):
                    _revoke_in(cursor, msg_id)
                    touched_chats.append(chat_jid)
                elif exists and text_val:
                    _record_edit(cursor, msg_id, text_val)
                    cursor.execute("UPDATE messages SET text = ?, edited = 1, summary = NULL WHERE id = ?",
                                   (text_val, msg_id))
                    touched_chats.append(chat_jid)
                continue

            # Omitir mensajes vacíos de desencriptación fallida o stubs
            if not text_val and media_type == "text" and not media_path and not quoted_text:
                continue

            from_me_val = 1 if m.get("from_me") else 0
            ts = m.get("timestamp", int(time.time()))

            cursor.execute("""
                INSERT INTO messages (
                    id, chat_jid, sender_jid, sender_name, from_me, text, media_type, media_path,
                    quoted_text, quoted_sender, timestamp, status, media_raw
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    media_raw = COALESCE(excluded.media_raw, messages.media_raw),
                    media_path = COALESCE(messages.media_path, excluded.media_path),
                    status = CASE
                        WHEN messages.from_me = 1 AND (CASE excluded.status WHEN 'read' THEN 3 WHEN 'delivered' THEN 2 ELSE 1 END)
                                                     > (CASE messages.status WHEN 'read' THEN 3 WHEN 'delivered' THEN 2 ELSE 1 END)
                        THEN excluded.status ELSE messages.status END
            """, (
                msg_id, chat_jid, m.get("sender_jid"), m.get("sender_name"),
                from_me_val, text_val, media_type, media_path,
                quoted_text, quoted_sender, ts, m.get("status", "sent"), m.get("media_raw")
            ))

            touched_chats.append(chat_jid)

        refresh_chat_previews(cursor, touched_chats)
        merge_lid_duplicates(conn)
        conn.commit()


def save_or_update_contact(jid: str, name: str, phone: Optional[str] = None) -> bool:
    """Guarda o actualiza un contacto tanto en la tabla contacts como en chats."""
    clean_name = (name or "").strip()
    if not clean_name:
        return False
    clean_phone = phone.strip() if phone else None
    if clean_phone:
        clean_phone = "".join(ch for ch in clean_phone if ch.isdigit())

    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO contacts (jid, name, phone, push_name, updated_at)
            VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(jid) DO UPDATE SET
                name = excluded.name,
                phone = COALESCE(excluded.phone, contacts.phone),
                updated_at = CURRENT_TIMESTAMP
        """, (jid, clean_name, clean_phone, clean_name))

        # Actualizar en chats (y fijar el nombre propio: no lo pisa lo que mande WhatsApp después)
        cursor.execute("UPDATE chats SET custom_name = ? WHERE jid = ? OR jid LIKE ?",
                       (clean_name, jid, f"{jid.split('@')[0]}@%"))
        cursor.execute("""
            UPDATE chats 
            SET name = ?,
                phone = COALESCE(?, phone),
                updated_at = CURRENT_TIMESTAMP
            WHERE jid = ? OR jid LIKE ?
        """, (clean_name, clean_phone, jid, f"{jid.split('@')[0]}@%"))

        # Si el chat no existía, crearlo
        cursor.execute("SELECT jid FROM chats WHERE jid = ?", (jid,))
        if not cursor.fetchone():
            get_or_create_chat(jid, name=clean_name, phone=clean_phone, conn=conn)

        conn.commit()
        return True


def get_all_contacts(search: Optional[str] = None) -> List[Dict[str, Any]]:
    """Retorna lista de contactos registrados para la agenda/picker."""
    with db_conn() as conn:
        cursor = conn.cursor()
        query = """
            SELECT DISTINCT 
                c.jid, 
                COALESCE(NULLIF(c.name, ''), NULLIF(ch.name, ''), c.phone, c.jid) as name,
                COALESCE(c.phone, ch.phone) as phone,
                COALESCE(c.avatar_path, ch.avatar_path) as avatar_path
            FROM contacts c
            LEFT JOIN chats ch ON c.jid = ch.jid
            WHERE ((c.name IS NOT NULL AND c.name != '' AND c.name NOT LIKE 'Usuario%' AND c.name != 'Yo' AND c.name != 'Mag Tecnologia')
               OR (ch.name IS NOT NULL AND ch.name != '' AND ch.name NOT LIKE 'Usuario%' AND ch.name != 'Mag Tecnologia (Tú)'))
        """
        params = []
        if search and search.strip():
            term = f"%{search.strip()}%"
            query += " AND (c.name LIKE ? OR ch.name LIKE ? OR c.phone LIKE ?)"
            params.extend([term, term, term])

        query += " ORDER BY name COLLATE NOCASE ASC LIMIT 200"
        cursor.execute(query, params)
        return [dict(row) for row in cursor.fetchall()]


def save_contact(jid: str, name: Optional[str] = None, phone: Optional[str] = None, push_name: Optional[str] = None, avatar_path: Optional[str] = None):
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO contacts (jid, name, phone, push_name, avatar_path, updated_at)
            VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(jid) DO UPDATE SET
                name = COALESCE(NULLIF(excluded.name, ''), contacts.name),
                phone = COALESCE(NULLIF(excluded.phone, ''), contacts.phone),
                push_name = COALESCE(NULLIF(excluded.push_name, ''), contacts.push_name),
                avatar_path = COALESCE(excluded.avatar_path, contacts.avatar_path),
                updated_at = CURRENT_TIMESTAMP
        """, (jid, name, phone, push_name, avatar_path))

        if name and not name.isdigit():
            cursor.execute("""
                UPDATE chats 
                SET name = ?, phone = COALESCE(?, phone), avatar_path = COALESCE(?, avatar_path)
                WHERE jid = ? AND (name IS NULL OR name = '' OR name GLOB '[0-9]*')
            """, (name, phone, avatar_path, jid))

        conn.commit()


def resolve_phone(chat: Dict[str, Any]) -> str:
    """Teléfono (solo dígitos) de un chat: campo phone, JID de teléfono, mapa LID o tabla contacts."""
    phone = "".join(ch for ch in (chat.get("phone") or "") if ch.isdigit())
    if phone:
        return phone
    jid = chat.get("jid") or ""
    if jid.endswith("@g.us"):
        return ""
    base = jid.split("@")[0].split(":")[0]
    if jid.endswith("@s.whatsapp.net") and base.isdigit():
        return base
    mapped = _load_lid_phone_map().get(base)
    if mapped:
        return "".join(ch for ch in mapped.split("@")[0] if ch.isdigit())
    with db_conn() as conn:
        row = conn.execute(
            "SELECT phone FROM contacts WHERE jid = ? OR jid LIKE ?", (jid, f"{base}@%")
        ).fetchone()
    return "".join(ch for ch in (row["phone"] or "") if ch.isdigit()) if row else ""


def get_contact_notes(jid: str) -> str:
    """Notas privadas sobre un contacto/cliente (guardadas en el chat)."""
    with db_conn() as conn:
        row = conn.execute("SELECT notes FROM chats WHERE jid = ?", (jid,)).fetchone()
        return (row["notes"] or "") if row else ""


def set_contact_notes(jid: str, notes: str) -> None:
    with db_conn() as conn:
        conn.execute("UPDATE chats SET notes = ? WHERE jid = ?", (notes.strip() or None, jid))
        conn.commit()


def get_chat_media(chat_jid: str) -> Dict[str, Any]:
    """Archivos (fotos, videos, documentos) y enlaces de un chat, del más nuevo al más viejo."""
    import re
    files: List[Dict[str, Any]] = []
    links: List[Dict[str, Any]] = []
    url_re = re.compile(r"https?://\S+")
    with db_conn() as conn:
        rows = conn.execute(
            "SELECT id, text, media_type, media_path, timestamp FROM messages "
            "WHERE chat_jid = ? ORDER BY timestamp DESC", (chat_jid,)
        ).fetchall()
    for r in rows:
        mt = r["media_type"] or "text"
        if mt in ("image", "video", "document"):
            files.append(dict(r))
        for url in url_re.findall(r["text"] or ""):
            links.append({"id": r["id"], "url": url.rstrip(".,);"), "timestamp": r["timestamp"]})
    return {"files": files, "links": links}


def update_chat_avatar(jid: str, avatar_path: str):
    """Actualiza la foto de perfil en el chat y en contactos."""
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE chats SET avatar_path = ? WHERE jid = ? OR jid LIKE ?", (avatar_path, jid, f"{jid.split('@')[0]}@%"))
        cursor.execute("UPDATE contacts SET avatar_path = ? WHERE jid = ? OR jid LIKE ?", (avatar_path, jid, f"{jid.split('@')[0]}@%"))
        conn.commit()


# Todos muestra los chats individuales y los grupos van en su pestaña; comunidades, canales y difusiones se ocultan
INDIVIDUAL_CHAT_SQL = "(jid LIKE '%@s.whatsapp.net' OR jid LIKE '%@lid')"
GROUP_CHAT_SQL = "jid LIKE '%@g.us'"
VISIBLE_CHAT_SQL = f"({INDIVIDUAL_CHAT_SQL} OR {GROUP_CHAT_SQL})"


def get_chat_custom_name(jid: str) -> str:
    """Nombre que puso el usuario para el chat ('' si no hay)."""
    with db_conn() as conn:
        row = conn.execute("SELECT custom_name FROM chats WHERE jid = ?", (jid,)).fetchone()
    return (row["custom_name"] or "") if row else ""


def set_chat_custom_name(jid: str, name: str) -> None:
    """Nombre propio del contacto: prevalece sobre el de WhatsApp/agenda. Vacío = volver al de WhatsApp."""
    with db_conn() as conn:
        conn.execute("UPDATE chats SET custom_name = ? WHERE jid = ?", ((name or "").strip() or None, jid))


def get_chats_count() -> int:
    """Retorna la cantidad total de chats individuales registrados."""
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute(f"SELECT COUNT(*) FROM chats WHERE {INDIVIDUAL_CHAT_SQL}")
        return cursor.fetchone()[0]


def recompute_unread_counts(conn: Optional[sqlite3.Connection] = None, jids: Optional[List[str]] = None) -> int:
    """Recalcula chats.unread_count desde los mensajes recibidos que siguen sin leer: el contador nunca puede
    superar lo realmente pendiente y un chat sin pendientes queda en 0 (salvo marcado como no leído a mano).
    Devuelve cuántos chats cambiaron."""
    if conn is None:
        with db_conn() as own:
            return recompute_unread_counts(own, jids)
    where = ""
    params: list = []
    if jids:
        where = f" AND c.jid IN ({','.join('?' * len(jids))})"
        params = list(jids)
    real = ("(SELECT COUNT(*) FROM messages m WHERE m.chat_jid = c.jid AND m.from_me = 0 "
            "AND m.status != 'read' AND m.media_type != 'revoked')")
    cur = conn.execute(f"""
        UPDATE chats AS c SET unread_count = CASE
            WHEN {real} > 0 THEN MIN(c.unread_count, {real})
            WHEN c.manual_unread = 1 THEN 1
            ELSE 0 END
        WHERE c.unread_count != CASE
            WHEN {real} > 0 THEN MIN(c.unread_count, {real})
            WHEN c.manual_unread = 1 THEN 1
            ELSE 0 END{where}
    """, params)
    return cur.rowcount


def get_total_unread_count(include_muted: bool = True, include_groups: bool = True) -> int:
    """Retorna la cantidad total de mensajes no leídos sumados en los chats individuales y los grupos.
    include_muted=False deja afuera los chats silenciados (como WhatsApp en el ícono de la app);
    include_groups=False cuenta solo los chats individuales."""
    where = VISIBLE_CHAT_SQL if include_groups else INDIVIDUAL_CHAT_SQL
    params: list = []
    if not include_muted:
        where += " AND NOT (COALESCE(muted_until, 0) = -1 OR COALESCE(muted_until, 0) > ?)"
        params.append(int(time.time()))
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute(f"SELECT COALESCE(SUM(unread_count), 0) FROM chats WHERE {where}", params)
        row = cursor.fetchone()
        return int(row[0]) if row else 0


def get_chats(search: Optional[str] = None, filter_unread: bool = False, limit: int = 40, offset: int = 0,
              include_hidden: bool = False, groups_only: bool = False) -> List[Dict[str, Any]]:
    """Obtiene la lista de chats ordenados por actividad reciente con paginación de alto rendimiento.
    Sin filtros devuelve los individuales; los sin leer y las búsquedas también abarcan los grupos.
    groups_only=True deja solo los grupos; include_hidden=True suma además canales y difusiones."""
    with db_conn() as conn:
        cursor = conn.cursor()
        query = "SELECT * FROM chats WHERE 1=1"
        params: list = []

        if groups_only:
            query += f" AND {GROUP_CHAT_SQL}"
        elif not include_hidden:
            everywhere = filter_unread or bool(search and search.strip())
            query += f" AND {VISIBLE_CHAT_SQL if everywhere else INDIVIDUAL_CHAT_SQL}"

        if filter_unread:
            query += " AND unread_count > 0"

        if search and search.strip():
            term = f"%{search.strip()}%"
            # El texto del último mensaje de un chat bloqueado no es buscable (se vería en los resultados)
            query += (" AND (name LIKE ? OR custom_name LIKE ? OR phone LIKE ? OR (last_message_text LIKE ? "
                      "AND jid NOT IN (SELECT chat_jid FROM chat_locks)))")
            params.extend([term, term, term, term])

        query += " ORDER BY is_pinned DESC, last_message_time DESC, updated_at DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        cursor.execute(query, params)
        return [dict(row) for row in cursor.fetchall()]


def get_messages(chat_jid: str, limit: int = 50, before_timestamp: Optional[int] = None) -> List[Dict[str, Any]]:
    """Obtiene el historial de mensajes paginado de un chat específico."""
    with db_conn() as conn:
        cursor = conn.cursor()
        query = "SELECT * FROM messages WHERE chat_jid = ?"
        params: list = [chat_jid]

        if before_timestamp:
            query += " AND timestamp < ?"
            params.append(before_timestamp)

        query += " ORDER BY timestamp DESC, created_at DESC LIMIT ?"
        params.append(limit)

        cursor.execute(query, params)
        rows = cursor.fetchall()
        # Retornar en orden cronológico ascendente (los más viejos primero)
        return [dict(row) for row in reversed(rows)]


def mark_chat_as_read(chat_jid: str) -> List[tuple]:
    """Marca el chat como leído. Devuelve [(message_id, sender_jid)] de los mensajes recibidos que
    aún no estaban leídos, para poder avisar a WhatsApp (tildes azules en el teléfono del otro)."""
    with db_conn() as conn:
        cursor = conn.cursor()
        newly_read = [
            (r["id"], r["sender_jid"]) for r in cursor.execute(
                "SELECT id, sender_jid FROM messages WHERE chat_jid = ? AND from_me = 0 AND status != 'read' "
                "ORDER BY timestamp DESC LIMIT 200", (chat_jid,))
        ]
        cursor.execute("UPDATE chats SET unread_count = 0, manual_unread = 0 WHERE jid = ?", (chat_jid,))
        cursor.execute("UPDATE messages SET status = 'read' WHERE chat_jid = ? AND from_me = 0", (chat_jid,))
        conn.commit()
    return newly_read


def mark_messages_read_elsewhere(message_ids: List[str]) -> List[str]:
    """Mensajes recibidos que leíste en el celular (u otro dispositivo). Los marca leídos y ajusta el
    contador de cada chat. Si el aviso llega antes que el mensaje, queda guardado para cuando llegue.
    Devuelve los chats que cambiaron."""
    ids = list(dict.fromkeys(i for i in (message_ids or []) if i))
    if not ids:
        return []
    newly: Dict[str, int] = {}
    with db_conn() as conn:
        found = set()
        for start in range(0, len(ids), 500):
            part = ids[start:start + 500]
            marks = ",".join("?" * len(part))
            for r in conn.execute(f"SELECT id, chat_jid, from_me, status FROM messages WHERE id IN ({marks})", part):
                found.add(r["id"])
                if not r["from_me"] and r["status"] != "read":
                    newly[r["chat_jid"]] = newly.get(r["chat_jid"], 0) + 1
            conn.execute(f"UPDATE messages SET status = 'read' WHERE id IN ({marks}) AND from_me = 0", part)
        for mid in ids:
            if mid not in found:
                _stash_early_status(mid, "read")
        for chat_jid, count in newly.items():
            _settle_unread(conn, chat_jid, count)
        recompute_unread_counts(conn, list(newly))
        conn.commit()
    return list(newly)


def _settle_unread(conn, chat_jid: str, newly_read: int) -> None:
    """Descuenta del contador los mensajes recién leídos; si el último recibido ya está leído, queda en 0
    (en WhatsApp leer un chat lee todo lo anterior, aunque esos mensajes viejos no estén guardados acá)."""
    conn.execute("""
        UPDATE chats SET unread_count = CASE
            WHEN (SELECT status FROM messages WHERE chat_jid = ? AND from_me = 0
                  ORDER BY timestamp DESC LIMIT 1) = 'read' THEN 0
            ELSE MAX(0, unread_count - ?) END
        WHERE jid = ?
    """, (chat_jid, newly_read, chat_jid))


def mark_chat_read_elsewhere(chat_jid: str) -> bool:
    """El chat se marcó como leído en el celular: sin avisar de nuevo a WhatsApp. True si cambió algo."""
    with db_conn() as conn:
        row = conn.execute("SELECT unread_count FROM chats WHERE jid = ?", (chat_jid,)).fetchone()
        if not row:
            return False
        changed = conn.execute(
            "UPDATE messages SET status = 'read' WHERE chat_jid = ? AND from_me = 0 AND status != 'read'",
            (chat_jid,)).rowcount
        conn.execute("UPDATE chats SET unread_count = 0, manual_unread = 0 WHERE jid = ?", (chat_jid,))
        conn.commit()
        return bool(row["unread_count"]) or changed > 0


def mark_chat_unread_elsewhere(chat_jid: str) -> bool:
    """El chat se marcó como no leído en el celular: se muestra con contador (como en WhatsApp)."""
    with db_conn() as conn:
        changed = conn.execute("UPDATE chats SET unread_count = 1, manual_unread = 1 WHERE jid = ? AND unread_count = 0",
                               (chat_jid,)).rowcount
        conn.commit()
        return changed > 0


def mark_all_chats_as_read() -> Dict[str, List[str]]:
    """Botón "Marcar todos como leídos": deja todos los chats en 0. Devuelve {chat_jid: [ids]} de los
    mensajes recibidos que no estaban leídos, para avisar a WhatsApp."""
    with db_conn() as conn:
        jids = [r["jid"] for r in conn.execute("SELECT jid FROM chats WHERE unread_count != 0")]
    return {jid: [mid for mid, _ in mark_chat_as_read(jid)] for jid in jids}


_REACTIONS_OF_CHAT = "DELETE FROM reactions WHERE message_id IN (SELECT id FROM messages WHERE chat_jid = ?)"


def set_reaction(msg_id: str, reactor_jid: str, emoji: str, ts: int = 0) -> None:
    """Fija la reacción de una persona a un mensaje (reemplaza la anterior). Emoji vacío = quitarla."""
    if not msg_id or not reactor_jid:
        return
    with db_conn() as conn:
        if not (emoji or "").strip():
            conn.execute("DELETE FROM reactions WHERE message_id = ? AND reactor_jid = ?", (msg_id, reactor_jid))
        else:
            conn.execute("""
                INSERT INTO reactions (message_id, reactor_jid, emoji, timestamp) VALUES (?, ?, ?, ?)
                ON CONFLICT(message_id, reactor_jid) DO UPDATE SET emoji = excluded.emoji, timestamp = excluded.timestamp
            """, (msg_id, reactor_jid, emoji.strip(), ts or int(time.time())))


def get_reactions(msg_ids) -> Dict[str, List[tuple]]:
    """{message_id: [(emoji, reactor_jid), ...]} para los mensajes dados, en orden de llegada."""
    ids = [i for i in msg_ids if i]
    out: Dict[str, List[tuple]] = {}
    with db_conn() as conn:
        for start in range(0, len(ids), 500):
            chunk = ids[start:start + 500]
            marks = ",".join("?" * len(chunk))
            for r in conn.execute(
                f"SELECT message_id, emoji, reactor_jid FROM reactions WHERE message_id IN ({marks}) "
                "ORDER BY timestamp, rowid", chunk
            ):
                out.setdefault(r["message_id"], []).append((r["emoji"], r["reactor_jid"]))
    return out


def clear_chat_messages(chat_jid: str):
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute(_REACTIONS_OF_CHAT, (chat_jid,))
        cursor.execute("DELETE FROM messages WHERE chat_jid = ?", (chat_jid,))
        cursor.execute("UPDATE chats SET last_message_text = 'Sin mensajes' WHERE jid = ?", (chat_jid,))
        conn.commit()


def delete_chat(chat_jid: str):
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute(_REACTIONS_OF_CHAT, (chat_jid,))
        cursor.execute("DELETE FROM messages WHERE chat_jid = ?", (chat_jid,))
        cursor.execute("DELETE FROM chats WHERE jid = ?", (chat_jid,))
        try:
            cursor.execute("DELETE FROM chat_tags WHERE chat_jid = ?", (chat_jid,))
            cursor.execute("DELETE FROM chat_locks WHERE chat_jid = ?", (chat_jid,))
        except sqlite3.OperationalError:
            pass
        conn.commit()


_STATUS_RANK = {"sent": 1, "delivered": 2, "read": 3}

# Estados que llegan de WhatsApp antes de que el mensaje enviado esté guardado (se aplican al guardarlo)
_early_status: Dict[str, str] = {}
_early_status_lock = threading.Lock()
_EARLY_STATUS_MAX = 2000   # también guarda "leído en el celular" de mensajes recibidos que aún no llegaron


def _stash_early_status(msg_id: str, status: str) -> None:
    with _early_status_lock:
        prev = _early_status.get(msg_id)
        if prev is None or _STATUS_RANK[status] > _STATUS_RANK[prev]:
            _early_status[msg_id] = status
        while len(_early_status) > _EARLY_STATUS_MAX:
            _early_status.pop(next(iter(_early_status)))


def update_message_status(msg_id: str, status: str) -> bool:
    """Actualiza el tick de un mensaje propio. Nunca retrocede (leído > entregado > enviado)."""
    if status not in _STATUS_RANK:
        return False
    with db_conn() as conn:
        row = conn.execute("SELECT status, from_me FROM messages WHERE id = ?", (msg_id,)).fetchone()
        if not row:
            _stash_early_status(msg_id, status)   # el aviso llegó antes que el guardado del mensaje
            return False
        if not row["from_me"]:
            return False
        if _STATUS_RANK.get(row["status"], 0) >= _STATUS_RANK[status]:
            return False
        conn.execute("UPDATE messages SET status = ? WHERE id = ?", (status, msg_id))
        conn.commit()
        return True


def get_message(msg_id: str) -> Optional[Dict[str, Any]]:
    with db_conn() as conn:
        row = conn.execute("SELECT * FROM messages WHERE id = ?", (msg_id,)).fetchone()
        return dict(row) if row else None


MAX_PINNED = 3   # igual que WhatsApp: hasta 3 mensajes fijados por chat


def get_pinned_messages(chat_jid: str) -> List[Dict[str, Any]]:
    """Mensajes fijados del chat, el fijado más recientemente primero."""
    with db_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM messages WHERE chat_jid = ? AND pinned_at IS NOT NULL "
            "ORDER BY pinned_at DESC", (chat_jid,)
        ).fetchall()
        return [dict(r) for r in rows]


def set_message_pinned(msg_id: str, pinned: bool) -> bool:
    """Fija o desfija un mensaje. Al pasar el máximo se desfija el fijado más antiguo del chat."""
    with db_conn() as conn:
        row = conn.execute("SELECT chat_jid FROM messages WHERE id = ?", (msg_id,)).fetchone()
        if not row:
            return False
        if not pinned:
            conn.execute("UPDATE messages SET pinned_at = NULL WHERE id = ?", (msg_id,))
            return True
        conn.execute("UPDATE messages SET pinned_at = ? WHERE id = ?", (int(time.time() * 1000), msg_id))
        conn.execute("""
            UPDATE messages SET pinned_at = NULL WHERE chat_jid = ? AND pinned_at IS NOT NULL AND id NOT IN (
                SELECT id FROM messages WHERE chat_jid = ? AND pinned_at IS NOT NULL
                ORDER BY pinned_at DESC LIMIT ?)
        """, (row["chat_jid"], row["chat_jid"], MAX_PINNED))
        return True


def count_messages_since(chat_jid: str, timestamp: int) -> int:
    """Cuántos mensajes del chat son de `timestamp` en adelante (para cargar el historial hasta uno antiguo)."""
    with db_conn() as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM messages WHERE chat_jid = ? AND timestamp >= ?", (chat_jid, timestamp)
        ).fetchone()[0]


def delete_messages(msg_ids) -> None:
    """Elimina mensajes solo de este equipo ('eliminar para mí') y recompone la vista previa de sus chats."""
    ids = [i for i in msg_ids if i]
    if not ids:
        return
    with db_conn() as conn:
        marks = ",".join("?" * len(ids))
        chats = [r[0] for r in conn.execute(f"SELECT DISTINCT chat_jid FROM messages WHERE id IN ({marks})", ids)]
        conn.execute(f"DELETE FROM reactions WHERE message_id IN ({marks})", ids)
        conn.execute(f"DELETE FROM message_edits WHERE message_id IN ({marks})", ids)
        conn.execute(f"DELETE FROM messages WHERE id IN ({marks})", ids)
        refresh_chat_previews(conn.cursor(), chats)
        for jid in chats:
            if not conn.execute("SELECT 1 FROM messages WHERE chat_jid = ?", (jid,)).fetchone():
                conn.execute("UPDATE chats SET last_message_text = 'Sin mensajes' WHERE jid = ?", (jid,))


def _keep_history() -> bool:
    """Ajuste «Conservar mensajes eliminados y versiones editadas» (activado por defecto)."""
    from app.config import get_pref
    return get_pref("keep_deleted")


def _record_edit(cursor, msg_id: str, new_text: str) -> None:
    """Antes de pisar el texto por una edición, guarda la versión anterior (si el ajuste está activo)."""
    if not _keep_history():
        return
    row = cursor.execute("SELECT text FROM messages WHERE id = ?", (msg_id,)).fetchone()
    if not row or (row[0] or "") == (new_text or ""):
        return
    cursor.execute("INSERT INTO message_edits (message_id, text, edited_at) VALUES (?, ?, ?)",
                   (msg_id, row[0] or "", int(time.time())))


def _revoke_in(cursor, msg_id: str) -> None:
    if _keep_history():
        row = cursor.execute(
            "SELECT media_type, text, media_path, media_raw, quoted_text, quoted_sender, transcript "
            "FROM messages WHERE id = ?", (msg_id,)).fetchone()
        if row and row[0] != "revoked":
            import json
            keys = ("media_type", "text", "media_path", "media_raw", "quoted_text", "quoted_sender", "transcript")
            cursor.execute("UPDATE messages SET revoked_orig = ? WHERE id = ?",
                           (json.dumps({k: row[i] for i, k in enumerate(keys)}, ensure_ascii=False), msg_id))
    cursor.execute(
        "UPDATE messages SET media_type = 'revoked', text = '', media_path = NULL, media_raw = NULL, "
        "quoted_text = NULL, quoted_sender = NULL, transcript = NULL, summary = NULL, pinned_at = NULL, "
        "starred_at = CASE WHEN revoked_orig IS NULL THEN NULL ELSE starred_at END WHERE id = ?", (msg_id,))
    cursor.execute("DELETE FROM reactions WHERE message_id = ?", (msg_id,))


def revoke_message_local(msg_id: str) -> bool:
    """Marca un mensaje como eliminado para todos: queda el aviso, sin contenido. False si no existe."""
    with db_conn() as conn:
        row = conn.execute("SELECT chat_jid FROM messages WHERE id = ?", (msg_id,)).fetchone()
        if not row:
            return False
        _revoke_in(conn.cursor(), msg_id)
        refresh_chat_previews(conn.cursor(), [row["chat_jid"]])
        return True


def message_exists(msg_id: str) -> bool:
    if not msg_id:
        return False
    with db_conn() as conn:
        return conn.execute("SELECT 1 FROM messages WHERE id = ?", (msg_id,)).fetchone() is not None


def apply_message_edit(msg_id: str, text: str) -> bool:
    """El remitente editó el mensaje: cambia el texto (no el tipo ni el archivo) y queda marcado como editado."""
    clean = (text or "").strip()
    with db_conn() as conn:
        row = conn.execute("SELECT chat_jid FROM messages WHERE id = ?", (msg_id,)).fetchone()
        if not row or not clean:
            return False
        _record_edit(conn.cursor(), msg_id, clean)
        conn.execute("UPDATE messages SET text = ?, edited = 1, summary = NULL WHERE id = ?", (clean, msg_id))
        refresh_chat_previews(conn.cursor(), [row["chat_jid"]])
        return True


def get_message_edits(msg_id: str) -> List[Dict[str, Any]]:
    """Versiones anteriores de un mensaje editado, de la más vieja a la más nueva."""
    with db_conn() as conn:
        rows = conn.execute("SELECT text, edited_at FROM message_edits WHERE message_id = ? ORDER BY rowid",
                            (msg_id,)).fetchall()
        return [dict(r) for r in rows]


def get_revoked_original(msg_id: str) -> Optional[Dict[str, Any]]:
    """Contenido original de un mensaje que el remitente eliminó, o None si no se conservó."""
    import json
    with db_conn() as conn:
        row = conn.execute("SELECT revoked_orig FROM messages WHERE id = ?", (msg_id,)).fetchone()
    if not row or not row["revoked_orig"]:
        return None
    try:
        return json.loads(row["revoked_orig"])
    except ValueError:
        return None


def set_message_starred(msg_id: str, starred: bool) -> bool:
    with db_conn() as conn:
        cur = conn.execute("UPDATE messages SET starred_at = ? WHERE id = ?",
                           (int(time.time() * 1000) if starred else None, msg_id))
        return cur.rowcount > 0


def get_starred_messages(search: str = "", limit: int = 300) -> List[Dict[str, Any]]:
    """Mensajes destacados de todos los chats (el último destacado primero), con el nombre del chat; filtra por texto/chat."""
    term = search.strip().lower()
    like = f"%{term}%"
    with db_conn() as conn:
        rows = conn.execute(
            "SELECT m.*, COALESCE(NULLIF(c.custom_name, ''), c.name, c.phone, m.chat_jid) AS chat_name "
            "FROM messages m LEFT JOIN chats c ON c.jid = m.chat_jid "
            "WHERE m.starred_at IS NOT NULL AND m.media_type != 'revoked' "
            "AND (? = '' OR LOWER(COALESCE(m.text, '')) LIKE ? OR LOWER(COALESCE(m.transcript, '')) LIKE ? "
            "OR LOWER(COALESCE(c.custom_name, c.name, '')) LIKE ?) "
            "ORDER BY m.starred_at DESC LIMIT ?", (term, like, like, like, limit)).fetchall()
        return [dict(r) for r in rows]


def set_message_poll(msg_id: str, poll: Optional[Dict[str, Any]]) -> bool:
    """Guarda la encuesta de un mensaje (opciones y votos) tal como la manda el gateway."""
    import json
    with db_conn() as conn:
        cur = conn.execute("UPDATE messages SET poll_json = ? WHERE id = ?",
                           (json.dumps(poll, ensure_ascii=False) if poll else None, msg_id))
        return cur.rowcount > 0


def parse_poll(msg: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    import json
    raw = msg.get("poll_json")
    if not raw:
        return None
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) and data.get("options") else None
    except ValueError:
        return None


def get_chat(jid: str) -> Optional[Dict[str, Any]]:
    if not jid:
        return None
    with db_conn() as conn:
        row = conn.execute("SELECT * FROM chats WHERE jid = ?", (jid,)).fetchone()
        return dict(row) if row else None


def set_chat_muted(jid: str, until: int) -> None:
    """Silencia las notificaciones de un chat hasta `until` (epoch); -1 = siempre; 0 = activar sonido."""
    with db_conn() as conn:
        conn.execute("UPDATE chats SET muted_until = ? WHERE jid = ?", (int(until), jid))


def is_chat_muted(chat: Optional[Dict[str, Any]]) -> bool:
    if not chat:
        return False
    until = int(chat.get("muted_until") or 0)
    return until == -1 or until > time.time()


def find_chat_avatar(jid: str) -> Optional[str]:
    """Foto de perfil guardada de un chat o contacto (por jid exacto o por número)."""
    if not jid:
        return None
    clean = jid.split("@")[0].split(":")[0]
    with db_conn() as conn:
        row = conn.execute(
            "SELECT avatar_path FROM chats WHERE (jid = ? OR jid LIKE ?) AND avatar_path IS NOT NULL LIMIT 1",
            (jid, f"{clean}@%")).fetchone()
        path = row["avatar_path"] if row else None
    return path if path and os.path.exists(path) else None


def set_message_media_path(msg_id: str, media_path: str):
    with db_conn() as conn:
        conn.execute("UPDATE messages SET media_path = ? WHERE id = ?", (media_path, msg_id))
        conn.commit()


def set_message_transcript(msg_id: str, transcript: str):
    with db_conn() as conn:
        conn.execute("UPDATE messages SET transcript = ?, transcript_failed = 0 WHERE id = ?", (transcript, msg_id))
        conn.commit()


def set_message_transcript_failed(msg_id: str):
    """Marca que la transcripción automática falló: no se vuelve a intentar sola."""
    with db_conn() as conn:
        conn.execute("UPDATE messages SET transcript_failed = 1 WHERE id = ?", (msg_id,))
        conn.commit()


def is_message_transcript_failed(msg_id: str) -> bool:
    with db_conn() as conn:
        row = conn.execute("SELECT transcript_failed FROM messages WHERE id = ?", (msg_id,)).fetchone()
        return bool(row and row["transcript_failed"])


def get_audios_pending_transcript(since_ts: int, limit: int = 30) -> List[Dict[str, Any]]:
    """Audios recientes (desde `since_ts`) sin transcripción y sin intento fallido, los más nuevos primero."""
    with db_conn() as conn:
        rows = conn.execute(
            "SELECT id, media_type, media_path, media_raw, from_me FROM messages "
            "WHERE media_type = 'audio' AND timestamp >= ? AND COALESCE(transcript, '') = '' "
            "AND COALESCE(transcript_failed, 0) = 0 AND (media_raw IS NOT NULL OR media_path IS NOT NULL) "
            "ORDER BY timestamp DESC LIMIT ?", (since_ts, limit)).fetchall()
        return [dict(r) for r in rows]


def set_message_summary(msg_id: str, summary: Optional[str]):
    with db_conn() as conn:
        conn.execute("UPDATE messages SET summary = ? WHERE id = ?", (summary, msg_id))
        conn.commit()


def get_chats_for_self_analysis(per_chat_limit: int = 200, include_others: bool = True) -> List[Dict[str, Any]]:
    """Mensajes más recientes de cada chat individual donde escribí algo, para el análisis interno.
    include_others=False deja solo mis mensajes (from_me); si no, los del otro van como contexto.
    Devuelve [{'jid','name','messages':[...cronológicos]}] ordenado por actividad reciente."""
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute(f"""
            SELECT m.chat_jid, m.from_me, m.text, m.media_type, m.transcript, m.timestamp, c.name, c.phone
            FROM (
                SELECT *, ROW_NUMBER() OVER (PARTITION BY chat_jid ORDER BY timestamp DESC) AS rn
                FROM messages WHERE (? OR from_me = 1)
                  AND chat_jid IN (SELECT DISTINCT chat_jid FROM messages WHERE from_me = 1)
            ) m JOIN chats c ON c.jid = m.chat_jid
            WHERE m.rn <= ? AND {INDIVIDUAL_CHAT_SQL.replace('jid', 'c.jid')}
            ORDER BY c.last_message_time DESC, m.chat_jid, m.timestamp ASC
        """, (1 if include_others else 0, per_chat_limit))
        chats: Dict[str, Dict[str, Any]] = {}
        for row in cursor.fetchall():
            entry = chats.setdefault(row["chat_jid"], {
                "jid": row["chat_jid"], "name": row["name"] or row["phone"] or row["chat_jid"].split("@")[0],
                "messages": []})
            entry["messages"].append({"from_me": row["from_me"], "text": row["text"],
                                      "media_type": row["media_type"], "transcript": row["transcript"],
                                      "timestamp": row["timestamp"]})
        return list(chats.values())
