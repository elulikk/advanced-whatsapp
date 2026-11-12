package com.advancedwhatsapp.app.widget

import android.app.PendingIntent
import android.appwidget.AppWidgetManager
import android.appwidget.AppWidgetProvider
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.widget.RemoteViews
import android.widget.RemoteViewsService
import android.widget.RemoteViewsService.RemoteViewsFactory
import com.advancedwhatsapp.app.Prefs
import com.advancedwhatsapp.app.R
import com.advancedwhatsapp.app.data.ChatFilter
import com.advancedwhatsapp.app.data.ChatLock
import com.advancedwhatsapp.app.data.Db
import com.advancedwhatsapp.app.notify.Notifier
import com.advancedwhatsapp.app.ui.MainActivity

/**
 * Widget de pantalla de inicio con los chats que tienen mensajes sin leer. RemoteViews con una lista (lo más liviano:
 * sin dependencias nuevas). Respeta los silenciados (no aparecen), los grupos (según el ajuste de notificaciones de
 * grupos) y los bloqueados (aparecen sin mostrar el mensaje). Tocar un chat lo abre.
 */
class UnreadWidget : AppWidgetProvider() {
    override fun onUpdate(context: Context, manager: AppWidgetManager, ids: IntArray) {
        for (id in ids) manager.updateAppWidget(id, build(context, id))
        manager.notifyAppWidgetViewDataChanged(ids, R.id.widget_list)
    }

    companion object {
        private const val MAX_ITEMS = 30

        private fun build(ctx: Context, widgetId: Int): RemoteViews {
            val views = RemoteViews(ctx.packageName, R.layout.widget_unread)
            val adapter = Intent(ctx, UnreadWidgetService::class.java).apply {
                putExtra(AppWidgetManager.EXTRA_APPWIDGET_ID, widgetId)
                data = Uri.parse(toUri(Intent.URI_INTENT_SCHEME))      // un intent distinto por widget
            }
            views.setRemoteAdapter(R.id.widget_list, adapter)
            views.setEmptyView(R.id.widget_list, R.id.widget_empty)
            val flags = PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_MUTABLE    // el chat se agrega al tocar cada fila
            val template = Intent(ctx, MainActivity::class.java).setAction(Intent.ACTION_VIEW)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_SINGLE_TOP)
            views.setPendingIntentTemplate(R.id.widget_list, PendingIntent.getActivity(ctx, 0, template, flags))
            val open = PendingIntent.getActivity(
                ctx, 1, Intent(ctx, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK),
                PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
            )
            views.setOnClickPendingIntent(R.id.widget_title, open)
            return views
        }

        /** Avisa a los widgets puestos que cambió la lista. Barato: no hace nada si no hay ninguno. */
        fun refresh(ctx: Context) {
            runCatching {
                val manager = AppWidgetManager.getInstance(ctx)
                val ids = manager.getAppWidgetIds(ComponentName(ctx, UnreadWidget::class.java))
                if (ids.isNotEmpty()) manager.notifyAppWidgetViewDataChanged(ids, R.id.widget_list)
            }
        }

        /** Las filas a mostrar, ya filtradas. */
        fun rows(): List<Row> = Db.getChats(filter = ChatFilter.Unread, limit = 200)
            .filter { !it.isMuted && (!it.isGroup || Prefs.notifyGroups) }
            .take(MAX_ITEMS)
            .map {
                val locked = ChatLock.isLocked(it.jid)
                Row(it.jid, it.name.ifEmpty { it.phone.orEmpty() }, if (locked) ChatLock.LOCKED_NOTIFICATION else it.lastText, it.unread.coerceAtLeast(1))
            }
    }

    data class Row(val jid: String, val name: String, val text: String, val unread: Int)
}

class UnreadWidgetService : RemoteViewsService() {
    override fun onGetViewFactory(intent: Intent): RemoteViewsFactory = Factory(applicationContext)

    private class Factory(private val ctx: Context) : RemoteViewsFactory {
        private var rows: List<UnreadWidget.Row> = emptyList()

        override fun onCreate() = Unit
        override fun onDataSetChanged() { rows = runCatching { UnreadWidget.rows() }.getOrDefault(emptyList()) }
        override fun onDestroy() { rows = emptyList() }
        override fun getCount(): Int = rows.size
        override fun getViewAt(position: Int): RemoteViews? {
            val r = rows.getOrNull(position) ?: return null
            return RemoteViews(ctx.packageName, R.layout.widget_unread_item).apply {
                setTextViewText(R.id.item_name, r.name)
                setTextViewText(R.id.item_text, r.text)
                setTextViewText(R.id.item_count, if (r.unread > 99) "99+" else r.unread.toString())
                setOnClickFillInIntent(R.id.item_root, Intent().putExtra(Notifier.EXTRA_CHAT, r.jid))
            }
        }
        override fun getLoadingView(): RemoteViews? = null
        override fun getViewTypeCount(): Int = 1
        override fun getItemId(position: Int): Long = rows.getOrNull(position)?.jid?.hashCode()?.toLong() ?: position.toLong()
        override fun hasStableIds(): Boolean = true
    }
}
