package com.advancedwhatsapp.app.gateway

import android.util.Log
import com.advancedwhatsapp.app.App
import com.advancedwhatsapp.app.AppState
import com.advancedwhatsapp.app.Prefs
import com.advancedwhatsapp.app.data.Db
import com.advancedwhatsapp.app.notify.Notifier
import org.json.JSONArray
import org.json.JSONObject
import java.io.BufferedInputStream
import java.io.ByteArrayOutputStream
import java.io.InputStream
import java.net.InetAddress
import java.net.ServerSocket
import java.net.Socket
import java.security.MessageDigest
import java.util.concurrent.Executors

/**
 * Recibe los eventos del gateway por HTTP local: es el equivalente de app/webhook_server.py.
 * Si algo falla responde 500 y el gateway lo reintenta desde su bandeja de salida.
 */
class WebhookServer {
    private var server: ServerSocket? = null
    private val pool = Executors.newCachedThreadPool()
    private val syncLock = Any()

    fun start() {
        if (server != null) return
        val socket = ServerSocket(App.WEBHOOK_PORT, 64, InetAddress.getByName("127.0.0.1")).apply { reuseAddress = true }
        server = socket
        Thread({
            while (!socket.isClosed) {
                try {
                    val c = socket.accept()
                    pool.execute { serve(c) }
                } catch (_: Exception) {
                }
            }
        }, "webhook").start()
    }

    fun stop() {
        runCatching { server?.close() }
        server = null
    }

    private fun serve(c: Socket) {
        c.use {
            try {
                c.soTimeout = 60_000
                val input = BufferedInputStream(c.getInputStream())
                val status = respondTo(input)
                val reason = if (status == 200) "OK" else "Error"
                val body = if (status == 200) "{\"status\":\"ok\"}" else "{}"
                c.getOutputStream().apply {
                    write(
                        ("HTTP/1.1 $status $reason\r\nContent-Type: application/json\r\n" +
                            "Content-Length: ${body.length}\r\nConnection: close\r\n\r\n$body").toByteArray()
                    )
                    flush()
                }
            } catch (e: Exception) {
                Log.w(TAG, "Conexión fallida: ${e.message}")
            }
        }
    }

    private fun respondTo(input: InputStream): Int {
        val first = readLine(input) ?: return 400
        val parts = first.split(" ")
        if (parts.size < 2 || parts[0] != "POST") return 404
        val path = parts[1]
        var length = 0
        var token = ""
        while (true) {
            val line = readLine(input) ?: break
            if (line.isEmpty()) break
            val i = line.indexOf(':')
            if (i <= 0) continue
            val k = line.substring(0, i).trim().lowercase()
            val v = line.substring(i + 1).trim()
            if (k == "content-length") length = v.toIntOrNull() ?: 0
            if (k == GatewayClient.TOKEN_HEADER.lowercase()) token = v
        }
        if (!MessageDigest.isEqual(token.toByteArray(), Prefs.token.toByteArray())) return 401
        val buf = ByteArray(length)
        var read = 0
        while (read < length) {
            val n = input.read(buf, read, length - read)
            if (n < 0) break
            read += n
        }
        val data = runCatching { JSONObject(String(buf, 0, read)) }.getOrDefault(JSONObject())
        return try {
            handle(path, data)
        } catch (e: Exception) {
            Log.e(TAG, "Error en $path", e)
            500
        }
    }

    private fun readLine(input: InputStream): String? {
        val out = ByteArrayOutputStream()
        while (true) {
            val b = input.read()
            if (b < 0) return if (out.size() == 0) null else out.toString("UTF-8")
            if (b == '\n'.code) return out.toString("UTF-8").trimEnd('\r')
            out.write(b)
        }
    }

    private fun handle(path: String, d: JSONObject): Int {
        when (path.removePrefix("/api/whatsapp/")) {
            "incoming" -> incoming(d)
            "sync_batch" -> synchronized(syncLock) {
                Db.saveSyncBatch(d.optJSONArray("chats"), d.optJSONArray("contacts"), d.optJSONArray("messages"),
                    d.optBoolean("rename_chats"))
            }
            "read_elsewhere" -> readElsewhere(d.optJSONArray("chats"))
            "avatar" -> {
                val jid = d.str("jid")
                val p = d.str("avatar_path")
                if (jid != null && p != null) Db.updateAvatar(jid, p)
            }
            "status" -> {
                val id = d.str("message_id")
                val st = d.str("status")
                if (id != null && st != null) Db.updateMessageStatus(id, st)
            }
            "reaction" -> {
                val id = d.str("message_id")
                val who = d.str("reactor_jid")
                if (id != null && who != null) {
                    Db.setReaction(id, who, d.optString("emoji"), d.optLong("timestamp"), d.str("chat_jid"))
                }
            }
            "connection" -> AppState.updateConnection(d)
            "presence" -> AppState.updatePresence(d)
            "call" -> Notifier.call(d)
            "contact_status" -> Unit   // Los estados quedan para una versión posterior
            else -> return 404
        }
        return 200
    }

    private fun incoming(d: JSONObject) {
        val id = d.str("message_id") ?: return
        val chat = d.str("chat_jid") ?: return
        val sender = d.str("sender_jid") ?: chat
        val push = d.optString("push_name").takeUnless { d.isNull("push_name") }.orEmpty()
        val fromMe = d.optBoolean("from_me")
        val phone = d.str("phone")
        if ((push.isNotEmpty() || phone != null) && !fromMe && push.lowercase() !in listOf("yo", "null", "none")) {
            Db.saveContact(sender, push, phone, push)
        }
        val existed = Db.messageExists(id)
        if (existed && d.optBoolean("revoked")) {
            Db.revokeMessage(id)
            Notifier.refresh(chat)
            return
        }
        if (existed && d.optBoolean("edited")) {
            Db.applyEdit(id, d.optString("text"))
            Notifier.refresh(chat)
            return
        }
        val saved = Db.saveMessage(
            id = id, chat = chat, sender = sender, senderName = push, fromMe = fromMe, text = d.optString("text"),
            timestamp = d.optLong("timestamp", System.currentTimeMillis() / 1000),
            mediaType = d.str("media_type") ?: "text", mediaPath = d.str("media_path"),
            quotedText = d.str("quoted_text"), quotedSender = d.str("quoted_sender"),
            status = if (fromMe) "sent" else "delivered", chatName = d.str("chat_name"),
            mediaRaw = d.str("media_raw"), alreadyRead = d.optBoolean("already_read"),
        )
        val isNew = !existed && !d.optBoolean("edited") && !d.optBoolean("revoked")
        if (saved && isNew && !fromMe && d.optString("upsert_type") == "notify" && !d.optBoolean("already_read")) {
            Notifier.message(chat)
        }
    }

    private fun readElsewhere(chats: JSONArray?) {
        if (chats == null) return
        for (i in 0 until chats.length()) {
            val ch = chats.getJSONObject(i)
            val ids = ch.optJSONArray("message_ids")
            if (ids != null && ids.length() > 0) {
                Db.markMessagesReadElsewhere(List(ids.length()) { ids.getString(it) })
            }
            for (jid in listOfNotNull(ch.str("chat_jid"), ch.str("alt_jid"))) {
                if (ch.optBoolean("all")) {
                    Db.markChatReadElsewhere(jid)
                    Notifier.cancel(jid)
                } else if (ch.optBoolean("unread")) {
                    Db.markChatUnreadElsewhere(jid)
                }
            }
        }
    }

    private fun JSONObject.str(k: String): String? = if (isNull(k)) null else optString(k).ifEmpty { null }

    private companion object {
        const val TAG = "Webhook"
    }
}
