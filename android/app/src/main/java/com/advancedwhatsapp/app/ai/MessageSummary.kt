package com.advancedwhatsapp.app.ai

import com.advancedwhatsapp.app.data.Message

/** Modo resumen de un mensaje. Traducción de app/ai/message_summary.py. */
object MessageSummary {
    const val CONTEXT_MESSAGES = 8
    const val SUMMARY_PROVIDER = "Claude"
    const val SUMMARY_MODEL = "claude-haiku-5-5"

    private val SECTIONS = listOf("RESUMEN" to "En pocas palabras", "INTERPRETACIÓN" to "Qué quiere decir", "ESPERA" to "Qué espera")

    private const val RULES =
        "Eres mi asistente para entender mensajes de WhatsApp. Te paso UN mensaje y, como contexto, los " +
            "mensajes anteriores de la conversación. Tu trabajo es que yo entienda ese mensaje rápido y bien: " +
            "resumirlo, simplificarlo e interpretar correctamente qué quiere decir.\n" +
            "Reglas:\n" +
            "- Céntrate SOLO en el mensaje indicado; el contexto sirve para interpretarlo (a qué responde, " +
            "ironías, sobreentendidos), no para resumir la conversación.\n" +
            "- Conserva los datos concretos (nombres, números, precios, fechas, horarios, direcciones). No " +
            "inventes nada que no esté en el mensaje o en el contexto; si algo es ambiguo, dilo.\n" +
            "- Lenguaje simple y directo, en el idioma del mensaje. Si el mensaje es corto o ya es claro, " +
            "responde igual de corto: no estires la explicación.\n" +
            "- Responde EXACTAMENTE con estas tres líneas, sin nada antes ni después:\n" +
            "RESUMEN: <lo esencial en una o dos frases cortas>\n" +
            "INTERPRETACIÓN: <qué quiere decir de verdad: intención, tono o emoción y lo que queda implícito>\n" +
            "ESPERA: <qué espera o pide (respuesta, decisión, acción, plazo); si no pide nada, «Nada en particular»>"

    fun messageText(m: Message): String = when (m.mediaType.lowercase()) {
        "revoked" -> ""
        // foto o sticker: pie + lo que leyó la IA (descripción y texto); sin lectura, solo el pie de la foto
        "image", "sticker" -> listOf(if (m.mediaType == "image") m.text.trim() else "", m.imageDesc?.trim().orEmpty(), m.imageOcr?.trim().orEmpty())
            .filter { it.isNotEmpty() }.joinToString(" · ")
        "audio" -> m.transcript?.trim().orEmpty().let { if (it.startsWith("(sin voz")) "" else it }
        else -> m.text.trim()
    }

    fun canSummarize(m: Message) = messageText(m).isNotEmpty()

    fun buildPrompt(text: String, author: String, context: String): String {
        val parts = mutableListOf(RULES)
        if (context.isNotBlank()) parts += "=== MENSAJES ANTERIORES (contexto) ===\n${context.trim()}\n=== FIN ==="
        parts += "=== MENSAJE A RESUMIR (de $author) ===\n${text.trim()}\n=== FIN ==="
        return parts.joinToString("\n\n")
    }

    /** (título, texto) de cada sección; si la IA no respetó el formato, todo va como resumen. */
    fun parse(raw: String): List<Pair<String, String>> {
        var t = raw.trim()
        Regex("^```[\\w-]*\\n([\\s\\S]*?)\\n?```$").find(t)?.let { t = it.groupValues[1].trim() }
        val labels = "RESUMEN|INTERPRETACIÓN|ESPERA|INTERPRETACION"
        val re = Regex(
            "^\\W*($labels)\\W*:\\s*([\\s\\S]*?)(?=^\\W*(?:$labels)\\W*:|\\z)",
            setOf(RegexOption.MULTILINE, RegexOption.IGNORE_CASE),
        )
        val found = HashMap<String, String>()
        for (m in re.findAll(t)) {
            val key = m.groupValues[1].uppercase().replace("INTERPRETACION", "INTERPRETACIÓN")
            val value = m.groupValues[2].trim().trim('*').trim()
            if (value.isNotEmpty()) found[key] = value
        }
        if (found.isEmpty()) return if (t.isNotEmpty()) listOf(SECTIONS[0].second to t) else emptyList()
        return SECTIONS.filter { it.first in found }.map { (k, title) -> title to found.getValue(k) }
    }

    /** Claude Haiku si hay clave de Anthropic; si no, el proveedor y modelo elegidos en el redactor. */
    fun chooseAi(fallbackProvider: String, fallbackModel: String): Pair<String, String> =
        if (AiService.hasKey("Claude")) SUMMARY_PROVIDER to SUMMARY_MODEL else fallbackProvider to fallbackModel

    /** Devuelve el resumen en bruto o un texto de error. Bloqueante. */
    fun summarize(m: Message, previous: List<Message>, contact: String, provider: String, model: String): String {
        val text = messageText(m)
        if (text.isEmpty()) return "Error: este mensaje no tiene texto para resumir."
        val author = if (m.fromMe) "mí (Yo)" else m.senderName?.ifEmpty { null } ?: contact.ifEmpty { "el contacto" }
        val lines = previous.takeLast(CONTEXT_MESSAGES).map { Psych.messageLine(it, Psych.whoOf(it, contact)) }.filter { it.isNotEmpty() }
        val out = AiService.ask(buildPrompt(text, author, lines.joinToString("\n")), provider, model)
        if (AiService.isError(out)) return out
        return if (parse(out).isNotEmpty()) out.trim() else "Error: la IA devolvió una respuesta vacía."
    }
}
