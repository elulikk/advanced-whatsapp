package com.advancedwhatsapp.app.data

import com.advancedwhatsapp.app.Prefs
import com.advancedwhatsapp.app.gateway.GatewayClient
import com.advancedwhatsapp.app.gateway.GatewayException
import org.json.JSONArray
import org.json.JSONObject
import kotlin.math.min

data class StatusItem(
    val id: String,
    val type: String,          // text, image, video o audio
    val text: String,
    val background: String?,
    val textColor: String?,
    val timestamp: Long,
    val seconds: Int?,
    val thumb: String?,        // miniatura JPEG en base64
    val hasMedia: Boolean,
    val seen: Boolean,
    val deleted: Boolean = false,   // el contacto lo eliminó y el gateway lo conservó
)

data class StatusContact(
    val sender: String,
    val jid: String,
    val name: String,
    val avatarPath: String?,
    val items: List<StatusItem>,   // del más viejo al más nuevo
    val unseen: Int,
    val lastTime: Long,
)

/** Estados de los contactos: el gateway los junta y los borra a las 24 h. Bloqueante: usar desde Dispatchers.IO. */
object Statuses {
    private const val IMAGE_MS = 6_000L
    private const val DEFAULT_MEDIA_SECONDS = 30

    /** Primero quien tiene estados sin ver, del más reciente al más viejo (el orden lo da el gateway). */
    fun list(): List<StatusContact> {
        val contacts = GatewayClient.get("/contact-statuses").optJSONArray("contacts") ?: return emptyList()
        return List(contacts.length()) { i ->
            val c = contacts.getJSONObject(i)
            val items = c.getJSONArray("items")
            StatusContact(
                sender = c.optString("sender"),
                jid = c.optString("sender_jid"),
                name = c.optString("name"),
                avatarPath = c.text("avatar_path"),
                items = List(items.length()) { toItem(items.getJSONObject(it)) },
                unseen = c.optInt("unseen"),
                lastTime = c.optLong("last_ts"),
            )
        }
    }

    private fun toItem(s: JSONObject) = StatusItem(
        id = s.getString("id"),
        type = s.optString("type"),
        text = s.text("text").orEmpty(),
        background = s.text("background"),
        textColor = s.text("text_color"),
        timestamp = s.optLong("timestamp"),
        seconds = s.optInt("seconds").takeIf { it > 0 },
        thumb = s.text("thumb"),
        hasMedia = s.optBoolean("has_media"),
        seen = s.optBoolean("seen"),
        deleted = s.optBoolean("deleted"),
    )

    /** Baja la foto, el video o el audio y devuelve su ruta local. */
    fun media(id: String): String =
        GatewayClient.post("/contact-statuses/media", JSONObject().put("id", id)).optString("media_path").ifEmpty {
            throw GatewayException("No se pudo descargar este estado.")
        }

    /** Con la confirmación activada, el contacto ve que viste su estado (igual que en la app oficial). */
    fun markSeen(id: String) {
        GatewayClient.post(
            "/contact-statuses/seen",
            JSONObject().put("ids", JSONArray(listOf(id))).put("receipt", Prefs.statusReceipts),
        )
    }

    /** Se empieza por el primer estado sin ver; si ya viste todos, desde el principio. */
    fun startIndex(c: StatusContact): Int = c.items.indexOfFirst { !it.seen }.coerceAtLeast(0)

    /** Cuánto se muestra cada estado (mismos tiempos que en la PC). */
    fun durationMs(s: StatusItem): Long = when (s.type) {
        "text" -> min(14_000L, 4_000L + s.text.length * 1000L / 18)
        "video", "audio" -> (s.seconds ?: DEFAULT_MEDIA_SECONDS) * 1000L
        else -> IMAGE_MS
    }

    private fun JSONObject.text(k: String): String? = if (isNull(k)) null else optString(k).ifEmpty { null }
}
