package com.advancedwhatsapp.app

import com.advancedwhatsapp.app.data.Updater
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** Lógica pura de actualizaciones: versiones, nombre del asset, hash y repo. */
class UpdaterTest {
    @Test fun comparaVersiones() {
        assertTrue(Updater.isNewer("0.5.0", "0.4.0"))
        assertTrue(Updater.isNewer("0.10.0", "0.9.9"))
        assertTrue(Updater.isNewer("android-v1.0.0", "0.9.9"))
        assertFalse(Updater.isNewer("0.4.0", "0.4.0"))
        assertFalse(Updater.isNewer("0.3.9", "0.4.0"))
        assertTrue(Updater.isNewer("1.0.0", "1.0.0-beta.1"))
        assertTrue(Updater.isNewer("1.0.0-beta.2", "1.0.0-beta.1"))
        assertFalse(Updater.isNewer("basura", "1.0.0"))
    }

    @Test fun eligeElAsset() {
        assertEquals("0.5.0", Updater.versionOfAsset("AdvancedWhatsapp-Android-0.5.0.apk"))
        assertNull(Updater.versionOfAsset("AdvancedWhatsapp-Setup-1.1.0.exe"))
        assertNull(Updater.versionOfAsset("AdvancedWhatsapp-Android-0.5.0.apk.sha256"))
    }

    @Test fun hashYRepo() {
        val h = "a".repeat(64)
        assertEquals(h, Updater.parseSha256("${h.uppercase()}  AdvancedWhatsapp-Android-0.5.0.apk"))
        assertEquals("", Updater.parseSha256("nada"))
        assertEquals("elulikk/advanced-whatsapp", Updater.normalizeRepo("https://github.com/elulikk/advanced-whatsapp.git"))
        assertEquals("", Updater.normalizeRepo("a b"))
    }
}
