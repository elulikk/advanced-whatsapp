package com.advancedwhatsapp.app.notify

import android.app.Notification
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Person
import android.app.RemoteInput
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.graphics.BitmapFactory
import android.graphics.drawable.Icon
import com.advancedwhatsapp.app.App
import com.advancedwhatsapp.app.AppState
import com.advancedwhatsapp.app.Prefs
import com.advancedwhatsapp.app.R
import com.advancedwhatsapp.app.data.Actions
import com.advancedwhatsapp.app.data.Db
import com.advancedwhatsapp.app.ui.MainActivity
import org.json.JSONObject
import kotlin.concurrent.thread

/** Una notificación por chat, con responder y marcar como leído. No avisa del chat que estás mirando ni de los silenciados. */
object Notifier {
    private val ctx get() = App.instance
    private val nm get() = ctx.getSystemService(NotificationManager::class.java)
    private fun idFor(jid: String) = jid.hashCode() and 0x7fffffff or 0x10000
    const val EXTRA_CHAT = "chat_jid"

    fun message(jid: String) {
        if (!Prefs.notifications) return
        if (AppState.inForeground && AppState.activeChat == jid) return
        val chat = Db.getChat(jid) ?: return
        if (chat.isMuted) return
        val pending = Db.unreadMessages(jid).filter { it.mediaType != "revoked" }
        if (pending.isEmpty()) return

        val avatar = chat.avatarPath?.let { p -> runCatching { BitmapFactory.decodeFile(p) }.getOrNull() }
        val person = Person.Builder().setName(chat.name).setKey(jid)
            .apply { if (avatar != null) setIcon(Icon.createWithBitmap(avatar)) }.build()
        val me = Person.Builder().setName("Yo").build()
        val style = Notification.MessagingStyle(me)
        for (m in pending) {
            val text = Db.preview(m.mediaType, m.text).ifEmpty { "Mensaje" }
            style.addMessage(Notification.MessagingStyle.Message(text, m.timestamp * 1000, person))
        }

        val reply = Notification.Action.Builder(
            Icon.createWithResource(ctx, R.drawable.ic_notification), "Responder",
            broadcast(jid, ACTION_REPLY, PendingIntent.FLAG_MUTABLE),
        ).addRemoteInput(RemoteInput.Builder(KEY_REPLY).setLabel("Responder").build())
            .setAllowGeneratedReplies(true).build()
        val read = Notification.Action.Builder(
            Icon.createWithResource(ctx, R.drawable.ic_notification), "Marcar como leído",
            broadcast(jid, ACTION_READ, PendingIntent.FLAG_IMMUTABLE),
        ).build()

        nm.notify(
            idFor(jid),
            Notification.Builder(ctx, App.CH_MESSAGES)
                .setSmallIcon(R.drawable.ic_notification)
                .setStyle(style)
                .setContentIntent(openChat(jid))
                .setAutoCancel(true)
                .setCategory(Notification.CATEGORY_MESSAGE)
                .setShortcutId(null)
                .addAction(reply)
                .addAction(read)
                .build()
        )
    }

    /** Un mensaje avisado se editó o se borró: la notificación no puede seguir mostrando el texto viejo. */
    fun refresh(jid: String) {
        val id = idFor(jid)
        if (nm.activeNotifications.none { it.id == id }) return
        if (Db.unreadMessages(jid).none { it.mediaType != "revoked" }) nm.cancel(id) else message(jid)
    }

    fun call(d: JSONObject) {
        val jid = d.optString("chat_jid").ifEmpty { return }
        val id = idFor("call_$jid")
        when (d.optString("status")) {
            "offer", "ringing" -> {
                if (d.optBoolean("offline") || !Prefs.notifications) return
                val kind = if (d.optBoolean("is_video")) "Videollamada" else "Llamada"
                nm.notify(
                    id,
                    Notification.Builder(ctx, App.CH_CALLS)
                        .setSmallIcon(R.drawable.ic_notification)
                        .setContentTitle("$kind de ${d.optString("name")}")
                        .setContentText("Atendela en la app oficial de WhatsApp")
                        .setContentIntent(openChat(jid))
                        .setCategory(Notification.CATEGORY_CALL)
                        .setAutoCancel(true)
                        .build()
                )
            }
            else -> nm.cancel(id)
        }
    }

    fun cancel(jid: String) = nm.cancel(idFor(jid))

    private fun openChat(jid: String): PendingIntent = PendingIntent.getActivity(
        ctx, idFor(jid),
        Intent(ctx, MainActivity::class.java).putExtra(EXTRA_CHAT, jid).addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP),
        PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
    )

    private fun broadcast(jid: String, action: String, mutability: Int): PendingIntent = PendingIntent.getBroadcast(
        ctx, (action + jid).hashCode(),
        Intent(ctx, NotificationReceiver::class.java).setAction(action).putExtra(EXTRA_CHAT, jid),
        mutability or PendingIntent.FLAG_UPDATE_CURRENT,
    )

    const val ACTION_REPLY = "responder"
    const val ACTION_READ = "leido"
    const val KEY_REPLY = "respuesta"
}

class NotificationReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        val jid = intent.getStringExtra(Notifier.EXTRA_CHAT) ?: return
        val text = RemoteInput.getResultsFromIntent(intent)?.getCharSequence(Notifier.KEY_REPLY)?.toString()
        val done = goAsync()
        thread {
            try {
                if (intent.action == Notifier.ACTION_REPLY && !text.isNullOrBlank()) {
                    runCatching { Actions.sendText(jid, text) }
                }
                runCatching { Actions.markRead(jid) }
                Notifier.cancel(jid)
            } finally {
                done.finish()
            }
        }
    }
}
