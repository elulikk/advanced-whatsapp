package com.advancedwhatsapp.app.ui

import android.app.DatePickerDialog
import android.app.TimePickerDialog
import android.content.Context
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.automirrored.filled.Send
import androidx.compose.material.icons.filled.AttachFile
import androidx.compose.material.icons.filled.ChatBubbleOutline
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material.icons.filled.Edit
import androidx.compose.material.icons.filled.Image
import androidx.compose.material.icons.filled.Mic
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material.icons.filled.Videocam
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.compose.LocalLifecycleOwner
import androidx.lifecycle.repeatOnLifecycle
import com.advancedwhatsapp.app.AppState
import com.advancedwhatsapp.app.data.Scheduled
import com.advancedwhatsapp.app.data.ScheduledItem
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.collectLatest
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.text.SimpleDateFormat
import java.util.Calendar
import java.util.Date
import java.util.Locale

/** Hoy 09:00 · Mañana 09:00 · lun 10/11 09:00 */
fun scheduleLabel(ts: Long): String {
    val tomorrow = Calendar.getInstance().apply { add(Calendar.DAY_OF_YEAR, 1) }.timeInMillis / 1000
    return when (dayKey(ts)) {
        dayKey(System.currentTimeMillis() / 1000) -> "Hoy ${timeOf(ts)}"
        dayKey(tomorrow) -> "Mañana ${timeOf(ts)}"
        else -> SimpleDateFormat("EEE dd/MM HH:mm", Locale.getDefault()).format(Date(ts * 1000))
    }
}

/** Pide día y después hora. Los selectores del sistema no suman peso al APK. [initial] en epoch segundos. */
fun pickDateTime(c: Context, initial: Long?, onPicked: (Long) -> Unit) {
    val start = Calendar.getInstance()
    if (initial != null && initial * 1000 > start.timeInMillis) {
        start.timeInMillis = initial * 1000
    } else {
        start.add(Calendar.HOUR_OF_DAY, 1)
        start.set(Calendar.MINUTE, 0)
    }
    val date = DatePickerDialog(c, { _, year, month, day ->
        TimePickerDialog(c, { _, hour, minute ->
            val out = Calendar.getInstance().apply {
                set(year, month, day, hour, minute, 0)
                set(Calendar.MILLISECOND, 0)
            }
            onPicked(out.timeInMillis / 1000)
        }, start.get(Calendar.HOUR_OF_DAY), start.get(Calendar.MINUTE), true).show()
    }, start.get(Calendar.YEAR), start.get(Calendar.MONTH), start.get(Calendar.DAY_OF_MONTH))
    date.datePicker.minDate = System.currentTimeMillis() - 1000
    date.show()
}

/** Lo que hay en cola: de un chat o, con [chat] nulo, de todos. */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun ScheduledScreen(chat: String?, onBack: () -> Unit) {
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    val owner = LocalLifecycleOwner.current
    val snackbar = remember { SnackbarHostState() }
    var items by remember { mutableStateOf<List<ScheduledItem>?>(null) }
    var editing by remember { mutableStateOf<ScheduledItem?>(null) }

    LaunchedEffect(owner) {
        owner.lifecycle.repeatOnLifecycle(Lifecycle.State.STARTED) {
            AppState.scheduledEvents.collectLatest {
                val fresh = withContext(Dispatchers.IO) { runCatching { Scheduled.list() }.getOrNull() }
                items = (fresh ?: items.orEmpty()).filter { chat == null || it.to == chat }
            }
        }
    }

    fun act(block: () -> Unit) {
        scope.launch(Dispatchers.IO) {
            try {
                block()
            } catch (e: Exception) {
                snackbar.showSnackbar(e.message ?: "Ocurrió un error")
            }
        }
    }

    fun reschedule(item: ScheduledItem) {
        pickDateTime(context, item.at) { at ->
            if (at <= System.currentTimeMillis() / 1000) {
                scope.launch { snackbar.showSnackbar("Elegí una fecha y hora futuras.") }
            } else {
                act { Scheduled.update(item.id, at = at) }
            }
        }
    }

    Scaffold(
        snackbarHost = { SnackbarHost(snackbar) },
        topBar = {
            TopAppBar(title = { Text("Mensajes programados") }, navigationIcon = {
                IconButton(onClick = onBack) { Icon(Icons.AutoMirrored.Filled.ArrowBack, "Volver") }
            })
        },
    ) { pad ->
        val all = items
        Column(Modifier.padding(pad).fillMaxSize().background(C.panel)) {
            when {
                all == null -> Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) { CircularProgressIndicator() }
                all.isEmpty() -> Box(Modifier.fillMaxSize().padding(32.dp), contentAlignment = Alignment.Center) {
                    Text(
                        "No hay mensajes programados. En un chat, tocá el clip y elegí «Programar envío».",
                        color = C.text2, textAlign = TextAlign.Center,
                    )
                }
                else -> {
                    val (upcoming, history) = all.partition { it.isActive }
                    LazyColumn {
                        section("Próximos", upcoming.sortedBy { it.at }, chat == null, ::reschedule, { editing = it }, ::act)
                        section("Historial", history.sortedByDescending { it.sentAt ?: it.at }, chat == null, ::reschedule, { editing = it }, ::act)
                    }
                }
            }
        }
    }

    editing?.let { item ->
        EditDialog(item, onDismiss = { editing = null }, onSave = { at, text ->
            editing = null
            act { Scheduled.update(item.id, at = at, text = text) }
        })
    }
}

private fun androidx.compose.foundation.lazy.LazyListScope.section(
    title: String, rows: List<ScheduledItem>, showChat: Boolean, onReschedule: (ScheduledItem) -> Unit,
    onEdit: (ScheduledItem) -> Unit, act: (() -> Unit) -> Unit,
) {
    if (rows.isEmpty()) return
    item(key = title) {
        Text(title, color = C.ember, fontSize = 13.sp, fontWeight = FontWeight.SemiBold, modifier = Modifier.padding(start = 14.dp, top = 12.dp, bottom = 4.dp))
    }
    items(rows, key = { it.id }) { ScheduledRow(it, showChat, onReschedule, onEdit, act) }
}

private fun kindIcon(type: String): ImageVector = when (type) {
    "image" -> Icons.Default.Image
    "video" -> Icons.Default.Videocam
    "audio" -> Icons.Default.Mic
    "document", "sticker" -> Icons.Default.AttachFile
    else -> Icons.Default.ChatBubbleOutline
}

private fun stateLabel(state: String): Pair<String, Color> = when (state) {
    "pending" -> "Programado" to C.accent
    "sending" -> "Enviando…" to C.ember
    "sent" -> "Enviado" to C.text2
    "failed" -> "Falló" to C.danger
    "expired" -> "Vencido" to C.ember
    else -> "Cancelado" to C.text3
}

private fun ScheduledItem.summary(showChat: Boolean): String {
    val kind = when (type) {
        "image" -> "Foto"
        "video" -> "Video"
        "audio" -> "Nota de voz"
        "document" -> sourceName ?: "Documento"
        "sticker" -> "Sticker"
        else -> ""
    }
    val body = listOf(kind, text.replace('\n', ' ')).filter { it.isNotEmpty() }.joinToString(" · ")
    return if (showChat && chatName != null) "$chatName: $body" else body
}

@Composable
private fun ScheduledRow(
    item: ScheduledItem, showChat: Boolean, onReschedule: (ScheduledItem) -> Unit, onEdit: (ScheduledItem) -> Unit, act: (() -> Unit) -> Unit,
) {
    val (label, color) = stateLabel(item.state)
    Row(Modifier.fillMaxWidth().padding(start = 14.dp, end = 4.dp, top = 6.dp, bottom = 6.dp), verticalAlignment = Alignment.CenterVertically) {
        Icon(kindIcon(item.type), null, tint = C.text2, modifier = Modifier.size(24.dp).background(C.raised, CircleShape).padding(4.dp))
        Column(Modifier.padding(start = 12.dp).weight(1f)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(scheduleLabel(item.at), color = C.text, fontWeight = FontWeight.Medium, fontSize = 15.sp)
                Text(label, color = color, fontSize = 12.sp, modifier = Modifier.padding(start = 8.dp))
            }
            item.repeat?.let { Text("↻ $it", color = C.accent, fontSize = 12.sp) }
            Text(item.summary(showChat), color = C.text2, fontSize = 13.sp, maxLines = 2, overflow = TextOverflow.Ellipsis)
            if (item.error != null && item.state != "sent") Text(item.error, color = C.text3, fontSize = 12.sp, maxLines = 2, overflow = TextOverflow.Ellipsis)
        }
        when (item.state) {
            "pending" -> {
                IconButton(onClick = { act { Scheduled.sendNow(item.id) } }) { Icon(Icons.AutoMirrored.Filled.Send, "Enviar ahora", tint = C.accent) }
                IconButton(onClick = { if (item.type == "audio") onReschedule(item) else onEdit(item) }) { Icon(Icons.Default.Edit, "Editar") }
                IconButton(onClick = { act { Scheduled.cancel(item.id) } }) { Icon(Icons.Default.Close, "Cancelar") }
            }
            "failed", "expired", "canceled" -> {
                IconButton(onClick = { onReschedule(item) }) { Icon(Icons.Default.Refresh, "Reprogramar") }
                IconButton(onClick = { act { Scheduled.remove(item.id) } }) { Icon(Icons.Default.Delete, "Quitar") }
            }
            "sent" -> IconButton(onClick = { act { Scheduled.remove(item.id) } }) { Icon(Icons.Default.Delete, "Quitar del historial") }
        }
    }
}

/** Corrige la hora y el texto de un mensaje que todavía no salió. */
@Composable
private fun EditDialog(item: ScheduledItem, onDismiss: () -> Unit, onSave: (Long, String) -> Unit) {
    val context = LocalContext.current
    var text by remember { mutableStateOf(item.text) }
    var at by remember { mutableStateOf(item.at) }
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("Editar mensaje") },
        text = {
            Column {
                TextButton(onClick = { pickDateTime(context, at) { picked -> if (picked > System.currentTimeMillis() / 1000) at = picked } }) {
                    Text(scheduleLabel(at))
                }
                Field(text, { text = it }, if (item.type == "text") "Mensaje" else "Texto (opcional)", minLines = 2)
            }
        },
        confirmButton = {
            TextButton(enabled = item.type != "text" || text.isNotBlank(), onClick = { onSave(at, text) }) { Text("Guardar") }
        },
        dismissButton = { TextButton(onClick = onDismiss) { Text("Cancelar") } },
    )
}
