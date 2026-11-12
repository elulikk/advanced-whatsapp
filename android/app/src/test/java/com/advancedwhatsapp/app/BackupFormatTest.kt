package com.advancedwhatsapp.app

import com.advancedwhatsapp.app.data.AwCrypto
import com.advancedwhatsapp.app.data.Backup
import com.advancedwhatsapp.app.data.BackupException
import com.advancedwhatsapp.app.data.Item
import com.advancedwhatsapp.app.data.WrongPasswordException
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test

/** Los mismos vectores que tests/test_backup.py de la PC: si cambia el formato, fallan los dos lados. */
class BackupFormatTest {
    private val password = "contraseña-ñ-123"
    private val plaintext = "Advanced WhatsApp ✓ backup".toByteArray(Charsets.UTF_8)
    private val salt = ByteArray(16) { it.toByte() }
    private val nonce = ByteArray(8) { (16 + it).toByte() }
    private val vectorHex = "4157424b0101000003e8000102030405060708090a0b0c0d0e0f10111213141516170000002cbcda0fae90a062ee1f27a2c91a10f1ff" +
        "e11eb2b8a0bd594fb1375ba44afff445cf0db05d1b0ecd043614bce6"

    private fun hex(b: ByteArray) = b.joinToString("") { "%02x".format(it) }
    private fun unhex(s: String) = ByteArray(s.length / 2) { s.substring(it * 2, it * 2 + 2).toInt(16).toByte() }

    @Test
    fun cifradoGeneraElVectorDeLaPc() {
        val blob = AwCrypto.encryptBytes(plaintext, password, iterations = 1000, salt = salt, noncePrefix = nonce)
        assertEquals(vectorHex, hex(blob))
    }

    @Test
    fun descifraElVectorDeLaPc() {
        assertArrayEquals(plaintext, AwCrypto.decryptBytes(unhex(vectorHex), password))
    }

    @Test
    fun contraseniaIncorrectaYTruncado() {
        try {
            AwCrypto.decryptBytes(unhex(vectorHex), "otra")
            fail("tenía que fallar")
        } catch (e: WrongPasswordException) {
            // esperado
        }
        try {
            AwCrypto.decryptBytes(unhex(vectorHex).copyOf(40), password)
            fail("tenía que fallar")
        } catch (e: BackupException) {
            // esperado
        }
    }

    @Test
    fun variosTrozosIdaYVuelta() {
        val data = ByteArray(AwCrypto.CHUNK * 2 + 777) { (it * 31).toByte() }
        assertArrayEquals(data, AwCrypto.decryptBytes(AwCrypto.encryptBytes(data, "k", iterations = 1000), "k"))
        assertArrayEquals(ByteArray(0), AwCrypto.decryptBytes(AwCrypto.encryptBytes(ByteArray(0), "k", iterations = 1000), "k"))
    }

    @Test
    fun valorCanonicoYHashIgualesALosDePython() {
        val value = mapOf("b" to listOf(1L, true, null), "a" to "ñ\"\n\u0001é")
        assertEquals("{\"a\":\"ñ\\\"\\n\\u0001é\",\"b\":[1,true,null]}", Backup.canonical(value))
        assertEquals("669c877d6c75f59b6676e7084af5ade4de111389", Backup.valueHash(value))
    }

    private fun it(v: Any?, t: Long) = Item(t, v)
    private fun tomb(t: Long) = Item(t, deleted = true)

    @Test
    fun ganaElUltimoCambio() {
        val local = mapOf("a" to it("x", 10), "b" to it("y", 50), "c" to it("z", 10))
        val remote = mapOf("a" to it("nuevo", 20), "b" to it("viejo", 40), "d" to it("solo-remoto", 5))
        val (winners, localWins) = Backup.mergeItems(local, remote)
        assertEquals(setOf("a", "d"), winners.keys)
        assertEquals(2, localWins)
    }

    @Test
    fun borradosYEmpates() {
        assertTrue("a" in Backup.mergeItems(mapOf("a" to it("x", 10)), mapOf("a" to tomb(20))).first)
        assertFalse("a" in Backup.mergeItems(mapOf("a" to tomb(30)), mapOf("a" to it("x", 20))).first)
        val a = mapOf("k" to it("uno", 7))
        val b = mapOf("k" to it("dos", 7))
        // en empate exactamente uno de los dos lados cambia, así que convergen
        assertEquals(1, Backup.mergeItems(a, b).first.size + Backup.mergeItems(b, a).first.size)
        assertTrue("k" in Backup.mergeItems(mapOf("k" to tomb(7)), mapOf("k" to it("x", 7))).first)
        assertTrue(Backup.mergeItems(mapOf("k" to it("x", 7)), mapOf("k" to it("x", 7))).first.isEmpty())
    }
}
