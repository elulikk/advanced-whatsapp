package com.advancedwhatsapp.app.notify

import android.app.Notification
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Person
import android.app.RemoteInput
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.pm.ShortcutInfo
import android.content.pm.ShortcutManager
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.Typeface
import android.graphics.drawable.Icon
import android.media.ThumbnailUtils
import android.os.Build
import com.advancedwhatsapp.app.App
import com.advancedwhatsapp.app.AppState
import com.advancedwhatsapp.app.Prefs
import com.advancedwhatsapp.app.R
import com.advancedwhatsapp.app.data.Actions
import com.advancedwhatsapp.app.data.Chat
import com.advancedwhatsapp.app.data.ChatLock
import com.advancedwhatsapp.app.data.Db
import com.advancedwhatsapp.app.data.Photos
import com.advancedwhatsapp.app.ui.MainActivity
import org.json.JSONObject
import kotlin.concurrent.thread

/** Una notificación por chat, con responder y marcar como leído. No avisa del chat que estás mirando ni de los silenciados. */
object Notifier {
    private val ctx get() = App.instance
    private val nm get() = ctx.getSystemService(NotificationManager::class.java)
    private fun idFor(jid: String) = jid.hashCode() and 0x7fffffff or 0x10000
    const val EXTRA_CHAT = "chat_jid"

    /** alert = false actualiza lo que ya se ve sin volver a sonar. */
    fun message(jid: String, alert: Boolean = true) {
        if (!Prefs.notifications) return
        if (AppState.inForeground && AppState.activeChat == jid) return
        val chat = Db.getChat(jid) ?: return
        if (chat.isMuted) return
        val pending = Db.unreadMessages(jid).filter { it.mediaType != "revoked" }
        if (pending.isEmpty()) return

        val icon = Icon.createWithBitmap(avatarBitmap(chat))
        val person = Person.Builder().setName(chat.name).setKey(jid).setIcon(icon).build()
        publishShortcut(jid, chat.name, person, icon)
        val me = Person.Builder().setName("Yo").build()
        val style = Notification.MessagingStyle(me)
        if (chat.isGroup) style.setConversationTitle(chat.name).setGroupConversation(true)
        val members = HashMap<String, Person>()
        val locked = ChatLock.isLocked(jid)
        for (m in if (locked) pending.takeLast(1) else pending) {
            val text = if (locked) ChatLock.LOCKED_NOTIFICATION else Db.preview(m.mediaType, m.text).ifEmpty { "Mensaje" }
            val sender = if (!chat.isGroup) person else members.getOrPut(m.senderJid ?: m.senderName.orEmpty()) {
                Person.Builder().setName(m.senderName?.ifEmpty { null } ?: "Alguien").setKey(m.senderJid).build()
            }
            style.addMessage(Notification.MessagingStyle.Message(text, m.timestamp * 1000, sender))
        }

        val reply = Notification.Action.Builder(
            Icon.createWithResource(ctx, R.drawable.ic_notification), "Responder",
            broadcast(jid, ACTION_REPLY, PendingIntent.FLAG_MUTABLE),
        ).addRemoteInput(RemoteInput.Builder(KEY_REPLY).setLabel("Responder").build())
            .setAllowGeneratedReplies(true)
            .setSemanticAction(Notification.Action.SEMANTIC_ACTION_REPLY).build()
        val read = Notification.Action.Builder(
            Icon.createWithResource(ctx, R.drawable.ic_notification), "Marcar como leído",
            broadcast(jid, ACTION_READ, PendingIntent.FLAG_IMMUTABLE),
        ).setSemanticAction(Notification.Action.SEMANTIC_ACTION_MARK_AS_READ).build()

        nm.notify(
            idFor(jid),
            Notification.Builder(ctx, App.CH_MESSAGES)
                .setSmallIcon(R.drawable.ic_notification)
                .setColor(ACCENT)
                .setStyle(style)
                .setContentIntent(openChat(jid))
                .setAutoCancel(true)
                .setCategory(Notification.CATEGORY_MESSAGE)
                .setShortcutId(jid)
                .setNumber(if (chat.unread > 0) chat.unread else pending.size)
                .setOnlyAlertOnce(!alert)
                .addAction(reply)
                .addAction(read)
                .build()
        )
    }

    /** Un mensaje avisado se editó o se borró: la notificación no puede seguir mostrando el texto viejo. */
    fun refresh(jid: String) {
        val id = idFor(jid)
        if (nm.activeNotifications.none { it.id == id }) return
        if (Db.unreadMessages(jid).none { it.mediaType != "revoked" }) nm.cancel(id) else message(jid, alert = false)
    }

    /** Reemplaza la notificación del chat: Android deja de mostrar "enviando" recién cuando esta cambia. */
    fun replyFailed(jid: String, text: String) {
        val name = Db.getChat(jid)?.name.orEmpty()
        nm.notify(
            idFor(jid),
            Notification.Builder(ctx, App.CH_MESSAGES)
                .setSmallIcon(R.drawable.ic_notification)
                .setColor(ACCENT)
                .setContentTitle("No se envió tu respuesta a $name")
                .setContentText(text)
                .setStyle(Notification.BigTextStyle().bigText(text))
                .setContentIntent(openChat(jid))
                .setAutoCancel(true)
                .setOnlyAlertOnce(true)
                .build()
        )
    }

    /** Quien programó un mensaje confía en que salió: si no salió, hay que avisarle. */
    fun scheduledFailed(d: JSONObject) {
        val jid = d.optString("to").ifEmpty { return }
        fun text(k: String) = if (d.isNull(k)) "" else d.optString(k)
        val name = text("chat_name").ifEmpty { Db.getChat(jid)?.name.orEmpty() }
        val preview = text("text").ifEmpty { text("source_name") }.ifEmpty { "Mensaje" }
        nm.notify(
            idFor("programado_${d.optString("id")}"),
            Notification.Builder(ctx, App.CH_MESSAGES)
                .setSmallIcon(R.drawable.ic_notification)
                .setColor(ACCENT)
                .setContentTitle("No se envió tu mensaje programado a $name")
                .setContentText(preview)
                .setStyle(Notification.BigTextStyle().bigText("$preview\n${text("error")}".trim()))
                .setContentIntent(openChat(jid))
                .setAutoCancel(true)
                .build()
        )
    }

    /**
     * Android 11 en adelante solo trata como conversación (sección propia, prioridad, foto del contacto)
     * a las notificaciones atadas a un acceso directo de larga duración.
     */
    private fun publishShortcut(jid: String, name: String, person: Person, icon: Icon) {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.R) return
        val open = Intent(ctx, MainActivity::class.java).setAction(Intent.ACTION_VIEW).putExtra(EXTRA_CHAT, jid)
        val shortcut = ShortcutInfo.Builder(ctx, jid)
            .setShortLabel(name.ifEmpty { "Chat" })
            .setLongLived(true)
            .setPerson(person)
            .setIcon(icon)
            .setIntent(open)
            .setCategories(setOf(ShortcutInfo.SHORTCUT_CATEGORY_CONVERSATION))
            .build()
        runCatching { ctx.getSystemService(ShortcutManager::class.java).pushDynamicShortcut(shortcut) }
    }

    /** Foto achicada (la original pesa de más para una notificación) o, si no hay, la inicial del contacto. */
    private fun avatarBitmap(chat: Chat): Bitmap =
        chat.avatarPath?.let { p -> runCatching { decodeAvatar(p) }.getOrNull() } ?: letterAvatar(chat.name, chat.jid)

    private fun decodeAvatar(path: String): Bitmap? {
        val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
        BitmapFactory.decodeFile(path, bounds)
        if (bounds.outWidth <= 0) return null
        val options = BitmapFactory.Options().apply { inSampleSize = Photos.sampleSize(bounds.outWidth, bounds.outHeight, AVATAR_PX) }
        val raw = BitmapFactory.decodeFile(path, options) ?: return null
        return ThumbnailUtils.extractThumbnail(raw, AVATAR_PX, AVATAR_PX, ThumbnailUtils.OPTIONS_RECYCLE_INPUT)
    }

    private fun letterAvatar(name: String, jid: String): Bitmap {
        val bitmap = Bitmap.createBitmap(AVATAR_PX, AVATAR_PX, Bitmap.Config.ARGB_8888)
        val canvas = Canvas(bitmap)
        canvas.drawColor(AVATAR_COLORS[(jid.hashCode() and 0x7fffffff) % AVATAR_COLORS.size])
        val letter = name.firstOrNull { it.isLetterOrDigit() }?.uppercase() ?: "?"
        val paint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
            color = Color.WHITE
            textSize = AVATAR_PX * 0.46f
            textAlign = Paint.Align.CENTER
            typeface = Typeface.DEFAULT_BOLD
        }
        canvas.drawText(letter, AVATAR_PX / 2f, AVATAR_PX / 2f - (paint.descent() + paint.ascent()) / 2, paint)
        return bitmap
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
                        .setColor(ACCENT)
                        .setContentTitle("$kind de ${d.optString("name")}")
                        .setContentText("Atendela en la app oficial de WhatsApp")
                        .setContentIntent(openChat(jid))
                        .setCategory(Notification.CATEGORY_CALL)
                        .setAutoCancel(true)
                        .setTimeoutAfter(CALL_TIMEOUT_MS)   // por si nunca llega el aviso de que terminó
                        .build()
                )
            }
            else -> nm.cancel(id)
        }
    }

    fun cancel(jid: String) = nm.cancel(idFor(jid))

    /**
     * El globo del ícono sale de las notificaciones que siguen puestas: se quitan las de los chats ya leídos y se
     * actualiza el número de las demás. Va al abrir la app, al leer un chat y al leer desde el teléfono.
     */
    fun sync() {
        runCatching {
            for (sbn in nm.activeNotifications) {
                if (sbn.notification.channelId != App.CH_MESSAGES) continue
                val jid = sbn.notification.shortcutId ?: continue   // las de aviso de falla no llevan atajo: se dejan
                refresh(jid)
            }
        }
    }

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
    const val ACCENT = 0xFF1FA37F.toInt()
    private const val AVATAR_PX = 192
    private const val CALL_TIMEOUT_MS = 90_000L
    private val AVATAR_COLORS = intArrayOf(
        0xFF1FA37F.toInt(), 0xFF2F7FBF.toInt(), 0xFF8E5BC2.toInt(), 0xFFC2566F.toInt(), 0xFFD9822B.toInt(), 0xFF4C8C5A.toInt(),
    )
}

class NotificationReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        val jid = intent.getStringExtra(Notifier.EXTRA_CHAT) ?: return
        val text = RemoteInput.getResultsFromIntent(intent)?.getCharSequence(Notifier.KEY_REPLY)?.toString()
        val done = goAsync()
        thread {
            try {
                if (intent.action == Notifier.ACTION_REPLY && !text.isNullOrBlank()) {
                    if (runCatching { Actions.sendText(jid, text) }.isFailure) {
                        Notifier.replyFailed(jid, text)   // lo que escribiste no puede perderse sin avisar
                        return@thread
                    }
                }
                runCatching { Actions.markRead(jid) }
                Notifier.cancel(jid)
            } finally {
                done.finish()
            }
        }
    }
}
