package com.advancedwhatsapp.app.data

import android.content.ContentValues
import android.provider.MediaStore
import android.webkit.MimeTypeMap
import com.advancedwhatsapp.app.App
import java.io.File

/** Copia los archivos descargados a la carpeta pública Descargas del teléfono. Bloqueante: llamar desde Dispatchers.IO. */
object Downloads {
    private const val FOLDER = "Download/AdvancedWhatsapp"

    /** MIME según la extensión del nombre; los APK y otros casos conocidos quedan bien resueltos. */
    fun mimeOf(name: String): String =
        MimeTypeMap.getSingleton().getMimeTypeFromExtension(name.substringAfterLast('.', "").lowercase())
            ?: if (name.endsWith(".apk", ignoreCase = true)) "application/vnd.android.package-archive" else "application/octet-stream"

    /** Un nombre de archivo válido: si el texto del mensaje no parece uno, se usa el del archivo local. */
    fun displayName(text: String, file: File): String {
        val t = text.trim().replace(Regex("[\\\\/:*?\"<>|\\n\\r]"), "_")
        val ext = file.extension.lowercase()
        return if (t.isNotEmpty() && t.length <= 120 && ext.isNotEmpty() && t.endsWith(".$ext", ignoreCase = true)) t else file.name
    }

    /**
     * Guarda una copia en Descargas/AdvancedWhatsapp (MediaStore, sin permisos en Android 10+).
     * Si ya hay una con ese nombre y tamaño no la repite. Devuelve false si no se pudo; nunca lanza.
     */
    fun saveCopy(src: File, name: String): Boolean = try {
        copyToDownloads(src, name)
    } catch (e: Exception) {
        false
    }

    private fun copyToDownloads(src: File, name: String): Boolean {
        if (!src.exists()) return false
        val cr = App.instance.contentResolver
        val collection = MediaStore.Downloads.EXTERNAL_CONTENT_URI
        val exists = cr.query(
            collection, arrayOf(MediaStore.Downloads._ID, MediaStore.Downloads.SIZE),
            "${MediaStore.Downloads.DISPLAY_NAME} = ? AND ${MediaStore.Downloads.RELATIVE_PATH} LIKE ?",
            arrayOf(name, "$FOLDER%"), null,
        )?.use { c -> c.moveToFirst() && c.getLong(1) == src.length() } == true
        if (exists) return true
        val values = ContentValues().apply {
            put(MediaStore.Downloads.DISPLAY_NAME, name)
            put(MediaStore.Downloads.MIME_TYPE, mimeOf(name))
            put(MediaStore.Downloads.RELATIVE_PATH, FOLDER)
            put(MediaStore.Downloads.IS_PENDING, 1)
        }
        val uri = cr.insert(collection, values) ?: return false
        return try {
            val out = cr.openOutputStream(uri) ?: throw IllegalStateException("sin salida")
            out.use { o -> src.inputStream().use { it.copyTo(o) } }
            cr.update(uri, ContentValues().apply { put(MediaStore.Downloads.IS_PENDING, 0) }, null, null)
            true
        } catch (e: Exception) {
            cr.delete(uri, null, null)
            false
        }
    }
}
