package com.advancedwhatsapp.app.data

/**
 * Estadísticas de mensajes: con quién más hablás, mensajes por día y por hora y tiempos de respuesta.
 * Equivale a app/stats.py. En Android 10 (SQLite 3.22) no hay funciones de ventana, así que los tiempos de respuesta
 * se calculan recorriendo los mensajes en orden (chat, fecha) una sola vez, con un índice por fecha para el período.
 */
object Stats {
    data class Period(val key: String, val label: String, val days: Int)

    val PERIODS = listOf(
        Period("week", "Semana", 7), Period("month", "Mes", 30), Period("quarter", "3 meses", 90),
        Period("year", "Año", 365), Period("all", "Todo", 0),
    )

    /** Una respuesta que tarda más que esto es una conversación nueva, no una demora. */
    const val RESPONSE_CAP_SECONDS = 24 * 3600L

    data class ChatCount(val jid: String, val name: String, val sent: Int, val received: Int) {
        val total: Int get() = sent + received
    }

    data class Bucket(val label: String, val sent: Int, val received: Int) {
        val total: Int get() = sent + received
    }

    data class Response(val mineAvg: Double?, val theirsAvg: Double?, val mineCount: Int, val theirsCount: Int)

    data class Report(
        val sent: Int, val received: Int, val chats: Int,
        val topChats: List<ChatCount>, val byDay: List<Bucket>, val byHour: List<Bucket>, val response: Response,
    ) {
        val total: Int get() = sent + received
    }

    /**
     * Acumula los tiempos de respuesta. Se alimenta con los mensajes ordenados por chat y fecha: una «respuesta» es el
     * primer mensaje de un lado después de uno del otro, dentro del mismo chat. Pura (se prueba sin Android).
     */
    class ResponseAccumulator(private val cap: Long = RESPONSE_CAP_SECONDS) {
        private var chat: String? = null
        private var prevMe = -1
        private var prevTs = 0L
        private var mineTotal = 0.0
        private var mineN = 0
        private var theirsTotal = 0.0
        private var theirsN = 0

        fun add(chatJid: String, fromMe: Boolean, ts: Long) {
            val me = if (fromMe) 1 else 0
            if (chatJid == chat && prevMe != -1 && prevMe != me) {
                val d = ts - prevTs
                if (d in 0..cap) {
                    if (fromMe) { mineTotal += d; mineN++ } else { theirsTotal += d; theirsN++ }
                }
            }
            chat = chatJid
            prevMe = me
            prevTs = ts
        }

        fun result() = Response(
            mineAvg = if (mineN > 0) mineTotal / mineN else null,
            theirsAvg = if (theirsN > 0) theirsTotal / theirsN else null,
            mineCount = mineN, theirsCount = theirsN,
        )
    }

    fun formatDuration(seconds: Double?): String {
        if (seconds == null) return "—"
        val s = Math.round(seconds).toInt()
        return when {
            s < 60 -> "$s s"
            s < 3600 -> "${s / 60} min"
            else -> {
                val m = (s / 60) % 60
                if (m == 0) "${s / 3600} h" else "${s / 3600} h %02d min".format(m)
            }
        }
    }

    fun periodStart(period: Period, now: Long = System.currentTimeMillis() / 1000): Long =
        if (period.days <= 0) 0L else now - period.days * 86_400L

    /** Todo junto, para la pantalla. Bloqueante: Dispatchers.IO. */
    fun report(period: Period): Report {
        val db = Db.handle
        val since = periodStart(period).toString()
        db.execSQL("CREATE INDEX IF NOT EXISTS idx_messages_ts ON messages(timestamp)")
        val counted = "media_type != 'revoked'"

        var sent = 0
        var total = 0
        var chats = 0
        db.rawQuery(
            "SELECT COUNT(*), COALESCE(SUM(from_me), 0), COUNT(DISTINCT chat_jid) FROM messages WHERE timestamp >= ? AND $counted",
            arrayOf(since),
        ).use { if (it.moveToFirst()) { total = it.getInt(0); sent = it.getInt(1); chats = it.getInt(2) } }

        val top = db.rawQuery(
            """SELECT m.chat_jid, COUNT(*) AS n, COALESCE(SUM(m.from_me), 0),
                      COALESCE(NULLIF(c.custom_name, ''), NULLIF(c.name, ''), NULLIF(c.phone, ''), m.chat_jid)
               FROM messages m LEFT JOIN chats c ON c.jid = m.chat_jid
               WHERE m.timestamp >= ? AND m.$counted GROUP BY m.chat_jid ORDER BY n DESC LIMIT 15""",
            arrayOf(since),
        ).use { c -> buildList { while (c.moveToNext()) add(ChatCount(c.getString(0), c.getString(3), c.getInt(2), c.getInt(1) - c.getInt(2))) } }

        val byDay = db.rawQuery(
            """SELECT strftime('%Y-%m-%d', timestamp, 'unixepoch', 'localtime') AS d, COUNT(*), COALESCE(SUM(from_me), 0)
               FROM messages WHERE timestamp >= ? AND $counted GROUP BY d ORDER BY d""",
            arrayOf(since),
        ).use { c -> buildList { while (c.moveToNext()) add(Bucket(c.getString(0), c.getInt(2), c.getInt(1) - c.getInt(2))) } }

        val hours = Array(24) { Bucket("$it", 0, 0) }
        db.rawQuery(
            """SELECT CAST(strftime('%H', timestamp, 'unixepoch', 'localtime') AS INTEGER) AS h, COUNT(*), COALESCE(SUM(from_me), 0)
               FROM messages WHERE timestamp >= ? AND $counted GROUP BY h""",
            arrayOf(since),
        ).use { c ->
            while (c.moveToNext()) {
                val h = c.getInt(0).coerceIn(0, 23)
                hours[h] = Bucket("$h", c.getInt(2), c.getInt(1) - c.getInt(2))
            }
        }

        val acc = ResponseAccumulator()
        db.rawQuery(
            """SELECT chat_jid, from_me, timestamp FROM messages
               WHERE timestamp >= ? AND $counted AND chat_jid NOT LIKE '%@g.us' AND chat_jid NOT LIKE '%@broadcast'
               ORDER BY chat_jid, timestamp, rowid""",
            arrayOf(since),
        ).use { c -> while (c.moveToNext()) acc.add(c.getString(0), c.getInt(1) != 0, c.getLong(2)) }

        return Report(sent, total - sent, chats, top, byDay, hours.toList(), acc.result())
    }
}
