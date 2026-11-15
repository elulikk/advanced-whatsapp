package com.advancedwhatsapp.app.ui

import android.media.MediaPlayer
import android.media.MediaRecorder
import android.os.Build
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.ui.platform.LocalView
import com.advancedwhatsapp.app.App
import com.advancedwhatsapp.app.Prefs
import com.advancedwhatsapp.app.data.AudioQueue
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.io.File
import kotlin.math.max
import kotlin.math.sqrt

data class Recording(val file: File, val seconds: Int, val waveform: List<Int>)

/** Graba una nota de voz en OGG/Opus, el formato que WhatsApp espera, y arma su onda. */
class VoiceRecorder {
    private var recorder: MediaRecorder? = null
    private var file: File? = null
    private val samples = ArrayList<Int>()
    private var sampler: Job? = null
    private var elapsedMs = 0L
    val seconds = MutableStateFlow(0)
    val paused = MutableStateFlow(false)

    fun start(scope: CoroutineScope) {
        val out = File(App.sentDir, "nota_${System.currentTimeMillis()}.ogg")
        file = out
        val created = if (Build.VERSION.SDK_INT >= 31) MediaRecorder(App.instance) else @Suppress("DEPRECATION") MediaRecorder()
        recorder = created.apply {
            setAudioSource(MediaRecorder.AudioSource.MIC)
            setOutputFormat(MediaRecorder.OutputFormat.OGG)
            setAudioEncoder(MediaRecorder.AudioEncoder.OPUS)
            setAudioChannels(1)
            setAudioSamplingRate(48000)
            setAudioEncodingBitRate(32000)
            setOutputFile(out.path)
            prepare()
            start()
        }
        sampler = scope.launch(Dispatchers.Default) {
            while (isActive) {
                delay(STEP_MS)
                if (paused.value) continue
                elapsedMs += STEP_MS
                seconds.value = (elapsedMs / 1000).toInt()
                recorder?.let { r -> runCatching { samples += r.maxAmplitude } }
            }
        }
    }

    fun togglePause() {
        val r = recorder ?: return
        if (paused.value) r.resume() else r.pause()
        paused.value = !paused.value
    }

    /** Termina la grabación. Null si duró menos de un segundo o falló. */
    fun stop(): Recording? {
        val r = recorder ?: return null
        sampler?.cancel()
        recorder = null
        val ok = runCatching { r.stop() }.isSuccess
        r.release()
        val f = file ?: return null
        if (!ok || elapsedMs < 1000) {
            f.delete()
            return null
        }
        return Recording(f, max(1, (elapsedMs / 1000).toInt()), buildWaveform(samples))
    }

    fun cancel() {
        val r = recorder ?: return
        sampler?.cancel()
        recorder = null
        runCatching { r.stop() }
        r.release()
        file?.delete()
    }

    companion object {
        private const val STEP_MS = 100L
        private const val BARS = 64

        /** Reduce las muestras a 64 barras de 0 a 100, escaladas con raíz para que se noten los tonos bajos. */
        fun buildWaveform(samples: List<Int>): List<Int> {
            if (samples.isEmpty()) return List(BARS) { 0 }
            val peak = max(1, samples.max()).toDouble()
            return List(BARS) { i ->
                val from = i * samples.size / BARS
                val to = max(from + 1, (i + 1) * samples.size / BARS).coerceAtMost(samples.size)
                val slice = samples.subList(from.coerceAtMost(samples.size - 1), to)
                (sqrt(slice.max() / peak) * 100).toInt().coerceIn(0, 100)
            }
        }
    }
}

/** Reproductor único: empezar un audio corta el anterior. */
object VoicePlayer {
    val playingId = MutableStateFlow<String?>(null)
    val progress = MutableStateFlow(0f)
    val positionMs = MutableStateFlow(0)
    val durationMs = MutableStateFlow(0)
    val speed = MutableStateFlow(Prefs.audioSpeed.takeIf { it == 1.5f || it == 2f } ?: 1f)
    private var player: MediaPlayer? = null
    private var ticker: Job? = null
    private var currentId: String? = null

    fun toggle(id: String, path: String, scope: CoroutineScope) {
        val p = player
        if (currentId == id && p != null) {
            if (p.isPlaying) {
                p.pause()
                playingId.value = null
            } else {
                p.start()
                playingId.value = id
                startTicker(scope)
            }
            return
        }
        release()
        currentId = id
        progress.value = 0f
        positionMs.value = 0
        durationMs.value = 0
        player = MediaPlayer().apply {
            setDataSource(path)
            setOnCompletionListener {
                val finished = currentId
                this@VoicePlayer.release()    // sin el calificador sería el release() del MediaPlayer
                if (finished != null && Prefs.audioAutoplay) playNext(finished, scope)
            }
            prepare()
            durationMs.value = duration
            applySpeed(this)
            start()
        }
        playingId.value = id
        startTicker(scope)
    }

    /** Pasa solo a la nota que sigue en el chat, si es un audio ya bajado (no arranca nada si hay otro cosa en el medio). */
    private fun playNext(afterId: String, scope: CoroutineScope) {
        scope.launch(Dispatchers.IO) {
            val next = runCatching { AudioQueue.nextAfter(afterId) }.getOrNull() ?: return@launch
            withContext(Dispatchers.Main) {
                if (currentId == null) runCatching { toggle(next.id, next.path, scope) }
            }
        }
    }

    fun seek(id: String, fraction: Float) {
        val p = player ?: return
        if (currentId == id) {
            p.seekTo((p.duration * fraction).toInt())
            progress.value = fraction
        }
    }

    fun currentIdOf(): String? = currentId

    fun nextSpeed() {
        speed.value = when (speed.value) {
            1f -> 1.5f
            1.5f -> 2f
            else -> 1f
        }
        Prefs.audioSpeed = speed.value
        player?.let { if (it.isPlaying) applySpeed(it) }
    }

    private fun applySpeed(p: MediaPlayer) {
        runCatching { p.playbackParams = p.playbackParams.setSpeed(speed.value) }
    }

    private fun startTicker(scope: CoroutineScope) {
        ticker?.cancel()
        ticker = scope.launch {
            while (isActive) {
                val p = player ?: break
                runCatching {
                    if (p.duration > 0) progress.value = p.currentPosition.toFloat() / p.duration
                    positionMs.value = p.currentPosition
                }
                delay(120)
            }
        }
    }

    fun release() {
        ticker?.cancel()
        runCatching { player?.release() }
        player = null
        currentId = null
        playingId.value = null
        progress.value = 0f
        positionMs.value = 0
        durationMs.value = 0
    }
}

private var keepAwakeUsers = 0

/** Mantiene la pantalla encendida mientras [active] (grabando o reproduciendo un audio); se saca sola al terminar. */
@Composable
fun KeepScreenOn(active: Boolean) {
    val view = LocalView.current
    DisposableEffect(active, view) {
        if (active) {
            keepAwakeUsers++
            view.keepScreenOn = true
        }
        onDispose {
            if (active) {
                keepAwakeUsers = (keepAwakeUsers - 1).coerceAtLeast(0)
                if (keepAwakeUsers == 0) view.keepScreenOn = false
            }
        }
    }
}

fun formatSeconds(s: Int) = "%d:%02d".format(s / 60, s % 60)
