package com.advancedwhatsapp.app

import com.advancedwhatsapp.app.ai.MessageSummary
import com.advancedwhatsapp.app.ai.Psych
import com.advancedwhatsapp.app.ai.ReplyPolisher
import com.advancedwhatsapp.app.data.Message
import com.advancedwhatsapp.app.ui.VoiceRecorder
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class AiLogicTest {
    private fun msg(text: String, fromMe: Boolean = false, type: String = "text", transcript: String? = null) = Message(
        id = "x", chatJid = "1@s.whatsapp.net", senderJid = null, senderName = "Ana", fromMe = fromMe, text = text,
        mediaType = type, mediaPath = null, mediaRaw = null, quotedText = null, quotedSender = null,
        timestamp = 1_700_000_000, status = "sent", transcript = transcript, summary = null, edited = false,
    )

    @Test
    fun summaryParsesTheThreeSections() {
        val out = MessageSummary.parse("RESUMEN: Viene el viernes\nINTERPRETACION: Está contenta\n**ESPERA:** Que confirmes")
        assertEquals(listOf("En pocas palabras", "Qué quiere decir", "Qué espera"), out.map { it.first })
        assertEquals("Está contenta", out[1].second)
        assertEquals("Que confirmes", out[2].second)
    }

    @Test
    fun summaryWithoutFormatGoesAsSummary() {
        assertEquals(listOf("En pocas palabras" to "Texto libre"), MessageSummary.parse("```\nTexto libre\n```"))
        assertTrue(MessageSummary.parse("  ").isEmpty())
    }

    @Test
    fun onlyMessagesWithTextCanBeSummarized() {
        assertTrue(MessageSummary.canSummarize(msg("hola")))
        assertFalse(MessageSummary.canSummarize(msg("", type = "revoked")))
        assertFalse(MessageSummary.canSummarize(msg("", type = "audio")))
        assertTrue(MessageSummary.canSummarize(msg("", type = "audio", transcript = "dijo algo")))
        assertFalse(MessageSummary.canSummarize(msg("", type = "audio", transcript = "(sin voz reconocible)")))
    }

    @Test
    fun cleanReplyRemovesFencesAndQuotes() {
        assertEquals("hola", ReplyPolisher.cleanReply("```text\nhola\n```"))
        assertEquals("hola", ReplyPolisher.cleanReply("«hola»"))
        assertEquals("dijo \"hola\" ayer", ReplyPolisher.cleanReply("dijo \"hola\" ayer"))
    }

    @Test
    fun polishPromptIncludesOnlyWhatWasGiven() {
        val bare = ReplyPolisher.buildPolishPrompt("mi borrador", "", "breve", "", "")
        assertTrue("mi borrador" in bare)
        assertFalse("INDICACIÓN PRIVADA" in bare)
        assertFalse("LO QUE SÉ" in bare)
        val full = ReplyPolisher.buildPolishPrompt("b", "Yo: x", "no-existe", "decile que no", "perfil")
        assertTrue(ReplyPolisher.MODES.getValue(ReplyPolisher.DEFAULT_MODE).second in full)
        assertTrue("decile que no" in full && "perfil" in full && "Yo: x" in full)
    }

    @Test
    fun suggestOnlyUsesToneModes() {
        assertFalse("TONO DESEADO" in ReplyPolisher.buildSuggestPrompt("c", "ordenar", "", ""))
        assertTrue("TONO DESEADO" in ReplyPolisher.buildSuggestPrompt("c", "breve", "", ""))
    }

    @Test
    fun analysisDepthFollowsHowMuchTheContactWrote() {
        val little = "[01/01/24 10:00] Ana: gracias\n[01/01/24 10:01] Yo: de nada con mucho gusto para vos siempre"
        assertEquals(1 to 1, Psych.subjectStats(little, "Ana"))
        val prompt = Psych.buildPrompt(little, "normal", "full", "Ana", 2)
        assertTrue("escribió muy poco" in prompt)
        assertFalse("Perfil de personalidad" in prompt)

        val rich = "Ana: " + List(150) { "palabra" }.joinToString(" ")
        assertTrue("Perfil de personalidad" in Psych.buildPrompt(rich, "normal", "full", "Ana", 1))
    }

    @Test
    fun analysisSearchesReplaceTheGeneralReading() {
        val p = Psych.buildPrompt("Ana: hola", "normal", "full", "Ana", 1, searches = listOf("¿Me miente?"))
        assertTrue("1. ¿Me miente?" in p)
        assertFalse("Perfil de personalidad" in p)
    }

    @Test
    fun relationHintGoesInThePrompt() {
        assertTrue("Es un AMIGO" in Psych.buildPrompt("Ana: hola", "normal", "short", "Ana", 1, relation = "amigo"))
        assertTrue("Vecina" in Psych.buildPrompt("Ana: hola", "normal", "short", "Ana", 1, relation = "Vecina"))
        assertFalse("tipo de relación" in Psych.buildPrompt("Ana: hola", "normal", "short", "Ana", 1, relation = "General"))
    }

    @Test
    fun selfMaterialRepresentsEveryChatWithinBudget() {
        val a = List(50) { msg("mensaje largo de ana número $it", fromMe = it % 2 == 0) }
        val b = listOf(msg("hola de beto", fromMe = true))
        val s = Psych.formatSelfMaterial(listOf("Ana" to a, "Beto" to b), 600)
        assertEquals(2, s.chats)
        assertTrue("Chat con Beto" in s.text && "hola de beto" in s.text)
        assertTrue(s.text.length <= 600)
        assertTrue("número 49" in s.text)   // se priorizan los más recientes
    }

    @Test
    fun waveformHas64BarsWithinRange() {
        val w = VoiceRecorder.buildWaveform(List(30) { it * 100 })
        assertEquals(64, w.size)
        assertTrue(w.all { it in 0..100 })
        assertEquals(100, w.last())
        assertEquals(List(64) { 0 }, VoiceRecorder.buildWaveform(emptyList()))
        assertEquals(64, VoiceRecorder.buildWaveform(List(1000) { 5 }).size)
    }
}
