package com.advancedwhatsapp.app.gateway

import android.app.Notification
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.BroadcastReceiver
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.content.ServiceConnection
import android.content.pm.ServiceInfo
import android.os.IBinder
import android.os.PowerManager
import android.os.Process
import android.util.Log
import com.advancedwhatsapp.app.App
import com.advancedwhatsapp.app.AppState
import com.advancedwhatsapp.app.Prefs
import com.advancedwhatsapp.app.R
import com.advancedwhatsapp.app.data.Db
import com.advancedwhatsapp.app.data.LinkPreviews
import com.advancedwhatsapp.app.data.MediaCleanup
import com.advancedwhatsapp.app.data.Thumbs
import com.advancedwhatsapp.app.widget.UnreadWidget
import com.advancedwhatsapp.app.notify.Notifier
import com.advancedwhatsapp.app.ui.MainActivity
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.collectLatest
import kotlinx.coroutines.launch
import java.io.File

/**
 * Servicio en primer plano: recibe los eventos del gateway, lo vigila y mantiene la app viva con la pantalla apagada.
 */
class GatewayService : Service() {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private val webhook = WebhookServer()
    private var wakeLock: PowerManager.WakeLock? = null
    private var bound = false

    private val connection = object : ServiceConnection {
        override fun onServiceConnected(name: ComponentName?, service: IBinder?) = Unit

        // Android vuelve a crear el servicio solo mientras siga el bind
        override fun onServiceDisconnected(name: ComponentName?) {
            Log.w(TAG, "El proceso de Node terminó; Android lo reinicia.")
        }
    }

    override fun onCreate() {
        super.onCreate()
        startForeground(
            NOTIFICATION_ID, buildNotification("Conectando…"),
            ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE,
        )
        // Al iniciar: el globo del ícono se recalcula desde lo realmente sin leer
        scope.launch { runCatching { Db.recomputeUnread(); Notifier.sync() } }
        runCatching { webhook.start() }.onFailure { Log.e(TAG, "No se pudo abrir el webhook", it) }
        bindNode()
        applyWakeLock()
        scope.launch { watchdog() }
        // Widget «Sin leer»: se actualiza cuando se calma el flujo de cambios (el delay en collectLatest hace de debounce)
        scope.launch { Db.changes.collectLatest { delay(1500); UnreadWidget.refresh(applicationContext) } }
        // Mantenimiento: limpieza automática de multimedia (si está activada) y poda de miniaturas y previews viejos
        scope.launch {
            delay(60_000)
            runCatching { MediaCleanup.runAutoIfDue() }
            runCatching { Thumbs.prune() }
            runCatching { LinkPreviews.prune() }
        }
        scope.launch {
            AppState.connection.collectLatest { c ->
                getSystemService(NotificationManager::class.java)
                    .notify(NOTIFICATION_ID, buildNotification(c.label))
            }
        }
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == ACTION_APPLY_SETTINGS) applyWakeLock()
        return START_STICKY
    }

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onDestroy() {
        scope.cancel()
        webhook.stop()
        if (bound) runCatching { unbindService(connection) }
        wakeLock?.takeIf { it.isHeld }?.release()
        super.onDestroy()
    }

    private fun bindNode() {
        val i = Intent(this, NodeService::class.java).putExtra(NodeService.EXTRA_TOKEN, Prefs.token)
        bound = bindService(i, connection, BIND_AUTO_CREATE or BIND_IMPORTANT)
    }

    private fun applyWakeLock() {
        if (Prefs.keepCpuAwake) {
            if (wakeLock == null) {
                wakeLock = getSystemService(PowerManager::class.java)
                    .newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "advancedwhatsapp:conexion")
                    .apply { setReferenceCounted(false) }
            }
            if (wakeLock?.isHeld != true) wakeLock?.acquire()
        } else {
            wakeLock?.takeIf { it.isHeld }?.release()
        }
    }

    /** Refresca el estado y, si el gateway dejó de responder, mata su proceso para que se reinicie. */
    private suspend fun watchdog() {
        var failures = 0
        delay(3000)
        while (true) {
            try {
                val st = GatewayClient.get("/status")
                failures = 0
                val qr = if (st.optBoolean("has_qr")) runCatching { GatewayClient.get("/qr") }.getOrNull() else null
                st.put("qr_data_url", qr?.optString("qr_data_url"))
                AppState.updateConnection(st)
            } catch (e: Exception) {
                failures++
                AppState.markGatewayDown()
                if (failures >= WATCHDOG_MAX_FAILURES) {
                    failures = 0
                    killNode()
                }
            }
            delay(if (AppState.connection.value.status == "connected") 30_000 else 4_000)
        }
    }

    private fun killNode() {
        val pid = runCatching { File(App.dataDir, NodeService.PID_FILE).readText().trim().toInt() }.getOrNull() ?: return
        Log.w(TAG, "El gateway no responde; se reinicia el proceso $pid.")
        Process.killProcess(pid)
    }

    private fun buildNotification(text: String): Notification {
        val open = PendingIntent.getActivity(
            this, 0, Intent(this, MainActivity::class.java), PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )
        return Notification.Builder(this, App.CH_SERVICE)
            .setSmallIcon(R.drawable.ic_notification)
            .setColor(Notifier.ACCENT)
            .setContentTitle("Advanced WhatsApp")
            .setContentText(text)
            .setContentIntent(open)
            .setOngoing(true)
            .setShowWhen(false)
            .setCategory(Notification.CATEGORY_SERVICE)
            .build()
    }

    companion object {
        const val ACTION_APPLY_SETTINGS = "aplicar_ajustes"
        private const val NOTIFICATION_ID = 1
        private const val WATCHDOG_MAX_FAILURES = 15
        private const val TAG = "GatewayService"

        fun start(c: Context, action: String? = null) {
            c.startForegroundService(Intent(c, GatewayService::class.java).setAction(action))
        }
    }
}

class BootReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        GatewayService.start(context)
    }
}
