package com.advancedwhatsapp.app

import com.advancedwhatsapp.app.data.FolderRules
import com.advancedwhatsapp.app.data.Organization
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/** Reglas de las carpetas inteligentes: la condición SQL que arman (sin base de datos). */
class OrganizationTest {
    @Test
    fun sinReglasNoMuestraNada() {
        assertTrue(FolderRules().isEmpty)
        assertEquals("1 = 0", Organization.rulesWhere(FolderRules()))
    }

    @Test
    fun reglasSeCombinanConY() {
        val sql = Organization.rulesWhere(
            FolderRules(tabs = setOf(1L), kind = FolderRules.KIND_GROUP, unread = true, muted = FolderRules.MUTED_NONE), now = 1000,
        )
        assertTrue("tab_id IN (1)" in sql)
        assertTrue("@g.us" in sql)
        assertTrue("unread_count != 0" in sql)
        assertTrue("NOT (COALESCE(muted_until, 0) = -1 OR COALESCE(muted_until, 0) > 1000)" in sql)
        assertTrue(" AND " in sql)
    }

    @Test
    fun contactosElegidosEntranSiempre() {
        val sql = Organization.rulesWhere(FolderRules(unread = true, jids = setOf("a@s.whatsapp.net", "o'brien@s.whatsapp.net")))
        assertTrue(" OR jid IN ('a@s.whatsapp.net','o''brien@s.whatsapp.net')" in sql)   // comillas escapadas
        assertEquals("jid IN ('x@g.us')", Organization.rulesWhere(FolderRules(jids = setOf("x@g.us"))))
    }

    @Test
    fun silenciadosSoloUsaLaMarcaDeSilencio() {
        val sql = Organization.rulesWhere(FolderRules(muted = FolderRules.MUTED_ONLY), now = 50)
        assertTrue(sql.startsWith("((COALESCE(muted_until, 0) = -1") || sql.startsWith("(COALESCE(muted_until, 0) = -1"))
        assertFalse("NOT" in sql)
    }

    @Test
    fun describeResumeLasReglas() {
        assertEquals("sin reglas", Organization.describe(FolderRules()))
        val text = Organization.describe(FolderRules(tabs = setOf(1L), unread = true, jids = setOf("a")), mapOf(1L to "Marketplaces"))
        assertEquals("pestañas: Marketplaces · no leídos · 1 contacto(s) elegido(s)", text)
    }
}
