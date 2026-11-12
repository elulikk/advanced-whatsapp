package com.advancedwhatsapp.app.data

import android.util.Base64
import android.util.LruCache
import com.advancedwhatsapp.app.App
import com.advancedwhatsapp.app.Prefs
import okhttp3.OkHttpClient
import okhttp3.Request
import org.json.JSONObject
import java.io.File
import java.net.URLEncoder
import java.security.MessageDigest
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.Semaphore
import java.util.concurrent.TimeUnit

data class LinkPreview(val url: String, val title: String, val description: String, val imagePath: String?)

/**
 * Vista previa de links (título, descripción e imagen). Prioridad: la que trae el propio mensaje de WhatsApp (el gateway
 * la manda en `link_preview`), la caché local (memoria + data/previews) y, si falta, se la pide al gateway
 * (GET /link-preview, con timeouts cortos). Nunca se llama desde el hilo principal: `load` es bloqueante.
 */
object LinkPreviews {
    private val URL = Regex("https?://\\S+")
    private const val FAILED_RETRY_MS = 60 * 60 * 1000L

    private val memory = LruCache<String, LinkPreview>(200)
    private val failedAt = ConcurrentHashMap<String, Long>()
    private val slots = Semaphore(2)      // a lo sumo dos consultas a la vez: no satura el gateway al abrir un chat con muchos links
    private val http = OkHttpClient.Builder()
        .connectTimeout(3, TimeUnit.SECONDS).readTimeout(10, TimeUnit.SECONDS).build()

    private val dir: File get() = File(App.dataDir, "previews").apply { mkdirs() }

    /** Primer link http(s) del texto, sin la puntuación del final. */
    fun firstUrl(text: String): String? =
        URL.find(text)?.value?.trimEnd('.', ',', ';', ':', '!', '?', ')', ']', '}', '\'', '"')?.takeIf { it.length > 8 }

    fun domainOf(url: String): String =
        url.substringAfter("://").substringBefore('/').substringBefore('?').lowercase().removePrefix("www.")

    private fun hash(url: String) = MessageDigest.getInstance("SHA-1").digest(url.toByteArray()).joinToString("") { "%02x".format(it) }

    fun peek(url: String): LinkPreview? = memory.get(url)

    /** Preview ya disponible (memoria o disco) o, si no, bajado del gateway. Null si no hay. Bloqueante. */
    fun load(url: String): LinkPreview? {
        if (!Prefs.linkPreviews) return null
        memory.get(url)?.let { return it }
        readDisk(url)?.let { memory.put(url, it); return it }
        failedAt[url]?.let { if (System.currentTimeMillis() - it < FAILED_RETRY_MS) return null }
        slots.acquire()
        try {
            memory.get(url)?.let { return it }   // otro hilo lo trajo mientras esperábamos
            val fetched = fetch(url)
            if (fetched == null) failedAt[url] = System.currentTimeMillis()
            return fetched
        } finally {
            slots.release()
        }
    }

    private fun fetch(url: String): LinkPreview? = runCatching {
        val req = Request.Builder()
            .url("http://127.0.0.1:${App.GATEWAY_PORT}/link-preview?url=${URLEncoder.encode(url, "UTF-8")}")
            .header("X-AdvancedWhatsapp-Token", Prefs.token).build()
        http.newCall(req).execute().use { res ->
            if (!res.isSuccessful) return@runCatching null
            val d = JSONObject(res.body?.string().orEmpty())
            val p = d.optJSONObject("preview")
            if (!d.optBoolean("success") || p == null) null
            else save(url, p.optString("title"), p.optString("description"), p.optString("image_b64").ifEmpty { null })
        }
    }.getOrNull()

    /** Guarda un preview (de WhatsApp o bajado) en memoria y en disco. */
    fun save(url: String, title: String, description: String, imageB64: String?): LinkPreview? {
        if (title.isBlank() && description.isBlank() && imageB64 == null) return null
        val key = hash(url)
        var imagePath: String? = null
        if (imageB64 != null) {
            runCatching {
                val img = File(dir, "$key.img")
                img.writeBytes(Base64.decode(imageB64, Base64.DEFAULT))
                imagePath = img.path
            }
        }
        val p = LinkPreview(url, title.trim(), description.trim(), imagePath)
        runCatching {
            File(dir, "$key.json").writeText(
                JSONObject().put("title", p.title).put("description", p.description).put("image", p.imagePath ?: "").toString()
            )
        }
        memory.put(url, p)
        failedAt.remove(url)
        return p
    }

    private fun readDisk(url: String): LinkPreview? = runCatching {
        val f = File(dir, hash(url) + ".json")
        if (!f.exists()) return null
        val d = JSONObject(f.readText())
        val img = d.optString("image").takeIf { it.isNotEmpty() && File(it).exists() }
        LinkPreview(url, d.optString("title"), d.optString("description"), img)
    }.getOrNull()

    /** El preview que viene dentro de un mensaje entrante (lo llama el webhook). */
    fun storeEvent(d: JSONObject) {
        val lp = d.optJSONObject("link_preview") ?: return
        val url = firstUrl(d.optString("text")) ?: lp.optString("url").ifEmpty { return }
        runCatching { save(url, lp.optString("title"), lp.optString("description"), lp.optString("image_b64").ifEmpty { null }) }
    }

    /** Borra los previews guardados hace más de `days` días. */
    fun prune(days: Int = 30): Int {
        val cutoff = System.currentTimeMillis() - days * 86_400_000L
        var n = 0
        dir.listFiles()?.forEach { if (it.lastModified() < cutoff && it.delete()) n++ }
        return n
    }
}
