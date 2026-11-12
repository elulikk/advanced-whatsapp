package com.advancedwhatsapp.app.ai

import com.advancedwhatsapp.app.Prefs
import com.advancedwhatsapp.app.data.Db
import com.advancedwhatsapp.app.data.Message
import java.text.Normalizer
import java.util.Calendar

/**
 * Funciones inteligentes extra. Traducción de app/ai/smart_features.py:
 * «Lo que te perdiste», respuestas rápidas, búsqueda por significado, fechas detectadas, traducción y «Requiere respuesta».
 * La parte pura (regex, parsers, prompts) no depende de Android; lo que llama a la IA es bloqueante (Dispatchers.IO).
 */
object SmartFeatures {
    const val MISSED_MIN_UNREAD = 15
    const val MISSED_MAX_MESSAGES = 150
    const val MISSED_MAX_CHARS = 14000
    const val QUICK_CONTEXT = 12
    const val SEARCH_CANDIDATES = 40
    const val SEARCH_RESULTS = 12
    val LANGUAGES = listOf("español", "inglés", "portugués", "francés", "italiano", "alemán", "catalán", "japonés", "chino")
    const val DEFAULT_LANG = "español"

    /** Caché LRU chica y con candado: guarda lo que contestó la IA para no repetir la consulta. */
    class Lru<K, V>(private val max: Int) {
        private val map = object : LinkedHashMap<K, V>(16, 0.75f, true) {
            override fun removeEldestEntry(eldest: MutableMap.MutableEntry<K, V>?) = size > max
        }

        @Synchronized fun get(k: K): V? = map[k]
        @Synchronized fun put(k: K, v: V) { map[k] = v }
        @Synchronized fun clear() = map.clear()
    }

    private val quickCache = Lru<Pair<String, String>, List<String>>(64)
    private val missedCache = Lru<Triple<String, String, Int>, String>(24)
    private val translateCache = Lru<Pair<String, String>, String>(200)
    private val searchCache = Lru<String, List<String>>(30)

    fun stripAccents(s: String): String = Normalizer.normalize(s, Normalizer.Form.NFD).replace(Regex("\\p{Mn}+"), "")

    fun text(m: Message): String = MessageSummary.messageText(m)

    fun aiAvailable(): Boolean = AiService.PROVIDERS.any { AiService.hasKey(it) }

    /** Claude Haiku si hay clave de Anthropic; si no, el proveedor preferido con su modelo. */
    fun chooseAi(): Pair<String, String> {
        val provider = Prefs.provider
        return MessageSummary.chooseAi(provider, AiService.defaultModel(provider))
    }

    private fun isErr(out: String?) = AiService.isError(out)

    private fun cleanReply(raw: String): String {
        var t = raw.trim()
        Regex("^```[\\w-]*\\n([\\s\\S]*?)\\n?```$").find(t)?.let { t = it.groupValues[1].trim() }
        if (t.length >= 2 && t[0] in "\"“«" && t.last() in "\"”»") {
            val inner = t.substring(1, t.length - 1)
            if (t[0] !in inner && t.last() !in inner) t = inner.trim()
        }
        return t
    }

    // ─── Preferencias ────────────────────────────────────────────────────────

    var quickRepliesOn: Boolean
        get() = Prefs.getString("quick_replies", "1") == "1"
        set(v) = Prefs.putString("quick_replies", if (v) "1" else "0")

    var dateRemindersOn: Boolean
        get() = Prefs.getString("date_reminders", "1") == "1"
        set(v) = Prefs.putString("date_reminders", if (v) "1" else "0")

    var translateLang: String
        get() = Prefs.getString("translate_lang", DEFAULT_LANG).ifBlank { DEFAULT_LANG }
        set(v) = Prefs.putString("translate_lang", v.ifBlank { DEFAULT_LANG })

    // ─── «Requiere respuesta» ────────────────────────────────────────────────

    private val ACK_RE = Regex("^\\W*(ok|oka|okey|dale|listo|gracias|graciass*|jaja+j*|jeje+|bueno|genial|perfecto|👍|👌|🙏)\\W*$", RegexOption.IGNORE_CASE)
    private val REQUEST_RE = Regex(
        "(avis|decime|dec[ií]me|pasame|p[aá]same|mandame|m[aá]ndame|enviame|confirm|contest|respond|fijate|necesito|te pido|" +
            "podr[ií]as|pod[eé]s|puedes|me pas|me mand|me avis|me conf)",
        RegexOption.IGNORE_CASE,
    )

    /** ¿El mensaje hace una pregunta o pide algo? Heurística barata: signo de pregunta o pedido explícito. */
    fun looksLikeRequest(text: String): Boolean {
        val t = text.trim()
        if (t.isEmpty() || ACK_RE.matches(t)) return false
        if ('?' in t || '¿' in t) return true
        return t.length >= 8 && REQUEST_RE.containsMatchIn(t)
    }

    /** msgs en orden cronológico. True si, después de mi último mensaje, quedó una pregunta o pedido sin contestar. */
    fun needsReply(msgs: List<Message>): Boolean {
        val tail = ArrayList<Message>()
        for (m in msgs.asReversed()) {
            if (m.mediaType == "revoked") continue
            if (m.fromMe) break
            tail += m
            if (tail.size >= 5) break
        }
        return tail.any { looksLikeRequest(text(it)) }
    }

    /** Chats individuales (de los recibidos) con algo sin responder, de los últimos [maxAgeDays] días. */
    fun needsReplyJids(jids: List<String>, maxAgeDays: Int = 14): Set<String> {
        val since = System.currentTimeMillis() / 1000 - maxAgeDays * 86400L
        return jids.filter { !it.endsWith("@g.us") && !com.advancedwhatsapp.app.data.ChatLock.isLocked(it) }.filter { jid ->
            val msgs = Db.lastMessages(jid, 6)
            msgs.isNotEmpty() && normTs(msgs.last().timestamp) >= since && needsReply(msgs)
        }.toSet()
    }

    private fun normTs(ts: Long) = if (ts > 1_000_000_000_000L) ts / 1000 else ts

    // ─── Fechas y horas en español ───────────────────────────────────────────

    data class Detected(val ts: Long, val label: String, val hasTime: Boolean)

    private val WEEKDAYS = mapOf("lunes" to 0, "martes" to 1, "miercoles" to 2, "jueves" to 3, "viernes" to 4, "sabado" to 5, "domingo" to 6)
    private val WEEKDAY_NAMES = listOf("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo")
    private val MONTHS = mapOf(
        "enero" to 1, "febrero" to 2, "marzo" to 3, "abril" to 4, "mayo" to 5, "junio" to 6, "julio" to 7, "agosto" to 8,
        "septiembre" to 9, "setiembre" to 9, "octubre" to 10, "noviembre" to 11, "diciembre" to 12,
    )
    private val NUMWORDS = mapOf("un" to 1, "una" to 1, "dos" to 2, "tres" to 3, "cuatro" to 4, "cinco" to 5)

    private val TIME_RE = Regex(
        "(?:\\ba la?s?\\s+(\\d{1,2})(?:[:.h](\\d{2}))?\\s*(?:hs?\\.?|horas?)?(?:\\s*(am|pm|a\\.m\\.|p\\.m\\.))?" +
            "(?:\\s*(?:de la|del|en la|por la)\\s+(manana|tarde|noche|madrugada|mediodia))?)" +
            "|(?:\\b(\\d{1,2}):(\\d{2})\\s*(?:hs?\\.?|horas?)?(?:\\s*(am|pm))?\\b)" +
            "|(?:\\b(\\d{1,2})\\s*(am|pm)\\b)" +
            "|(?:\\b(\\d{1,2})\\s*(?:hs|h)\\b)",
        RegexOption.IGNORE_CASE,
    )
    private val REL_RE = Regex("\\ben\\s+(\\d{1,3}|una|un|media|dos|tres|cuatro|cinco)\\s*(minutos?|min|horas?|hs|dias?|semanas?)\\b")
    private val MONTH_DATE_RE = Regex("\\b(\\d{1,2})\\s+de\\s+(${MONTHS.keys.joinToString("|")})(?:\\s+(?:de|del)\\s+(\\d{4}))?\\b")
    private val NUM_DATE_RE = Regex("\\b(?:el|para el|dia|del|este|hasta el)\\s+(\\d{1,2})[/-](\\d{1,2})(?:[/-](\\d{2,4}))?\\b")
    private val WEEKDAY_RE = Regex("\\b(?:el|este|proximo|del|para el|hasta el)?\\s*(${WEEKDAYS.keys.joinToString("|")})\\b")

    /** (hora, minuto) de la primera hora que aparece en el texto sin acentos, o null. */
    private fun parseTime(norm: String): Pair<Int, Int>? {
        for (m in TIME_RE.findAll(norm)) {
            val g = m.groups
            var hh: Int
            var mm = 0
            var ap = ""
            var period: String? = null
            var strong = false
            when {
                g[1] != null -> { hh = g[1]!!.value.toInt(); mm = g[2]?.value?.toInt() ?: 0; ap = g[3]?.value.orEmpty(); period = g[4]?.value; strong = true }
                g[5] != null -> { hh = g[5]!!.value.toInt(); mm = g[6]!!.value.toInt(); ap = g[7]?.value.orEmpty() }
                g[8] != null -> { hh = g[8]!!.value.toInt(); ap = g[9]?.value.orEmpty() }
                else -> hh = g[10]!!.value.toInt()
            }
            if (hh > 24 || mm > 59) continue
            ap = ap.replace(".", "")
            if (ap == "pm" && hh < 12) hh += 12
            else if (ap == "am" && hh == 12) hh = 0
            else if (ap.isEmpty() && period != null) {
                if ((period == "tarde" || period == "noche") && hh < 12) hh += 12
                else if (period == "noche" && hh == 12) hh = 0
                else if (period == "mediodia" && hh < 6) hh += 12
            } else if (ap.isEmpty() && strong && hh in 1..7) hh += 12   // «a las 5» casi siempre es de la tarde
            if (hh == 24) hh = 0
            return hh to mm
        }
        return null
    }

    private fun dayStart(cal: Calendar): Calendar = (cal.clone() as Calendar).apply {
        set(Calendar.HOUR_OF_DAY, 0); set(Calendar.MINUTE, 0); set(Calendar.SECOND, 0); set(Calendar.MILLISECOND, 0)
    }

    private fun addDays(cal: Calendar, n: Int): Calendar = (cal.clone() as Calendar).apply { add(Calendar.DAY_OF_YEAR, n) }

    private fun safeDate(y: Int, mo: Int, d: Int, ref: Calendar, yearGiven: Boolean): Calendar? {
        fun make(year: Int): Calendar? {
            if (mo !in 1..12 || d !in 1..31) return null
            val c = Calendar.getInstance().apply { isLenient = false; clear(); set(year, mo - 1, d) }
            return try { c.timeInMillis; c } catch (e: Exception) { null }
        }
        val dt = make(y) ?: return null
        if (!yearGiven && dayStart(dt).before(dayStart(ref))) return make(y + 1)
        return dt
    }

    private fun label(c: Calendar, withTime: Boolean): String {
        val wd = (c.get(Calendar.DAY_OF_WEEK) + 5) % 7
        val s = "${WEEKDAY_NAMES[wd]} ${c.get(Calendar.DAY_OF_MONTH)}/${c.get(Calendar.MONTH) + 1}"
        return if (withTime) "%s %02d:%02d".format(s, c.get(Calendar.HOUR_OF_DAY), c.get(Calendar.MINUTE)) else s
    }

    private fun made(c: Calendar, withTime: Boolean) = Detected(c.timeInMillis / 1000, label(c, withTime), withTime)

    /**
     * Detecta una fecha/hora concreta («nos vemos el jueves a las 8», «el 15 de marzo», «mañana 18:30», «en 2 horas»).
     * [refSec] es la hora del mensaje (los «mañana» se cuentan desde ahí). El resultado puede estar en el pasado.
     */
    fun detectDatetime(text: String, refSec: Long): Detected? {
        val raw = text.trim()
        if (raw.length < 4 || raw.length > 600) return null
        val ref = Calendar.getInstance().apply { timeInMillis = (if (refSec > 0) normTs(refSec) else System.currentTimeMillis() / 1000) * 1000 }
        val norm = stripAccents(raw.lowercase())

        REL_RE.find(norm)?.let { rel ->
            val nRaw = rel.groupValues[1]
            val n = if (nRaw == "media") 0.5 else (NUMWORDS[nRaw] ?: nRaw.toIntOrNull() ?: 0).toDouble()
            val unit = rel.groupValues[2]
            val ok = if (unit.startsWith("min") || unit.startsWith("hora") || unit.startsWith("hs")) n > 0 && n <= 72 else n > 0 && n <= 60
            if (ok) {
                val millis = when {
                    unit.startsWith("min") -> n * 60_000
                    unit.startsWith("hora") || unit.startsWith("hs") -> n * 3_600_000
                    unit.startsWith("di") -> n * 86_400_000
                    else -> n * 7 * 86_400_000
                }
                val c = Calendar.getInstance().apply { timeInMillis = ref.timeInMillis + millis.toLong() }
                return made(c, true)
            }
        }

        // «de la mañana» no es el día de mañana
        var clean = norm.replace(Regex("\\b(?:de|a|por|en|la)\\s+la\\s+manana\\b|\\bde\\s+manana\\b(?!\\s+(?:a|temprano))"), " ")
        clean = clean.replace(Regex("\\bpor\\s+la\\s+manana\\b|\\ba\\s+la\\s+manana\\b|\\ben\\s+la\\s+manana\\b"), " ")
        val tm = parseTime(norm)

        var day: Calendar? = null
        var explicitDate = false
        MONTH_DATE_RE.find(clean)?.let { m ->
            val yg = m.groups[3] != null
            day = safeDate(m.groupValues[3].toIntOrNull() ?: ref.get(Calendar.YEAR), MONTHS.getValue(m.groupValues[2]), m.groupValues[1].toInt(), ref, yg)
            explicitDate = day != null
        }
        if (day == null) {
            NUM_DATE_RE.find(clean)?.let { m ->
                var y = m.groupValues[3].toIntOrNull() ?: ref.get(Calendar.YEAR)
                if (y < 100) y += 2000
                day = safeDate(y, m.groupValues[2].toInt(), m.groupValues[1].toInt(), ref, m.groups[3] != null)
                explicitDate = day != null
            }
        }
        if (day == null) {
            day = when {
                Regex("\\bpasado\\s+manana\\b").containsMatchIn(clean) -> dayStart(addDays(ref, 2))
                Regex("\\bmanana\\b").containsMatchIn(clean) -> dayStart(addDays(ref, 1))
                Regex("\\bhoy\\b").containsMatchIn(clean) -> dayStart(ref)
                else -> WEEKDAY_RE.find(clean)?.let { wd ->
                    val target = WEEKDAYS.getValue(wd.groupValues[1])
                    val refWd = (ref.get(Calendar.DAY_OF_WEEK) + 5) % 7
                    var ahead = ((target - refWd) % 7 + 7) % 7
                    val refMin = ref.get(Calendar.HOUR_OF_DAY) * 60 + ref.get(Calendar.MINUTE)
                    if (ahead == 0 && tm != null && tm.first * 60 + tm.second <= refMin) ahead = 7
                    dayStart(addDays(ref, ahead))
                }
            }
        }

        var d = day
        if (d == null && tm == null) return null
        if (d == null) {
            // Solo hora («a las 8»): hoy si todavía no pasó, si no mañana. Exige «a las».
            if (!Regex("\\ba la?s?\\s+\\d").containsMatchIn(norm)) return null
            d = dayStart(ref)
            val refMin = ref.get(Calendar.HOUR_OF_DAY) * 60 + ref.get(Calendar.MINUTE)
            if (tm!!.first * 60 + tm.second <= refMin) d = addDays(d, 1)
        } else if (tm == null && !explicitDate) {
            return null   // «mañana» o «el jueves» sin hora son demasiado comunes para ofrecer un recordatorio
        }
        val out = (d.clone() as Calendar).apply {
            set(Calendar.HOUR_OF_DAY, tm?.first ?: 9); set(Calendar.MINUTE, tm?.second ?: 0)
            set(Calendar.SECOND, 0); set(Calendar.MILLISECOND, 0)
        }
        return made(out, tm != null)
    }

    private val detectCache = Lru<String, Detected?>(500)
    private val NONE = Detected(0, "", false)

    /** detectDatetime sobre un mensaje (texto o transcripción), con caché, y solo si la fecha todavía no pasó. */
    fun futureDatetime(m: Message, nowSec: Long = System.currentTimeMillis() / 1000): Detected? {
        val t = text(m)
        if (t.isEmpty()) return null
        val key = "${m.id}|${t.hashCode()}"
        val found = detectCache.get(key) ?: (detectDatetime(t, m.timestamp) ?: NONE).also { detectCache.put(key, it) }
        return found.takeIf { it !== NONE && it.ts > nowSec + 30 }
    }

    // ─── Respuestas rápidas ──────────────────────────────────────────────────

    private const val QUICK_RULES =
        "Eres mi asistente de redacción. Te paso los últimos mensajes de una conversación de WhatsApp. Escribe 3 respuestas " +
            "MUY cortas y distintas entre sí (por ejemplo una afirmativa, una negativa o postergando, y una que pregunte algo) al " +
            "último mensaje de la otra persona, tal como las escribiría yo ('Yo').\n" +
            "Reglas:\n" +
            "- Cada una de 1 a 12 palabras, natural, imitando mi forma de escribir en la conversación (idioma, tratamiento " +
            "tú/vos/usted, emojis).\n" +
            "- No inventes datos, horarios, precios ni compromisos que no estén en la conversación.\n" +
            "- Responde ÚNICAMENTE con las 3 respuestas, una por línea, sin numerar, sin comillas y sin explicaciones."

    private const val PROFILE_RULES =
        "Es contexto PRIVADO sobre la otra persona. Úsalo para ajustar el tono y el enfoque, pero NUNCA lo menciones ni " +
            "insinúes que la analicé o que tengo notas. Si contradice lo que dicen los mensajes recientes, mandan los mensajes."

    fun buildQuickPrompt(context: String, profile: String = ""): String {
        val parts = mutableListOf(QUICK_RULES)
        if (profile.isNotBlank()) parts += "=== LO QUE SÉ DE ESTA PERSONA (privado) ===\n$PROFILE_RULES\n\n${profile.trim()}\n=== FIN ==="
        parts += "=== ÚLTIMOS MENSAJES DEL CHAT ===\n${context.trim()}\n=== FIN ==="
        return parts.joinToString("\n\n")
    }

    fun parseQuickReplies(raw: String, limit: Int = 3): List<String> {
        val out = ArrayList<String>()
        for (line in cleanReply(raw).lines()) {
            val s = cleanReply(line.replace(Regex("^\\s*(?:[-*•>]+|\\d+[.)])\\s*"), "")).trim()
            if (s.isEmpty() || s.length > 160 || out.any { it.equals(s, true) }) continue
            out += s
            if (out.size >= limit) break
        }
        return out
    }

    fun cachedQuickReplies(jid: String, lastId: String): List<String>? = quickCache.get(jid to lastId)

    /** Solo se sugiere cuando lo último del chat es un mensaje de la otra persona con texto. */
    fun shouldSuggest(last: Message?): Boolean = last != null && !last.fromMe && text(last).isNotEmpty()

    private fun context(messages: List<Message>, contact: String): String =
        messages.map { Psych.messageLine(it, Psych.whoOf(it, contact)) }.filter { it.isNotEmpty() }.joinToString("\n")

    /** (respuestas, error). Caché por último mensaje salvo [force]. Bloqueante. */
    fun quickReplies(jid: String, messages: List<Message>, contact: String, provider: String, model: String,
                     profile: String = "", force: Boolean = false): Pair<List<String>, String> {
        val last = messages.lastOrNull() ?: return emptyList<String>() to "No hay mensajes."
        val key = jid to last.id
        if (!force) quickCache.get(key)?.let { return it to "" }
        val ctx = context(messages.takeLast(QUICK_CONTEXT), contact)
        if (ctx.isBlank()) return emptyList<String>() to "No hay mensajes de texto."
        val out = AiService.ask(buildQuickPrompt(ctx, profile), provider, model)
        if (isErr(out)) return emptyList<String>() to out.trim().ifEmpty { "Error: respuesta vacía." }
        val replies = parseQuickReplies(out)
        if (replies.isEmpty()) return emptyList<String>() to "La IA no devolvió respuestas."
        quickCache.put(key, replies)
        return replies to ""
    }

    // ─── «Lo que te perdiste» ────────────────────────────────────────────────

    val MISSED_SECTIONS = listOf("RESUMEN", "QUIÉN DIJO QUÉ", "DECISIONES", "PREGUNTAS PARA MÍ")

    private const val MISSED_RULES =
        "Eres mi asistente. Te paso los mensajes de WhatsApp que NO leí de un chat (yo soy 'Yo'; si aparece 'Yo' es porque " +
            "ya respondí algo). Hazme un resumen para ponerme al día rápido.\n" +
            "Reglas:\n" +
            "- Usa EXACTAMENTE estas secciones, cada título en su línea terminado en dos puntos, y omite las que estén vacías:\n" +
            "RESUMEN: (2-4 líneas con lo esencial)\n" +
            "QUIÉN DIJO QUÉ: (viñetas «- Nombre: lo más importante que dijo», una por persona relevante)\n" +
            "DECISIONES: (viñetas con lo que se acordó o resolvió, con fechas/horas/lugares)\n" +
            "PREGUNTAS PARA MÍ: (viñetas con lo que me preguntaron o esperan de mí, indicando quién)\n" +
            "- Conserva datos concretos (nombres, números, fechas, horarios, direcciones). No inventes nada.\n" +
            "- Idioma de la conversación, directo y sin relleno."

    fun buildMissedPrompt(conversation: String, chatName: String, isGroup: Boolean): String =
        "$MISSED_RULES\n\n=== MENSAJES SIN LEER (${if (isGroup) "grupo" else "chat"} «$chatName») ===\n${conversation.trim()}\n=== FIN ==="

    /** Los mensajes como líneas «[fecha] Nombre: texto»; si pasa el tope, conserva los más recientes. */
    fun formatMissedConversation(messages: List<Message>, contact: String, maxChars: Int = MISSED_MAX_CHARS): String {
        val lines = ArrayDeque(messages.map { Psych.messageLine(it, Psych.whoOf(it, contact)).take(600) }.filter { it.isNotEmpty() })
        while (lines.isNotEmpty() && lines.sumOf { it.length + 1 } > maxChars) lines.removeFirst()
        return lines.joinToString("\n")
    }

    /** (título, líneas) según [MISSED_SECTIONS]; si la IA no respetó el formato, todo va como RESUMEN. */
    fun parseMissed(raw: String): List<Pair<String, List<String>>> {
        val t = cleanReply(raw)
        if (t.isEmpty()) return emptyList()
        val labels = MISSED_SECTIONS.associateBy { stripAccents(it).uppercase() }
        val sections = ArrayList<Pair<String, MutableList<String>>>()
        var current: Pair<String, MutableList<String>>? = null
        for (line in t.lines()) {
            val stripped = line.trim().trim('*', '#').trim()
            val m = Regex("^([A-ZÁÉÍÓÚÑa-záéíóúñ ]+?)\\s*:\\s*(.*)$").find(stripped)
            val key = m?.let { stripAccents(it.groupValues[1]).uppercase().trim() }.orEmpty()
            if (m != null && key in labels) {
                current = labels.getValue(key) to mutableListOf<String>().also { l -> if (m.groupValues[2].isNotBlank()) l += m.groupValues[2].trim() }
                sections += current
            } else if (stripped.isNotEmpty()) {
                if (current == null) {
                    current = "RESUMEN" to mutableListOf()
                    sections += current
                }
                current.second += stripped.replace(Regex("^[-*•]\\s*"), "")
            }
        }
        return sections
    }

    fun cachedMissed(jid: String, lastId: String, n: Int): String? = missedCache.get(Triple(jid, lastId, n))

    /** (texto crudo, error). Caché por (chat, último mensaje, cantidad). Bloqueante. */
    fun summarizeMissed(jid: String, chatName: String, isGroup: Boolean, unread: Int, provider: String, model: String,
                        force: Boolean = false): Pair<String, String> {
        val messages = Db.getMessages(jid, unread.coerceIn(1, MISSED_MAX_MESSAGES))
        val last = messages.lastOrNull() ?: return "" to "No hay mensajes para resumir."
        val key = Triple(jid, last.id, unread)
        if (!force) missedCache.get(key)?.let { return it to "" }
        val conv = formatMissedConversation(messages, chatName)
        if (conv.isBlank()) return "" to "No hay mensajes de texto para resumir."
        val out = AiService.ask(buildMissedPrompt(conv, chatName, isGroup), provider, model)
        if (isErr(out) || parseMissed(out).isEmpty()) return "" to out.trim().ifEmpty { "Error: respuesta vacía." }
        missedCache.put(key, out.trim())
        return out.trim() to ""
    }

    // ─── Traducción ──────────────────────────────────────────────────────────

    private const val TRANSLATE_RULES =
        "Traduce el texto al idioma indicado. Reglas:\n" +
            "- Conserva el sentido, el tono (formal/informal), los emojis, los saltos de línea, los nombres propios, números, " +
            "enlaces y menciones tal cual.\n" +
            "- Si el texto ya está en ese idioma, devuélvelo igual.\n" +
            "- Responde ÚNICAMENTE con la traducción: sin comillas, sin notas ni explicaciones."

    fun buildTranslatePrompt(text: String, target: String) =
        "$TRANSLATE_RULES\n\n=== IDIOMA DE DESTINO ===\n$target\n\n=== TEXTO ===\n${text.trim()}\n=== FIN ==="

    /** (traducción, error). Caché por (texto, idioma). Bloqueante. */
    fun translate(text: String, target: String, provider: String, model: String): Pair<String, String> {
        val t = text.trim()
        if (t.isEmpty()) return "" to "No hay texto para traducir."
        val key = t to target.lowercase()
        translateCache.get(key)?.let { return it to "" }
        val out = AiService.ask(buildTranslatePrompt(t, target), provider, model)
        if (isErr(out)) return "" to out.trim().ifEmpty { "Error: respuesta vacía." }
        val res = cleanReply(out)
        if (res.isEmpty()) return "" to "La IA devolvió una respuesta vacía."
        translateCache.put(key, res)
        return res to ""
    }

    // ─── Búsqueda por significado ────────────────────────────────────────────

    private val STOPWORDS = (
        "el la los las un una unos unas de del al y o u e en a ante con para por sobre sin que como cuando donde quien cual " +
            "cuanto es era fue son eran ser estar esta estaba estaban hay habia me te se lo le les nos mi tu su mis tus sus yo vos " +
            "tu mensaje mensajes chat dijo dijeron hablaron hablo hablamos hable busco buscar alguien algo todo toda ya muy mas " +
            "pero si no ni cosa cosas ese esa eso este esto"
        ).split(" ").toSet()

    fun queryKeywords(query: String, limit: Int = 8): List<String> =
        Regex("[\\wñÑ]+").findAll(stripAccents(query).lowercase()).map { it.value }
            .filter { it.length >= 3 && it !in STOPWORDS }.distinct().take(limit).toList()

    /** Raíz tosca para el LIKE: quita letras finales de palabras largas (cumpleaños→cumplean). */
    fun stem(w: String) = when {
        w.length >= 7 -> w.dropLast(2)
        w.length == 6 -> w.dropLast(1)
        else -> w
    }

    fun buildExpandPrompt(query: String) =
        "Voy a buscar mensajes de WhatsApp (incluidas transcripciones de audios) por significado. Dame hasta 12 " +
            "palabras sueltas, en español rioplatense, que probablemente aparezcan en el mensaje que busco: sinónimos, " +
            "formas relacionadas y términos concretos que se usarían de verdad al escribirlo. Responde SOLO con las " +
            "palabras separadas por comas, sin explicaciones.\n\n=== BÚSQUEDA ===\n${query.trim()}\n=== FIN ==="

    fun parseKeywords(raw: String, limit: Int = 14): List<String> =
        cleanReply(raw).split(Regex("[,\\n;]+")).map { stripAccents(it.trim().trim('-', '*', '•', '"', '\'').trim().lowercase()) }
            .filter { it.length in 3..24 && Regex("[\\w ñ]+").matches(it) && it !in STOPWORDS }.distinct().take(limit)

    /** Mensajes (texto o transcripción) con alguna palabra; los que matchean más palabras, primero. Solo SQL + LIKE. */
    fun searchCandidates(terms: List<String>, limit: Int = SEARCH_CANDIDATES, scan: Int = 600): List<Message> {
        val stems = terms.filter { it.isNotEmpty() }.map { stem(it) }.distinct()
        if (stems.isEmpty()) return emptyList()
        return Db.searchMessages(stems.map { it.replace('n', '_') }, scan)   // «_» deja pasar la ñ
            .map { m ->
                val hay = stripAccents("${m.text} ${m.transcript.orEmpty()}").lowercase()
                Triple(stems.count { it in hay }, m.timestamp, m)
            }
            .filter { it.first > 0 }
            .sortedWith(compareByDescending<Triple<Int, Long, Message>> { it.first }.thenByDescending { it.second })
            .take(limit).map { it.third }
    }

    private fun candidateLine(i: Int, m: Message, chatName: (String) -> String, width: Int = 220): String {
        var t = text(m).split(Regex("\\s+")).joinToString(" ")
        if (t.length > width) t = t.take(width) + "…"
        val who = if (m.fromMe) "Yo" else m.senderName?.ifEmpty { null } ?: chatName(m.chatJid)
        val kind = if (m.mediaType == "audio") " (audio transcripto)" else ""
        return "$i. [$who]$kind $t"
    }

    fun buildRerankPrompt(query: String, candidates: List<Message>, chatName: (String) -> String): String {
        val lines = candidates.mapIndexed { i, m -> candidateLine(i + 1, m, chatName) }.joinToString("\n")
        return "Busco un mensaje de WhatsApp por su significado. Te paso la búsqueda y una lista numerada de mensajes " +
            "candidatos. Elige los que responden a lo que busco, del más relevante al menos relevante (máximo " +
            "$SEARCH_RESULTS). Si ninguno sirve, responde NINGUNO.\n" +
            "Responde SOLO con los números separados por comas, sin explicaciones.\n\n" +
            "=== BÚSQUEDA ===\n${query.trim()}\n=== FIN ===\n\n=== CANDIDATOS ===\n$lines\n=== FIN ==="
    }

    /** Índices (base 0) válidos y sin repetir, en el orden que dio la IA. */
    fun parseRerank(raw: String, n: Int): List<Int> =
        Regex("\\d+").findAll(raw).map { it.value.toInt() - 1 }.filter { it in 0 until n }.distinct().take(SEARCH_RESULTS).toList()

    /** (mensajes por relevancia, aviso). Máximo 2 llamadas a la IA (sinónimos + reranking), con caché por búsqueda. */
    fun semanticSearch(query: String, provider: String, model: String): Pair<List<Message>, String> {
        val q = query.trim().split(Regex("\\s+")).joinToString(" ")
        if (q.length < 3) return emptyList<Message>() to "Escribí qué querés encontrar."
        searchCache.get(q.lowercase())?.let { ids ->
            val found = ids.mapNotNull { Db.getMessage(it) }
            if (found.isNotEmpty()) return found to ""
        }
        var terms = queryKeywords(q)
        var note = ""
        val exp = AiService.ask(buildExpandPrompt(q), provider, model)
        if (isErr(exp)) note = "La IA no está disponible: resultados por palabras." else terms = (terms + parseKeywords(exp)).distinct()
        val cands = searchCandidates(terms)
        if (cands.isEmpty()) return emptyList<Message>() to note.ifEmpty { "No encontré mensajes parecidos." }
        var ranked = cands.take(SEARCH_RESULTS)
        val names = HashMap<String, String>()
        val nameOf = { jid: String -> names.getOrPut(jid) { Db.getChat(jid)?.name.orEmpty() } }
        if (note.isEmpty()) {
            val out = AiService.ask(buildRerankPrompt(q, cands, nameOf), provider, model)
            if (isErr(out)) {
                note = "No pude ordenar con IA: resultados por palabras."
            } else if ("ningun" in stripAccents(out).lowercase() && !Regex("\\d").containsMatchIn(out)) {
                return emptyList<Message>() to "Ningún mensaje responde a eso."
            } else {
                val idx = parseRerank(out, cands.size)
                if (idx.isNotEmpty()) ranked = idx.map { cands[it] }
            }
        }
        if (note.isEmpty()) searchCache.put(q.lowercase(), ranked.map { it.id })
        return ranked to note
    }
}
