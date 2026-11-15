package com.advancedwhatsapp.app

import android.app.Application
import android.app.NotificationChannel
import android.app.NotificationManager
import com.advancedwhatsapp.app.data.Db
import java.io.File

class App : Application() {
    override fun onCreate() {
        super.onCreate()
        instance = this
        Prefs.init(this)
        // El proceso :node no usa base de datos ni canales
        if (isMainProcess()) {
            Db.init(this)
            createChannels()
            // Recordatorios creados desde fechas detectadas: las alarmas se pierden al reiniciar el teléfono
            kotlin.concurrent.thread { runCatching { com.advancedwhatsapp.app.data.Reminders.rescheduleAll() } }
            startUpdateChecks()
        }
    }

    /** Busca versiones nuevas con demora (no frena el arranque) y cada 12 h mientras el proceso viva. */
    private fun startUpdateChecks() {
        kotlin.concurrent.thread(isDaemon = true, name = "update-check") {
            runCatching { Thread.sleep(30_000) }
            while (true) {
                runCatching { com.advancedwhatsapp.app.data.Updater.autoCheck() }
                try { Thread.sleep(com.advancedwhatsapp.app.data.Updater.CHECK_INTERVAL_MS) } catch (e: InterruptedException) { return@thread }
            }
        }
    }

    private fun isMainProcess(): Boolean = getProcessName() == packageName

    private fun createChannels() {
        val nm = getSystemService(NotificationManager::class.java)
        nm.createNotificationChannels(
            listOf(
                NotificationChannel(CH_SERVICE, "Conexión", NotificationManager.IMPORTANCE_MIN).apply {
                    description = "Aviso fijo mientras la app mantiene la conexión con WhatsApp"
                    setShowBadge(false)
                },
                NotificationChannel(CH_MESSAGES, "Mensajes", NotificationManager.IMPORTANCE_HIGH),
                NotificationChannel(CH_UPDATES, "Actualizaciones", NotificationManager.IMPORTANCE_LOW).apply {
                    description = "Avisa cuando hay una versión nueva de la app"
                },
                NotificationChannel(CH_CALLS, "Llamadas", NotificationManager.IMPORTANCE_HIGH).apply {
                    description = "Avisa de las llamadas; se atienden en la app oficial"
                },
            )
        )
    }

    companion object {
        const val CH_SERVICE = "conexion"
        const val CH_MESSAGES = "mensajes"
        const val CH_CALLS = "llamadas"
        const val CH_UPDATES = "actualizaciones"
        const val GATEWAY_PORT = 5016
        const val WEBHOOK_PORT = 5015

        lateinit var instance: App
            private set

        /** Carpeta compartida con el gateway (equivale a ~/.magsapp en la PC). */
        val dataDir: File get() = File(instance.filesDir, "data").apply { mkdirs() }
        val mediaDir: File get() = File(dataDir, "media").apply { mkdirs() }
        val sentDir: File get() = File(mediaDir, "enviados").apply { mkdirs() }
        val analysisDir: File get() = File(dataDir, "analisis").apply { mkdirs() }
    }
}
