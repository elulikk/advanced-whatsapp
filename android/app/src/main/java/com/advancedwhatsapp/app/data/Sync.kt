package com.advancedwhatsapp.app.data

import com.advancedwhatsapp.app.App
import com.advancedwhatsapp.app.AppState
import com.advancedwhatsapp.app.Prefs
import com.advancedwhatsapp.app.gateway.GatewayClient
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.io.FileInputStream
import java.io.FileOutputStream

/**
 * Sincronización con la app de la PC por el chat «Mi número» (equivale a app/sync.py). Cada app, cuando tiene cambios propios,
 * manda un documento `awsync-<dispositivo>-<ts>.awsync` (contenedor cifrado con la clave de sincronización) con las secciones
 * sincronizables; la otra lo reconoce por el nombre, lo mezcla ítem por ítem (gana el último cambio) y no lo muestra como mensaje.
 * Cada envío borra el anterior para todos: es una foto completa. Bloqueante: llamar desde Dispatchers.IO.
 */
object Sync {
    const val PREFIX = "awsync-"
    private const val SECRET = "sync"
    private const val MAX_FILES_BYTES = 20L * 1024 * 1024
    private val busy = Any()
    @Volatile private var sending = false

    // ── Ajustes ──

    var enabled: Boolean
        get() = Prefs.getString("sync_enabled") == "1" && hasPassword()
        set(v) = Prefs.putString("sync_enabled", if (v) "1" else "0")

    fun hasPassword() = SecretStore.get(SECRET).isNotEmpty()
    fun setPassword(p: String) = SecretStore.set(SECRET, p)

    private val metaFile: File get() = File(App.dataDir, "sync_meta.json")
    private fun meta(): JSONObject = runCatching { JSONObject(metaFile.readText()) }.getOrDefault(JSONObject())
    private fun saveMeta(m: JSONObject) {
        val tmp = File(App.dataDir, "sync_meta.json.tmp")
        tmp.writeText(m.toString())
        tmp.renameTo(metaFile)
    }

    /** (último envío, última recepción) en segundos epoch; 0 = nunca. */
    fun lastTimes(): Pair<Long, Long> = meta().let { it.optLong("sent_at") to it.optLong("received_at") }

    private fun phoneDigits(): String = (AppState.connection.value.phone ?: "").filter { it.isDigit() }

    /** JID del chat «Mi número», o null si no hay sesión. */
    fun ownJid(): String? {
        val phone = phoneDigits()
        return if (AppState.connection.value.isConnected && phone.isNotEmpty()) "$phone@s.whatsapp.net" else null
    }

    private fun isOwnChat(jid: String): Boolean = phoneDigits().let { it.isNotEmpty() && jid.substringBefore('@').substringBefore(':') == it }

    fun isSyncDoc(d: JSONObject): Boolean = Db.isSyncDoc(d.optString("media_type"), d.optString("text"))

    private fun currentDigest(): String =
        Backup.digest(Backup.sections(syncOnly = true, includeSecret = false).associate { it.name to Backup.localItems(it) })

    // ── Enviar ──

    /** Manda a «Mi número» las novedades (o todo, con `full`). Devuelve null si se envió, o el motivo/mensaje si no. */
    fun sendNow(full: Boolean = false): String? {
        val password = SecretStore.get(SECRET)
        if (password.isEmpty()) return "Falta la clave de sincronización."
        synchronized(busy) {
            if (sending) return null
            sending = true
        }
        val tmpZip = File(App.instance.cacheDir, "awsync_${System.nanoTime()}.zip")
        var outFile: File? = null
        try {
            val jid = ownJid() ?: return "WhatsApp no está conectado todavía."
            val m = meta()
            val sentFiles = HashSet<String>().also { s ->
                m.optJSONArray("sent_files")?.let { a -> for (i in 0 until a.length()) s += a.getString(i) }
            }
            val built = Backup.buildZip(
                tmpZip, "sync", Backup.sections(syncOnly = true, includeSecret = false),
                fileFilter = { sec, items ->
                    items.filter { (k, v) -> !v.deleted && (full || "${sec.name}:$k" !in sentFiles) }.keys.toList()
                },
                maxFilesBytes = MAX_FILES_BYTES,
            )
            val dig = Backup.digest(built.items)
            if (!full && dig == m.optString("last_digest")) return null   // nada nuevo
            val name = "$PREFIX${Backup.deviceId().take(8)}-${System.currentTimeMillis() / 1000}.awsync"
            val target = File(App.sentDir, name)
            outFile = target
            FileInputStream(tmpZip).use { src -> FileOutputStream(target).use { AwCrypto.encrypt(src, it, password) } }
            val res = GatewayClient.post(
                "/send", JSONObject().put("to", jid).put("text", "").put("document_path", target.absolutePath).put("document_name", name),
            )
            if (!res.optBoolean("success")) return res.optString("error").ifEmpty { "No se pudo enviar." }
            val old = m.optString("last_msg_id")
            for ((sec, keys) in built.filesSent) keys.forEach { sentFiles += "$sec:$it" }
            m.put("last_digest", dig).put("last_msg_id", res.optString("message_id")).put("sent_at", System.currentTimeMillis() / 1000)
                .put("sent_files", JSONArray(sentFiles.sorted()))
            saveMeta(m)
            if (old.isNotEmpty()) runCatching { GatewayClient.post("/delete", JSONObject().put("to", jid).put("message_id", old)) }
            return null
        } catch (e: BackupException) {
            return e.message
        } catch (e: Exception) {
            return e.message ?: "No se pudo sincronizar."
        } finally {
            sending = false
            tmpZip.delete()
            outFile?.delete()
        }
    }

    /** Llamada periódica: manda lo nuevo si la sincronización está activa y hay conexión. */
    fun tick() {
        if (enabled && AppState.connection.value.isConnected) sendNow(full = false)
    }

    // ── Recibir ──

    /** Procesa un documento de sincronización llegado por el webhook. Devuelve cuántos cambios entraron (o null si se ignoró). */
    fun handleIncoming(d: JSONObject): Int? {
        val password = SecretStore.get(SECRET)
        if (password.isEmpty() || Prefs.getString("sync_enabled") != "1") return null
        if (!isOwnChat(d.optString("chat_jid"))) return null   // un documento con ese nombre de otra persona no se toca
        if (d.isNull("media_raw")) return null
        val res = GatewayClient.post(
            "/media/download",
            JSONObject().put("message_id", d.optString("message_id")).put("media_type", "document").put("media_raw", d.optString("media_raw")),
        )
        val path = res.optString("media_path")
        if (path.isEmpty() || !File(path).exists()) return null
        val zip = File(App.instance.cacheDir, "awsync_in_${System.nanoTime()}.zip")
        try {
            FileInputStream(path).use { src -> FileOutputStream(zip).use { AwCrypto.decrypt(src, it, password) } }
            val (zf, manifest) = Backup.openZip(zip)
            val result: Int? = zf.use { z ->
                if (manifest.optString("kind") != "sync" || manifest.optJSONObject("device")?.optString("id") == Backup.deviceId()) {
                    null
                } else {
                    val report = Backup.applyDocument(z, manifest, onlySync = true, includeSecrets = false)
                    val applied: Int = report.values.sumOf { r -> r.applied }
                    val localWins: Int = report.values.sumOf { r -> r.localWins }
                    val m = meta()
                    m.put("received_at", System.currentTimeMillis() / 1000)
                    // El otro lado ya tiene todo lo mío: no hace falta contestar (evita el ida y vuelta infinito)
                    if (localWins == 0) m.put("last_digest", currentDigest())
                    saveMeta(m)
                    if (applied > 0) Db.refreshAfterMerge(emptyList())
                    applied
                }
            }
            return result
        } finally {
            zip.delete()
            File(path).delete()
        }
    }
}
