package com.advancedwhatsapp.app.data

import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.Matrix
import android.media.ExifInterface
import android.util.LruCache
import com.advancedwhatsapp.app.App
import java.io.File
import java.security.MessageDigest
import kotlin.math.max
import kotlin.math.roundToInt

/**
 * Caché de miniaturas de las fotos del chat, en dos niveles:
 *  - memoria: LruCache de bitmaps ya reducidos (al volver a pasar por la burbuja no se decodifica nada);
 *  - disco: un JPEG chico por foto en data/thumbs (la original puede pesar varios MB y decodificarla en cada
 *    redibujado era lo más caro del chat). La clave incluye fecha y tamaño del archivo: si cambia, se regenera.
 * `load` es bloqueante: llamarla desde Dispatchers.IO.
 */
object Thumbs {
    const val SIDE = 720
    private const val QUALITY = 82
    const val DISK_LIMIT_BYTES = 300L * 1024 * 1024

    private val dir: File get() = File(App.dataDir, "thumbs").apply { mkdirs() }

    private val memory = object : LruCache<String, Bitmap>(memoryBudget()) {
        override fun sizeOf(key: String, value: Bitmap): Int = value.byteCount
    }

    /** Una décima del heap, entre 4 y 48 MB. */
    private fun memoryBudget(): Int = (Runtime.getRuntime().maxMemory() / 10).toInt().coerceIn(4 * 1024 * 1024, 48 * 1024 * 1024)

    fun keyOf(path: String, modified: Long, length: Long, side: Int): String {
        val raw = "$path|$modified|$length|$side"
        return MessageDigest.getInstance("SHA-1").digest(raw.toByteArray()).joinToString("") { "%02x".format(it) }
    }

    private fun keyOf(f: File, side: Int) = keyOf(f.absolutePath, f.lastModified(), f.length(), side)

    /** Lo que ya está en memoria, sin tocar el disco (para dibujar sin parpadeo al volver a pasar por la foto). */
    fun peek(path: String, side: Int = SIDE): Bitmap? {
        val f = File(path)
        return if (f.exists()) memory.get(keyOf(f, side)) else null
    }

    fun load(path: String, side: Int = SIDE): Bitmap? {
        val src = File(path)
        if (!src.exists()) return null
        val key = keyOf(src, side)
        memory.get(key)?.let { return it }
        val cached = File(dir, "$key.jpg")
        var bmp: Bitmap? = null
        if (cached.exists()) {
            bmp = BitmapFactory.decodeFile(cached.path)
            if (bmp != null) cached.setLastModified(System.currentTimeMillis())   // «usada hace poco», para la poda
            else cached.delete()                                                   // quedó a medias: se regenera
        }
        if (bmp == null) bmp = generate(src, cached, side)
        if (bmp != null) memory.put(key, bmp)
        return bmp
    }

    private fun generate(src: File, out: File, side: Int): Bitmap? {
        val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
        BitmapFactory.decodeFile(src.path, bounds)
        if (bounds.outWidth <= 0 || bounds.outHeight <= 0) return null
        val opts = BitmapFactory.Options().apply { inSampleSize = Photos.sampleSize(bounds.outWidth, bounds.outHeight, side) }
        var bmp = BitmapFactory.decodeFile(src.path, opts) ?: return null
        val big = max(bmp.width, bmp.height)
        if (big > side) {
            val ratio = side.toFloat() / big
            bmp = Bitmap.createScaledBitmap(bmp, (bmp.width * ratio).roundToInt(), (bmp.height * ratio).roundToInt(), true)
        }
        val orientation = runCatching {
            ExifInterface(src.path).getAttributeInt(ExifInterface.TAG_ORIENTATION, ExifInterface.ORIENTATION_NORMAL)
        }.getOrDefault(ExifInterface.ORIENTATION_NORMAL)
        bmp = rotate(bmp, orientation)
        runCatching {
            val tmp = File(out.path + ".tmp")
            tmp.outputStream().use { bmp.compress(Bitmap.CompressFormat.JPEG, QUALITY, it) }
            if (!tmp.renameTo(out)) tmp.delete()
        }
        return bmp
    }

    private fun rotate(bitmap: Bitmap, orientation: Int): Bitmap {
        val m = Matrix()
        when (orientation) {
            ExifInterface.ORIENTATION_ROTATE_90 -> m.setRotate(90f)
            ExifInterface.ORIENTATION_ROTATE_180 -> m.setRotate(180f)
            ExifInterface.ORIENTATION_ROTATE_270 -> m.setRotate(270f)
            else -> return bitmap
        }
        return Bitmap.createBitmap(bitmap, 0, 0, bitmap.width, bitmap.height, m, true)
    }

    fun diskBytes(): Long = dir.listFiles()?.sumOf { it.length() } ?: 0L

    /** Borra las miniaturas menos usadas hasta quedar por debajo del 80 % del tope. Devuelve cuántas borró. */
    fun prune(limit: Long = DISK_LIMIT_BYTES): Int {
        val files = dir.listFiles()?.sortedBy { it.lastModified() } ?: return 0
        var total = files.sumOf { it.length() }
        if (total <= limit) return 0
        var removed = 0
        for (f in files) {
            val size = f.length()
            if (f.delete()) {
                total -= size
                removed++
            }
            if (total <= limit * 0.8) break
        }
        memory.evictAll()
        return removed
    }
}
