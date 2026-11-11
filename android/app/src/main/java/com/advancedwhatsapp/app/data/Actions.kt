package com.advancedwhatsapp.app.data

import android.net.Uri
import android.provider.OpenableColumns
import com.advancedwhatsapp.app.App
import com.advancedwhatsapp.app.Prefs
import com.advancedwhatsapp.app.ai.AutoTranscriber
import com.advancedwhatsapp.app.gateway.GatewayClient
import com.advancedwhatsapp.app.gateway.GatewayException
import org.json.JSONArray
import org.json.JSONObject
import java.io.File

/** Operaciones que pasan por el gateway. Bloqueantes: llamarlas desde Dispatchers.IO. */
object Actions {
    private fun nowSec() = System.currentTimeMillis() / 1000

    private fun quotedJson(q: Message?): JSONObject? = q?.let {
        JSONObject().put("message_id", it.id).put("from_me", it.fromMe).put("participant", it.senderJid)
            .put("text", Db.preview(it.mediaType, it.text))
    }

    /**
     * El gateway no devuelve el eco de lo enviado: se guarda acá con el id que asignó WhatsApp.
     * Con [at] (epoch segundos) no se envía: queda programado y llega al chat cuando sale.
     */
    private fun saveSent(
        to: String, body: JSONObject, text: String, mediaType: String, path: String?, quoted: Message?, at: Long?,
    ) {
        if (at != null) {
            Scheduled.enqueue(to, at, body, path != null, quoted)
            return
        }
        val id = GatewayClient.post("/send", body).optString("message_id")
        if (id.isEmpty()) throw GatewayException("WhatsApp no confirmó el envío.")
        Db.saveMessage(
            id = id, chat = to, sender = "me", senderName = "Yo", fromMe = true, text = text, timestamp = nowSec(),
            mediaType = mediaType, mediaPath = path,
            quotedText = quoted?.let { Db.preview(it.mediaType, it.text) },
            quotedSender = quoted?.let { if (it.fromMe) "Yo" else it.senderName },
        )
        if (mediaType == "audio") AutoTranscriber.enqueue(id)
    }

    fun sendText(to: String, text: String, quoted: Message? = null, at: Long? = null) {
        val body = JSONObject().put("to", to).put("text", text)
        quotedJson(quoted)?.let { body.put("quoted", it) }
        saveSent(to, body, text, "text", null, quoted, at)
    }

    /** kind: image, video o document. */
    fun sendFile(
        to: String, file: File, kind: String, caption: String, fileName: String?, mime: String?, quoted: Message?, at: Long? = null,
    ) {
        val body = JSONObject().put("to", to).put("text", caption)
        when (kind) {
            "image" -> body.put("image_path", file.path).put("image_thumb", Photos.thumbnailBase64(file))
            "video" -> body.put("video_path", file.path)
            else -> body.put("document_path", file.path).put("document_name", fileName ?: file.name).put("mimetype", mime)
        }
        quotedJson(quoted)?.let { body.put("quoted", it) }
        saveSent(to, body, if (kind == "document") caption.ifEmpty { fileName ?: file.name } else caption, kind, file.path, quoted, at)
    }

    fun sendVoice(to: String, file: File, seconds: Int, waveform: List<Int>, quoted: Message?, at: Long? = null) {
        val body = JSONObject().put("to", to).put("audio_path", file.path).put("audio_seconds", seconds)
            .put("audio_waveform", JSONArray(waveform))
        quotedJson(quoted)?.let { body.put("quoted", it) }
        saveSent(to, body, "", "audio", file.path, quoted, at)
    }

    /** Manda un sticker de la biblioteca (webp 512x512). Se copia a enviados: el mensaje conserva su archivo aunque se borre de la biblioteca. */
    fun sendSticker(to: String, sticker: Sticker, quoted: Message?, at: Long? = null) {
        val name = if (at != null) "sticker_${sticker.id}_${System.currentTimeMillis()}.webp" else "sticker_${sticker.id}.webp"
        val copy = File(App.sentDir, name)
        if (!copy.exists()) sticker.file.copyTo(copy, overwrite = true)
        val body = JSONObject().put("to", to).put("sticker_path", copy.path)
        if (sticker.animated) body.put("sticker_animated", true)
        quotedJson(quoted)?.let { body.put("quoted", it) }
        saveSent(to, body, "", "sticker", copy.path, quoted, at)
        StickerLibrary.markUsed(sticker.id)
    }

    fun markRead(jid: String) {
        val ids = Db.markChatRead(jid)
        com.advancedwhatsapp.app.notify.Notifier.cancel(jid)   // leído: que la notificación (y el globo del ícono) baje
        if (ids.isEmpty() || !Prefs.readReceipts) return
        // Los leídos se reintentan solos en la siguiente apertura si no hay conexión
        runCatching { GatewayClient.post("/read", JSONObject().put("to", jid).put("messages", JSONArray(ids))) }
    }

    fun react(m: Message, emoji: String) {
        GatewayClient.post(
            "/react",
            JSONObject().put("to", m.chatJid).put("message_id", m.id).put("from_me", m.fromMe)
                .put("participant", m.senderJid).put("emoji", emoji),
        )
        Db.setReaction(m.id, "me", emoji, nowSec(), m.chatJid)
    }

    fun deleteForEveryone(m: Message) {
        GatewayClient.post("/delete", JSONObject().put("to", m.chatJid).put("message_id", m.id))
        Db.revokeMessage(m.id)
    }

    /** Baja un archivo recibido (solo cuando se pide) y devuelve su ruta local. */
    fun downloadMedia(m: Message): String {
        m.mediaPath?.takeIf { File(it).exists() }?.let { return it }
        val raw = m.mediaRaw ?: throw GatewayException("Este archivo ya no se puede descargar.")
        val res = GatewayClient.post(
            "/media/download",
            JSONObject().put("message_id", m.id).put("media_type", m.mediaType).put("media_raw", raw),
        )
        val path = res.optString("media_path")
        if (path.isEmpty()) throw GatewayException("No se pudo descargar el archivo.")
        Db.setMediaPath(m.id, path)
        return path
    }

    fun setPresence(available: Boolean) {
        runCatching { GatewayClient.post("/presence", JSONObject().put("available", available)) }
    }

    fun chatPresence(jid: String, subscribe: Boolean = false, state: String? = null) {
        val body = JSONObject().put("to", jid).put("subscribe", subscribe)
        if (state != null) body.put("state", state)
        runCatching { GatewayClient.post("/chat-presence", body) }
    }

    fun requestPairingCode(phone: String): String =
        GatewayClient.post("/pairing-code", JSONObject().put("phone", phone)).optString("code")

    fun unlink() {
        GatewayClient.post("/disconnect")
    }

    /** Copia un archivo elegido en el selector a la carpeta de enviados, de donde lo lee el gateway. */
    fun importUri(uri: Uri): Pair<File, String> {
        val cr = App.instance.contentResolver
        val name = cr.query(uri, arrayOf(OpenableColumns.DISPLAY_NAME), null, null, null)?.use { c ->
            if (c.moveToFirst()) c.getString(0) else null
        } ?: "archivo"
        val safe = name.replace(Regex("[^\\w.\\- ]"), "_")
        val out = File(App.sentDir, "${System.currentTimeMillis()}_$safe")
        val input = cr.openInputStream(uri) ?: throw GatewayException("No se pudo abrir el archivo.")
        input.use { i -> out.outputStream().use { i.copyTo(it) } }
        return out to name
    }
}
