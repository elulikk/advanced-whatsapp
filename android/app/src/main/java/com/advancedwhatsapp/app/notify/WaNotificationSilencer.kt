package com.advancedwhatsapp.app.notify

import android.app.Notification
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.provider.Settings
import android.service.notification.NotificationListenerService
import android.service.notification.StatusBarNotification
import android.util.Log
import androidx.core.app.NotificationManagerCompat
import com.advancedwhatsapp.app.AppState
import com.advancedwhatsapp.app.Prefs
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.launch

/**
 * Reemplaza las notificaciones de WhatsApp (y WhatsApp Business) por las de Advanced WhatsApp: las borra apenas
 * aparecen, pero solo mientras AW está conectado. Si AW se desconecta, las de WhatsApp quedan y no te perdés nada.
 * No toca llamadas (en curso ni perdidas) ni servicios en primer plano de WhatsApp. Las fijas que no se pueden borrar
 * (reproductor de audios, progreso de envío) se posponen. Necesita "Acceso a notificaciones".
 */
class WaNotificationSilencer : NotificationListenerService() {
    private var scope: CoroutineScope? = null

    override fun onListenerConnected() {
        val s = CoroutineScope(SupervisorJob() + Dispatchers.Main)
        scope = s
        // Al conectar AW (o al activar el permiso) se limpian las que ya estaban
        s.launch { AppState.connection.collect { if (it.isConnected) sweep() } }
    }

    override fun onListenerDisconnected() {
        scope?.cancel()
        scope = null
    }

    override fun onNotificationPosted(sbn: StatusBarNotification) {
        if (shouldHide(sbn)) hide(sbn)
    }

    private fun sweep() {
        if (!active()) return
        runCatching { activeNotifications?.filter(::shouldHide)?.forEach(::hide) }
    }

    /**
     * Android no deja que un listener borre las notificaciones fijas (ej. el reproductor de audios de WhatsApp Business,
     * que queda clavado en la pantalla de bloqueo): a esas se las posterga. Cuando vuelven, pasan otra vez por acá.
     */
    private fun hide(sbn: StatusBarNotification) {
        runCatching {
            if (sbn.isOngoing) snoozeNotification(sbn.key, SNOOZE_MS) else cancelNotification(sbn.key)
        }.onFailure { Log.w(TAG, "No se pudo ocultar: ${it.message}") }
    }

    private fun active() = Prefs.replaceWaNotifications && AppState.connection.value.isConnected

    private fun shouldHide(sbn: StatusBarNotification): Boolean {
        if (sbn.packageName !in WHATSAPP_PACKAGES || !active()) return false
        val n = sbn.notification
        if (n.flags and Notification.FLAG_FOREGROUND_SERVICE != 0) return false
        if (n.category == Notification.CATEGORY_CALL || n.category == "missed_call") return false   // AW no avisa llamadas
        return true
    }

    companion object {
        private const val TAG = "WaSilencer"
        private const val SNOOZE_MS = 6 * 60 * 60 * 1000L   // al volver se re-evalúa (si AW está desconectado, queda)
        val WHATSAPP_PACKAGES = setOf("com.whatsapp", "com.whatsapp.w4b")

        fun hasAccess(ctx: Context): Boolean = NotificationManagerCompat.getEnabledListenerPackages(ctx).contains(ctx.packageName)

        /** Abre la pantalla del sistema para dar el permiso (directo a esta app si Android lo permite). */
        fun openAccessSettings(ctx: Context) {
            val component = ComponentName(ctx, WaNotificationSilencer::class.java).flattenToString()
            val direct = Intent("android.settings.NOTIFICATION_LISTENER_DETAIL_SETTINGS")
                .putExtra("android.provider.extra.NOTIFICATION_LISTENER_COMPONENT_NAME", component)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            val list = Intent(Settings.ACTION_NOTIFICATION_LISTENER_SETTINGS).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            runCatching { ctx.startActivity(direct) }.onFailure { runCatching { ctx.startActivity(list) } }
        }
    }
}
