package com.advancedwhatsapp.app

import com.advancedwhatsapp.app.data.LinkPreviews
import com.advancedwhatsapp.app.data.MediaCleanup
import com.advancedwhatsapp.app.data.MediaCleanup.Entry
import com.advancedwhatsapp.app.data.Stats
import com.advancedwhatsapp.app.data.Thumbs
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** Lógica pura de calidad de vida: estadísticas, limpieza de multimedia, links y claves de miniaturas. */
class QolLogicTest {
    private val day = 86_400L
    private val now = 1_800_000_000L

    private fun entry(id: String, ageDays: Int, size: Long = 1000, removable: Boolean = true) =
        Entry(id, "chat@s.whatsapp.net", "Ana", "image", "/media/$id", size, now - ageDays * day, removable)

    // ── Estadísticas ──
    @Test
    fun responseTimesSeparateMineFromTheirs() {
        val a = Stats.ResponseAccumulator()
        a.add("A", false, 1000)   // ellos
        a.add("A", true, 1060)    // yo contesto en 60 s
        a.add("A", false, 1200)   // ellos en 140 s
        a.add("A", true, 1500)    // yo en 300 s
        a.add("A", true, 1510)    // dos seguidos míos: no es una respuesta
        val r = a.result()
        assertEquals(180.0, r.mineAvg!!, 0.001)
        assertEquals(2, r.mineCount)
        assertEquals(140.0, r.theirsAvg!!, 0.001)
        assertEquals(1, r.theirsCount)
    }

    @Test
    fun responseTimesDoNotCrossChatsAndDropLongPauses() {
        val a = Stats.ResponseAccumulator()
        a.add("A", false, 1000)
        a.add("B", true, 1010)            // otro chat: no cuenta
        a.add("B", false, 1020)
        a.add("B", true, 1020 + 3 * day)  // más de 24 h: se descarta
        val r = a.result()
        assertEquals(1, r.theirsCount)
        assertEquals(0, r.mineCount)
        assertNull(r.mineAvg)
    }

    @Test
    fun durationsAreReadable() {
        assertEquals("—", Stats.formatDuration(null))
        assertEquals("45 s", Stats.formatDuration(45.0))
        assertEquals("3 min", Stats.formatDuration(180.0))
        assertEquals("2 h", Stats.formatDuration(7200.0))
        assertEquals("3 h 15 min", Stats.formatDuration(3 * 3600 + 900.0))
    }

    @Test
    fun periodsStartWhereExpected() {
        assertEquals(0L, Stats.periodStart(Stats.PERIODS.last(), now))
        assertEquals(now - 30 * day, Stats.periodStart(Stats.PERIODS[1], now))
    }

    // ── Limpieza de multimedia ──
    @Test
    fun cleanupByAgeKeepsRecentAndNotRemovable() {
        val plan = MediaCleanup.plan(listOf(entry("viejo", 90), entry("nuevo", 2), entry("propio", 90, removable = false)), 30, 0.0, now)
        assertEquals(listOf("viejo"), plan.map { it.id })
    }

    @Test
    fun cleanupBySizeRemovesOldestFirst() {
        val all = listOf(entry("m1", 30), entry("m2", 20), entry("m3", 10))
        val plan = MediaCleanup.plan(all, 0, 1600.0 / (1024 * 1024 * 1024), now)
        assertEquals(listOf("m1", "m2"), plan.map { it.id })
    }

    @Test
    fun cleanupWithoutCriteriaDoesNothing() {
        assertTrue(MediaCleanup.plan(listOf(entry("m1", 400)), 0, 0.0, now).isEmpty())
    }

    @Test
    fun cleanupCombinesAgeAndSizeWithoutRepeating() {
        val all = listOf(entry("m1", 90), entry("m2", 20), entry("m3", 10))
        val plan = MediaCleanup.plan(all, 30, 1600.0 / (1024 * 1024 * 1024), now)
        assertEquals(listOf("m1", "m2"), plan.map { it.id })
    }

    @Test
    fun usageAddsUpByTypeAndChat() {
        val u = MediaCleanup.usage(listOf(entry("a", 1, 1000), entry("b", 1, 500, removable = false)))
        assertEquals(1500L, u.totalBytes)
        assertEquals(1000L, u.removableBytes)
        assertEquals(2, u.byType["image"]!!.count)
        assertEquals(1, u.byChat.size)
    }

    @Test
    fun sizesAreReadable() {
        assertEquals("500 B", MediaCleanup.formatSize(500))
        assertEquals("%.1f KB".format(1.5), MediaCleanup.formatSize(1536))   // separador decimal según el idioma del equipo
    }

    // ── Links ──
    @Test
    fun firstUrlStripsPunctuation() {
        assertEquals("https://ejemplo.com/a?b=1", LinkPreviews.firstUrl("mirá https://ejemplo.com/a?b=1, che"))
        assertEquals("https://x.org/p", LinkPreviews.firstUrl("(https://x.org/p)."))
        assertNull(LinkPreviews.firstUrl("sin links"))
    }

    @Test
    fun domainIgnoresWwwAndPath() {
        assertEquals("ejemplo.com", LinkPreviews.domainOf("https://www.ejemplo.com/a/b?c=1"))
    }

    // ── Miniaturas ──
    @Test
    fun thumbKeyChangesWhenTheFileChanges() {
        val k = Thumbs.keyOf("/media/a.jpg", 100L, 5000L, 720)
        assertEquals(k, Thumbs.keyOf("/media/a.jpg", 100L, 5000L, 720))
        assertNotEquals(k, Thumbs.keyOf("/media/a.jpg", 101L, 5000L, 720))
        assertNotEquals(k, Thumbs.keyOf("/media/a.jpg", 100L, 5000L, 480))
    }
}
