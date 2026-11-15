package com.advancedwhatsapp.app

import com.advancedwhatsapp.app.data.ChatExport
import com.advancedwhatsapp.app.data.ChatExport.Options
import com.advancedwhatsapp.app.data.ChatExport.Original
import com.advancedwhatsapp.app.data.Message
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import java.util.TimeZone

/** Armado puro de «Exportar chat»: rangos, cuerpo del mensaje, líneas de TXT y HTML. */
class ChatExportTest {
    @Before
    fun utc() = TimeZone.setDefault(TimeZone.getTimeZone("UTC"))

    private fun msg(
        text: String = "hola", type: String = "text", fromMe: Boolean = false, ts: Long = 1_700_000_000L,
        transcript: String? = null, summary: String? = null, edited: Boolean = false, quoted: String? = null,
    ) = Message(
        id = "m1", chatJid = "1@s.whatsapp.net", senderJid = "1@s.whatsapp.net", senderName = "Ana", fromMe = fromMe, text = text,
        mediaType = type, mediaPath = null, mediaRaw = null, quotedText = quoted, quotedSender = if (quoted != null) "Beto" else null,
        timestamp = ts, status = "sent", transcript = transcript, summary = summary, edited = edited,
    )

    @Test
    fun rangeFor() {
        assertEquals(null to null, ChatExport.rangeFor("all", 1000))
        assertEquals((10_000_000L - 30 * 86_400L) to null, ChatExport.rangeFor("month", 10_000_000))
        assertEquals(100L to (200L + 86_400 - 1), ChatExport.rangeFor("custom", 0, 100, 200))
    }

    @Test
    fun txtUsaElFormatoDeWhatsapp() {
        val m = msg("Hola\nsegunda línea")
        val lines = ChatExport.txtLines(m, ChatExport.bodyOf(m, Options()), "Ana")
        assertEquals(listOf("14/11/23 22:13 - Ana: Hola", "segunda línea"), lines)
    }

    @Test
    fun txtMultimediaOmitidaOAdjunta() {
        val m = msg("mirá", "image")
        assertEquals("14/11/23 22:13 - Ana: <Multimedia omitida>", ChatExport.txtLines(m, ChatExport.bodyOf(m, Options()), "Ana")[0])
        val withFile = ChatExport.txtLines(m, ChatExport.bodyOf(m, Options(), file = "/x/foto.jpg"), "Ana")
        assertEquals("14/11/23 22:13 - Ana: foto.jpg (archivo adjunto)", withFile[0])
        assertEquals("mirá", withFile[1])
    }

    @Test
    fun transcripcionesYResumenesSonOpcionales() {
        val m = msg("", "audio", transcript = "dicho", summary = "resumen")
        val b1 = ChatExport.bodyOf(m, Options())
        assertTrue(b1.notes.any { it.contains("Transcripción: dicho") } && b1.notes.none { it.contains("Resumen") })
        val b2 = ChatExport.bodyOf(m, Options(transcripts = false, summaries = true))
        assertTrue(b2.notes.none { it.contains("Transcripción") } && b2.notes.any { it.contains("Resumen IA: resumen") })
    }

    @Test
    fun eliminadosYEdiciones() {
        val revoked = msg("", "revoked")
        val orig = Original("text", "secreto", null)
        val kept = ChatExport.bodyOf(revoked, Options(), orig)
        assertEquals("secreto", kept.text)
        assertTrue(kept.deleted && kept.notes.first().contains("Eliminado por el remitente"))
        val hidden = ChatExport.bodyOf(revoked, Options(deleted = false), orig)
        assertEquals("🚫 Mensaje eliminado", hidden.text)
        assertFalse(hidden.deleted)
        val edited = ChatExport.bodyOf(msg("v2", edited = true), Options(), edits = listOf("v1"))
        assertTrue(edited.notes.contains("✏ Versión anterior 1: v1"))
        assertTrue(ChatExport.bodyOf(msg("v2", edited = true), Options(deleted = false), edits = listOf("v1")).notes.none { it.contains("Versión anterior") })
    }

    @Test
    fun htmlEscapaYMarcaMisMensajes() {
        val m = msg("<b>x</b> & \"y\"", fromMe = true)
        val html = ChatExport.htmlBubble(m, ChatExport.bodyOf(m, Options()), "Yo", null)
        assertTrue(html.contains("&lt;b&gt;x&lt;/b&gt; &amp; &quot;y&quot;") && html.contains("class=\"b me\""))
        assertFalse(html.contains("<b>"))
        val other = ChatExport.htmlBubble(msg("hola"), ChatExport.bodyOf(msg("hola"), Options()), "Ana", null)
        assertTrue(other.contains("class=\"who\">Ana"))
    }

    @Test
    fun htmlImagenIncrustadaOMarcador() {
        val m = msg("foto", "image")
        val b = ChatExport.bodyOf(m, Options())
        assertTrue(ChatExport.htmlBubble(m, b, "Ana", "data:image/png;base64,AAA").contains("<img src=\"data:image/png;base64,AAA\""))
        assertTrue(ChatExport.htmlBubble(m, b, "Ana", null).contains("[imagen no incluido]"))
    }

    @Test
    fun remitenteYNombresSeguros() {
        assertEquals("Yo", ChatExport.senderOf(msg(fromMe = true), "Yo", "Ana"))
        assertEquals("Ana", ChatExport.senderOf(msg(), "Yo", "X"))
        assertEquals("a_b", ChatExport.safeName("a/b"))
        assertNull(ChatExport.bodyOf(msg(), Options()).file)
    }
}
