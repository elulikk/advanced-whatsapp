package com.advancedwhatsapp.app.data

import com.advancedwhatsapp.app.Prefs
import com.advancedwhatsapp.app.gateway.GatewayClient
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.io.FileOutputStream
import java.security.MessageDigest

/**
 * Secciones propias de la app para las copias y la sincronización (equivale a app/backup_sections.py de la PC).
 * Para sumar datos nuevos (plantillas, carpetas inteligentes, chats ocultos...) se agrega acá una `Section` con `collect` y
 * `apply` y se la registra en [registerAll]. Las claves y los valores tienen que ser iguales a los de la PC.
 */
object BackupSections {
    private val STICKER_FILE = Regex("^[0-9a-f]{16}\\.webp$")
    private val SHARED_PREFS = listOf("auto_transcribe", "status_receipts", "notify_groups")
    private val PROVIDERS = mapOf("gemini" to "Gemini", "groq" to "Groq", "openai" to "OpenAI", "anthropic" to "Claude")

    fun registerAll() {
        val reg = Backup::register
        reg(Section("chat_names", "Nombres de contactos", { chatColumn("custom_name") }, { applyChatColumn("custom_name", it) }, order = 10))
        reg(Section("chat_notes", "Notas de contactos", { chatColumn("notes") }, { applyChatColumn("notes", it) }, order = 11))
        reg(Section("chat_locks", "Chats bloqueados", this::collectLocks, this::applyLocks, order = 30))
        reg(
            Section(
                "stickers", "Stickers", this::collectStickers, this::applyStickers, files = this::stickerFile,
                importFile = this::importStickerFile,
                order = 40,
            ),
        )
        reg(Section("prefs", "Preferencias compartidas", this::collectPrefs, this::applyPrefs, order = 50))
        reg(Section("config", "Configuración del celular", this::collectConfig, this::applyConfig, sync = false, platforms = listOf("android"), order = 60))
        reg(Section("secrets", "Claves de IA", this::collectSecrets, this::applySecrets, sync = false, secret = true, order = 61))
        reg(Section("scheduled_messages", "Mensajes programados", this::collectScheduled, this::applyScheduled, sync = false, order = 70))
    }

    // ── Nombres y notas ──

    private fun chatColumn(col: String): Map<String, Any?> {
        val out = LinkedHashMap<String, Any?>()
        Db.raw().rawQuery("SELECT jid, $col FROM chats WHERE $col IS NOT NULL AND trim($col) != ''", null).use {
            while (it.moveToNext()) out[it.getString(0)] = it.getString(1)
        }
        return out
    }

    private fun applyChatColumn(col: String, changes: Map<String, Any?>): Set<String> {
        val done = HashSet<String>()
        val d = Db.raw()
        for ((jid, v) in changes) {
            val clean = (v as? String)?.trim().orEmpty()
            val cv = android.content.ContentValues()
            if (clean.isEmpty()) cv.putNull(col) else cv.put(col, clean)
            if (d.update("chats", cv, "jid = ?", arrayOf(jid)) > 0) done += jid   // si el chat todavía no existe queda pendiente
        }
        Db.refreshAfterMerge(emptyList())
        return done
    }

    // ── Chats bloqueados ──

    private fun collectLocks(): Map<String, Any?> {
        val (locked, pw) = ChatLock.syncExport()
        val out = LinkedHashMap<String, Any?>()
        for (jid in locked) out["lock:$jid"] = true
        if (pw != null) out["pw"] = mapOf("hash" to pw.first, "salt" to pw.second, "iters" to pw.third.toLong())
        return out
    }

    private fun applyLocks(changes: Map<String, Any?>): Set<String> {
        val lock = HashSet<String>()
        val unlock = HashSet<String>()
        var pw: Triple<String, String, Int>? = null
        var removePw = false
        val done = HashSet<String>()
        for ((k, v) in changes) {
            if (k == "pw") {
                val m = v as? Map<*, *>
                val hash = m?.get("hash") as? String
                if (m == null || hash.isNullOrEmpty()) removePw = true
                else pw = Triple(hash, (m["salt"] as? String).orEmpty(), ((m["iters"] as? Number)?.toInt()) ?: 120_000)
                done += k
            } else if (k.startsWith("lock:")) {
                if (v == true) lock += k.removePrefix("lock:") else unlock += k.removePrefix("lock:")
                done += k
            }
        }
        ChatLock.syncApply(lock, unlock, pw, removePw)
        return done
    }

    // ── Stickers ──

    private fun collectStickers(): Map<String, Any?> = StickerLibrary.withIndex { idx, dir ->
        val out = LinkedHashMap<String, Any?>()
        val packs = idx.getJSONArray("packs")
        for (i in 0 until packs.length()) out["pack:${packs.getString(i)}"] = true
        val map = idx.getJSONObject("stickers")
        for (id in map.keys()) {
            val r = map.getJSONObject(id)
            if (!File(dir, r.optString("file")).exists()) continue
            out["sticker:$id"] = mapOf(
                "pack" to r.optString("pack", StickerLibrary.DEFAULT_PACK), "fav" to r.optBoolean("fav"),
                "animated" to r.optBoolean("animated"), "added" to r.optDouble("added", 0.0).toLong(),
            )
        }
        out
    }

    private fun applyStickers(changes: Map<String, Any?>): Set<String> = StickerLibrary.withIndex { idx, dir ->
        val done = HashSet<String>()
        val packs = idx.getJSONArray("packs")
        val map = idx.getJSONObject("stickers")
        fun hasPack(n: String) = (0 until packs.length()).any { packs.getString(it) == n }
        for ((k, v) in changes) {
            if (!k.startsWith("pack:")) continue
            val name = k.removePrefix("pack:")
            if (v == true && !hasPack(name)) packs.put(name)
            done += k
        }
        for ((k, v) in changes) {
            if (!k.startsWith("sticker:")) continue
            val id = k.removePrefix("sticker:")
            if (v == null) {
                (map.remove(id) as? JSONObject)?.let { File(dir, it.optString("file")).delete() }
                done += k
            } else if (v is Map<*, *>) {
                if (!STICKER_FILE.matches("$id.webp") || !File(dir, "$id.webp").exists()) continue   // el archivo todavía no llegó
                val pack = ((v["pack"] as? String)?.trim()).orEmpty().ifEmpty { StickerLibrary.DEFAULT_PACK }
                if (!hasPack(pack)) packs.put(pack)
                val rec = map.optJSONObject(id) ?: JSONObject().put("id", id).put("file", "$id.webp").put("used", 0.0).put("uses", 0)
                rec.put("pack", pack).put("fav", v["fav"] == true).put("animated", v["animated"] == true)
                if (rec.optDouble("added", 0.0) == 0.0) rec.put("added", ((v["added"] as? Number)?.toDouble()) ?: 0.0)
                map.put(id, rec)
                done += k
            }
        }
        StickerLibrary.saveIndex(idx)
        done
    }

    private fun stickerFile(key: String): Pair<String, File>? {
        if (!key.startsWith("sticker:")) return null
        val id = key.removePrefix("sticker:")
        return StickerLibrary.withIndex { _, dir -> "$id.webp" to File(dir, "$id.webp") }
    }

    private fun importStickerFile(rest: String, data: ByteArray) {
        if (!STICKER_FILE.matches(rest) || data.size > 4 * 1024 * 1024) return
        // El id es el hash del contenido: si no coincide, el archivo está adulterado o dañado
        val sha = MessageDigest.getInstance("SHA-1").digest(data).joinToString("") { "%02x".format(it) }.take(16)
        if (sha != rest.removeSuffix(".webp")) return
        StickerLibrary.withIndex { _, dir ->
            val f = File(dir, rest)
            if (!f.exists()) {
                val tmp = File(dir, "$rest.tmp")
                FileOutputStream(tmp).use { it.write(data) }
                tmp.renameTo(f)
            }
        }
    }

    // ── Preferencias compartidas con la PC ──

    private fun collectPrefs(): Map<String, Any?> = mapOf(
        "auto_transcribe" to Prefs.autoTranscribe, "status_receipts" to Prefs.statusReceipts,
        "notify_groups" to Prefs.notifyGroups, "use_profile" to Prefs.useProfileInReplies,
    )

    private fun applyPrefs(changes: Map<String, Any?>): Set<String> {
        val done = HashSet<String>()
        for ((k, v) in changes) {
            val b = v as? Boolean ?: continue
            when (k) {
                "auto_transcribe" -> Prefs.autoTranscribe = b
                "status_receipts" -> Prefs.statusReceipts = b
                "notify_groups" -> Prefs.notifyGroups = b
                "use_profile" -> Prefs.useProfileInReplies = b
                else -> continue
            }
            done += k
        }
        return done
    }

    // ── Configuración propia del celular y claves de IA ──

    private fun collectConfig(): Map<String, Any?> = Prefs.exportNonSecret()

    private fun applyConfig(changes: Map<String, Any?>): Set<String> {
        val done = HashSet<String>()
        for ((k, v) in changes) {
            if (k == "token" || k.startsWith("key_") || k.startsWith("lock_") || k.startsWith("secret_") || k.startsWith("bk_") || k.startsWith("sync_")) continue
            if (v == null) Prefs.remove(k) else if (v is Boolean || v is Long || v is String) Prefs.setRaw(k, v!!) else continue
            done += k
        }
        return done
    }

    private fun collectSecrets(): Map<String, Any?> {
        val out = LinkedHashMap<String, Any?>()
        for ((canon, prov) in PROVIDERS) {
            val key = Prefs.apiKey(prov)
            if (key.isEmpty()) continue
            out["ai.$canon.key"] = key
            Prefs.model(prov).takeIf { it.isNotEmpty() }?.let { out["ai.$canon.model"] = it }
        }
        return out
    }

    private fun applySecrets(changes: Map<String, Any?>): Set<String> {
        val done = HashSet<String>()
        val re = Regex("^ai\\.(gemini|groq|openai|anthropic)\\.(key|model)$")
        for ((k, v) in changes) {
            val m = re.matchEntire(k) ?: continue
            val value = v as? String ?: continue
            val prov = PROVIDERS.getValue(m.groupValues[1])
            if (m.groupValues[2] == "key") Prefs.setApiKey(prov, value) else Prefs.setModel(prov, value)
            done += k
        }
        return done
    }

    // ── Mensajes programados (solo copias: cada gateway envía los suyos) ──

    private fun schedKey(to: String, at: Long, text: String): String {
        val h = MessageDigest.getInstance("SHA-1").digest(text.toByteArray(Charsets.UTF_8)).joinToString("") { "%02x".format(it) }.take(8)
        return "msg:$to|$at|$h"
    }

    private fun collectScheduled(): Map<String, Any?> {
        val items: JSONArray = runCatching { GatewayClient.get("/message-schedule").optJSONArray("items") }.getOrNull() ?: return emptyMap()
        val now = System.currentTimeMillis() / 1000
        val out = LinkedHashMap<String, Any?>()
        for (i in 0 until items.length()) {
            val it = items.getJSONObject(i)
            val text = it.optString("text")
            if (it.optString("state") != "pending" || it.optString("type") != "text" || it.optLong("at") <= now || text.isEmpty()) continue
            val to = it.optString("to")
            out[schedKey(to, it.optLong("at"), text)] = mapOf(
                "to" to to, "text" to text, "at" to it.optLong("at"), "chat_name" to (if (it.isNull("chat_name")) "" else it.optString("chat_name")),
            )
        }
        return out
    }

    private fun applyScheduled(changes: Map<String, Any?>): Set<String> {
        val have = collectScheduled().keys
        val done = HashSet<String>()
        val now = System.currentTimeMillis() / 1000
        for ((k, v) in changes) {
            val m = v as? Map<*, *> ?: continue   // los borrados no se deshacen
            val at = (m["at"] as? Number)?.toLong() ?: continue
            if (k in have || at <= now + 30) continue
            val body = JSONObject().put("to", m["to"]).put("text", m["text"]).put("at", at)
            (m["chat_name"] as? String)?.takeIf { it.isNotEmpty() }?.let { body.put("chat_name", it) }
            if (runCatching { GatewayClient.post("/message-schedule", body) }.isSuccess) done += k
        }
        return done
    }
}
