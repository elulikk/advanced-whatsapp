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
