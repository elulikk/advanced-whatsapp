package com.advancedwhatsapp.app.ai

import com.advancedwhatsapp.app.data.Message
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/**
 * Análisis psicológico: prompts y formato del material. Traducción de app/ai/psych_analysis.py: los textos para la IA
 * son los mismos que en la PC. Sin dependencias de Android, para poder probarlo en la JVM.
 */
object Psych {
    const val DEFAULT_KIND = "General"
    val KIND_HINTS = linkedMapOf(
        "General" to "",
        "Amigo" to "Es un AMIGO: mira la reciprocidad, la confianza, el apoyo mutuo y si la amistad se siente pareja o desbalanceada.",
        "Familia" to "Es un FAMILIAR: mira la dinámica familiar, los roles, los reproches o cargas históricas y cómo se cuidan o se presionan.",
        "Pareja" to "Es mi PAREJA (o alguien con quien hay un vínculo afectivo): mira el afecto, el compromiso, los conflictos y la comunicación emocional.",
        "Trabajo" to "Es alguien del TRABAJO: mira el trato profesional, la jerarquía, los acuerdos, los límites y si hay presión o ambigüedad.",
    )
    val DEFAULT_SEARCHES = listOf(
        "¿Me quiere estafar?", "¿Me está poniendo a prueba?", "¿Está enojado/a conmigo?", "¿Me está mintiendo?",
        "¿Me está manipulando?", "¿Está interesado/a en mí?", "¿Qué quiere realmente de mí?", "¿Cómo está de ánimo?",
    )
    val LENGTHS = linkedMapOf("short" to "Corto", "full" to "Detallado")
    val MESSAGE_COUNTS = listOf(12, 30, 50, 100, 200)
    const val DEFAULT_MESSAGES = 50
    const val SELF_JID = "_yo"
    val CHAR_BUDGET = mapOf("Gemini" to 150_000, "OpenAI" to 80_000, "Claude" to 150_000, "Groq" to 18_000)
    private const val NOTES_IN_PROMPT = 4000
    private const val MINIMAL_WORDS = 25
    private const val MODERATE_WORDS = 120

    private val MEDIA_LABELS = mapOf(
        "image" to "[imagen]", "video" to "[video]", "audio" to "[nota de voz]", "ptt" to "[nota de voz]",
        "document" to "[documento]", "sticker" to "[sticker]", "location" to "[ubicación]",
    )
    private const val DISCLAIMER = "Sé concreto y honesto: si hay poca información, indícalo y no inventes. " +
        "Es una lectura orientativa, no un diagnóstico clínico."

    fun messageLine(m: Message, who: String): String {
        var text = m.text.trim()
        val transcript = m.transcript?.trim().orEmpty()
        val media = MEDIA_LABELS[m.mediaType.lowercase()].orEmpty()
        if (transcript.isNotEmpty()) text = "$media (transcripción) $transcript".trim()
        else if (media.isNotEmpty()) text = "$media $text".trim()
        if (text.isEmpty()) return ""
        var ts = m.timestamp
        if (ts <= 0) return "$who: $text"
        if (ts > 1_000_000_000_000L) ts /= 1000
        val stamp = SimpleDateFormat("dd/MM/yy HH:mm", Locale.US).format(Date(ts * 1000))
        return "[$stamp] $who: $text"
    }

    fun whoOf(m: Message, contact: String) = if (m.fromMe) "Yo" else m.senderName?.ifEmpty { null } ?: contact.ifEmpty { "Contacto" }

    /** Transcript cronológico de un chat, con «Yo» y el nombre del contacto. */
    fun formatConversation(messages: List<Message>, name: String): String =
        messages.map { messageLine(it, if (it.fromMe) "Yo" else name) }.filter { it.isNotEmpty() }.joinToString("\n")

    data class SelfMaterial(val text: String, val mine: Int, val chats: Int)

    /** Conversaciones por chat, tomando por turnos los mensajes más recientes para que todos entren en el presupuesto. */
    fun formatSelfMaterial(chats: List<Pair<String, List<Message>>>, budget: Int): SelfMaterial {
        val perChat = chats.map { (name, msgs) ->
            msgs.mapNotNull { m -> messageLine(m, if (m.fromMe) "Yo" else name).ifEmpty { null }?.let { it to m.fromMe } }
        }
        val headers = chats.map { "### Chat con ${it.first}" }
        val taken = IntArray(chats.size)
        var used = 0
        var full = false
        while (!full) {
            var progress = false
            for (i in perChat.indices) {
                val lines = perChat[i]
                if (taken[i] >= lines.size) continue
                val cost = lines[lines.size - 1 - taken[i]].first.length + 1 + (if (taken[i] == 0) headers[i].length + 2 else 0)
                if (used + cost > budget) {
                    full = true
                    break
                }
                used += cost
                taken[i]++
                progress = true
            }
            if (!progress) break
        }
        val kept = perChat.indices.filter { taken[it] > 0 }.map { it to perChat[it].takeLast(taken[it]) }
        val blocks = kept.map { (i, lines) -> headers[i] + "\n" + lines.joinToString("\n") { it.first } }
        return SelfMaterial(blocks.joinToString("\n\n"), kept.sumOf { (_, l) -> l.count { it.second } }, blocks.size)
    }

    private fun format(focus: String, length: String): String = when (focus to length) {
        "normal" to "short" -> "Responde en español, breve (máximo 150 palabras), en viñetas cortas: personalidad y estilo de " +
            "comunicación, estado emocional, puntos fuertes, puntos débiles, y cómo conviene tratarla."
        "normal" to "full" -> "Responde en español, con texto claro (sin tablas) y estas secciones:\n" +
            "1. Perfil de personalidad (rasgos predominantes, estilo de comunicación)\n" +
            "2. Estado emocional y lo que parece estar viviendo\n" +
            "3. Puntos fuertes\n" +
            "4. Puntos débiles, inseguridades o temas sensibles\n" +
            "5. Necesidades, motivaciones y valores que se perciben\n" +
            "6. Cómo se relaciona conmigo (dinámica de la conversación)\n" +
            "7. Recomendaciones para comunicarme mejor con esta persona\n" +
            "8. Notas clave para recordar (viñetas cortas)"
        "interno" to "short" -> "Responde en español, hablándome de tú, breve (máximo 200 palabras), en viñetas: rasgos de " +
            "personalidad, estilo de comunicación, puntos fuertes, puntos débiles y patrones a trabajar."
        else -> "Responde en español, hablándome de tú, con texto claro (sin tablas) y estas secciones:\n" +
            "1. Perfil de personalidad (rasgos predominantes; si se puede, una aproximación a los Big Five)\n" +
            "2. Estilo de comunicación (tono, y cómo cambia según con quién hablo)\n" +
            "3. Estado emocional y temas recurrentes\n" +
            "4. Puntos fuertes\n" +
            "5. Puntos débiles y patrones que me juegan en contra\n" +
            "6. Cómo me relaciono con los demás (afecto, límites, conflicto, trabajo)\n" +
            "7. Recomendaciones concretas de crecimiento personal\n" +
            "8. Notas clave para recordar (viñetas cortas)"
    }

    private fun relationLine(relation: String): String {
        val r = relation.trim()
        if (r.isEmpty() || r.equals(DEFAULT_KIND, true)) return ""
        val hint = KIND_HINTS.entries.firstOrNull { it.key.equals(r, true) }?.value.orEmpty()
        return " " + hint.ifEmpty { "El tipo de relación es: $r. Tenlo en cuenta al interpretar el trato y los silencios." }
    }

    private fun intro(focus: String, name: String, nMessages: Int, nChats: Int, relation: String): String =
        if (focus == "interno") {
            "Eres un psicólogo experto en análisis de personalidad y comunicación. A continuación tienes " +
                "mis conversaciones de WhatsApp con $nChats personas distintas, agrupadas por chat " +
                "($nMessages mensajes míos). «Yo» soy yo; el resto son las personas con las que hablo. " +
                "Haz un análisis psicológico de MÍ. Los mensajes de los demás son solo contexto para " +
                "interpretar los míos (a qué respondo, cómo reacciono, cómo me tratan y cómo cambio según la " +
                "persona): no analices a los demás."
        } else {
            "Eres un psicólogo experto en análisis de personalidad y comunicación interpersonal. " +
                "A continuación tienes los últimos mensajes de una conversación de WhatsApp entre yo y $name. " +
                "Analiza SOLO a $name, basándote en lo que escribió/dijo (mis mensajes son contexto)." + relationLine(relation)
        }

    private val STAMP = Regex("^\\[[^\\]]*] ")
    private val MEDIA_TAG = Regex("\\[[^\\]]*]|\\(transcripción\\)")

    /** (mensajes, palabras) que escribió el contacto dentro del material. */
    fun subjectStats(material: String, name: String): Pair<Int, Int> {
        var msgs = 0
        var words = 0
        var current: String? = null
        for (line in material.lines()) {
            val body = STAMP.replaceFirst(line, "")
            val text = when {
                body.startsWith("$name: ") -> {
                    current = "other"
                    msgs++
                    body.substring(name.length + 2)
                }
                body.startsWith("Yo: ") -> {
                    current = "me"
                    ""
                }
                else -> if (current == "other") line else ""
            }
            words += MEDIA_TAG.replace(text, " ").trim().split(Regex("\\s+")).count { it.isNotEmpty() }
        }
        return msgs to words
    }

    private fun depthRule(msgs: Int, words: Int): Pair<String, String> {
        val amount = "$msgs mensaje${if (msgs != 1) "s" else ""}, ~$words palabras"
        return when {
            words < MINIMAL_WORDS -> "minimal" to (
                "IMPORTANTE: esta persona escribió muy poco ($amount). Responde en español en 1 a 3 líneas: qué " +
                    "dijo o pidió y, solo si es evidente, qué quiere. NO deduzcas ni infieras nada más " +
                    "(personalidad, emociones, intenciones, motivaciones): si solo saludó, " +
                    "agradeció o confirmó, dilo así y termina. No uses las notas previas para rellenar."
                )
            words < MODERATE_WORDS -> "moderate" to (
                "La conversación es corta (esta persona: $amount). El análisis debe ser proporcional: breve, " +
                    "incluye solo los puntos que se apoyan en algo que escribió y omite el resto. No infieras " +
                    "rasgos ni intenciones sin evidencia directa."
                )
            else -> "rich" to (
                "Cada conclusión debe apoyarse en algo concreto que escribió (cítalo brevemente cuando ayude). " +
                    "La profundidad debe corresponder a lo que hay en la conversación: no rellenes."
                )
        }
    }

    private fun searchBlock(searches: List<String>, name: String, words: Int): String {
        val numbered = searches.withIndex().joinToString("\n") { (i, q) -> "${i + 1}. $q" }
        val parts = mutableListOf(
            "Responde SOLO estas preguntas sobre $name, una por una y en español:\n$numbered",
            "Para cada una indica: Veredicto (Sí / Probable / Poco probable / No / Sin evidencia suficiente), " +
                "Confianza (baja / media / alta) y la evidencia concreta, citando brevemente lo que escribió. " +
                "No supongas mala intención por defecto ni inventes: si los mensajes no alcanzan, responde " +
                "«Sin evidencia suficiente». Cierra con una recomendación práctica de 1 o 2 líneas.",
        )
        if (words < MINIMAL_WORDS) parts += "$name escribió muy poco: es esperable que casi todo sea «Sin evidencia suficiente»."
        return parts.joinToString("\n\n")
    }

    fun buildPrompt(
        material: String, focus: String, length: String, name: String, nMessages: Int, nChats: Int = 1,
        notes: String = "", relation: String = "", searches: List<String> = emptyList(),
    ): String {
        val parts = mutableListOf(intro(focus, name, nMessages, nChats, relation))
        val qs = searches.map { it.trim() }.filter { it.isNotEmpty() }
        if (focus == "interno") {
            parts += listOf(format(focus, length), DISCLAIMER)
        } else if (qs.isNotEmpty()) {
            parts += listOf(searchBlock(qs, name, subjectStats(material, name).second), DISCLAIMER)
        } else {
            val (msgs, words) = subjectStats(material, name)
            val (level, rule) = depthRule(msgs, words)
            if (level == "minimal") parts += rule
            else parts += listOf(format(focus, if (level == "moderate") "short" else length), rule, DISCLAIMER)
        }
        val n = notes.trim()
        if (n.isNotEmpty()) {
            parts += "=== MIS NOTAS (las escribí yo; son mi contexto, no conclusiones de un análisis anterior) ===\n" +
                "${n.takeLast(NOTES_IN_PROMPT)}\n=== FIN NOTAS ==="
        }
        parts += "Haz la lectura desde cero, solo con la evidencia de los mensajes: no hay análisis previos."
        val label = if (focus == "interno") "MIS CONVERSACIONES" else "CONVERSACIÓN (últimos $nMessages mensajes)"
        parts += "=== $label ===\n$material\n=== FIN ==="
        return parts.joinToString("\n\n")
    }

    private fun qaText(qa: List<Pair<String, String>>) = qa.joinToString("\n\n") { (q, a) -> "Pregunta: $q\nRespuesta: $a" }

    fun buildFollowupPrompt(material: String, focus: String, name: String, analysis: String,
                            qa: List<Pair<String, String>>, question: String): String {
        val subject = if (focus == "interno") "mí (el autor de los mensajes; háblame de tú)" else name
        val parts = mutableListOf(
            "Eres el psicólogo que hizo el análisis de abajo sobre $subject. Responde en español la nueva " +
                "pregunta basándote en el material y el análisis. Si el material no alcanza para responder, " +
                "dilo en lugar de inventar.",
            "=== MATERIAL (los mensajes de otras personas son solo contexto) ===\n$material\n=== FIN MATERIAL ===",
            "=== ANÁLISIS ===\n$analysis\n=== FIN ANÁLISIS ===",
        )
        if (qa.isNotEmpty()) parts += "=== PREGUNTAS ANTERIORES ===\n${qaText(qa)}\n=== FIN ==="
        parts += "=== NUEVA PREGUNTA ===\n$question"
        return parts.joinToString("\n\n")
    }

    fun buildExport(focus: String, name: String, analysis: String, qa: List<Pair<String, String>>, material: String): String {
        val subject = if (focus == "interno") "de mí mismo, hecho a partir de mis conversaciones de WhatsApp en todos mis chats"
        else "de una persona con la que hablo ($name), hecho a partir de nuestra conversación de WhatsApp"
        val parts = mutableListOf(
            "Te comparto un análisis psicológico $subject, generado por una IA. Quiero seguir " +
                "profundizándolo contigo: tómalo como punto de partida, hazme preguntas si te falta contexto y " +
                "señala lo que te parezca dudoso. Es una lectura orientativa, no un diagnóstico clínico.",
            "=== ANÁLISIS ===\n${analysis.trim()}",
        )
        if (qa.isNotEmpty()) parts += "=== PREGUNTAS Y RESPUESTAS ===\n${qaText(qa)}"
        if (material.isNotBlank()) parts += "=== MENSAJES ANALIZADOS ===\n${material.trim()}"
        return parts.joinToString("\n\n")
    }
}
