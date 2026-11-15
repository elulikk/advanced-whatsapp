package com.advancedwhatsapp.app.ai

import android.graphics.Bitmap
import android.util.Log
import com.advancedwhatsapp.app.Prefs
import com.advancedwhatsapp.app.data.Db
import com.advancedwhatsapp.app.data.Photos
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.update
import java.io.ByteArrayOutputStream
import java.io.File
import java.util.concurrent.Executors
import java.util.concurrent.atomic.AtomicBoolean

/**
 * Leer imágenes con IA: descripción corta + texto (OCR) de una foto YA descargada, con el modelo con visión del
 * proveedor configurado. Se guarda en la base (image_desc / image_ocr) y entra en las búsquedas igual que las
 * transcripciones. Corre en el proceso del gateway, con 2 trabajadores como máximo. Nunca baja una foto para leerla.
 * Las lecturas automáticas que fallan se recuerdan (también tras reiniciar) y no se reintentan solas.
 */
object ImageReader {
    private const val TAG = "ImageReader"
    private const val BACKLOG_DAYS = 3L
    private const val BACKLOG_LIMIT = 15
    private const val FAILED_KEEP = 300
    private const val MAX_SIDE = 1568
    private const val MAX_FILE_BYTES = 25L * 1024 * 1024
    const val NO_TEXT = "NINGUNO"

    const val PROMPT = "Mirá esta imagen de un chat de WhatsApp y respondé en español rioplatense, con exactamente este formato:\n" +
        "DESCRIPCION: <una o dos oraciones cortas que digan qué se ve>\n" +
        "TEXTO: <todo el texto legible que aparezca en la imagen, transcripto tal cual y en el mismo orden; si no hay texto, escribí $NO_TEXT>\n" +
        "No agregues nada más ni uses markdown."

    private val workers = Executors.newFixedThreadPool(2) { r -> Thread(r, "image-read").apply { isDaemon = true } }
    private val inFlight = HashSet<String>()
    private val backlogDone = AtomicBoolean(false)

    /** Ids en lectura ahora (para el «Leyendo imagen…» de la burbuja) y último error por id. */
    private val _working = MutableStateFlow<Set<String>>(emptySet())
    val working: StateFlow<Set<String>> = _working
    private val _errors = MutableStateFlow<Map<String, String>>(emptyMap())
    val errors: StateFlow<Map<String, String>> = _errors

    fun available() = AiService.visionProviders().isNotEmpty()

    private fun failedIds(): List<String> = Prefs.getString("auto_read_failed").split(',').filter { it.isNotEmpty() }

    private fun markFailed(id: String) {
        Prefs.putString("auto_read_failed", (failedIds() + id).distinct().takeLast(FAILED_KEEP).joinToString(","))
    }

    /** Lectura automática de una foto recién descargada o enviada. No hace nada si el ajuste está apagado (por defecto). */
    fun autoEnqueue(id: String): Boolean {
        if (!Prefs.autoReadImages || !available()) return false
        synchronized(inFlight) { if (id in failedIds()) return false }
        return enqueue(id, auto = true)
    }

    /** Lectura pedida con el botón de la burbuja (o «Rehacer»): también reintenta las que fallaron. */
    fun readNow(id: String) = enqueue(id, auto = false)

    private fun enqueue(id: String, auto: Boolean): Boolean {
        synchronized(inFlight) {
            if (!inFlight.add(id)) return false
        }
        _working.update { it + id }
        _errors.update { it - id }
        workers.execute {
            try {
                // Ajuste apagado mientras esperaba en la cola: se descarta (solo las automáticas)
                if (auto && !Prefs.autoReadImages) return@execute
                val m = Db.getMessage(id) ?: return@execute
                val file = m.mediaPath?.let { File(it) }?.takeIf { it.isFile }
                    ?: throw IllegalStateException("La imagen no está descargada")
                if (!auto || (m.imageDesc.isNullOrBlank() && m.imageOcr.isNullOrBlank())) {
                    val (desc, ocr) = read(file)
                    Db.setImageRead(id, desc, ocr)
                }
            } catch (e: Exception) {
                Log.w(TAG, "No se pudo leer $id: ${e.message}")
                if (auto) markFailed(id)
                _errors.update { it + (id to (e.message ?: "error").lineSequence().first().take(160)) }
            } finally {
                synchronized(inFlight) { inFlight.remove(id) }
                _working.update { it - id }
            }
        }
        return true
    }

    /** Encola las fotos recientes ya descargadas y sin leer. Una vez por arranque, o de nuevo con [force]. */
    fun backlogOnce(force: Boolean = false) {
        if (!Prefs.autoReadImages || !available()) return
        if (!force && !backlogDone.compareAndSet(false, true)) return
        workers.execute {
            try {
                val since = System.currentTimeMillis() / 1000 - BACKLOG_DAYS * 86400
                Db.imagesPendingRead(since, BACKLOG_LIMIT).forEach { autoEnqueue(it.id) }
            } catch (e: Exception) {
                Log.w(TAG, "Pendientes: ${e.message}")
            }
        }
    }

    /** Descripción y texto de la imagen. Bloqueante; lanza una excepción si ningún proveedor pudo. */
    fun read(file: File): Pair<String, String> {
        if (file.length() > MAX_FILE_BYTES) throw IllegalStateException("La imagen es demasiado grande para leerla")
        val bitmap = Photos.decode(file, MAX_SIDE) ?: throw IllegalStateException("No se pudo abrir la imagen")
        val out = ByteArrayOutputStream()
        bitmap.compress(Bitmap.CompressFormat.JPEG, 85, out)
        val answer = AiService.askImage(PROMPT, out.toByteArray())
        if (AiService.isError(answer)) throw IllegalStateException(answer.trim().lineSequence().first())
        val parsed = parseReading(answer)
        if (parsed.first.isEmpty() && parsed.second.isEmpty()) throw IllegalStateException("La IA no devolvió nada legible")
        return parsed
    }

    private val DESC_RE = Regex("DESCRIPCI[ÓO]N\\s*:\\s*(.*?)(?:\\n\\s*TEXTO\\s*:|$)", setOf(RegexOption.DOT_MATCHES_ALL, RegexOption.IGNORE_CASE))
    private val TEXT_RE = Regex("TEXTO\\s*:\\s*(.*)$", setOf(RegexOption.DOT_MATCHES_ALL, RegexOption.IGNORE_CASE))
    private val NONE_RE = Regex("[\\W_]*(ninguno|ninguna|no hay texto|sin texto|none|n/a)[\\W_]*", RegexOption.IGNORE_CASE)

    /**
     * (descripción, texto) a partir de la respuesta de la IA. Tolera que no respete el formato (sin etiquetas, todo
     * es descripción) y guarda «NINGUNO» como texto vacío.
     */
    fun parseReading(raw: String): Pair<String, String> {
        val text = raw.trim().removePrefix("```").removeSuffix("```").trim()
        val d = DESC_RE.find(text)?.groupValues?.get(1)?.trim()
        val t = TEXT_RE.find(text)?.groupValues?.get(1)?.trim()
        val desc = d ?: if (t != null) "" else text
        val ocr = if (t == null || NONE_RE.matches(t)) "" else t
        return desc to ocr
    }
}
