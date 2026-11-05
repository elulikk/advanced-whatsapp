package com.advancedwhatsapp.app.gateway

import com.advancedwhatsapp.app.App
import com.advancedwhatsapp.app.Prefs
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONObject
import java.util.concurrent.TimeUnit

class GatewayException(message: String, val code: Int = 0) : Exception(message)

/** API local del gateway (las mismas rutas que usa la app de escritorio). Bloqueante: usar fuera del hilo principal. */
object GatewayClient {
    private val base = "http://127.0.0.1:${App.GATEWAY_PORT}"
    private val json = "application/json".toMediaType()
    private val http = OkHttpClient.Builder()
        .connectTimeout(3, TimeUnit.SECONDS)
        .readTimeout(120, TimeUnit.SECONDS)
        .writeTimeout(60, TimeUnit.SECONDS)
        .build()
    private val quick = http.newBuilder().readTimeout(5, TimeUnit.SECONDS).build()

    fun get(path: String): JSONObject {
        val req = Request.Builder().url(base + path).header(TOKEN_HEADER, Prefs.token).build()
        return run(quick, req)
    }

    fun post(path: String, body: JSONObject = JSONObject()): JSONObject {
        val req = Request.Builder().url(base + path).header(TOKEN_HEADER, Prefs.token)
            .post(body.toString().toRequestBody(json)).build()
        return run(http, req)
    }

    private fun run(client: OkHttpClient, req: Request): JSONObject {
        try {
            client.newCall(req).execute().use { res ->
                val text = res.body?.string().orEmpty()
                val data = runCatching { JSONObject(text) }.getOrDefault(JSONObject())
                if (!res.isSuccessful) {
                    throw GatewayException(data.optString("error").ifEmpty { "Error ${res.code} del gateway" }, res.code)
                }
                return data
            }
        } catch (e: java.io.IOException) {
            throw GatewayException("El servicio de WhatsApp todavía está arrancando. Probá de nuevo en unos segundos.")
        }
    }

    const val TOKEN_HEADER = "X-AdvancedWhatsapp-Token"
}
