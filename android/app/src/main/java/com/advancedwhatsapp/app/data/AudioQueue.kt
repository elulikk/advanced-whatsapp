package com.advancedwhatsapp.app.data

/** Reproducción continua de notas de voz: al terminar una suena la siguiente del chat (equivale a app/audio_queue.py). */
object AudioQueue {
    data class Next(val id: String, val path: String)

    /**
     * El mensaje que sigue a `msgId` en su chat, solo si es un audio ya bajado. Si en el medio hay texto, una foto, etc.,
     * o el audio todavía no se bajó, devuelve null. Bloqueante: Dispatchers.IO.
     */
    fun nextAfter(msgId: String): Next? {
        val db = Db.handle
        var chat: String? = null
        var ts = 0L
        var rowId = 0L
        db.rawQuery("SELECT chat_jid, timestamp, rowid FROM messages WHERE id = ?", arrayOf(msgId)).use {
            if (it.moveToFirst()) { chat = it.getString(0); ts = it.getLong(1); rowId = it.getLong(2) }
        }
        val jid = chat ?: return null
        return db.rawQuery(
            """SELECT id, media_type, media_path FROM messages
               WHERE chat_jid = ? AND media_type != 'revoked' AND (timestamp > ? OR (timestamp = ? AND rowid > ?))
               ORDER BY timestamp, rowid LIMIT 1""",
            arrayOf(jid, ts.toString(), ts.toString(), rowId.toString()),
        ).use { c ->
            if (c.moveToFirst() && c.getString(1) == "audio") {
                val path = c.getString(2)
                if (!path.isNullOrEmpty() && java.io.File(path).exists()) Next(c.getString(0), path) else null
            } else null
        }
    }
}
