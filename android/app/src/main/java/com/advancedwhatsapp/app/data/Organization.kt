package com.advancedwhatsapp.app.data

import android.content.ContentValues
import android.database.sqlite.SQLiteDatabase
import com.advancedwhatsapp.app.Prefs
import org.json.JSONArray
import org.json.JSONObject

/**
 * Reglas de una carpeta inteligente (misma idea que app/organization.py en la PC; acá en vez de tags se usan las pestañas):
 * un chat entra si cumple TODAS las condiciones puestas, o si es uno de los contactos elegidos ([jids]).
 * Sin ninguna regla la carpeta no muestra nada.
 */
data class FolderRules(
    val tabs: Set<Long> = emptySet(),
    val kind: String = KIND_ANY,        // any | individual | group
    val unread: Boolean = false,
    val muted: String = MUTED_ANY,      // any | only | none
    val jids: Set<String> = emptySet(),
) {
    val isEmpty: Boolean get() = tabs.isEmpty() && kind == KIND_ANY && !unread && muted == MUTED_ANY && jids.isEmpty()

    companion object {
        const val KIND_ANY = "any"
        const val KIND_INDIVIDUAL = "individual"
        const val KIND_GROUP = "group"
        const val MUTED_ANY = "any"
        const val MUTED_ONLY = "only"
        const val MUTED_NONE = "none"
    }
}

data class SmartFolder(val id: Long, val name: String, val rules: FolderRules)

/** Un enlace de la galería (de un mensaje de texto). */
data class GalleryLink(val messageId: String, val url: String, val timestamp: Long)

/** Una página de la galería: elementos y cuántos mensajes se leyeron (hay más mientras read == pedido). */
data class GalleryPage(val messages: List<Message>, val links: List<GalleryLink>, val read: Int)

/**
 * Organización y privacidad de la lista de chats: carpetas inteligentes, archivado (manual y automático)
 * y lectura invisible por chat. Los chats ocultos y el bloqueo de la app viven en [ChatLock].
 */
object Organization {
    const val GALLERY_MEDIA = "media"
    const val GALLERY_DOCS = "docs"
    const val GALLERY_LINKS = "links"
    const val GALLERY_AUDIO = "audio"

    private const val INDIVIDUAL = "(jid LIKE '%@s.whatsapp.net' OR jid LIKE '%@lid')"
    private const val GROUP = "jid LIKE '%@g.us'"
    private val URL_RE = Regex("https?://\\S+")
    private fun nowSec() = System.currentTimeMillis() / 1000
    private val db: SQLiteDatabase get() = Db.raw()

    // ── Reglas ──

    private fun quote(s: String) = "'" + s.replace("'", "''") + "'"

    /** Condición SQL (sobre `chats`) de las reglas. */
    fun rulesWhere(r: FolderRules, now: Long = nowSec()): String {
        if (r.isEmpty) return "1 = 0"
        val muted = "(COALESCE(muted_until, 0) = -1 OR COALESCE(muted_until, 0) > $now)"
        val parts = buildList<String> {
            if (r.tabs.isNotEmpty()) add("tab_id IN (${r.tabs.joinToString(",")})")
            when (r.kind) {
                FolderRules.KIND_INDIVIDUAL -> add(INDIVIDUAL)
                FolderRules.KIND_GROUP -> add(GROUP)
                else -> {}
            }
            if (r.unread) add("unread_count != 0")
            when (r.muted) {
                FolderRules.MUTED_ONLY -> add(muted)
                FolderRules.MUTED_NONE -> add("NOT $muted")
                else -> {}
            }
        }
        val rule = if (parts.isEmpty()) "" else parts.joinToString(" AND ", "(", ")")
        val picked = if (r.jids.isEmpty()) "" else "jid IN (${r.jids.joinToString(",") { quote(it) }})"
        return when {
            rule.isNotEmpty() && picked.isNotEmpty() -> "($rule OR $picked)"
            else -> rule.ifEmpty { picked }
        }
    }

    fun rulesToJson(r: FolderRules): String = JSONObject()
        .put("tabs", JSONArray(r.tabs.toList()))
        .put("kind", r.kind).put("unread", r.unread).put("muted", r.muted)
        .put("jids", JSONArray(r.jids.toList()))
        .toString()

    private fun longsOf(a: JSONArray?): Set<Long> = if (a == null) emptySet() else (0 until a.length()).map { a.getLong(it) }.toSet()
    private fun stringsOf(a: JSONArray?): Set<String> = if (a == null) emptySet() else (0 until a.length()).map { a.getString(it) }.toSet()

    fun rulesFromJson(s: String?): FolderRules = runCatching {
        val o = JSONObject(s.orEmpty().ifEmpty { "{}" })
        FolderRules(
            tabs = longsOf(o.optJSONArray("tabs")),
            kind = o.optString("kind").takeIf { it in listOf(FolderRules.KIND_INDIVIDUAL, FolderRules.KIND_GROUP) } ?: FolderRules.KIND_ANY,
            unread = o.optBoolean("unread"),
            muted = o.optString("muted").takeIf { it in listOf(FolderRules.MUTED_ONLY, FolderRules.MUTED_NONE) } ?: FolderRules.MUTED_ANY,
            jids = stringsOf(o.optJSONArray("jids")),
        )
    }.getOrDefault(FolderRules())

    fun describe(r: FolderRules, tabNames: Map<Long, String> = emptyMap()): String {
        val parts = buildList<String> {
            if (r.tabs.isNotEmpty()) add("pestañas: " + r.tabs.joinToString(", ") { tabNames[it] ?: "#$it" })
            when (r.kind) {
                FolderRules.KIND_INDIVIDUAL -> add("solo individuales")
                FolderRules.KIND_GROUP -> add("solo grupos")
                else -> {}
            }
            if (r.unread) add("no leídos")
            when (r.muted) {
                FolderRules.MUTED_ONLY -> add("solo silenciados")
                FolderRules.MUTED_NONE -> add("sin silenciar")
                else -> {}
            }
            if (r.jids.isNotEmpty()) add("${r.jids.size} contacto(s) elegido(s)")
        }
        return parts.joinToString(" · ").ifEmpty { "sin reglas" }
    }

    // ── Carpetas ──

    fun folders(): List<SmartFolder> = db.rawQuery("SELECT id, name, rules FROM smart_folders ORDER BY sort_order, id", null).use { c ->
        buildList { while (c.moveToNext()) add(SmartFolder(c.getLong(0), c.getString(1).orEmpty(), rulesFromJson(c.getString(2)))) }
    }

    fun folder(id: Long): SmartFolder? = folders().firstOrNull { it.id == id }

    /** Condición SQL de la carpeta (si ya no existe, no muestra nada). */
    fun folderWhere(id: Long): String = folder(id)?.let { rulesWhere(it.rules) } ?: "1 = 0"

    /** Cuántos chats con mensajes sin leer hay en la carpeta (el numerito de su pestaña). */
    fun folderUnread(f: SmartFolder): Int = db.rawQuery(
        "SELECT COUNT(*) FROM chats WHERE (jid LIKE '%@s.whatsapp.net' OR jid LIKE '%@lid' OR jid LIKE '%@g.us') " +
            "AND COALESCE(is_archived, 0) = 0 AND unread_count != 0 AND ${rulesWhere(f.rules)}${hiddenClause()}", null,
    ).use { if (it.moveToFirst()) it.getInt(0) else 0 }

    fun createFolder(name: String, rules: FolderRules): Long {
        val order = db.rawQuery("SELECT COALESCE(MAX(sort_order), -1) + 1 FROM smart_folders", null).use { if (it.moveToFirst()) it.getInt(0) else 0 }
        val id = db.insert("smart_folders", null, ContentValues().apply {
            put("name", name.trim().ifEmpty { "Carpeta" }); put("rules", rulesToJson(rules)); put("sort_order", order)
        })
        Db.changed()
        return id
    }

    fun updateFolder(id: Long, name: String, rules: FolderRules) {
        db.update("smart_folders", ContentValues().apply {
            put("name", name.trim().ifEmpty { "Carpeta" }); put("rules", rulesToJson(rules))
        }, "id = ?", arrayOf(id.toString()))
        Db.changed()
    }

    fun deleteFolder(id: Long) {
        db.delete("smart_folders", "id = ?", arrayOf(id.toString()))
        Db.changed()
    }

    /** Mueve la carpeta una posición (-1 arriba, +1 abajo). */
    fun moveFolder(id: Long, delta: Int) {
        val ids = folders().map { it.id }.toMutableList()
        val i = ids.indexOf(id)
        val j = i + delta
        if (i < 0 || j !in ids.indices) return
        ids[i] = ids[j].also { ids[j] = ids[i] }
        ids.forEachIndexed { pos, fid ->
            db.update("smart_folders", ContentValues().apply { put("sort_order", pos) }, "id = ?", arrayOf(fid.toString()))
        }
        Db.changed()
    }

    /** Los chats ocultos no cuentan en ninguna carpeta mientras no se escriba el código. */
    fun hiddenClause(): String {
        ChatLock.load()
        if (ChatLock.revealed) return ""
        val h = ChatLock.hidden
        return if (h.isEmpty()) "" else " AND jid NOT IN (${h.joinToString(",") { quote(it) }})"
    }

    // ── Archivados ──

    fun setArchived(jid: String, archived: Boolean) {
        db.update("chats", ContentValues().apply { put("is_archived", if (archived) 1 else 0) }, "jid = ?", arrayOf(jid))
        Db.changed(jid)
    }

    fun archivedCount(): Int = db.rawQuery(
        "SELECT COUNT(*) FROM chats WHERE is_archived = 1 AND (jid LIKE '%@s.whatsapp.net' OR jid LIKE '%@lid' OR jid LIKE '%@g.us')${hiddenClause()}", null,
    ).use { if (it.moveToFirst()) it.getInt(0) else 0 }

    /** Archiva los chats sin actividad hace [days] días (por omisión, el ajuste; 0 = apagado). Los fijados no se archivan. */
    fun autoArchive(days: Int = Prefs.autoArchiveDays, now: Long = nowSec()): Int {
        if (days <= 0) return 0
        val limit = now - days * 86400L
        val n = db.compileStatement(
            "UPDATE chats SET is_archived = 1 WHERE COALESCE(is_archived, 0) = 0 AND COALESCE(is_pinned, 0) = 0 " +
                "AND last_message_time > 0 AND last_message_time < ?",
        ).use { it.bindLong(1, limit); it.executeUpdateDelete() }
        if (n > 0) Db.changed()
        return n
    }

    /** Llegó (o se mandó) un mensaje: el chat sale de Archivados, salvo que esté silenciado. Dentro de la transacción de saveMessage. */
    fun unarchiveOnMessage(d: SQLiteDatabase, jid: String, now: Long = nowSec()) {
        d.execSQL(
            "UPDATE chats SET is_archived = 0 WHERE jid = ? AND is_archived = 1 " +
                "AND NOT (COALESCE(muted_until, 0) = -1 OR COALESCE(muted_until, 0) > ?)",
            arrayOf<Any>(jid, now),
        )
    }

    // ── Lectura invisible por chat ──

    private var silent: Set<String>? = null

    private fun loadSilent(): Set<String> = silent ?: db.rawQuery("SELECT chat_jid FROM chat_silent_read", null).use { c ->
        buildSet { while (c.moveToNext()) add(c.getString(0)) }
    }.also { silent = it }

    /** ¿En este chat no se mandan tildes azules aunque el ajuste general esté activado? */
    fun isSilentRead(jid: String): Boolean = jid in loadSilent()

    fun setSilentRead(jid: String, on: Boolean) {
        if (on) db.execSQL("INSERT OR IGNORE INTO chat_silent_read (chat_jid) VALUES (?)", arrayOf<Any>(jid))
        else db.execSQL("DELETE FROM chat_silent_read WHERE chat_jid = ?", arrayOf<Any>(jid))
        silent = if (on) loadSilent() + jid else loadSilent() - jid
    }

    // ── Galería ──

    /** Una página de la galería de un chat, del más nuevo al más viejo. */
    fun gallery(jid: String, kind: String, limit: Int, offset: Int): GalleryPage {
        if (kind == GALLERY_LINKS) {
            val rows = Db.messagesLike(jid, "%http%", limit, offset)
            val links = rows.flatMap { m ->
                URL_RE.findAll(m.text).map { GalleryLink(m.id, it.value.trimEnd('.', ',', ')', ';'), m.timestamp) }.toList()
            }
            return GalleryPage(emptyList(), links, rows.size)
        }
        val types = when (kind) {
            GALLERY_MEDIA -> listOf("image", "video")
            GALLERY_DOCS -> listOf("document")
            GALLERY_AUDIO -> listOf("audio")
            else -> return GalleryPage(emptyList(), emptyList(), 0)
        }
        val rows = Db.messagesOfTypes(jid, types, limit, offset)
        return GalleryPage(rows, emptyList(), rows.size)
    }
}
