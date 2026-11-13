package com.advancedwhatsapp.app

import com.advancedwhatsapp.app.ai.SmartFeatures
import com.advancedwhatsapp.app.data.Message
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.util.Calendar

class SmartFeaturesTest {
    private fun msg(id: String, text: String, fromMe: Boolean = false, type: String = "text", transcript: String? = null) =
        Message(id, "111@s.whatsapp.net", null, "Ana", fromMe, text, type, null, null, null, null, 1_700_000_000L, "sent", transcript, null, false)

    /** Lunes 2 de marzo de 2026, 10:00 (hora local), en segundos. */
    private val ref = Calendar.getInstance().apply { clear(); set(2026, Calendar.MARCH, 2, 10, 0, 0) }.timeInMillis / 1000

    private fun at(ts: Long) = Calendar.getInstance().apply { timeInMillis = ts * 1000 }

    @Test
    fun detectsWeekdayWithTime() {
        val d = SmartFeatures.detectDatetime("nos vemos el jueves a las 8", ref)!!
        val c = at(d.ts)
        assertEquals(5, c.get(Calendar.DAY_OF_MONTH))
        assertEquals(8, c.get(Calendar.HOUR_OF_DAY))
        assertTrue(d.hasTime)
    }

    @Test
    fun detectsPartOfDayQualifiers() {
        assertEquals(17, at(SmartFeatures.detectDatetime("mañana a las 5", ref)!!.ts).get(Calendar.HOUR_OF_DAY))
        assertEquals(21, at(SmartFeatures.detectDatetime("mañana a las 9 de la noche", ref)!!.ts).get(Calendar.HOUR_OF_DAY))
        val c = at(SmartFeatures.detectDatetime("mañana a las 10 de la mañana", ref)!!.ts)
        assertEquals(3, c.get(Calendar.DAY_OF_MONTH))
        assertEquals(10, c.get(Calendar.HOUR_OF_DAY))
    }

    @Test
    fun detectsExplicitDatesAndRelativeTimes() {
        val d = SmartFeatures.detectDatetime("la reunión es el 15 de abril", ref)!!
        assertFalse(d.hasTime)
        assertEquals(Calendar.APRIL, at(d.ts).get(Calendar.MONTH))
        assertEquals(ref + 2 * 3600, SmartFeatures.detectDatetime("llego en 2 horas", ref)!!.ts)
    }

    @Test
    fun ignoresVagueText() {
        for (t in listOf("buen día, hasta mañana", "nos vemos el jueves", "hola cómo estás", "son 3.50 por kilo", "ok", "")) {
            assertNull(t, SmartFeatures.detectDatetime(t, ref))
        }
    }

    @Test
    fun needsReplyOnlyWhenTheQuestionIsUnanswered() {
        assertTrue(SmartFeatures.needsReply(listOf(msg("1", "hola"), msg("2", "¿podés mañana?"))))
        assertFalse(SmartFeatures.needsReply(listOf(msg("1", "¿podés mañana?"), msg("2", "sí", fromMe = true))))
        assertTrue(SmartFeatures.needsReply(listOf(msg("1", "¿podés mañana?"), msg("2", "avisame"))))
        assertFalse(SmartFeatures.needsReply(listOf(msg("1", "dale"), msg("2", "gracias"))))
        assertFalse(SmartFeatures.needsReply(emptyList()))
        assertTrue(SmartFeatures.needsReply(listOf(msg("1", "", type = "audio", transcript = "che, ¿cuándo llegás?"))))
    }

    @Test
    fun parsesQuickReplies() {
        assertEquals(listOf("Dale, voy", "No puedo mañana", "¿A qué hora?"), SmartFeatures.parseQuickReplies("1. Dale, voy\n- \"No puedo mañana\"\n• ¿A qué hora?\nDale, voy\nextra"))
        assertTrue(SmartFeatures.parseQuickReplies("").isEmpty())
        assertTrue(SmartFeatures.shouldSuggest(msg("1", "hola")))
        assertFalse(SmartFeatures.shouldSuggest(msg("1", "hola", fromMe = true)))
    }

    @Test
    fun parsesMissedSections() {
        val sections = SmartFeatures.parseMissed("RESUMEN: Organizaron la cena.\nDECISIONES:\n- Sábado 21:00\nPREGUNTAS PARA MI:\n- Ana: ¿llevás vino?")
        assertEquals(listOf("RESUMEN", "DECISIONES", "PREGUNTAS PARA MÍ"), sections.map { it.first })
        assertEquals(listOf("Sábado 21:00"), sections[1].second)
        assertEquals("RESUMEN", SmartFeatures.parseMissed("Todo tranquilo").first().first)
        assertNotNull(SmartFeatures.formatMissedConversation(listOf(msg("1", "x".repeat(50))), "Ana", 500))
    }

    @Test
    fun searchHelpers() {
        assertEquals(listOf("cumpleanos", "marta"), SmartFeatures.queryKeywords("¿Cuándo era el cumpleaños de Marta?"))
        assertEquals(listOf("cumpleanos", "fiesta", "torta", "festejo"), SmartFeatures.parseKeywords("cumpleaños, fiesta; torta\n- festejo"))
        assertEquals(listOf(2, 0, 1), SmartFeatures.parseRerank("3, 1, 3, 99, 2", 5))
    }

    @Test
    fun lruEvictsOldest() {
        val c = SmartFeatures.Lru<String, Int>(2)
        c.put("a", 1); c.put("b", 2); c.get("a"); c.put("c", 3)
        assertEquals(1, c.get("a")); assertEquals(3, c.get("c")); assertNull(c.get("b"))
    }
}
