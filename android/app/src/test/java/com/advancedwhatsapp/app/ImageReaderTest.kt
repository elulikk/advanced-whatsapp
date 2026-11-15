package com.advancedwhatsapp.app

import com.advancedwhatsapp.app.ai.AiService
import com.advancedwhatsapp.app.ai.ImageReader
import org.junit.Assert.assertEquals
import org.junit.Test

class ImageReaderTest {
    @Test fun parseaDescripcionYTexto() {
        assertEquals("Un menú" to "Pizza \$10\nCafé \$2", ImageReader.parseReading("DESCRIPCION: Un menú\nTEXTO: Pizza \$10\nCafé \$2"))
    }

    @Test fun ningunoEsTextoVacio() {
        assertEquals("Un paisaje" to "", ImageReader.parseReading("DESCRIPCIÓN: Un paisaje\nTEXTO: NINGUNO"))
        assertEquals("Un paisaje" to "", ImageReader.parseReading("DESCRIPCIÓN: Un paisaje\nTEXTO: Ninguno."))
    }

    @Test fun sinFormatoTodoEsDescripcion() {
        assertEquals("Una foto de un auto" to "", ImageReader.parseReading("Una foto de un auto"))
    }

    @Test fun proveedoresConVisionSaltanLosQueNoTienenClave() {
        assertEquals(emptyList<String>(), AiService.visionProviders("Gemini") { false })
        assertEquals(listOf("OpenAI", "Groq"), AiService.visionProviders("Gemini") { it == "Groq" || it == "OpenAI" })
        assertEquals(listOf("Groq", "Gemini"), AiService.visionProviders("Groq") { it == "Groq" || it == "Gemini" })
    }

    @Test fun modeloConVisionPorDefecto() {
        assertEquals("gpt-4o-mini", AiService.visionModel("OpenAI", "o3-mini"))
        assertEquals("gpt-4o", AiService.visionModel("OpenAI", "gpt-4o"))
        assertEquals("meta-llama/llama-4-scout-17b-16e-instruct", AiService.visionModel("Groq", "llama-3.3-70b-versatile"))
        assertEquals("gemini-2.5-flash", AiService.visionModel("Gemini", ""))
    }
}
