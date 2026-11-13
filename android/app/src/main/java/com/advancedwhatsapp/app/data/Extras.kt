package com.advancedwhatsapp.app.data

import com.advancedwhatsapp.app.App
import com.advancedwhatsapp.app.Prefs
import com.advancedwhatsapp.app.gateway.GatewayClient
import kotlinx.coroutines.flow.MutableStateFlow
import org.json.JSONArray
import org.json.JSONObject
import java.io.File

data class Template(val shortcut: String, val text: String)

/** Una opción de encuesta con su conteo; [mine] = votaste esa. */
data class PollOption(val name: String, val votes: Int, val mine: Boolean)

data class PollView(val name: String, val multi: Boolean, val voters: Int, val options: List<PollOption>)

/**
 * Mensajería extra, igual que en la PC: plantillas/atajos, respuestas automáticas, encuestas, envío a varios y
 * descripciones de repeticiones. Bloqueante donde habla con el gateway: usar desde Dispatchers.IO.
 */
object Extras {
    /** Ajuste «Conservar mensajes eliminados y versiones editadas» (activado por defecto). */
    var keepDeleted: Boolean
        get() = Prefs.getString("keep_deleted", "1") == "1"
        set(v) = Prefs.putString("keep_deleted", if (v) "1" else "0")

    /** Último aviso de un envío a varios que terminó (lo manda el gateway). */
    val bulkDone = MutableStateFlow<JSONObject?>(null)

    // ─── Plantillas / atajos ────────────────────────────────────────────────
    // ~/.magsapp/plantillas.json en la PC; acá la misma carpeta de datos que usa el gateway. Mismo formato.

    private val templatesFile: File get() = File(App.dataDir, "plantillas.json")
    @Volatile private var templatesCache: List<Template>? = null
    private val trailing = Regex("(?:^|\\s)/([\\p{L}\\p{N}_-]+)$")

    fun cleanShortcut(s: String): String = s.trim().trimStart('/').lowercase().filter { it.isLetterOrDigit() || it == '_' || it == '-' }

    fun templates(): List<Template> {
        templatesCache?.let { return it }
        val out = mutableListOf<Template>()
        runCatching {
            val arr = JSONObject(templatesFile.readText()).optJSONArray("items") ?: JSONArray()
            for (i in 0 until arr.length()) {
                val o = arr.getJSONObject(i)
                val sc = cleanShortcut(o.optString("shortcut"))
                val text = o.optString("text").trim()
                if (sc.isNotEmpty() && text.isNotEmpty() && out.none { it.shortcut == sc }) out += Template(sc, text)
            }
        }
        templatesCache = out
        return out
    }

    fun saveTemplates(items: List<Template>): Boolean {
        val clean = mutableListOf<Template>()
        for (t in items) {
            val sc = cleanShortcut(t.shortcut)
            val text = t.text.trim()
            if (sc.isNotEmpty() && text.isNotEmpty() && clean.none { it.shortcut == sc }) clean += Template(sc, text)
        }
        return runCatching {
            val arr = JSONArray()
            clean.forEach { arr.put(JSONObject().put("shortcut", it.shortcut).put("text", it.text)) }
            val tmp = File(templatesFile.path + ".tmp")
            tmp.writeText(JSONObject().put("items", arr).toString(), Charsets.UTF_8)
            if (!tmp.renameTo(templatesFile)) {
                templatesFile.delete()
                tmp.renameTo(templatesFile)
            }
            templatesCache = clean
            true
        }.getOrDefault(false)
    }

    /** El «/atajo» con el que termina el texto (sin la barra), o null. */
    fun trailingShortcut(text: String): String? = trailing.find(text)?.groupValues?.get(1)

    /** Atajos que empiezan con lo escrito después de la barra (para las sugerencias sobre el teclado). */
    fun suggestions(text: String, limit: Int = 6): List<Template> {
        val sc = trailingShortcut(text)?.lowercase() ?: return emptyList()
        return templates().filter { it.shortcut.startsWith(sc) }.take(limit)
    }

    /** Si se acaba de escribir un espacio justo después de un «/atajo» existente, devuelve el texto con la plantilla puesta. */
    fun expandAfterSpace(text: String): String? {
        if (!text.endsWith(" ")) return null
        val body = text.dropLast(1)
        val sc = trailingShortcut(body) ?: return null
        val tpl = templates().firstOrNull { it.shortcut == sc.lowercase() } ?: return null
        return body.dropLast(sc.length + 1) + tpl.text + " "
    }

    /** Reemplaza el «/prefijo» del final por la plantilla elegida. */
    fun applyTemplate(text: String, t: Template): String {
        val sc = trailingShortcut(text) ?: return text + t.text
        return text.dropLast(sc.length + 1) + t.text
    }

    // ─── Respuestas automáticas (las ejecuta el gateway, funcionan con la app cerrada) ───

    fun autoReplyConfig(): JSONObject = GatewayClient.get("/auto-reply")
    fun saveAutoReply(cfg: JSONObject): JSONObject = GatewayClient.post("/auto-reply", cfg)

    fun newRule(manual: Boolean): JSONObject = JSONObject()
        .put("id", java.util.UUID.randomUUID().toString().take(8))
        .put("name", if (manual) "Estoy manejando" else "Fuera de horario")
        .put("enabled", true).put("kind", if (manual) "manual" else "schedule")
        .put("text", if (manual) "Estoy manejando, te respondo apenas llegue." else "Hola, ahora no estoy disponible. Te respondo apenas pueda.")
        .put("days", JSONArray()).put("from", "22:00").put("to", "08:00")
        .put("individual_only", true).put("exclude", JSONArray()).put("only", JSONArray()).put("cooldown_hours", 12)

    private val DAY_NAMES = listOf("Dom", "Lun", "Mar", "Mié", "Jue", "Vie", "Sáb")

    fun describeRule(r: JSONObject): String {
        val when_ = if (r.optString("kind") == "manual") "modo manual" else {
            val days = r.optJSONArray("days")?.let { a -> List(a.length()) { a.getInt(it) } }.orEmpty()
            val dayText = if (days.isEmpty() || days.size == 7) "todos los días" else days.sorted().joinToString(", ") { DAY_NAMES[it] }
            "$dayText de ${r.optString("from")} a ${r.optString("to")}"
        }
        val extra = mutableListOf<String>()
        if (r.optBoolean("individual_only", true)) extra += "solo chats individuales"
        r.optJSONArray("only")?.length()?.takeIf { it > 0 }?.let { extra += "solo a $it contactos" }
        r.optJSONArray("exclude")?.length()?.takeIf { it > 0 }?.let { extra += "$it excepciones" }
        val h = r.optInt("cooldown_hours", 12)
        extra += if (h > 0) "una vez cada $h h por contacto" else "sin límite por contacto"
        return "$when_ · ${extra.joinToString(" · ")}"
    }

    // ─── Encuestas ───────────────────────────────────────────────────────────

    fun sendPoll(to: String, name: String, options: List<String>, multiple: Boolean) {
        GatewayClient.post("/poll", JSONObject().put("to", to).put("name", name).put("options", JSONArray(options)).put("multiple", multiple))
    }

    fun parsePoll(m: Message): PollView? {
        val raw = m.pollJson ?: return null
        return runCatching {
            val o = JSONObject(raw)
            val arr = o.optJSONArray("options") ?: return null
            val opts = List(arr.length()) {
                val x = arr.getJSONObject(it)
                PollOption(x.optString("name"), x.optInt("votes"), x.optBoolean("mine"))
            }
            if (opts.isEmpty()) null else PollView(o.optString("name"), o.optBoolean("multi"), o.optInt("voters"), opts)
        }.getOrNull()
    }

    // ─── Envío a varios ──────────────────────────────────────────────────────

    /** El mismo mensaje (o archivo) a cada contacto por separado, con una pausa al azar entre [delayMin] y [delayMax] segundos. */
    fun sendToMany(targets: List<Chat>, text: String, file: File?, kind: String?, fileName: String?, delayMin: Int, delayMax: Int) {
        val body = JSONObject().put("text", text).put("delay_min", delayMin).put("delay_max", maxOf(delayMin, delayMax))
        body.put("targets", JSONArray().also { arr -> targets.forEach { arr.put(JSONObject().put("to", it.jid).put("chat_name", it.name)) } })
        if (file != null) when (kind) {
            "image" -> body.put("image_path", file.path).put("image_thumb", Photos.thumbnailBase64(file))
            "video" -> body.put("video_path", file.path)
            else -> body.put("document_path", file.path).put("document_name", fileName ?: file.name)
        }
        GatewayClient.post("/send-many", body)
    }

    // ─── Repeticiones de mensajes programados ────────────────────────────────

    /** Repetición elegida para el próximo mensaje que se programe; [Scheduled.enqueue] la toma y la limpia. */
    @Volatile var nextRepeat: JSONObject? = null

    fun repeatOf(type: String, days: List<Int> = emptyList()): JSONObject =
        JSONObject().put("type", type).also { if (type == "weekly") it.put("days", JSONArray(days)) }

    fun describeRepeat(r: JSONObject?): String? {
        r ?: return null
        val every = r.optInt("every", 1).coerceAtLeast(1)
        return when (r.optString("type")) {
            "daily" -> if (every == 1) "Todos los días" else "Cada $every días"
            "weekly" -> {
                val days = r.optJSONArray("days")?.let { a -> List(a.length()) { a.getInt(it) } }.orEmpty()
                val head = if (every == 1) "Cada semana" else "Cada $every semanas"
                if (days.isEmpty()) head else "$head (${days.sorted().joinToString(", ") { DAY_NAMES[it].lowercase() }})"
            }
            "monthly" -> if (every == 1) "Todos los meses" else "Cada $every meses"
            "yearly" -> if (every == 1) "Todos los años" else "Cada $every años"
            else -> null
        }
    }

    // ─── Fijar mensajes ──────────────────────────────────────────────────────

    /** Fija o desfija un mensaje en su chat (hasta 3) y, si hay conexión, también en WhatsApp. Bloqueante. */
    fun pin(m: Message, on: Boolean) {
        Db.setMessagePinned(m.id, on)
        runCatching {
            GatewayClient.post(
                "/pin",
                JSONObject().put("to", m.chatJid).put("message_id", m.id).put("pinned", on).put("from_me", m.fromMe)
                    .put("participant", m.senderJid),
            )
        }
    }
}
