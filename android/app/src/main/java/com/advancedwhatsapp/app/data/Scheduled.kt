package com.advancedwhatsapp.app.data

import com.advancedwhatsapp.app.AppState
import com.advancedwhatsapp.app.gateway.GatewayClient
import org.json.JSONObject

data class ScheduledItem(
    val id: String,
    val type: String,          // text, image, video, document, audio o sticker
    val to: String,
    val chatName: String?,
    val text: String,
    val sourceName: String?,
    val mediaPath: String?,
    val at: Long,
    val sentAt: Long?,
    val state: String,         // pending, sending, sent, failed, expired o canceled
    val error: String?,
    val repeat: String? = null, // «Todos los años», «Cada semana (lun, mié)»… o null si no se repite
) {
    val isActive get() = state == "pending" || state == "sending"
}

/** Mensajes programados: la cola vive en el gateway. Bloqueante: usar desde Dispatchers.IO. */
object Scheduled {
    /** [body] es el mismo de /send. El archivo pasa a ser de la cola: se mueve, no se copia. */
    fun enqueue(to: String, at: Long, body: JSONObject, hasFile: Boolean, quoted: Message?) {
        body.put("at", at).put("consume", hasFile).put("chat_name", Db.getChat(to)?.name)
        quoted?.let {
            body.put("quoted_text", Db.preview(it.mediaType, it.text))
            body.put("quoted_sender", if (it.fromMe) "Yo" else it.senderName)
        }
        Extras.nextRepeat?.let { body.put("repeat", it) }
        GatewayClient.post("/message-schedule", body)
        Extras.nextRepeat = null
        AppState.scheduledChanged()
    }

    fun list(): List<ScheduledItem> {
        val items = GatewayClient.get("/message-schedule").optJSONArray("items") ?: return emptyList()
        return List(items.length()) { toItem(items.getJSONObject(it)) }
    }

    fun pendingCount(jid: String): Int = list().count { it.to == jid && it.isActive }

    fun update(id: String, at: Long? = null, text: String? = null) {
        val body = JSONObject().put("id", id)
        if (at != null) body.put("at", at)
        if (text != null) body.put("text", text)
        post("/message-schedule/update", body)
    }

    fun sendNow(id: String) = update(id, at = System.currentTimeMillis() / 1000)

    fun cancel(id: String) = post("/message-schedule/cancel", JSONObject().put("id", id))

    fun remove(id: String) = post("/message-schedule/remove", JSONObject().put("id", id))

    private fun post(path: String, body: JSONObject) {
        GatewayClient.post(path, body)
        AppState.scheduledChanged()
    }

    private fun toItem(m: JSONObject) = ScheduledItem(
        id = m.getString("id"),
        type = m.optString("type"),
        to = m.optString("to"),
        chatName = m.text("chat_name"),
        text = m.text("text").orEmpty(),
        sourceName = m.text("source_name"),
        mediaPath = m.text("media_path"),
        at = m.optLong("at"),
        sentAt = m.optLong("sent_at").takeIf { it > 0 },
        state = m.optString("state"),
        error = m.text("error"),
        repeat = Extras.describeRepeat(m.optJSONObject("repeat")),
    )

    private fun JSONObject.text(k: String): String? = if (isNull(k)) null else optString(k).ifEmpty { null }
}
