package com.advancedwhatsapp.app.data

import com.advancedwhatsapp.app.App
import com.advancedwhatsapp.app.Prefs
import java.io.File

/**
 * Uso y limpieza de la multimedia bajada (equivale a app/media_cleanup.py). Se borra lo más viejo, por antigüedad o
 * hasta entrar en un tope de GB; solo lo que se puede volver a bajar (el mensaje conserva `media_raw`). Lo enviado desde
 * este celular (enviados/, programados/) nunca se toca. Los mensajes quedan: al abrirlos, el archivo se baja de nuevo.
 */
object MediaCleanup {
    private val PROTECTED = setOf("enviados", "mensajes_programados", "estados_programados", "avatars", "estados_contactos")
    val TYPE_LABELS = mapOf(
        "image" to "Fotos", "video" to "Videos", "audio" to "Audios", "document" to "Documentos",
        "sticker" to "Stickers", "other" to "Otros",
    )
    private const val AUTO_INTERVAL_MS = 24 * 3600 * 1000L

    data class Entry(
        val id: String, val jid: String, val name: String, val type: String, val path: String,
        val size: Long, val timestamp: Long, val removable: Boolean,
    )

    data class TypeUsage(val bytes: Long, val count: Int)
    data class ChatUsage(val jid: String, val name: String, val bytes: Long, val count: Int)
    data class Usage(
        val totalBytes: Long, val totalFiles: Int, val removableBytes: Long,
        val byType: Map<String, TypeUsage>, val byChat: List<ChatUsage>,
    )

    data class Result(val files: Int, val bytes: Long, val errors: Int)

    fun isProtected(path: String, mediaDir: File = App.mediaDir): Boolean {
        val rel = runCatching { File(path).canonicalFile.relativeTo(mediaDir.canonicalFile).path }.getOrNull() ?: return true
        if (rel.startsWith("..")) return true            // fuera de la carpeta de multimedia: no es nuestro para borrar
        return rel.substringBefore(File.separatorChar) in PROTECTED
    }

    /** Una fila por mensaje con archivo que existe en disco. Bloqueante. */
    fun scan(mediaDir: File = App.mediaDir): List<Entry> {
        val db = Db.handle
        db.execSQL("CREATE INDEX IF NOT EXISTS idx_messages_media ON messages(timestamp) WHERE media_path IS NOT NULL")
        return db.rawQuery(
            """SELECT m.id, m.chat_jid, m.media_type, m.media_path, m.timestamp,
                      (m.media_raw IS NOT NULL AND m.media_raw != '') AS redownloadable,
                      COALESCE(NULLIF(c.custom_name, ''), NULLIF(c.name, ''), NULLIF(c.phone, ''), m.chat_jid)
               FROM messages m LEFT JOIN chats c ON c.jid = m.chat_jid
               WHERE m.media_path IS NOT NULL AND m.media_path != ''""",
            null,
        ).use { c ->
            buildList {
                while (c.moveToNext()) {
                    val path = c.getString(3)
                    val file = File(path)
                    if (!file.exists()) continue
                    val type = c.getString(2)?.takeIf { it in TYPE_LABELS } ?: "other"
                    add(Entry(c.getString(0), c.getString(1), c.getString(6), type, path, file.length(), c.getLong(4),
                        c.getInt(5) == 1 && !isProtected(path, mediaDir)))
                }
            }
        }
    }

    fun usage(entries: List<Entry> = scan()): Usage {
        val byType = HashMap<String, TypeUsage>()
        val byChat = LinkedHashMap<String, ChatUsage>()
        var total = 0L
        var removable = 0L
        for (e in entries) {
            total += e.size
            if (e.removable) removable += e.size
            byType[e.type] = byType[e.type]?.let { TypeUsage(it.bytes + e.size, it.count + 1) } ?: TypeUsage(e.size, 1)
            byChat[e.jid] = byChat[e.jid]?.let { it.copy(bytes = it.bytes + e.size, count = it.count + 1) } ?: ChatUsage(e.jid, e.name, e.size, 1)
        }
        return Usage(total, entries.size, removable, byType, byChat.values.sortedByDescending { it.bytes })
    }

    /**
     * Qué archivos se borrarían. Con `olderThanDays` > 0: los de mensajes más viejos que eso. Con `maxGb` > 0: además,
     * los más viejos que haga falta hasta que lo bajado entre en el tope. Sin ninguno de los dos, nada. Pura.
     */
    fun plan(entries: List<Entry>, olderThanDays: Int, maxGb: Double, now: Long = System.currentTimeMillis() / 1000): List<Entry> {
        if (olderThanDays <= 0 && maxGb <= 0.0) return emptyList()
        val sorted = entries.sortedBy { it.timestamp }       // los más viejos primero
        val chosen = LinkedHashMap<String, Entry>()
        if (olderThanDays > 0) {
            val cutoff = now - olderThanDays * 86_400L
            for (e in sorted) if (e.removable && e.timestamp < cutoff) chosen[e.id] = e
        }
        if (maxGb > 0.0) {
            val limit = (maxGb * 1024.0 * 1024.0 * 1024.0).toLong()
            var total = sorted.sumOf { it.size } - chosen.values.sumOf { it.size }
            for (e in sorted) {
                if (total <= limit) break
                if (e.removable && e.id !in chosen) {
                    chosen[e.id] = e
                    total -= e.size
                }
            }
        }
        return chosen.values.sortedBy { it.timestamp }
    }

    /** Borra los archivos y deja el mensaje sin ruta (se vuelve a bajar al abrirlo). Bloqueante. */
    fun apply(candidates: List<Entry>): Result {
        var freed = 0L
        var files = 0
        var errors = 0
        val done = ArrayList<String>()
        for (e in candidates) {
            val f = File(e.path)
            if (f.exists() && !f.delete()) { errors++; continue }
            freed += e.size
            files++
            done += e.id
        }
        if (done.isNotEmpty()) {
            val db = Db.handle
            db.beginTransaction()
            try {
                done.chunked(500).forEach { part ->
                    db.execSQL("UPDATE messages SET media_path = NULL WHERE id IN (${part.joinToString(",") { "?" }})", part.toTypedArray())
                }
                db.setTransactionSuccessful()
            } finally {
                db.endTransaction()
            }
            Db.announceChange()
        }
        return Result(files, freed, errors)
    }

    fun cleanup(olderThanDays: Int, maxGb: Double): Result = apply(plan(scan(), olderThanDays, maxGb))

    /** Limpieza programada: si está activada y pasó más de un día desde la última. Se llama desde un hilo de fondo. */
    fun runAutoIfDue(now: Long = System.currentTimeMillis()): Result? {
        val days = Prefs.mediaAutoDays
        val gb = Prefs.mediaAutoGb.toDouble()
        if (days <= 0 && gb <= 0.0) return null
        if (now - Prefs.mediaAutoLast < AUTO_INTERVAL_MS) return null
        Prefs.mediaAutoLast = now
        return cleanup(days, gb)
    }

    fun formatSize(bytes: Long): String {
        var n = bytes.toDouble()
        for (unit in listOf("B", "KB", "MB")) {
            if (n < 1024) return if (unit == "B") "${n.toLong()} B" else "%.1f %s".format(n, unit)
            n /= 1024
        }
        return "%.1f GB".format(n)
    }
}
