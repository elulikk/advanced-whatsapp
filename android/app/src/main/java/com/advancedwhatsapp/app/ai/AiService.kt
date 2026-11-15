package com.advancedwhatsapp.app.ai

import com.advancedwhatsapp.app.Prefs
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.MultipartBody
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.asRequestBody
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.util.concurrent.TimeUnit

/** Proveedores de IA (mismo comportamiento que app/ai/ai_service.py). Bloqueante: usar en Dispatchers.IO. */
object AiService {
    val PROVIDERS = listOf("Gemini", "Groq", "OpenAI", "Claude")
    private const val ANTHROPIC_API = "https://api.anthropic.com/v1"
    private const val ANTHROPIC_VERSION = "2023-06-01"

    private val FALLBACK_MODELS = mapOf(
        "Gemini" to listOf("gemini-2.5-flash", "gemini-2.5-flash-lite", "gemini-3.5-flash-lite", "gemini-3.7-flash",
            "gemini-3.5-flash", "gemini-2.5-pro", "gemini-flash-latest"),
        "Groq" to listOf("qwen/qwen3.8-27b", "openai/gpt-oss-120b", "openai/gpt-oss-20b", "llama-3.3-70b-versatile", "allam-2-7b"),
        "OpenAI" to listOf("gpt-4o-mini", "gpt-4o", "o3-mini", "gpt-5.6-luna", "gpt-5.6-terra", "gpt-4-turbo"),
        "Claude" to listOf("claude-sonnet-5-5", "claude-opus-5-5", "claude-haiku-5-5", "claude-fable-5-1"),
    )

    // Visión (Leer imágenes): orden de respaldo y modelo de Groq con visión (los demás de Groq son solo texto)
    private val VISION_ORDER = listOf("Gemini", "OpenAI", "Claude", "Groq")
    private const val GROQ_VISION_MODEL = "meta-llama/llama-4-scout-17b-16e-instruct"

    private val json = "application/json".toMediaType()
    private val http = OkHttpClient.Builder()
        .connectTimeout(15, TimeUnit.SECONDS)
        .readTimeout(120, TimeUnit.SECONDS)
        .writeTimeout(60, TimeUnit.SECONDS)
        .build()

    private class HttpError(val code: Int, message: String) : Exception("$code $message")

    fun hasKey(provider: String) = Prefs.apiKey(provider).isNotEmpty()
    fun fallbackModels(provider: String) = FALLBACK_MODELS[provider].orEmpty()
    fun defaultModel(provider: String) = Prefs.model(provider).ifEmpty { fallbackModels(provider).first() }

    /** Modelos disponibles para la clave; sin clave o sin internet, la lista conocida. */
    fun listModels(provider: String): List<String> {
        val fallback = fallbackModels(provider)
        val key = Prefs.apiKey(provider)
        if (key.isEmpty()) return fallback
        return runCatching {
            val found = when (provider) {
                "Gemini" -> {
                    val data = getJson("https://generativelanguage.googleapis.com/v1beta/models?pageSize=200", mapOf("x-goog-api-key" to key))
                    val arr = data.optJSONArray("models") ?: JSONArray()
                    (0 until arr.length()).map { arr.getJSONObject(it) }
                        .filter { m -> m.optJSONArray("supportedGenerationMethods")?.toString()?.contains("generateContent") == true }
                        .map { it.optString("name").removePrefix("models/") }
                        .filter { m -> listOf("robotics", "lyria", "tts", "transcribe", "banana", "computer-use").none { m.contains(it, true) } }
                }
                "Groq" -> ids(getJson("https://api.groq.com/openai/v1/models", bearer(key)))
                    .filter { m -> listOf("whisper", "guard", "orpheus").none { m.contains(it, true) } }
                "OpenAI" -> ids(getJson("https://api.openai.com/v1/models", bearer(key)))
                    .filter { m -> listOf("gpt", "o1", "o3").any { m.contains(it, true) } }
                    .filter { m -> listOf("audio", "realtime", "whisper", "dall-e", "tts", "embedding").none { m.contains(it, true) } }
                    .sorted()
                else -> ids(getJson("$ANTHROPIC_API/models?limit=100", anthropicHeaders(key))).filter { it.startsWith("claude") }
            }
            (fallback.filter { it in found } + found.filter { it !in fallback }).ifEmpty { fallback }
        }.getOrDefault(fallback)
    }

    private fun ids(data: JSONObject): List<String> {
        val arr = data.optJSONArray("data") ?: return emptyList()
        return (0 until arr.length()).map { arr.getJSONObject(it).optString("id") }
    }

    private fun bearer(key: String) = mapOf("Authorization" to "Bearer $key")
    private fun anthropicHeaders(key: String) = mapOf("x-api-key" to key, "anthropic-version" to ANTHROPIC_VERSION)

    private fun getJson(url: String, headers: Map<String, String>): JSONObject {
        val b = Request.Builder().url(url)
        headers.forEach { (k, v) -> b.header(k, v) }
        return execute(b.build())
    }

    private fun postJson(url: String, headers: Map<String, String>, body: JSONObject): JSONObject {
        val b = Request.Builder().url(url).post(body.toString().toRequestBody(json))
        headers.forEach { (k, v) -> b.header(k, v) }
        return execute(b.build())
    }

    private fun execute(req: Request): JSONObject = http.newCall(req).execute().use { res ->
        val text = res.body?.string().orEmpty()
        val data = runCatching { JSONObject(text) }.getOrDefault(JSONObject())
        if (!res.isSuccessful) {
            val e = data.opt("error")
            val detail = (e as? JSONObject)?.optString("message") ?: e?.toString() ?: text.take(200)
            throw HttpError(res.code, detail)
        }
        data
    }

    /** Envía el prompt al proveedor elegido, sin pasar a otros. Devuelve el texto o un mensaje de error. */
    fun ask(prompt: String, provider: String, model: String?): String {
        val m = model?.ifEmpty { null } ?: defaultModel(provider)
        val key = Prefs.apiKey(provider)
        if (key.isEmpty()) return "Error: No se ha configurado la API Key de ${title(provider)}."
        return try {
            when (provider) {
                "Groq" -> openAiLike("https://api.groq.com/openai/v1/chat/completions", key, m, prompt)
                "OpenAI" -> openAiLike("https://api.openai.com/v1/chat/completions", key, m, prompt)
                "Claude" -> claude(key, m, prompt)
                else -> gemini(key, m, prompt)
            }
        } catch (e: Exception) {
            formatError(title(provider), e, m)
        }
    }

    /** Proveedores que pueden leer imágenes, con clave, el preferido primero; los que no tienen clave se saltan. */
    fun visionProviders(preferred: String = Prefs.provider, keyOf: (String) -> Boolean = { hasKey(it) }): List<String> =
        (listOf(preferred) + VISION_ORDER.filter { it != preferred }).filter { it in VISION_ORDER && keyOf(it) }

    /** Modelo que ve imágenes: el configurado si se sabe que lo hace; si no, uno por defecto del proveedor. */
    fun visionModel(provider: String, configured: String = Prefs.model(provider)): String {
        val low = configured.lowercase()
        return when (provider) {
            "Gemini" -> if ("gemini" in low) configured else "gemini-2.5-flash"
            "Claude" -> if ("claude" in low) configured else "claude-sonnet-5-5"
            "OpenAI" -> if (listOf("gpt-4o", "gpt-4.1", "gpt-4-turbo", "gpt-5", "o4").any { it in low }) configured else "gpt-4o-mini"
            else -> if (listOf("llama-4", "vision", "scout", "maverick").any { it in low }) configured else GROQ_VISION_MODEL
        }
    }

    /**
     * Pregunta sobre una imagen: prueba el proveedor preferido y, si falla, los demás con clave y visión.
     * Devuelve el texto o un mensaje de error (ver [isError]).
     */
    fun askImage(prompt: String, jpeg: ByteArray, mime: String = "image/jpeg"): String {
        val providers = visionProviders()
        if (providers.isEmpty()) return "Error: Configurá una API key de Gemini, OpenAI, Claude o Groq para leer imágenes."
        val b64 = android.util.Base64.encodeToString(jpeg, android.util.Base64.NO_WRAP)
        var last = ""
        for (p in providers) {
            val model = visionModel(p)
            val key = Prefs.apiKey(p)
            val out = try {
                when (p) {
                    "Gemini" -> geminiImage(key, model, prompt, b64, mime)
                    "Claude" -> claudeImage(key, model, prompt, b64, mime)
                    "OpenAI" -> openAiImage("https://api.openai.com/v1/chat/completions", key, model, prompt, b64, mime)
                    else -> openAiImage("https://api.groq.com/openai/v1/chat/completions", key, model, prompt, b64, mime)
                }
            } catch (e: Exception) {
                formatError(title(p), e, model)
            }
            if (!isError(out)) return out
            last = out
        }
        return last.ifEmpty { "Error: Ningún proveedor de IA pudo leer la imagen." }
    }

    private fun openAiImage(url: String, key: String, model: String, prompt: String, b64: String, mime: String): String {
        val content = JSONArray()
            .put(JSONObject().put("type", "text").put("text", prompt))
            .put(JSONObject().put("type", "image_url").put("image_url", JSONObject().put("url", "data:$mime;base64,$b64")))
        val body = JSONObject().put("model", model).put("temperature", 0.2)
            .put("messages", JSONArray().put(JSONObject().put("role", "user").put("content", content)))
        return postJson(url, bearer(key), body).getJSONArray("choices").getJSONObject(0).getJSONObject("message").optString("content")
    }

    private fun claudeImage(key: String, model: String, prompt: String, b64: String, mime: String): String {
        val content = JSONArray()
            .put(JSONObject().put("type", "image").put("source", JSONObject().put("type", "base64").put("media_type", mime).put("data", b64)))
            .put(JSONObject().put("type", "text").put("text", prompt))
        val body = JSONObject().put("model", model).put("max_tokens", 1024)
            .put("messages", JSONArray().put(JSONObject().put("role", "user").put("content", content)))
        val blocks = postJson("$ANTHROPIC_API/messages", anthropicHeaders(key), body).optJSONArray("content") ?: JSONArray()
        return (0 until blocks.length()).map { blocks.getJSONObject(it) }
            .filter { it.optString("type") == "text" }.joinToString("") { it.optString("text") }
    }

    private fun geminiImage(key: String, model: String, prompt: String, b64: String, mime: String): String {
        val parts = JSONArray()
            .put(JSONObject().put("text", prompt))
            .put(JSONObject().put("inline_data", JSONObject().put("mime_type", mime).put("data", b64)))
        val body = JSONObject().put("contents", JSONArray().put(JSONObject().put("parts", parts)))
        val data = postJson("https://generativelanguage.googleapis.com/v1beta/models/$model:generateContent", mapOf("x-goog-api-key" to key), body)
        val out = data.optJSONArray("candidates")?.optJSONObject(0)?.optJSONObject("content")?.optJSONArray("parts") ?: JSONArray()
        return (0 until out.length()).joinToString("") { out.getJSONObject(it).optString("text") }
    }

    private fun title(provider: String) = when (provider) {
        "Claude" -> "Anthropic"
        "Gemini" -> "Google Gemini"
        else -> provider
    }

    private fun openAiLike(url: String, key: String, model: String, prompt: String): String {
        val body = JSONObject().put("model", model).put("temperature", 0.7).put(
            "messages", JSONArray().put(JSONObject().put("role", "user").put("content", prompt)),
        )
        return postJson(url, bearer(key), body).getJSONArray("choices").getJSONObject(0)
            .getJSONObject("message").optString("content")
    }

    private fun claude(key: String, model: String, prompt: String): String {
        val body = JSONObject().put("model", model).put("max_tokens", 4096).put(
            "messages", JSONArray().put(JSONObject().put("role", "user").put("content", prompt)),
        )
        val blocks = postJson("$ANTHROPIC_API/messages", anthropicHeaders(key), body).optJSONArray("content") ?: JSONArray()
        return (0 until blocks.length()).map { blocks.getJSONObject(it) }
            .filter { it.optString("type") == "text" }.joinToString("") { it.optString("text") }
    }

    private fun gemini(key: String, model: String, prompt: String): String {
        val body = JSONObject().put(
            "contents", JSONArray().put(JSONObject().put("parts", JSONArray().put(JSONObject().put("text", prompt)))),
        )
        val data = postJson(
            "https://generativelanguage.googleapis.com/v1beta/models/$model:generateContent",
            mapOf("x-goog-api-key" to key), body,
        )
        val parts = data.optJSONArray("candidates")?.optJSONObject(0)?.optJSONObject("content")?.optJSONArray("parts")
            ?: JSONArray()
        return (0 until parts.length()).joinToString("") { parts.getJSONObject(it).optString("text") }
    }

    private fun formatError(provider: String, e: Exception, model: String): String {
        val s = e.message.orEmpty()
        val low = s.lowercase()
        return when {
            "429" in s || "quota" in low || "rate limit" in low || "rate_limit" in low ->
                "⚠️ Cuota excedida en $provider ($model).\nSe ha alcanzado el límite de peticiones o tokens de tu plan."
            "401" in s || "unauthorized" in low || "api key" in low || "api_key_invalid" in low ->
                "❌ Error de autenticación en $provider.\nLa API Key configurada es inválida o expiró. Verifícala en Ajustes."
            "404" in s || "not_found" in low || "not exist" in low ->
                "⚠️ Modelo no encontrado en $provider ($model).\nEl modelo puede haber sido discontinuado o no está disponible en tu cuenta."
            "timeout" in low || e is java.io.InterruptedIOException ->
                "⏳ Tiempo de espera agotado con $provider. La conexión está lenta o el servicio está saturado."
            else -> "Error conectando con $provider ($model):\n${s.take(250)}"
        }
    }

    fun isError(text: String?): Boolean {
        val t = text?.trim().orEmpty()
        if (t.isEmpty() || t.lowercase().startsWith("error")) return true
        return listOf("⚠️", "❌", "⏳", "Cuota excedida", "Error de autenticación", "Error conectando").any { it in t }
    }

    /** Whisper por API: Groq primero y OpenAI de respaldo. Lanza una excepción si ninguno pudo. */
    fun transcribe(file: File): String {
        val providers = buildList {
            Prefs.apiKey("Groq").takeIf { it.isNotEmpty() }?.let {
                add(Triple("Groq", "https://api.groq.com/openai/v1/audio/transcriptions", it) to "whisper-large-v3-turbo")
            }
            Prefs.apiKey("OpenAI").takeIf { it.isNotEmpty() }?.let {
                add(Triple("OpenAI", "https://api.openai.com/v1/audio/transcriptions", it) to "whisper-1")
            }
        }
        if (providers.isEmpty()) throw IllegalStateException("Configurá una API key de Groq u OpenAI en Ajustes para transcribir audios.")
        var last: String? = null
        for ((p, model) in providers) {
            val (name, url, key) = p
            try {
                val body = MultipartBody.Builder().setType(MultipartBody.FORM)
                    .addFormDataPart("file", file.name, file.asRequestBody("audio/ogg".toMediaType()))
                    .addFormDataPart("model", model)
                    .addFormDataPart("response_format", "json")
                    .build()
                val req = Request.Builder().url(url).header("Authorization", "Bearer $key").post(body).build()
                return execute(req).optString("text").trim()
            } catch (e: Exception) {
                last = "$name: ${e.message}"
            }
        }
        throw IllegalStateException(last ?: "No se pudo transcribir el audio")
    }
}
