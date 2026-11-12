package com.advancedwhatsapp.app.data

import java.io.ByteArrayInputStream
import java.io.ByteArrayOutputStream
import java.io.InputStream
import java.io.OutputStream
import java.nio.ByteBuffer
import java.security.GeneralSecurityException
import java.security.SecureRandom
import javax.crypto.Cipher
import javax.crypto.SecretKeyFactory
import javax.crypto.spec.GCMParameterSpec
import javax.crypto.spec.PBEKeySpec
import javax.crypto.spec.SecretKeySpec

/** Error de copias o sincronización: el mensaje se puede mostrar tal cual. */
open class BackupException(message: String) : Exception(message)

class WrongPasswordException : BackupException("Contraseña incorrecta o archivo dañado.")

/**
 * Contenedor cifrado de los `.awbackup` y los documentos de sincronización. Es el mismo formato que `app/awcrypto.py` de la PC
 * (descrito en docs/ARQUITECTURA.md): encabezado de 34 bytes ("AWBK", versión 1, kdf 1 = PBKDF2-HMAC-SHA256, iteraciones u32,
 * sal 16, prefijo de nonce 8) y trozos de 1 MiB cifrados con AES-256-GCM (largo u32 + cifrado con etiqueta). Nonce = prefijo + índice
 * del trozo (u32); AAD = encabezado + 1 byte (1 en el último trozo).
 */
object AwCrypto {
    const val CHUNK = 1 shl 20
    const val TAG_LEN = 16
    const val HEADER_LEN = 34
    const val DEFAULT_ITERATIONS = 210_000
    private const val MIN_ITERATIONS = 1_000
    private const val MAX_ITERATIONS = 5_000_000
    private val MAGIC = byteArrayOf(0x41, 0x57, 0x42, 0x4B)   // "AWBK"

    fun deriveKey(password: String, salt: ByteArray, iterations: Int): ByteArray {
        val spec = PBEKeySpec(password.toCharArray(), salt, iterations, 256)
        return SecretKeyFactory.getInstance("PBKDF2WithHmacSHA256").generateSecret(spec).encoded
    }

    private fun u32(n: Int): ByteArray = ByteBuffer.allocate(4).putInt(n).array()

    private fun nonce(prefix: ByteArray, index: Int): ByteArray = prefix + u32(index)

    private fun gcm(mode: Int, key: SecretKeySpec, nonce: ByteArray, aad: ByteArray, data: ByteArray): ByteArray {
        val c = Cipher.getInstance("AES/GCM/NoPadding")
        c.init(mode, key, GCMParameterSpec(TAG_LEN * 8, nonce))
        c.updateAAD(aad)
        return c.doFinal(data)
    }

    private fun readFull(input: InputStream, n: Int): ByteArray {
        val buf = ByteArray(n)
        var off = 0
        while (off < n) {
            val r = input.read(buf, off, n - off)
            if (r < 0) break
            off += r
        }
        return if (off == n) buf else buf.copyOf(off)
    }

    /** `salt` y `noncePrefix` solo se fijan en las pruebas (vector de test); normalmente son aleatorios. */
    fun encrypt(
        input: InputStream, out: OutputStream, password: String, iterations: Int = DEFAULT_ITERATIONS,
        salt: ByteArray? = null, noncePrefix: ByteArray? = null,
    ) {
        val rnd = SecureRandom()
        val s = salt ?: ByteArray(16).also { rnd.nextBytes(it) }
        val np = noncePrefix ?: ByteArray(8).also { rnd.nextBytes(it) }
        require(s.size == 16 && np.size == 8)
        val header = MAGIC + byteArrayOf(1, 1) + u32(iterations) + s + np
        val key = SecretKeySpec(deriveKey(password, s, iterations), "AES")
        out.write(header)
        var index = 0
        var current = readFull(input, CHUNK)
        while (true) {
            val next = if (current.size == CHUNK) readFull(input, CHUNK) else ByteArray(0)
            val last = next.isEmpty()
            val ct = gcm(Cipher.ENCRYPT_MODE, key, nonce(np, index), header + byteArrayOf(if (last) 1 else 0), current)
            out.write(u32(ct.size))
            out.write(ct)
            if (last) return
            current = next
            index++
        }
    }

    fun decrypt(input: InputStream, out: OutputStream, password: String) {
        val header = readFull(input, HEADER_LEN)
        if (header.size != HEADER_LEN || !header.copyOfRange(0, 4).contentEquals(MAGIC)) {
            throw BackupException("No es un archivo de copia de Advanced WhatsApp.")
        }
        if (header[4].toInt() != 1 || header[5].toInt() != 1) {
            throw BackupException("El archivo es de una versión más nueva de Advanced WhatsApp.")
        }
        val iterations = ByteBuffer.wrap(header, 6, 4).int
        if (iterations !in MIN_ITERATIONS..MAX_ITERATIONS) throw BackupException("El archivo está dañado (parámetros de cifrado inválidos).")
        val salt = header.copyOfRange(10, 26)
        val prefix = header.copyOfRange(26, 34)
        val key = SecretKeySpec(deriveKey(password, salt, iterations), "AES")
        var index = 0
        var lead = readFull(input, 4)
        while (true) {
            if (lead.size != 4) throw BackupException("El archivo está incompleto.")
            val size = ByteBuffer.wrap(lead).int
            if (size < TAG_LEN || size > CHUNK + TAG_LEN) throw BackupException("El archivo está dañado.")
            val ct = readFull(input, size)
            if (ct.size != size) throw BackupException("El archivo está incompleto.")
            lead = readFull(input, 4)
            val last = lead.isEmpty()
            val plain = try {
                gcm(Cipher.DECRYPT_MODE, key, nonce(prefix, index), header + byteArrayOf(if (last) 1 else 0), ct)
            } catch (e: GeneralSecurityException) {
                throw WrongPasswordException()
            }
            out.write(plain)
            if (last) return
            index++
        }
    }

    fun encryptBytes(data: ByteArray, password: String, iterations: Int = DEFAULT_ITERATIONS, salt: ByteArray? = null, noncePrefix: ByteArray? = null): ByteArray {
        val out = ByteArrayOutputStream()
        encrypt(ByteArrayInputStream(data), out, password, iterations, salt, noncePrefix)
        return out.toByteArray()
    }

    fun decryptBytes(blob: ByteArray, password: String): ByteArray {
        val out = ByteArrayOutputStream()
        decrypt(ByteArrayInputStream(blob), out, password)
        return out.toByteArray()
    }
}
