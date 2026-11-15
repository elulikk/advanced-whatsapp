package com.advancedwhatsapp.app.ai

import android.util.Log
import com.advancedwhatsapp.app.Prefs
import com.advancedwhatsapp.app.data.Actions
import com.advancedwhatsapp.app.data.Db
import java.io.File
import java.util.concurrent.Executors
import java.util.concurrent.atomic.AtomicBoolean

/**
 * Transcribe solas las notas de voz (recibidas y enviadas). Corre en el proceso del servicio del gateway, así que
 * sigue andando con la app en segundo plano. Un solo trabajador: no satura la red ni la recepción de mensajes.
 * Los audios que fallan se recuerdan (también tras reiniciar) y no se reintentan solos: queda el «Transcribir» manual.
 */
object AutoTranscriber {
    private const val TAG = "AutoTranscriber"
    private const val BACKLOG_DAYS = 3L
    private const val BACKLOG_LIMIT = 20
    private const val FAILED_KEEP = 300

    private val worker = Executors.newSingleThreadExecutor { r -> Thread(r, "auto-transcribe").apply { isDaemon = true } }
    private val inFlight = HashSet<String>()
    private val backlogDone = AtomicBoolean(false)

    private fun available() = Prefs.autoTranscribe && (AiService.hasKey("Groq") || AiService.hasKey("OpenAI"))

    private fun failedIds(): List<String> = Prefs.getString("auto_transcribe_failed").split(',').filter { it.isNotEmpty() }

    private fun markFailed(id: String) {
        val ids = (failedIds() + id).distinct().takeLast(FAILED_KEEP)
        Prefs.putString("auto_transcribe_failed", ids.joinToString(","))
    }

    /** Encola un audio por id. No hace nada si el ajuste está apagado, no hay clave, ya tiene texto, falló antes o ya está en cola. */
    fun enqueue(id: String): Boolean {
        if (!available()) return false
        synchronized(inFlight) {
            if (id in inFlight || id in failedIds()) return false
            inFlight.add(id)
        }
        worker.execute {
            try {
                // Ajuste apagado mientras esperaba en la cola: se descarta
                if (!available()) return@execute
                val m = Db.getMessage(id) ?: return@execute
                if (m.mediaType != "audio" || !m.transcript.isNullOrBlank()) return@execute
                val path = Actions.downloadMedia(m)
                val t = AiService.transcribe(File(path))
                Db.setTranscript(id, t.ifEmpty { "(sin voz reconocible)" })
            } catch (e: Exception) {
                Log.w(TAG, "No se pudo transcribir $id: ${e.message}")
                markFailed(id)
            } finally {
                synchronized(inFlight) { inFlight.remove(id) }
            }
        }
        return true
    }

    /** Encola los audios recientes pendientes. Una vez por arranque (al conectar), o de nuevo si se pide con [force]. */
    fun backlogOnce(force: Boolean = false) {
        if (!available()) return
        if (!force && !backlogDone.compareAndSet(false, true)) return
        worker.execute {
            try {
                val since = System.currentTimeMillis() / 1000 - BACKLOG_DAYS * 86400
                Db.audiosPendingTranscript(since, BACKLOG_LIMIT).forEach { enqueue(it.id) }
            } catch (e: Exception) {
                Log.w(TAG, "Pendientes: ${e.message}")
            }
        }
    }
}
