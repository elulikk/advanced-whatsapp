package com.advancedwhatsapp.app.data

import android.app.AlarmManager
import android.app.Notification
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import com.advancedwhatsapp.app.App
import com.advancedwhatsapp.app.R
import com.advancedwhatsapp.app.notify.Notifier
import com.advancedwhatsapp.app.ui.MainActivity
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.util.UUID

data class Reminder(val id: String, val at: Long, val text: String, val chatJid: String, val chatName: String)

/**
 * Recordatorios locales creados desde una fecha/hora detectada en un mensaje (equivale a app/reminders.py).
 * Se guardan en recordatorios.json (escritura atómica) y se avisan con AlarmManager aunque la app esté cerrada.
 * Se usa una alarma inexacta (setAndAllowWhileIdle): no pide permisos especiales y puede atrasarse unos minutos.
 */
object Reminders {
    private const val STALE_AFTER = 12 * 3600L
    private const val EXTRA_ID = "reminder_id"
    private val file get() = File(App.dataDir, "recordatorios.json")
    private val lock = Any()

    private fun load(): List<Reminder> = runCatching {
        val a = JSONArray(file.readText())
        List(a.length()) { i ->
            val o = a.getJSONObject(i)
            Reminder(o.getString("id"), o.getLong("at"), o.optString("text"), o.optString("chat_jid"), o.optString("chat_name"))
        }
    }.getOrDefault(emptyList())

    private fun save(items: List<Reminder>) {
        val a = JSONArray()
        items.forEach {
            a.put(JSONObject().put("id", it.id).put("at", it.at).put("text", it.text).put("chat_jid", it.chatJid).put("chat_name", it.chatName))
        }
        val tmp = File(file.path + ".tmp")
        tmp.writeText(a.toString())
        if (!tmp.renameTo(file)) {
            file.delete()
            tmp.renameTo(file)
        }
    }

    fun list(): List<Reminder> = synchronized(lock) { load().sortedBy { it.at } }

    fun add(at: Long, text: String, chatJid: String, chatName: String): Reminder {
        val r = Reminder(UUID.randomUUID().toString().take(12), at, text.trim().take(300), chatJid, chatName)
        synchronized(lock) { save(load() + r) }
        schedule(r)
        return r
    }

    fun remove(id: String) {
        synchronized(lock) { save(load().filter { it.id != id }) }
        cancelAlarm(id)
    }

    private fun alarmIntent(id: String): PendingIntent {
        val ctx = App.instance
        return PendingIntent.getBroadcast(
            ctx, id.hashCode(), Intent(ctx, Receiver::class.java).putExtra(EXTRA_ID, id),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )
    }

    private fun schedule(r: Reminder) {
        val am = App.instance.getSystemService(AlarmManager::class.java)
        am.setAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, maxOf(r.at * 1000, System.currentTimeMillis() + 1000), alarmIntent(r.id))
    }

    private fun cancelAlarm(id: String) {
        App.instance.getSystemService(AlarmManager::class.java).cancel(alarmIntent(id))
    }

    /** Al arrancar la app: re-agenda lo pendiente (las alarmas se pierden al reiniciar el teléfono). */
    fun rescheduleAll() {
        list().forEach { r ->
            if (System.currentTimeMillis() / 1000 - r.at > STALE_AFTER) remove(r.id) else schedule(r)
        }
    }

    /** Llamado por la alarma: avisa y borra el recordatorio. */
    fun fire(id: String) {
        val r = synchronized(lock) { load().firstOrNull { it.id == id } } ?: return
        remove(id)
        if (System.currentTimeMillis() / 1000 - r.at > STALE_AFTER) return
        val ctx = App.instance
        val title = "⏰ Recordatorio" + if (r.chatName.isNotBlank()) " · ${r.chatName}" else ""
        val open = PendingIntent.getActivity(
            ctx, id.hashCode(),
            Intent(ctx, MainActivity::class.java).putExtra(Notifier.EXTRA_CHAT, r.chatJid).addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )
        val n = Notification.Builder(ctx, App.CH_MESSAGES)
            .setSmallIcon(R.drawable.ic_notification)
            .setContentTitle(title)
            .setContentText(r.text.ifBlank { "Tenés un recordatorio" })
            .setStyle(Notification.BigTextStyle().bigText(r.text))
            .setContentIntent(open)
            .setAutoCancel(true)
            .setCategory(Notification.CATEGORY_REMINDER)
            .build()
        ctx.getSystemService(NotificationManager::class.java).notify(0x20000 or (id.hashCode() and 0xffff), n)
    }

    class Receiver : BroadcastReceiver() {
        override fun onReceive(context: Context, intent: Intent) {
            val id = intent.getStringExtra(EXTRA_ID) ?: return
            val result = goAsync()
            Thread {
                try {
                    fire(id)
                } finally {
                    result.finish()
                }
            }.start()
        }
    }
}
