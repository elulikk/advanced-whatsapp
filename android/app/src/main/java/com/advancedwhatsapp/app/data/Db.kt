package com.advancedwhatsapp.app.data

import android.content.ContentValues
import android.content.Context
import android.database.Cursor
import android.database.sqlite.SQLiteDatabase
import android.database.sqlite.SQLiteOpenHelper
import com.advancedwhatsapp.app.App
import kotlinx.coroutines.channels.BufferOverflow
import kotlinx.coroutines.flow.MutableSharedFlow
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.util.Collections

data class Chat(
    val jid: String,
    val name: String,
    val phone: String?,
    val unread: Int,
    val lastText: String,
    val lastTime: Long,
    val pinned: Boolean,
    val avatarPath: String?,
    val mutedUntil: Long,
    val tabId: Long = 0,
    val waName: String = "",       // nombre que manda WhatsApp (name es el que se muestra: el propio si hay)
    val customName: String = "",
    val notes: String = "",
    val archived: Boolean = false,
) {
    val isMuted: Boolean
        get() = mutedUntil == -1L || mutedUntil > System.currentTimeMillis() / 1000
    val isGroup: Boolean get() = jid.endsWith("@g.us")
}

data class Message(
    val id: String,
    val chatJid: String,
    val senderJid: String?,
    val senderName: String?,
    val fromMe: Boolean,
    val text: String,
    val mediaType: String,
    val mediaPath: String?,
    val mediaRaw: String?,
    val quotedText: String?,
    val quotedSender: String?,
    val timestamp: Long,
    val status: String,
    val transcript: String?,
    val summary: String?,
    val edited: Boolean,
    val reactions: List<String> = emptyList(),
    val starredAt: Long = 0,          // destacado (epoch ms); 0 = no
    val pinnedAt: Long = 0,           // fijado en el chat (epoch ms); 0 = no
    val hasOriginal: Boolean = false, // eliminado por el remitente pero con el contenido original conservado
    val pollJson: String? = null,     // encuesta con opciones y votos
)

/** Un mensaje destacado con el nombre de su chat. */
data class StarredItem(val message: Message, val chatName: String)

/** Datos del contacto que edita el usuario; se guardan solo en este teléfono y prevalecen sobre los de WhatsApp. */
data class ContactInfo(val jid: String, val waName: String, val customName: String, val notes: String, val phone: String?)

data class Contact(val jid: String, val name: String, val phone: String?, val avatarPath: String?)

/** Pestaña de la lista de chats. unread = cuántos de sus chats tienen mensajes sin leer. */
data class ChatTab(val id: Long, val name: String, val unread: Int) {
    val isBuiltIn: Boolean get() = id == Db.TAB_MARKETPLACES
}

/** Qué chats muestra la lista. */
sealed interface ChatFilter {
    data object Inbox : ChatFilter
    data object Unread : ChatFilter
    data object Groups : ChatFilter
    data class Tab(val id: Long) : ChatFilter
    data object Archived : ChatFilter
    data class Folder(val id: Long) : ChatFilter
}

/** Base local: misma estructura y mismas reglas que app/db.py en la PC. */
object Db {
    private lateinit var helper: SQLiteOpenHelper
    private val db: SQLiteDatabase get() = helper.writableDatabase

    /** Avisa a las pantallas que algo cambió (jid del chat, o "" si cambió más de uno). */
    val changes = MutableSharedFlow<String>(extraBufferCapacity = 64, onBufferOverflow = BufferOverflow.DROP_OLDEST)

    private val rank = mapOf("sent" to 1, "delivered" to 2, "read" to 3)
    private val earlyStatus: MutableMap<String, String> = Collections.synchronizedMap(
        object : LinkedHashMap<String, String>() {
            override fun removeEldestEntry(eldest: MutableMap.MutableEntry<String, String>?) = size > 2000
        }
    )

    const val INDIVIDUAL = "(jid LIKE '%@s.whatsapp.net' OR jid LIKE '%@lid')"
    private const val GROUP = "jid LIKE '%@g.us'"
    private const val VISIBLE = "($INDIVIDUAL OR $GROUP)"
    const val TAB_MARKETPLACES = 1L
    private const val GROUP_PLACEHOLDER = "Grupo"
    private val RESERVED_TAB_NAMES = listOf("todos", "no leídos", "grupos")

    fun init(c: Context) {
        helper = object : SQLiteOpenHelper(c, "magsapp.db", null, 4) {
            override fun onConfigure(d: SQLiteDatabase) {
                d.enableWriteAheadLogging()
            }

            override fun onCreate(d: SQLiteDatabase) {
                d.execSQL(
                    """CREATE TABLE chats (
                        jid TEXT PRIMARY KEY, name TEXT, phone TEXT, unread_count INTEGER DEFAULT 0,
                        last_message_text TEXT, last_message_time INTEGER DEFAULT 0, is_pinned INTEGER DEFAULT 0,
                        avatar_path TEXT, muted_until INTEGER DEFAULT 0, updated_at INTEGER DEFAULT 0,
                        tab_id INTEGER DEFAULT 0, custom_name TEXT, notes TEXT, manual_unread INTEGER DEFAULT 0,
                        is_archived INTEGER DEFAULT 0)"""
                )
                d.execSQL(
                    """CREATE TABLE messages (
                        id TEXT PRIMARY KEY, chat_jid TEXT NOT NULL, sender_jid TEXT, sender_name TEXT,
                        from_me INTEGER DEFAULT 0, text TEXT, media_type TEXT DEFAULT 'text', media_path TEXT,
                        quoted_text TEXT, quoted_sender TEXT, timestamp INTEGER NOT NULL, status TEXT DEFAULT 'sent',
                        media_raw TEXT, transcript TEXT, summary TEXT, edited INTEGER DEFAULT 0,
                        created_at INTEGER DEFAULT 0)"""
                )
                d.execSQL(
                    """CREATE TABLE contacts (
                        jid TEXT PRIMARY KEY, name TEXT, phone TEXT, push_name TEXT, avatar_path TEXT)"""
                )
                d.execSQL(
                    """CREATE TABLE reactions (
                        message_id TEXT NOT NULL, reactor_jid TEXT NOT NULL, emoji TEXT NOT NULL,
                        timestamp INTEGER DEFAULT 0, PRIMARY KEY (message_id, reactor_jid))"""
                )
                d.execSQL("CREATE INDEX idx_messages_chat ON messages(chat_jid, timestamp)")
                d.execSQL("CREATE INDEX idx_chats_time ON chats(last_message_time DESC)")
                d.execSQL("CREATE INDEX idx_contacts_name ON contacts(name)")
                createTabs(d)
                createOrganization(d)
            }

            override fun onUpgrade(d: SQLiteDatabase, o: Int, n: Int) {
                if (o < 2) {
                    d.execSQL("ALTER TABLE chats ADD COLUMN tab_id INTEGER DEFAULT 0")
                    createTabs(d)
                }
                if (o < 3) {
                    for (col in listOf("custom_name TEXT", "notes TEXT", "manual_unread INTEGER DEFAULT 0")) {
                        runCatching { d.execSQL("ALTER TABLE chats ADD COLUMN $col") }
                    }
                }
                if (o < 4) {
                    runCatching { d.execSQL("ALTER TABLE chats ADD COLUMN is_archived INTEGER DEFAULT 0") }
                    createOrganization(d)
                }
            }

            /** Carpetas inteligentes, lectura invisible por chat e índice de archivados (idempotente). */
            private fun createOrganization(d: SQLiteDatabase) {
                d.execSQL(
                    """CREATE TABLE IF NOT EXISTS smart_folders (
                        id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, rules TEXT NOT NULL DEFAULT '{}',
                        sort_order INTEGER DEFAULT 0)"""
                )
                d.execSQL("CREATE TABLE IF NOT EXISTS chat_silent_read (chat_jid TEXT PRIMARY KEY)")
                d.execSQL("CREATE INDEX IF NOT EXISTS idx_chats_archived ON chats(is_archived)")
            }

            private fun createTabs(d: SQLiteDatabase) {
                d.execSQL("CREATE TABLE tabs (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL)")
                d.execSQL("INSERT INTO tabs (id, name) VALUES ($TAB_MARKETPLACES, 'Marketplaces')")
                d.execSQL("CREATE INDEX idx_chats_tab ON chats(tab_id)")
            }
        }
        ensureExtras(db)
        mergeLidDuplicates(db, null)
        recomputeUnread()
    }

    /** Columnas y tabla de la mensajería extra: destacados, fijados, eliminados conservados, ediciones y encuestas. */
    private fun ensureExtras(d: SQLiteDatabase) {
        for (col in listOf("starred_at INTEGER DEFAULT 0", "pinned_at INTEGER DEFAULT 0", "revoked_orig TEXT", "poll_json TEXT")) {
            runCatching { d.execSQL("ALTER TABLE messages ADD COLUMN $col") }
        }
        d.execSQL("CREATE TABLE IF NOT EXISTS message_edits (message_id TEXT NOT NULL, text TEXT, edited_at INTEGER DEFAULT 0)")
        d.execSQL("CREATE INDEX IF NOT EXISTS idx_message_edits_msg ON message_edits(message_id)")
    }

    private fun notifyChange(jid: String = "") {
        changes.tryEmit(jid)
    }

    private fun now() = System.currentTimeMillis() / 1000

    private fun Cursor.str(c: String): String? = getColumnIndex(c).let { if (it < 0 || isNull(it)) null else getString(it) }
    private fun Cursor.lng(c: String): Long = getColumnIndex(c).let { if (it < 0 || isNull(it)) 0 else getLong(it) }

    private fun exists(d: SQLiteDatabase, sql: String, vararg a: String): Boolean =
        d.rawQuery(sql, a).use { it.moveToFirst() }

    // ─── Lectura ─────────────────────────────────────────────────────────────

    private fun Cursor.toChat() = Chat(
        jid = str("jid")!!, name = str("custom_name")?.takeIf { it.isNotBlank() } ?: str("name").orEmpty(),
        phone = str("phone"), unread = lng("unread_count").toInt(),
        lastText = str("last_message_text").orEmpty(), lastTime = lng("last_message_time"), pinned = lng("is_pinned") != 0L,
        avatarPath = str("avatar_path"), mutedUntil = lng("muted_until"), tabId = lng("tab_id"),
        waName = str("name").orEmpty(), customName = str("custom_name").orEmpty(), notes = str("notes").orEmpty(),
        archived = lng("is_archived") != 0L,
    )

    private fun Cursor.toMessage() = Message(
        id = str("id")!!, chatJid = str("chat_jid")!!, senderJid = str("sender_jid"), senderName = str("sender_name"),
        fromMe = lng("from_me") != 0L, text = str("text").orEmpty(), mediaType = str("media_type") ?: "text",
        mediaPath = str("media_path"), mediaRaw = str("media_raw"), quotedText = str("quoted_text"),
        quotedSender = str("quoted_sender"), timestamp = lng("timestamp"), status = str("status") ?: "sent",
        transcript = str("transcript"), summary = str("summary"), edited = lng("edited") != 0L,
        starredAt = lng("starred_at"), pinnedAt = lng("pinned_at"), hasOriginal = !str("revoked_orig").isNullOrEmpty(),
        pollJson = str("poll_json"),
    )

    /**
     * Todos deja afuera lo que tiene pestaña propia, pero al buscar abarca todo para que nunca se pierda un chat.
     * Sin filtro devuelve solo los individuales (lo usa el análisis, que no sirve para grupos).
     */
    fun filterSql(filter: ChatFilter?, searching: Boolean): String = when (filter) {
        null -> INDIVIDUAL
        ChatFilter.Inbox -> if (searching) VISIBLE else "$INDIVIDUAL AND tab_id = 0"
        ChatFilter.Unread -> "$VISIBLE AND unread_count != 0"
        ChatFilter.Groups -> "$GROUP AND tab_id = 0"
        is ChatFilter.Tab -> "$VISIBLE AND tab_id = ${filter.id}"
        ChatFilter.Archived -> "$VISIBLE AND is_archived = 1"
        is ChatFilter.Folder -> VISIBLE   // las reglas de la carpeta se suman en getChats (leen la base)
    }

    /** Acceso para Organization.kt (carpetas, archivados, lectura invisible, galería). */
    fun raw(): SQLiteDatabase = db

    fun changed(jid: String = "") = notifyChange(jid)

    /** Mensajes de un chat de ciertos tipos, del más nuevo al más viejo (galería). */
    fun messagesOfTypes(jid: String, types: List<String>, limit: Int, offset: Int): List<Message> = db.rawQuery(
        "SELECT * FROM messages WHERE chat_jid = ? AND media_type IN (${types.joinToString(",") { "?" }}) " +
            "ORDER BY timestamp DESC LIMIT $limit OFFSET $offset",
        (listOf(jid) + types).toTypedArray(),
    ).use { c -> buildList { while (c.moveToNext()) add(c.toMessage()) } }

    /** Mensajes de un chat cuyo texto contiene [pattern] (LIKE), del más nuevo al más viejo (links de la galería). */
    fun messagesLike(jid: String, pattern: String, limit: Int, offset: Int): List<Message> = db.rawQuery(
        "SELECT * FROM messages WHERE chat_jid = ? AND text LIKE ? ORDER BY timestamp DESC LIMIT $limit OFFSET $offset",
        arrayOf(jid, pattern),
    ).use { c -> buildList { while (c.moveToNext()) add(c.toMessage()) } }

    fun getChats(search: String = "", filter: ChatFilter? = null, limit: Int = 300): List<Chat> {
        val where = StringBuilder(filterSql(filter, search.isNotBlank()))
        val args = mutableListOf<String>()
        if (search.isNotBlank()) {
            val lockedJids = ChatLock.locked.toList()
            if (lockedJids.isEmpty()) {
                where.append(" AND (name LIKE ? OR custom_name LIKE ? OR phone LIKE ? OR last_message_text LIKE ?)")
                repeat(4) { args += "%${search.trim()}%" }
            } else {
                // El texto del último mensaje de un chat bloqueado no es buscable (se vería en los resultados)
                where.append(" AND (name LIKE ? OR custom_name LIKE ? OR phone LIKE ? OR (last_message_text LIKE ? AND jid NOT IN (${lockedJids.joinToString(",") { "?" }})))")
                repeat(4) { args += "%${search.trim()}%" }
                args += lockedJids
            }
        }
        // Archivados: solo se ven en su pestaña (y al buscar, para que nunca se pierda un chat)
        if (filter != null && filter != ChatFilter.Archived && search.isBlank()) where.append(" AND COALESCE(is_archived, 0) = 0")
        if (filter is ChatFilter.Folder) where.append(" AND (${Organization.folderWhere(filter.id)})")
        // Chats ocultos: no salen en la lista ni en la búsqueda hasta escribir el código secreto
        where.append(Organization.hiddenClause())
        return db.rawQuery(
            "SELECT * FROM chats WHERE $where ORDER BY is_pinned DESC, last_message_time DESC, updated_at DESC LIMIT $limit",
            args.toTypedArray(),
        ).use { c -> buildList { while (c.moveToNext()) add(c.toChat()) } }
    }

    fun getTabs(): List<ChatTab> = db.rawQuery(
        """SELECT t.id AS id, t.name AS name, COUNT(c.jid) AS unread FROM tabs t
           LEFT JOIN chats c ON c.tab_id = t.id AND c.unread_count != 0 GROUP BY t.id ORDER BY t.id""",
        null,
    ).use { c -> buildList { while (c.moveToNext()) add(ChatTab(c.lng("id"), c.str("name").orEmpty(), c.lng("unread").toInt())) } }

    fun unreadGroups(): Int = db.rawQuery("SELECT COUNT(*) FROM chats WHERE $GROUP AND tab_id = 0 AND unread_count != 0", null)
        .use { if (it.moveToFirst()) it.getInt(0) else 0 }

    /** Nombre aceptable para una pestaña: sin repetir otra ni las que trae la app. */
    fun isValidTabName(name: String, others: List<String>): Boolean {
        val clean = name.trim().lowercase()
        return clean.isNotEmpty() && clean !in RESERVED_TAB_NAMES && others.none { it.trim().lowercase() == clean }
    }

    fun createTab(name: String) {
        db.insert("tabs", null, ContentValues().apply { put("name", name.trim()) })
        notifyChange()
    }

    fun renameTab(id: Long, name: String) {
        if (id == TAB_MARKETPLACES) return
        db.update("tabs", ContentValues().apply { put("name", name.trim()) }, "id = ?", arrayOf(id.toString()))
        notifyChange()
    }

    /** Sus chats vuelven a Todos (o a Grupos): borrar una pestaña no borra conversaciones. */
    fun deleteTab(id: Long) {
        if (id == TAB_MARKETPLACES) return
        val d = db
        d.beginTransaction()
        try {
            d.update("chats", ContentValues().apply { put("tab_id", 0) }, "tab_id = ?", arrayOf(id.toString()))
            d.delete("tabs", "id = ?", arrayOf(id.toString()))
            d.setTransactionSuccessful()
        } finally {
            d.endTransaction()
        }
        notifyChange()
    }

    fun moveChatToTab(jid: String, tabId: Long) {
        db.update("chats", ContentValues().apply { put("tab_id", tabId) }, "jid = ?", arrayOf(jid))
        notifyChange()
    }

    fun getChat(jid: String): Chat? =
        db.rawQuery("SELECT * FROM chats WHERE jid = ?", arrayOf(jid)).use { if (it.moveToFirst()) it.toChat() else null }

    fun totalChats(): Int = db.rawQuery("SELECT COUNT(*) FROM chats WHERE $INDIVIDUAL", null)
        .use { if (it.moveToFirst()) it.getInt(0) else 0 }

    /** Mensajes en orden cronológico; `before` pagina hacia atrás. */
    fun getMessages(jid: String, limit: Int = 60, before: Long? = null): List<Message> {
        val args = mutableListOf(jid)
        var q = "SELECT * FROM messages WHERE chat_jid = ?"
        if (before != null) {
            q += " AND timestamp < ?"
            args += before.toString()
        }
        q += " ORDER BY timestamp DESC, created_at DESC LIMIT $limit"
        val rows = db.rawQuery(q, args.toTypedArray()).use { c -> buildList { while (c.moveToNext()) add(c.toMessage()) } }
        return withReactions(rows).reversed()
    }

    /** Todos los stickers del historial (recibidos y enviados), del más nuevo al más viejo. */
    fun stickerMessages(): List<Message> =
        db.rawQuery("SELECT * FROM messages WHERE media_type = 'sticker' ORDER BY timestamp DESC", null)
            .use { c -> buildList { while (c.moveToNext()) add(c.toMessage()) } }

    fun getMessage(id: String): Message? =
        db.rawQuery("SELECT * FROM messages WHERE id = ?", arrayOf(id)).use { if (it.moveToFirst()) it.toMessage() else null }

    private fun withReactions(rows: List<Message>): List<Message> {
        if (rows.isEmpty()) return rows
        val map = HashMap<String, MutableList<String>>()
        rows.chunked(500).forEach { part ->
            val marks = part.joinToString(",") { "?" }
            db.rawQuery(
                "SELECT message_id, emoji FROM reactions WHERE message_id IN ($marks) ORDER BY timestamp, rowid",
                part.map { it.id }.toTypedArray(),
            ).use { c -> while (c.moveToNext()) map.getOrPut(c.getString(0)) { mutableListOf() }.add(c.getString(1)) }
        }
        return rows.map { m -> map[m.id]?.let { m.copy(reactions = it) } ?: m }
    }

    fun unreadMessages(jid: String, limit: Int = 8): List<Message> =
        db.rawQuery(
            "SELECT * FROM messages WHERE chat_jid = ? AND from_me = 0 AND status != 'read' ORDER BY timestamp DESC LIMIT $limit",
            arrayOf(jid),
        ).use { c -> buildList { while (c.moveToNext()) add(c.toMessage()) } }.reversed()

    /** Los últimos [n] mensajes del chat en orden cronológico, sin reacciones (barato: lo usa «Sin responder»). */
    fun lastMessages(jid: String, n: Int): List<Message> =
        db.rawQuery("SELECT * FROM messages WHERE chat_jid = ? ORDER BY timestamp DESC LIMIT $n", arrayOf(jid))
            .use { c -> buildList { while (c.moveToNext()) add(c.toMessage()) } }.reversed()

    /** Mensajes cuyo texto o transcripción contiene alguno de los fragmentos (LIKE), sin los de chats bloqueados. */
    fun searchMessages(likes: List<String>, scan: Int): List<Message> {
        if (likes.isEmpty()) return emptyList()
        val locked = ChatLock.locked.toList()
        val args = mutableListOf<String>()
        val cond = likes.joinToString(" OR ") { "(text LIKE ? OR transcript LIKE ?)" }
        likes.forEach {
            args += "%$it%"
            args += "%$it%"
        }
        var q = "SELECT * FROM messages WHERE ($cond) AND COALESCE(media_type, 'text') != 'revoked'"
        if (locked.isNotEmpty()) {
            q += " AND chat_jid NOT IN (${locked.joinToString(",") { "?" }})"
            args += locked
        }
        q += " ORDER BY timestamp DESC LIMIT $scan"
        return db.rawQuery(q, args.toTypedArray()).use { c -> buildList { while (c.moveToNext()) add(c.toMessage()) } }
    }

    fun getContacts(search: String = ""): List<Contact> {
        val args = mutableListOf<String>()
        var q = """SELECT c.jid AS jid, COALESCE(NULLIF(c.name,''), NULLIF(c.push_name,''), c.phone, c.jid) AS name,
                   c.phone AS phone, c.avatar_path AS avatar_path FROM contacts c
                   WHERE (c.jid LIKE '%@s.whatsapp.net') AND c.name IS NOT NULL AND c.name != '' AND c.name != 'Yo'"""
        if (search.isNotBlank()) {
            q += " AND (c.name LIKE ? OR c.phone LIKE ?)"
            repeat(2) { args += "%${search.trim()}%" }
        }
        q += " ORDER BY name COLLATE NOCASE ASC LIMIT 300"
        return db.rawQuery(q, args.toTypedArray()).use { c ->
            buildList { while (c.moveToNext()) add(Contact(c.str("jid")!!, c.str("name").orEmpty(), c.str("phone"), c.str("avatar_path"))) }
        }
    }

    fun messageExists(id: String) = exists(db, "SELECT 1 FROM messages WHERE id = ?", id)

    // ─── Escritura de mensajes ───────────────────────────────────────────────

    fun preview(mediaType: String, text: String): String = when (mediaType) {
        "image" -> if (text.isNotEmpty()) "📷 Foto: $text" else "📷 Foto"
        "video" -> if (text.isNotEmpty()) "🎥 Video: $text" else "🎥 Video"
        "document" -> if (text.isNotEmpty()) "📄 Archivo: $text" else "📄 Archivo"
        "audio" -> "🎵 Nota de voz"
        "sticker" -> "🎨 Sticker"
        "location" -> if (text.isNotEmpty()) "📍 Ubicación: $text" else "📍 Ubicación"
        "contact" -> if (text.isNotEmpty()) "👤 $text" else "👤 Contacto"
        "revoked" -> "🚫 Mensaje eliminado"
        else -> text
    }

    private fun isPlaceholderName(n: String) =
        n.isEmpty() || n.first().isDigit() || n.startsWith("Usuario") || n.startsWith("Contacto") || n == "Grupo"

    private fun ensureChat(d: SQLiteDatabase, jid: String, name: String?, phone: String? = null) {
        val clean = jid.substringBefore('@').substringBefore(':')
        val cur = d.rawQuery("SELECT name FROM chats WHERE jid = ?", arrayOf(jid)).use {
            if (it.moveToFirst()) (it.str("name").orEmpty()) else null
        }
        if (cur == null) {
            d.insert("chats", null, ContentValues().apply {
                put("jid", jid)
                put("name", name ?: phone?.let { "+$it" } ?: if (jid.endsWith("@g.us")) GROUP_PLACEHOLDER else clean)
                put("phone", phone)
                put("last_message_text", "")
                put("last_message_time", now())
                put("updated_at", now())
            })
        } else if (name != null && (cur == clean || cur.isEmpty() || cur.all(Char::isDigit))) {
            d.update("chats", ContentValues().apply {
                put("name", name)
                if (phone != null) put("phone", phone)
            }, "jid = ?", arrayOf(jid))
        }
    }

    /** Para las copias y la sincronización (data/Backup.kt): acceso directo a la base. */
    fun raw(): SQLiteDatabase = db

    /** Después de unir una copia: vistas previas y contadores de los chats tocados. */
    fun refreshAfterMerge(jids: Collection<String>) {
        if (jids.isNotEmpty()) {
            refreshPreviews(db, jids)
            recomputeUnread(jids)
        }
        notifyChange()
    }

    /** Los documentos de sincronización con la PC («awsync-….awsync») viajan por «Mi número» pero no son mensajes. */
    fun isSyncDoc(mediaType: String?, text: String?) = mediaType == "document" && (text ?: "").startsWith("awsync-")

    private fun refreshPreviews(d: SQLiteDatabase, jids: Collection<String>) {
        for (jid in jids.toSet()) {
            d.rawQuery(
                "SELECT text, media_type, timestamp FROM messages WHERE chat_jid = ? ORDER BY timestamp DESC, created_at DESC LIMIT 1",
                arrayOf(jid),
            ).use { c ->
                if (c.moveToFirst()) {
                    d.update("chats", ContentValues().apply {
                        put("last_message_text", preview(c.str("media_type") ?: "text", c.str("text").orEmpty()))
                        put("last_message_time", c.lng("timestamp"))
                    }, "jid = ?", arrayOf(jid))
                }
            }
        }
    }

    /** Guarda un mensaje; false si es un mensaje fantasma vacío. */
    fun saveMessage(
        id: String, chat: String, sender: String?, senderName: String?, fromMe: Boolean, text: String, timestamp: Long,
        mediaType: String = "text", mediaPath: String? = null, quotedText: String? = null, quotedSender: String? = null,
        status: String = "sent", chatName: String? = null, mediaRaw: String? = null, alreadyRead: Boolean = false,
    ): Boolean {
        val clean = text.trim()
        if (clean.isEmpty() && mediaType == "text" && mediaPath == null && quotedText == null) return false
        if (isSyncDoc(mediaType, clean)) return false
        val valid = senderName?.trim()?.takeIf { !fromMe && it.isNotEmpty() && it.lowercase() !in listOf("yo", "null", "none") }
        // Un grupo nunca toma el nombre de quien escribió
        val label = chatName ?: valid.takeUnless { chat.endsWith("@g.us") }
        val d = db
        d.beginTransaction()
        try {
            ensureChat(d, chat, label)
            val isNew = !exists(d, "SELECT 1 FROM messages WHERE id = ?", id)
            var st = status
            var read = alreadyRead
            if (isNew) {
                val early = earlyStatus.remove(id)
                if (fromMe && early != null && (rank[early] ?: 0) > (rank[st] ?: 0)) st = early
                else if (!fromMe && early == "read") read = true
            }
            if (!fromMe && read) st = "read"
            if (isNew) {
                d.insert("messages", null, ContentValues().apply {
                    put("id", id); put("chat_jid", chat); put("sender_jid", sender); put("sender_name", senderName)
                    put("from_me", if (fromMe) 1 else 0); put("text", clean); put("media_type", mediaType)
                    put("media_path", mediaPath); put("quoted_text", quotedText); put("quoted_sender", quotedSender)
                    put("timestamp", timestamp); put("status", st); put("media_raw", mediaRaw)
                    put("created_at", System.currentTimeMillis())
                })
            } else {
                val prev = getMessageIn(d, id)
                d.update("messages", ContentValues().apply {
                    if (prev?.text != clean) putNull("summary")
                    put("text", clean); put("media_type", mediaType)
                    if (mediaPath != null) put("media_path", mediaPath)
                    if (quotedText != null) put("quoted_text", quotedText)
                    if (quotedSender != null) put("quoted_sender", quotedSender)
                    if (mediaRaw != null) put("media_raw", mediaRaw)
                }, "id = ?", arrayOf(id))
            }
            val increment = if (isNew && !fromMe && !read && com.advancedwhatsapp.app.AppState.let { !(it.inForeground && it.activeChat == chat) }) 1 else 0
            d.execSQL(
                """UPDATE chats SET unread_count = unread_count + ?, updated_at = ?,
                   name = CASE WHEN ? IS NOT NULL AND (name IS NULL OR name = '' OR name GLOB '[0-9]*' OR name LIKE 'Usuario%'
                        OR name LIKE 'Contacto%' OR name = ?) THEN ? ELSE name END WHERE jid = ?""",
                arrayOf<Any?>(increment, now(), label, GROUP_PLACEHOLDER, label, chat),
            )
            refreshPreviews(d, listOf(chat))
            // Un mensaje nuevo saca al chat de Archivados (salvo que esté silenciado); el historial viejo no
            if (isNew && (fromMe || !read) && timestamp >= now() - 3600) Organization.unarchiveOnMessage(d, chat)
            if (chat.endsWith("@lid")) mergeLidDuplicates(d, chat)
            d.setTransactionSuccessful()
        } finally {
            d.endTransaction()
        }
        notifyChange(chat)
        return true
    }

    private fun getMessageIn(d: SQLiteDatabase, id: String): Message? =
        d.rawQuery("SELECT * FROM messages WHERE id = ?", arrayOf(id)).use { if (it.moveToFirst()) it.toMessage() else null }

    fun saveSyncBatch(chats: JSONArray?, contacts: JSONArray?, messages: JSONArray?, renameChats: Boolean) {
        val d = db
        val touched = HashSet<String>()
        d.beginTransaction()
        try {
            contacts?.let { arr ->
                for (i in 0 until arr.length()) {
                    val c = arr.optJSONObject(i) ?: continue
                    val phone = c.s("phone")
                    val lid = c.s("lid")
                    val jid = c.s("jid") ?: phone?.let { "$it@s.whatsapp.net" } ?: lid?.let { "$it@lid" } ?: continue
                    saveContactIn(d, jid, c.s("name"), phone, c.s("pushName"), c.s("avatar_path"))
                    val name = c.s("name")?.trim().orEmpty()
                    if (renameChats && isRealName(name)) {
                        for (j in setOfNotNull(jid, lid?.let { "$it@lid" })) {
                            d.update("chats", ContentValues().apply { put("name", name) }, "jid = ?", arrayOf(j))
                        }
                    }
                }
            }
            chats?.let { arr ->
                for (i in 0 until arr.length()) {
                    val ch = arr.optJSONObject(i) ?: continue
                    val jid = ch.s("jid") ?: continue
                    upsertChat(d, jid, ch)
                }
            }
            messages?.let { arr ->
                for (i in 0 until arr.length()) {
                    val m = arr.optJSONObject(i) ?: continue
                    if (saveHistoryMessage(d, m)) touched += m.getString("chat_jid")
                }
            }
            refreshPreviews(d, touched)
            mergeLidDuplicates(d, null)
            d.setTransactionSuccessful()
        } finally {
            d.endTransaction()
        }
        notifyChange()
    }

    private fun upsertChat(d: SQLiteDatabase, jid: String, ch: JSONObject) {
        val name = ch.s("name")
        val phone = ch.s("phone")
        val time = ch.optLong("last_message_time")
        val unread = if (ch.has("unread_count") && !ch.isNull("unread_count")) ch.optInt("unread_count") else null
        val cur = d.rawQuery("SELECT last_message_time FROM chats WHERE jid = ?", arrayOf(jid)).use {
            if (it.moveToFirst()) it.getLong(0) else null
        }
        if (cur == null) {
            d.insert("chats", null, ContentValues().apply {
                put("jid", jid); put("name", name); put("phone", phone); put("unread_count", unread ?: 0)
                put("last_message_text", ""); put("last_message_time", time); put("avatar_path", ch.s("avatar_path"))
            })
        } else {
            d.update("chats", ContentValues().apply {
                if (name != null && name != GROUP_PLACEHOLDER && !name.first().isDigit() && !name.startsWith("Usuario")) put("name", name)
                if (phone != null) put("phone", phone)
                if (unread != null) put("unread_count", unread)
                ch.s("avatar_path")?.let { put("avatar_path", it) }
                if (time > cur) put("last_message_time", time)
            }, "jid = ?", arrayOf(jid))
        }
    }

    private fun saveHistoryMessage(d: SQLiteDatabase, m: JSONObject): Boolean {
        val id = m.s("message_id") ?: return false
        val chat = m.s("chat_jid") ?: return false
        val text = m.optString("text").takeUnless { m.isNull("text") }.orEmpty().trim()
        val mediaType = m.s("media_type") ?: "text"
        if (isSyncDoc(mediaType, text)) return false
        val prev = getMessageIn(d, id)
        if (m.s("revoke_of") != null || m.s("edit_of") != null) {
            if (prev == null) return false
            if (m.s("revoke_of") != null) revokeIn(d, id)
            else if (text.isNotEmpty()) {
                recordEdit(d, id, text)
                d.update("messages", ContentValues().apply {
                    put("text", text); put("edited", 1); putNull("summary")
                }, "id = ?", arrayOf(id))
            }
            return true
        }
        if (text.isEmpty() && mediaType == "text" && m.s("media_path") == null && m.s("quoted_text") == null) return false
        val status = m.s("status") ?: "sent"
        if (prev == null) {
            d.insert("messages", null, ContentValues().apply {
                put("id", id); put("chat_jid", chat); put("sender_jid", m.s("sender_jid")); put("sender_name", m.s("sender_name"))
                put("from_me", if (m.optBoolean("from_me")) 1 else 0); put("text", text); put("media_type", mediaType)
                put("media_path", m.s("media_path")); put("quoted_text", m.s("quoted_text")); put("quoted_sender", m.s("quoted_sender"))
                put("timestamp", m.optLong("timestamp", now())); put("status", status); put("media_raw", m.s("media_raw"))
                put("created_at", System.currentTimeMillis())
            })
            ensureChat(d, chat, null)
        } else {
            d.update("messages", ContentValues().apply {
                m.s("media_raw")?.let { put("media_raw", it) }
                if (prev.mediaPath == null) m.s("media_path")?.let { put("media_path", it) }
                if (prev.fromMe && (rank[status] ?: 1) > (rank[prev.status] ?: 1)) put("status", status)
            }.also { if (it.size() == 0) return true }, "id = ?", arrayOf(id))
        }
        return true
    }

    private fun JSONObject.s(k: String): String? = if (isNull(k)) null else optString(k).ifEmpty { null }

    private fun saveContactIn(d: SQLiteDatabase, jid: String, name: String?, phone: String?, push: String?, avatar: String?) {
        val values = ContentValues().apply {
            if (!name.isNullOrEmpty()) put("name", name)
            if (!phone.isNullOrEmpty()) put("phone", phone)
            if (!push.isNullOrEmpty()) put("push_name", push)
            if (avatar != null) put("avatar_path", avatar)
        }
        if (exists(d, "SELECT 1 FROM contacts WHERE jid = ?", jid)) {
            if (values.size() > 0) d.update("contacts", values, "jid = ?", arrayOf(jid))
        } else {
            values.put("jid", jid)
            d.insert("contacts", null, values)
        }
    }

    fun saveContact(jid: String, name: String?, phone: String?, push: String?) {
        val d = db
        saveContactIn(d, jid, name, phone, push, null)
        if (!name.isNullOrEmpty() && !name.all(Char::isDigit)) {
            d.execSQL(
                "UPDATE chats SET name = ?, phone = COALESCE(?, phone) WHERE jid = ? AND (name IS NULL OR name = '' OR name GLOB '[0-9]*')",
                arrayOf<Any?>(name, phone, jid),
            )
        }
    }

    /** Abre (o crea) el chat de un número o contacto para escribirle. */
    fun openChat(jid: String, name: String?, phone: String?) {
        ensureChat(db, jid, name, phone)
        notifyChange()
    }

    fun updateAvatar(jid: String, path: String) {
        val base = jid.substringBefore('@').substringBefore(':')
        db.execSQL("UPDATE chats SET avatar_path = ? WHERE jid = ? OR jid LIKE ?", arrayOf<Any?>(path, jid, "$base%"))
        db.execSQL("UPDATE contacts SET avatar_path = ? WHERE jid = ? OR jid LIKE ?", arrayOf<Any?>(path, jid, "$base%"))
        notifyChange()
    }

    /** Antes de pisar el texto por una edición, guarda la versión anterior (si el ajuste está activo). */
    private fun recordEdit(d: SQLiteDatabase, id: String, newText: String) {
        if (!Extras.keepDeleted) return
        val old = getMessageIn(d, id)?.text ?: return
        if (old == newText) return
        d.insert("message_edits", null, ContentValues().apply { put("message_id", id); put("text", old); put("edited_at", now()) })
    }

    private fun revokeIn(d: SQLiteDatabase, id: String) {
        if (Extras.keepDeleted) {
            getMessageIn(d, id)?.takeIf { it.mediaType != "revoked" }?.let { m ->
                val orig = JSONObject().put("media_type", m.mediaType).put("text", m.text).put("media_path", m.mediaPath)
                    .put("media_raw", m.mediaRaw).put("quoted_text", m.quotedText).put("quoted_sender", m.quotedSender)
                    .put("transcript", m.transcript)
                d.update("messages", ContentValues().apply { put("revoked_orig", orig.toString()) }, "id = ?", arrayOf(id))
            }
        }
        d.update("messages", ContentValues().apply {
            put("text", ""); put("media_type", "revoked"); putNull("media_path"); putNull("media_raw")
            putNull("quoted_text"); putNull("quoted_sender"); putNull("transcript"); putNull("summary"); put("pinned_at", 0)
        }, "id = ?", arrayOf(id))
        d.delete("reactions", "message_id = ?", arrayOf(id))
    }

    fun revokeMessage(id: String) {
        val chat = getMessage(id)?.chatJid ?: return
        revokeIn(db, id)
        refreshPreviews(db, listOf(chat))
        notifyChange(chat)
    }

    fun applyEdit(id: String, text: String) {
        val chat = getMessage(id)?.chatJid ?: return
        recordEdit(db, id, text.trim())
        db.update("messages", ContentValues().apply {
            put("text", text.trim()); put("edited", 1); putNull("summary")
        }, "id = ?", arrayOf(id))
        refreshPreviews(db, listOf(chat))
        notifyChange(chat)
    }

    // ─── Mensajería extra: destacados, fijados, versiones, eliminados conservados y encuestas ───

    fun setStarred(id: String, on: Boolean) {
        db.update("messages", ContentValues().apply { put("starred_at", if (on) System.currentTimeMillis() else 0L) }, "id = ?", arrayOf(id))
        notifyChange(getMessage(id)?.chatJid ?: "")
    }

    /** Fija o desfija; en cada chat quedan como mucho 3 fijados (se suelta el más viejo). */
    fun setMessagePinned(id: String, on: Boolean) {
        val chat = getMessage(id)?.chatJid ?: return
        db.update("messages", ContentValues().apply { put("pinned_at", if (on) System.currentTimeMillis() else 0L) }, "id = ?", arrayOf(id))
        if (on) {
            db.execSQL(
                "UPDATE messages SET pinned_at = 0 WHERE chat_jid = ? AND pinned_at > 0 AND id NOT IN " +
                    "(SELECT id FROM messages WHERE chat_jid = ? AND pinned_at > 0 ORDER BY pinned_at DESC LIMIT 3)",
                arrayOf<Any?>(chat, chat),
            )
        }
        notifyChange(chat)
    }

    /** Destacados de todos los chats, el último destacado primero; [search] filtra por texto, transcripción o chat. */
    fun starredMessages(search: String): List<StarredItem> {
        val term = search.trim().lowercase()
        val like = "%$term%"
        val sql = "SELECT m.*, COALESCE(NULLIF(c.custom_name, ''), c.name, m.chat_jid) AS chat_name " +
            "FROM messages m LEFT JOIN chats c ON c.jid = m.chat_jid " +
            "WHERE m.starred_at > 0 AND m.media_type != 'revoked' AND (? = '' OR LOWER(COALESCE(m.text, '')) LIKE ? " +
            "OR LOWER(COALESCE(m.transcript, '')) LIKE ? OR LOWER(COALESCE(c.custom_name, c.name, '')) LIKE ?) " +
            "ORDER BY m.starred_at DESC LIMIT 300"
        return db.rawQuery(sql, arrayOf(term, like, like, like)).use { c ->
            buildList { while (c.moveToNext()) add(StarredItem(c.toMessage(), c.str("chat_name").orEmpty())) }
        }
    }

    /** Versiones anteriores de un mensaje editado (texto, momento del cambio), de la más vieja a la más nueva. */
    fun editsOf(id: String): List<Pair<String, Long>> =
        db.rawQuery("SELECT text, edited_at FROM message_edits WHERE message_id = ? ORDER BY rowid", arrayOf(id)).use { c ->
            buildList { while (c.moveToNext()) add((c.getString(0) ?: "") to c.getLong(1)) }
        }

    /** Contenido original de un mensaje que el remitente eliminó, o null si no se conservó. */
    fun revokedOriginal(id: String): JSONObject? =
        db.rawQuery("SELECT revoked_orig FROM messages WHERE id = ?", arrayOf(id)).use { c ->
            if (c.moveToFirst() && !c.isNull(0)) runCatching { JSONObject(c.getString(0)) }.getOrNull() else null
        }

    fun setPoll(id: String, json: String) {
        db.update("messages", ContentValues().apply { put("poll_json", json) }, "id = ?", arrayOf(id))
        notifyChange(getMessage(id)?.chatJid ?: "")
    }

    fun updateMessageStatus(id: String, status: String) {
        if (status !in rank) return
        val m = getMessage(id)
        if (m == null) {
            val prev = earlyStatus[id]
            if (prev == null || (rank[status] ?: 0) > (rank[prev] ?: 0)) earlyStatus[id] = status
            return
        }
        if (!m.fromMe || (rank[status] ?: 0) <= (rank[m.status] ?: 0)) return
        db.update("messages", ContentValues().apply { put("status", status) }, "id = ?", arrayOf(id))
        notifyChange(m.chatJid)
    }

    fun setReaction(id: String, reactor: String, emoji: String, ts: Long, chat: String?) {
        if (emoji.isBlank()) {
            db.delete("reactions", "message_id = ? AND reactor_jid = ?", arrayOf(id, reactor))
        } else {
            db.insertWithOnConflict("reactions", null, ContentValues().apply {
                put("message_id", id); put("reactor_jid", reactor); put("emoji", emoji.trim())
                put("timestamp", if (ts > 0) ts else now())
            }, SQLiteDatabase.CONFLICT_REPLACE)
        }
        notifyChange(chat ?: "")
    }

    /** Audios desde [since] (epoch seg.) sin transcripción y con archivo o clave para bajarlo; los más nuevos primero. */
    fun audiosPendingTranscript(since: Long, limit: Int): List<Message> = db.rawQuery(
        "SELECT * FROM messages WHERE media_type = 'audio' AND timestamp >= ? AND COALESCE(transcript, '') = '' " +
            "AND (media_raw IS NOT NULL OR media_path IS NOT NULL) ORDER BY timestamp DESC LIMIT ?",
        arrayOf(since.toString(), limit.toString()),
    ).use { c -> buildList { while (c.moveToNext()) add(c.toMessage()) } }

    fun setMediaPath(id: String, path: String) = updateMessage(id) { put("media_path", path) }
    fun setTranscript(id: String, t: String) = updateMessage(id) { put("transcript", t); putNull("summary") }
    fun setSummary(id: String, s: String?) = updateMessage(id) { if (s == null) putNull("summary") else put("summary", s) }

    private fun updateMessage(id: String, f: ContentValues.() -> Unit) {
        val chat = getMessage(id)?.chatJid ?: return
        db.update("messages", ContentValues().apply(f), "id = ?", arrayOf(id))
        notifyChange(chat)
    }

    // ─── Leídos ──────────────────────────────────────────────────────────────

    /** Marca el chat como leído; devuelve los ids para avisarle a WhatsApp. */
    fun markChatRead(jid: String): List<String> {
        val d = db
        val ids = d.rawQuery(
            "SELECT id FROM messages WHERE chat_jid = ? AND from_me = 0 AND status != 'read' ORDER BY timestamp DESC LIMIT 200",
            arrayOf(jid),
        ).use { c -> buildList { while (c.moveToNext()) add(c.getString(0)) } }
        val hadUnread = (getChat(jid)?.unread ?: 0) != 0
        d.update("chats", ContentValues().apply { put("unread_count", 0); put("manual_unread", 0) }, "jid = ?", arrayOf(jid))
        d.execSQL("UPDATE messages SET status = 'read' WHERE chat_jid = ? AND from_me = 0", arrayOf(jid))
        if (ids.isNotEmpty() || hadUnread) notifyChange(jid)
        return ids
    }

    fun markMessagesReadElsewhere(ids: List<String>) {
        val d = db
        val newly = HashMap<String, Int>()
        val found = HashSet<String>()
        ids.distinct().chunked(500).forEach { part ->
            val marks = part.joinToString(",") { "?" }
            d.rawQuery("SELECT id, chat_jid, from_me, status FROM messages WHERE id IN ($marks)", part.toTypedArray()).use { c ->
                while (c.moveToNext()) {
                    found += c.getString(0)
                    if (c.getInt(2) == 0 && c.getString(3) != "read") newly.merge(c.getString(1), 1, Int::plus)
                }
            }
            d.execSQL("UPDATE messages SET status = 'read' WHERE id IN ($marks) AND from_me = 0", part.toTypedArray())
        }
        ids.filter { it !in found }.forEach { earlyStatus[it] = "read" }
        for ((chat, count) in newly) {
            d.execSQL(
                """UPDATE chats SET unread_count = CASE
                     WHEN (SELECT status FROM messages WHERE chat_jid = ? AND from_me = 0 ORDER BY timestamp DESC LIMIT 1) = 'read' THEN 0
                     ELSE MAX(0, unread_count - ?) END WHERE jid = ?""",
                arrayOf<Any?>(chat, count, chat),
            )
        }
        if (newly.isNotEmpty()) {
            recomputeUnread(newly.keys)
            newly.keys.forEach { notifyChange(it) }
        }
    }

    fun markChatReadElsewhere(jid: String) {
        db.execSQL("UPDATE messages SET status = 'read' WHERE chat_jid = ? AND from_me = 0 AND status != 'read'", arrayOf(jid))
        db.update("chats", ContentValues().apply { put("unread_count", 0); put("manual_unread", 0) }, "jid = ?", arrayOf(jid))
        notifyChange(jid)
    }

    fun markChatUnreadElsewhere(jid: String) {
        db.update("chats", ContentValues().apply { put("unread_count", 1); put("manual_unread", 1) }, "jid = ? AND unread_count = 0", arrayOf(jid))
        notifyChange(jid)
    }

    /**
     * Recalcula unread_count desde los mensajes recibidos que siguen sin leer (misma regla que app/db.py): el contador
     * nunca supera lo realmente pendiente y un chat sin pendientes queda en 0, salvo el marcado como no leído a mano.
     */
    fun recomputeUnread(jids: Collection<String>? = null) {
        val real = "(SELECT COUNT(*) FROM messages m WHERE m.chat_jid = chats.jid AND m.from_me = 0 AND m.status != 'read' AND m.media_type != 'revoked')"
        val target = "CASE WHEN $real > 0 THEN MIN(unread_count, $real) WHEN manual_unread = 1 THEN 1 ELSE 0 END"
        val where = if (jids.isNullOrEmpty()) "" else " AND jid IN (${jids.joinToString(",") { "?" }})"
        val changed = db.compileStatement("UPDATE chats SET unread_count = $target WHERE unread_count != $target$where").use { st ->
            jids?.forEachIndexed { i, j -> st.bindString(i + 1, j) }
            st.executeUpdateDelete()
        }
        if (changed > 0) notifyChange()
    }

    fun getContactInfo(jid: String): ContactInfo? =
        db.rawQuery("SELECT * FROM chats WHERE jid = ?", arrayOf(jid)).use { c ->
            if (!c.moveToFirst()) return null
            ContactInfo(jid, c.str("name").orEmpty(), c.str("custom_name").orEmpty(), c.str("notes").orEmpty(), c.str("phone"))
        }

    /** Nombre propio y notas del contacto: solo en este teléfono, prevalecen sobre el nombre de WhatsApp. */
    fun setContactInfo(jid: String, customName: String, notes: String) {
        db.update("chats", ContentValues().apply {
            if (customName.isBlank()) putNull("custom_name") else put("custom_name", customName.trim())
            if (notes.isBlank()) putNull("notes") else put("notes", notes.trim())
        }, "jid = ?", arrayOf(jid))
        notifyChange()
    }

    fun setPinned(jid: String, pinned: Boolean) {
        db.update("chats", ContentValues().apply { put("is_pinned", if (pinned) 1 else 0) }, "jid = ?", arrayOf(jid))
        notifyChange()
    }

    fun setMuted(jid: String, until: Long) {
        db.update("chats", ContentValues().apply { put("muted_until", until) }, "jid = ?", arrayOf(jid))
        notifyChange()
    }

    /** Conversaciones recientes para el análisis interno: cada chat con sus últimos mensajes. */
    fun recentChatsWithMessages(maxChats: Int, perChat: Int): List<Pair<Chat, List<Message>>> =
        getChats(limit = maxChats).map { it to getMessages(it.jid, perChat) }.filter { it.second.isNotEmpty() }

    // ─── Duplicados LID / teléfono ───────────────────────────────────────────

    private fun lidMap(): JSONObject = runCatching {
        JSONObject(File(App.dataDir, "lid_phone_map.json").readText())
    }.getOrDefault(JSONObject())

    fun isRealName(n: String) = n.isNotEmpty() && !n.all(Char::isDigit) &&
        !n.startsWith("Contacto (") && !n.startsWith("Usuario (") && !n.startsWith("+")

    /** Une el chat @lid con el chat de teléfono del mismo contacto. */
    private fun mergeLidDuplicates(d: SQLiteDatabase, only: String?) {
        val map = lidMap()
        val lids = (if (only != null) d.rawQuery("SELECT jid, name FROM chats WHERE jid = ? AND jid LIKE '%@lid'", arrayOf(only))
        else d.rawQuery("SELECT jid, name FROM chats WHERE jid LIKE '%@lid'", null))
            .use { c -> buildList { while (c.moveToNext()) add(c.getString(0) to c.str("name").orEmpty().trim()) } }
        var merged = false
        for ((lid, name) in lids) {
            var target: String? = null
            val phone = map.optString(lid.substringBefore('@')).ifEmpty { null }
            if (phone != null) {
                val cand = phone.substringBefore('@') + "@s.whatsapp.net"
                if (exists(d, "SELECT 1 FROM chats WHERE jid = ?", cand)) target = cand
            }
            if (target == null && isRealName(name)) {
                val cands = d.rawQuery("SELECT jid FROM chats WHERE name = ? AND jid LIKE '%@s.whatsapp.net'", arrayOf(name))
                    .use { c -> buildList { while (c.moveToNext()) add(c.getString(0)) } }
                if (cands.size == 1) target = cands[0]
            }
            target ?: continue
            d.execSQL("UPDATE messages SET chat_jid = ? WHERE chat_jid = ?", arrayOf(target, lid))
            d.execSQL(
                """UPDATE chats SET
                   unread_count = unread_count + COALESCE((SELECT unread_count FROM chats WHERE jid = ?), 0),
                   last_message_time = MAX(last_message_time, COALESCE((SELECT last_message_time FROM chats WHERE jid = ?), 0)),
                   avatar_path = COALESCE(avatar_path, (SELECT avatar_path FROM chats WHERE jid = ?)),
                   tab_id = CASE WHEN tab_id != 0 THEN tab_id ELSE COALESCE((SELECT tab_id FROM chats WHERE jid = ?), 0) END
                   WHERE jid = ?""",
                arrayOf(lid, lid, lid, lid, target),
            )
            d.delete("chats", "jid = ?", arrayOf(lid))
            refreshPreviews(d, listOf(target))
            merged = true
        }
        if (merged) notifyChange()
    }

    /** Teléfono de un chat en dígitos, si se conoce. */
    fun phoneOf(chat: Chat): String {
        chat.phone?.filter(Char::isDigit)?.takeIf { it.isNotEmpty() }?.let { return it }
        val base = chat.jid.substringBefore('@').substringBefore(':')
        if (chat.jid.endsWith("@s.whatsapp.net")) return base
        return lidMap().optString(base).substringBefore('@').filter(Char::isDigit)
    }
}
