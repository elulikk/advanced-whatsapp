package com.advancedwhatsapp.app.ai

import com.advancedwhatsapp.app.data.Message

/** Redactor: reformula el borrador o sugiere una respuesta. Traducción de app/ai/reply_polisher.py. */
object ReplyPolisher {
    const val CONTEXT_MESSAGES = 10
    const val DEFAULT_MODE = "ordenar"

    val MODES: Map<String, Pair<String, String>> = linkedMapOf(
        "ordenar" to ("Ordenar y explicar mejor" to
            "Ordena y explica mejor lo que quiero decir. Reorganiza las ideas en una secuencia lógica " +
            "(contexto → punto principal → detalle → cierre o siguiente paso), agrupa lo que va junto, " +
            "elimina repeticiones, rodeos y muletillas, y aclara lo confuso. Si es largo, divídelo en " +
            "párrafos cortos o en una lista breve cuando ayude. Puedes cambiar el orden y la estructura " +
            "con total libertad."),
        "suavizar" to ("Suavizar (menos directo / hiriente)" to
            "Reformula con tacto y empatía lo que quiero decir. Quita el tono hiriente, acusatorio, " +
            "agresivo o seco: no culpes ni ataques a la persona, habla de la situación y de lo que " +
            "necesito o propongo. Mantén mi postura y mi mensaje de fondo (no lo vuelvas evasivo ni " +
            "cambies lo que pido o decido); cambia solo la forma de decirlo."),
        "firme" to ("Más directo y firme" to
            "Hazlo más claro, directo y firme: sin rodeos, disculpas innecesarias ni ambigüedad, pero " +
            "respetuoso y sin agresividad. Que quede claro qué quiero, qué decido o qué espero."),
        "breve" to ("Más breve y al grano" to
            "Resúmelo lo más posible sin perder lo esencial: ve directo al punto y deja solo lo que la " +
            "otra persona necesita saber."),
        "calido" to ("Más cálido y cercano" to
            "Hazlo más amable, cercano y humano, como hablaría una persona simpática y atenta, sin " +
            "exagerar ni sonar falso."),
        "formal" to ("Más formal y profesional" to
            "Hazlo más formal, cortés y profesional, con vocabulario cuidado, sin sonar rígido ni frío."),
        "persuasivo" to ("Más persuasivo (ventas)" to
            "Hazlo más convincente y comercial: destaca el beneficio para la otra persona, responde a " +
            "sus dudas y cierra con un siguiente paso claro, sin presionar ni exagerar."),
        "hiriente" to ("Hiriente (lo más duro posible)" to
            "Escríbelo con un tono duro, cortante y deliberadamente hiriente: directo a lo que la otra " +
            "persona dijo o hizo, sin suavizar, sin cortesías ni rodeos, de modo que duela. Apunta a sus " +
            "argumentos, su conducta o su actitud en esta conversación, con frases breves y contundentes. " +
            "Sin amenazas, sin insultos discriminatorios (raza, género, orientación, discapacidad, etc.) y " +
            "sin inventar hechos sobre la persona."),
        "sarcastico" to ("Sarcástico" to
            "Escríbelo con sarcasmo evidente y mordaz: elogia o aprueba con falsa cortesía o exageración " +
            "algo que en realidad criticas, para dejar en ridículo lo que dijo o hizo la otra persona. " +
            "Que sea ingenioso y afilado, no vulgar."),
        "pasivo_agresivo" to ("Pasivo-agresivo" to
            "Escríbelo con tono pasivo-agresivo: en apariencia educado y tranquilo, pero con indirectas, " +
            "reproches velados y frases de doble filo (del estilo «como ya te comenté…», «qué bueno que " +
            "por fin…»). El reproche se siente, pero nunca se dice abiertamente."),
        "frio" to ("Frío y cortante" to
            "Escríbelo frío, seco y mínimo: lo más corto posible, sin calidez, sin emojis, sin " +
            "explicaciones ni justificaciones. Que se note distancia."),
        "humor" to ("Con humor" to
            "Escríbelo con humor ligero e ingenioso, simpático y sin ofender, de los que sacan una sonrisa."),
        "libre" to ("Según mi indicación" to
            "Reformula el borrador siguiendo únicamente la INDICACIÓN PRIVADA de abajo."),
    )
    private val TONE_MODES = setOf("suavizar", "firme", "breve", "calido", "formal", "persuasivo",
        "hiriente", "sarcastico", "pasivo_agresivo", "frio", "humor")

    private const val PRIVATE_RULES =
        "Esto es una indicación PRIVADA para ti: la otra persona NUNCA debe enterarse de que existe.\n" +
            "- Úsala para decidir QUÉ decir (la decisión, la postura, lo que se concede o se niega) y aplícala " +
            "aunque el borrador diga otra cosa.\n" +
            "- NO cites ni parafrasees la indicación ni sus fuentes internas: nada de «mi jefe me dijo», " +
            "«me indicaron», «decidimos internamente», ni nombres de terceros o motivos internos que aparezcan " +
            "ahí. Preséntalo como algo natural, dicho por mí en primera persona.\n" +
            "- Si la decisión necesita un porqué, da una justificación creíble, amable y coherente con la " +
            "conversación (una excusa o explicación genérica, p. ej. que ya lo revisé y está bien así, por la " +
            "buena relación, que no es necesario seguir con el tema), sin inventar datos concretos verificables " +
            "(fechas, montos, comprobantes, personas).\n" +
            "- Solo lo que la indicación pida explícitamente comunicar («menciona…», «decile que…») se dice, y " +
            "aun así con mis palabras."

    private const val RULES =
        "Eres mi asistente de redacción. Te paso el borrador de un mensaje que quiero enviar por WhatsApp " +
            "y los últimos mensajes de la conversación como contexto. Tu trabajo NO es corregir ortografía: " +
            "es REFORMULAR mi mensaje según el OBJETIVO. Tienes libertad para cambiar frases, orden, " +
            "estructura y tono; la ortografía y puntuación correctas se dan por hechas.\n" +
            "Reglas:\n" +
            "- Conserva mi intención, mi postura y los datos concretos (nombres, números, precios, fechas, " +
            "horarios, direcciones). No inventes hechos, promesas ni compromisos que no estén en el borrador " +
            "o en la indicación privada.\n" +
            "- Mantén el idioma y el tratamiento (tú/vos/usted) de la conversación; si no hay contexto, el " +
            "del borrador. Escribe como una persona real en WhatsApp: natural, sin sonar a robot ni a carta.\n" +
            "- Usa el contexto para que el mensaje encaje con lo que se está hablando, no para repetirlo.\n" +
            "- Responde ÚNICAMENTE con el texto final del mensaje, listo para enviar: sin comillas, sin " +
            "explicaciones, sin encabezados ni alternativas."

    private const val PROFILE_RULES =
        "Es contexto PRIVADO sobre la otra persona. Úsalo para ajustar el tono y el enfoque (qué le cae bien o " +
            "mal, cómo conviene tratarla), pero NUNCA lo menciones ni insinúes que la analicé o que tengo notas. " +
            "Si contradice lo que dicen los mensajes recientes, mandan los mensajes."

    private const val SUGGEST_RULES =
        "Eres mi asistente de redacción. Te paso los últimos mensajes de una conversación de WhatsApp y " +
            "todavía NO tengo escrita ninguna respuesta. Escribe la respuesta que yo ('Yo') enviaría a " +
            "continuación, para que yo solo la revise y la envíe.\n" +
            "Reglas:\n" +
            "- Responde a lo último que dijo la otra persona (y a lo que haya quedado pendiente). Si el último " +
            "mensaje es mío, escribe un seguimiento natural, no repitas lo ya dicho.\n" +
            "- No inventes datos, precios, fechas, promesas ni compromisos que no estén en la conversación, en " +
            "mis notas o en mi indicación. Si falta un dato para responder, haz una pregunta corta en vez de " +
            "inventarlo.\n" +
            "- Imita mi forma de escribir en la conversación (idioma, tratamiento tú/vos/usted, largo, emojis) " +
            "y escribe como una persona real en WhatsApp: natural, breve, sin sonar a robot.\n" +
            "- Responde ÚNICAMENTE con el texto del mensaje, listo para enviar: sin comillas, sin " +
            "explicaciones, sin encabezados ni alternativas."

    private fun profileBlock(profile: String) = "=== LO QUE SÉ DE ESTA PERSONA (privado) ===\n$PROFILE_RULES\n\n${profile.trim()}\n=== FIN ==="

    fun formatContext(messages: List<Message>, contact: String): String =
        messages.map { Psych.messageLine(it, Psych.whoOf(it, contact)) }.filter { it.isNotEmpty() }.joinToString("\n")

    fun buildPolishPrompt(draft: String, context: String, mode: String, extra: String, profile: String): String {
        val instruction = (MODES[mode] ?: MODES.getValue(DEFAULT_MODE)).second
        val parts = mutableListOf(RULES, "=== OBJETIVO ===\n$instruction")
        if (extra.isNotBlank()) {
            parts += "=== INDICACIÓN PRIVADA (tiene prioridad; no revelar) ===\n$PRIVATE_RULES\n\nIndicación:\n${extra.trim()}\n=== FIN ==="
        }
        if (profile.isNotBlank()) parts += profileBlock(profile)
        if (context.isNotBlank()) parts += "=== ÚLTIMOS MENSAJES DEL CHAT (contexto) ===\n${context.trim()}\n=== FIN ==="
        parts += "=== MI BORRADOR ===\n${draft.trim()}\n=== FIN ==="
        return parts.joinToString("\n\n")
    }

    fun buildSuggestPrompt(context: String, mode: String, extra: String, profile: String): String {
        val parts = mutableListOf(SUGGEST_RULES)
        if (mode in TONE_MODES) parts += "=== TONO DESEADO ===\n${MODES.getValue(mode).second}"
        if (extra.isNotBlank()) {
            parts += "=== INDICACIÓN PRIVADA (qué quiero transmitir; tiene prioridad; no revelar) ===\n$PRIVATE_RULES\n\nIndicación:\n${extra.trim()}\n=== FIN ==="
        }
        if (profile.isNotBlank()) parts += profileBlock(profile)
        parts += "=== ÚLTIMOS MENSAJES DEL CHAT ===\n${context.trim()}\n=== FIN ==="
        return parts.joinToString("\n\n")
    }

    /** Quita vallas de código y comillas que a veces agrega el modelo. */
    fun cleanReply(text: String): String {
        var t = text.trim()
        Regex("^```[\\w-]*\\n([\\s\\S]*?)\\n?```$").find(t)?.let { t = it.groupValues[1].trim() }
        if (t.length >= 2 && t.first() in "\"“«" && t.last() in "\"”»") t = t.substring(1, t.length - 1).trim()
        return t
    }
}
