package com.advancedwhatsapp.app.ui

import android.Manifest
import android.content.ActivityNotFoundException
import android.content.pm.PackageManager
import android.net.Uri
import androidx.activity.compose.BackHandler
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.PickVisualMediaRequest
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.automirrored.filled.Send
import androidx.compose.material.icons.filled.AttachFile
import androidx.compose.material.icons.filled.AutoAwesome
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material.icons.filled.Mic
import androidx.compose.material.icons.filled.Pause
import androidx.compose.material.icons.filled.PhotoCamera
import androidx.compose.material.icons.filled.Psychology
import androidx.compose.material.icons.filled.Replay
import androidx.compose.material.icons.filled.Schedule
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextField
import androidx.compose.material3.TextFieldDefaults
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.core.content.ContextCompat
import androidx.core.content.FileProvider
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.compose.LocalLifecycleOwner
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.repeatOnLifecycle
import coil.compose.AsyncImage
import com.advancedwhatsapp.app.AppState
import com.advancedwhatsapp.app.Prefs
import com.advancedwhatsapp.app.ai.AiService
import com.advancedwhatsapp.app.ai.AnalysisStore
import com.advancedwhatsapp.app.ai.MessageSummary
import com.advancedwhatsapp.app.ai.ReplyPolisher
import com.advancedwhatsapp.app.data.Actions
import com.advancedwhatsapp.app.data.Chat
import com.advancedwhatsapp.app.data.Db
import com.advancedwhatsapp.app.data.Message
import com.advancedwhatsapp.app.data.Photos
import com.advancedwhatsapp.app.data.Scheduled
import com.advancedwhatsapp.app.notify.Notifier
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.collectLatest
import kotlinx.coroutines.flow.filter
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.io.File

private const val PAGE = 60

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun ChatScreen(jid: String, onBack: () -> Unit, onAnalysis: () -> Unit, onScheduled: () -> Unit) {
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    val owner = LocalLifecycleOwner.current
    val snackbar = remember { SnackbarHostState() }
    var chat by remember { mutableStateOf<Chat?>(null) }
    var messages by remember { mutableStateOf(emptyList<Message>()) }
    var limit by remember { mutableIntStateOf(PAGE) }
    var replyTo by remember { mutableStateOf<Message?>(null) }
    var draft by remember { mutableStateOf("") }
    var showAi by remember { mutableStateOf(false) }
    // Con hora elegida, lo próximo que se mande queda programado en vez de salir ya
    var scheduleAt by rememberSaveable { mutableStateOf<Long?>(null) }
    var scheduledCount by remember { mutableIntStateOf(0) }
    var viewerImage by remember { mutableStateOf<String?>(null) }
    val busyIds = remember { mutableStateOf(setOf<String>()) }
    val listState = rememberLazyListState()
    val presences by AppState.presence.collectAsStateWithLifecycle()
    val presence = presences[jid]

    fun notifyError(text: String?) {
        scope.launch { snackbar.showSnackbar(text ?: "Ocurrió un error") }
    }

    /** Ejecuta en segundo plano y muestra el error, si hay, en pantalla. */
    fun runTask(block: suspend CoroutineScope.() -> Unit) {
        scope.launch(Dispatchers.IO) {
            try {
                block()
            } catch (e: Exception) {
                withContext(Dispatchers.Main) { notifyError(e.message) }
            }
        }
    }

    fun withBusy(id: String, block: suspend () -> Unit) {
        busyIds.value = busyIds.value + id
        runTask {
            try {
                block()
            } finally {
                withContext(Dispatchers.Main) { busyIds.value = busyIds.value - id }
            }
        }
    }

    fun nowSec() = System.currentTimeMillis() / 1000

    fun chooseScheduleTime() = pickDateTime(context, scheduleAt) { at ->
        if (at > nowSec()) scheduleAt = at else notifyError("Elegí una fecha y hora futuras.")
    }

    /** La hora elegida ya pasó: se avisa en vez de mandar el mensaje a destiempo. */
    fun scheduleIsPast(): Boolean {
        val at = scheduleAt ?: return false
        if (at > nowSec()) return false
        notifyError("La hora que elegiste ya pasó. Elegí otra para programar el envío.")
        return true
    }

    fun confirmScheduled(at: Long?) {
        if (at != null) scope.launch { snackbar.showSnackbar("Programado: ${scheduleLabel(at)}") }
    }

    suspend fun reload() {
        val (c, m) = withContext(Dispatchers.IO) { Db.getChat(jid) to Db.getMessages(jid, limit) }
        chat = c
        messages = m
    }

    DisposableEffect(jid) {
        AppState.activeChat = jid
        Notifier.cancel(jid)
        onDispose {
            if (AppState.activeChat == jid) AppState.activeChat = null
            VoicePlayer.release()
        }
    }
    LaunchedEffect(jid, limit) { reload() }
    LaunchedEffect(jid) {
        withContext(Dispatchers.IO) { Actions.chatPresence(jid, subscribe = true) }
    }
    LaunchedEffect(jid, owner) {
        owner.lifecycle.repeatOnLifecycle(Lifecycle.State.STARTED) {
            withContext(Dispatchers.IO) { Actions.markRead(jid) }
            reload()
            Db.changes.filter { it.isEmpty() || it == jid }.collect {
                withContext(Dispatchers.IO) { Actions.markRead(jid) }
                reload()
            }
        }
    }
    LaunchedEffect(jid, owner) {
        owner.lifecycle.repeatOnLifecycle(Lifecycle.State.STARTED) {
            AppState.scheduledEvents.collectLatest {
                withContext(Dispatchers.IO) { runCatching { Scheduled.pendingCount(jid) }.getOrNull() }?.let { scheduledCount = it }
            }
        }
    }
    // Un mensaje nuevo baja la vista si ya estabas abajo; lo que enviás vos, siempre
    val newest = messages.lastOrNull()
    LaunchedEffect(newest?.id) {
        if (newest != null && (newest.fromMe || listState.firstVisibleItemIndex <= 2)) listState.scrollToItem(0)
    }
    // Avisar "escribiendo…" como máximo cada 8 s
    var lastTyping by remember { mutableStateOf(0L) }
    LaunchedEffect(draft) {
        if (draft.isNotEmpty() && System.currentTimeMillis() - lastTyping > 8000) {
            lastTyping = System.currentTimeMillis()
            withContext(Dispatchers.IO) { Actions.chatPresence(jid, state = "composing") }
        }
    }

    // Selectores de archivos: se copian a la app y se envían
    fun sendPicked(uri: Uri?, kind: String?) {
        uri ?: return
        if (scheduleIsPast()) return
        val at = scheduleAt
        val caption = draft.trim()
        val quoted = replyTo
        draft = ""
        replyTo = null
        scheduleAt = null
        runTask {
            val mime = context.contentResolver.getType(uri)
            val (file, name) = Actions.importUri(uri)
            val k = kind ?: if (mime?.startsWith("video/") == true) "video" else "image"
            Actions.sendFile(jid, file, k, caption, name, mime, quoted, at)
            confirmScheduled(at)
        }
    }
    val pickMedia = rememberLauncherForActivityResult(ActivityResultContracts.PickVisualMedia()) { sendPicked(it, null) }
    val pickDocument = rememberLauncherForActivityResult(ActivityResultContracts.OpenDocument()) { sendPicked(it, "document") }

    // Cámara: Android puede cerrar esta pantalla mientras la cámara está abierta, por eso las rutas se guardan
    var cameraFile by rememberSaveable { mutableStateOf<String?>(null) }
    var photoToSend by rememberSaveable { mutableStateOf<String?>(null) }
    val takePicture = rememberLauncherForActivityResult(ActivityResultContracts.TakePicture()) { ok ->
        val path = cameraFile ?: return@rememberLauncherForActivityResult
        cameraFile = null
        if (!ok) {
            File(path).delete()
            return@rememberLauncherForActivityResult
        }
        runTask {
            Photos.prepare(File(path))
            withContext(Dispatchers.Main) { photoToSend = path }
        }
    }

    fun openCamera() {
        val file = Photos.newCameraFile()
        cameraFile = file.path
        try {
            takePicture.launch(FileProvider.getUriForFile(context, "${context.packageName}.files", file))
        } catch (e: ActivityNotFoundException) {
            cameraFile = null
            notifyError("No hay ninguna app de cámara en este celular.")
        }
    }

    fun discardPhoto(path: String) {
        photoToSend = null
        File(path).delete()
    }

    fun sendPhoto(path: String, caption: String) {
        if (scheduleIsPast()) return
        val at = scheduleAt
        val quoted = replyTo
        photoToSend = null
        draft = ""
        replyTo = null
        scheduleAt = null
        scope.launch(Dispatchers.IO) {
            try {
                Actions.sendFile(jid, File(path), "image", caption.trim(), File(path).name, "image/jpeg", quoted, at)
                confirmScheduled(at)
            } catch (e: Exception) {
                withContext(Dispatchers.Main) {
                    draft = caption
                    replyTo = quoted
                    scheduleAt = at
                    photoToSend = path   // la foto no se pierde: queda lista para reintentar
                    notifyError(e.message)
                }
            }
        }
    }

    Scaffold(
        snackbarHost = { SnackbarHost(snackbar) },
        topBar = {
            TopAppBar(
                navigationIcon = { IconButton(onClick = onBack) { Icon(Icons.AutoMirrored.Filled.ArrowBack, "Volver") } },
                title = {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Avatar(chat?.name.orEmpty(), chat?.avatarPath, 38.dp)
                        Column(Modifier.padding(start = 10.dp)) {
                            Text(chat?.name.orEmpty(), fontSize = 17.sp, maxLines = 1, overflow = TextOverflow.Ellipsis)
                            when (presence?.state) {
                                "composing" -> Text("escribiendo…", fontSize = 12.sp, color = C.accent)
                                "recording" -> Text("grabando audio…", fontSize = 12.sp, color = C.accent)
                                "available" -> Text("en línea", fontSize = 12.sp, color = C.text2)
                            }
                        }
                    }
                },
                actions = {
                    // El análisis estudia a una persona: no aplica a un grupo
                    if (chat?.isGroup == false) IconButton(onClick = onAnalysis) { Icon(Icons.Default.Psychology, "Análisis") }
                },
            )
        },
    ) { pad ->
        photoToSend?.let { path ->
            PhotoPreview(
                path = path, initialCaption = draft, scheduled = scheduleAt != null, modifier = Modifier.padding(pad),
                onCancel = { discardPhoto(path) },
                onRetake = {
                    discardPhoto(path)
                    openCamera()
                },
                onSend = { caption -> sendPhoto(path, caption) },
            )
            return@Scaffold
        }
        Column(Modifier.padding(pad).fillMaxSize().background(C.chatBg)) {
            LazyColumn(
                state = listState, reverseLayout = true,
                modifier = Modifier.weight(1f).fillMaxWidth().padding(horizontal = 8.dp),
                verticalArrangement = Arrangement.spacedBy(3.dp),
            ) {
                val rows = messages.asReversed()
                items(rows.size, key = { rows[it].id }) { i ->
                    val m = rows[i]
                    val older = rows.getOrNull(i + 1)
                    Column {
                        if (older == null || dayKey(older.timestamp) != dayKey(m.timestamp)) DaySeparator(m.timestamp)
                        MessageBubble(
                            m = m, busy = m.id in busyIds.value, scope = scope,
                            // En un grupo, el nombre va solo en el primero de varios mensajes seguidos de la misma persona
                            showSender = chat?.isGroup == true && !m.fromMe && (older == null || older.fromMe || older.senderJid != m.senderJid),
                            onReply = { replyTo = m },
                            onReact = { emoji -> runTask { Actions.react(m, emoji) } },
                            onDelete = { runTask { Actions.deleteForEveryone(m) } },
                            onOpenMedia = {
                                withBusy(m.id) {
                                    val path = Actions.downloadMedia(m)
                                    withContext(Dispatchers.Main) {
                                        if (m.mediaType == "image" || m.mediaType == "sticker") viewerImage = path
                                        else if (m.mediaType != "audio") openFile(context, path)
                                    }
                                }
                            },
                            onTranscribe = {
                                withBusy(m.id) {
                                    val path = Actions.downloadMedia(m)
                                    val t = AiService.transcribe(File(path))
                                    Db.setTranscript(m.id, t.ifEmpty { "(sin voz reconocible)" })
                                }
                            },
                            onSummarize = { redo ->
                                withBusy(m.id) {
                                    if (redo) Db.setSummary(m.id, null)
                                    val before = Db.getMessages(jid, MessageSummary.CONTEXT_MESSAGES, m.timestamp)
                                    val provider = Prefs.provider
                                    val (p, model) = MessageSummary.chooseAi(provider, AiService.defaultModel(provider))
                                    val out = MessageSummary.summarize(m, before, chat?.name.orEmpty(), p, model)
                                    if (AiService.isError(out)) throw IllegalStateException(out)
                                    Db.setSummary(m.id, out)
                                }
                            },
                        )
                    }
                }
                if (messages.size >= limit) {
                    item {
                        Box(Modifier.fillMaxWidth().padding(8.dp), contentAlignment = Alignment.Center) {
                            OutlinedButton(onClick = { limit += PAGE }) { Text("Ver mensajes anteriores") }
                        }
                    }
                }
            }
            replyTo?.let { q -> ReplyPreview(q) { replyTo = null } }
            if (showAi) {
                AiPanel(jid, chat?.name.orEmpty(), draft, onResult = { draft = it }, onError = { notifyError(it) })
            }
            ScheduleBar(scheduleAt, scheduledCount, onChange = { chooseScheduleTime() }, onClear = { scheduleAt = null }, onOpenList = onScheduled)
            Composer(
                draft = draft, onDraft = { draft = it }, scope = scope, scheduled = scheduleAt != null,
                onChooseSchedule = { chooseScheduleTime() }, scheduleIsPast = { scheduleIsPast() },
                onToggleAi = { showAi = !showAi },
                onCamera = { openCamera() },
                onPickMedia = { pickMedia.launch(PickVisualMediaRequest(ActivityResultContracts.PickVisualMedia.ImageAndVideo)) },
                onPickDocument = { pickDocument.launch(arrayOf("*/*")) },
                onSend = onSend@{
                    if (scheduleIsPast()) return@onSend
                    val at = scheduleAt
                    val text = draft.trim()
                    val quoted = replyTo
                    draft = ""
                    replyTo = null
                    scheduleAt = null
                    scope.launch(Dispatchers.IO) {
                        try {
                            Actions.sendText(jid, text, quoted, at)
                            confirmScheduled(at)
                        } catch (e: Exception) {
                            withContext(Dispatchers.Main) {
                                if (draft.isEmpty()) draft = text   // no se pierde lo escrito
                                scheduleAt = at
                                notifyError(e.message)
                            }
                        }
                    }
                },
                onSendVoice = { rec ->
                    val at = scheduleAt
                    val quoted = replyTo
                    replyTo = null
                    scheduleAt = null
                    runTask {
                        Actions.sendVoice(jid, rec.file, rec.seconds, rec.waveform, quoted, at)
                        confirmScheduled(at)
                    }
                },
                onRecordingState = { runTask { Actions.chatPresence(jid, state = if (it) "recording" else "paused") } },
                onError = { notifyError(it) },
            )
        }
    }
    viewerImage?.let { ImageViewer(it) { viewerImage = null } }
}

@Composable
private fun DaySeparator(ts: Long) {
    Box(Modifier.fillMaxWidth().padding(vertical = 8.dp), contentAlignment = Alignment.Center) {
        Text(
            dayLabel(ts), color = C.text2, fontSize = 12.sp,
            modifier = Modifier.background(C.surface, RoundedCornerShape(8.dp)).padding(horizontal = 10.dp, vertical = 3.dp),
        )
    }
}

/** La hora elegida para lo próximo que se mande y cuántos mensajes de este chat están en cola. */
@Composable
private fun ScheduleBar(at: Long?, count: Int, onChange: () -> Unit, onClear: () -> Unit, onOpenList: () -> Unit) {
    if (at == null && count == 0) return
    Row(Modifier.fillMaxWidth().background(C.surface).padding(start = 12.dp, end = 4.dp), verticalAlignment = Alignment.CenterVertically) {
        if (at != null) {
            Icon(Icons.Default.Schedule, null, tint = C.accent, modifier = Modifier.size(18.dp))
            Text(
                "Se envía: ${scheduleLabel(at)}", color = C.text, fontSize = 13.sp, maxLines = 1,
                modifier = Modifier.padding(start = 6.dp).weight(1f).clickable(onClick = onChange),
            )
            IconButton(onClick = onClear) { Icon(Icons.Default.Close, "No programar") }
        } else {
            Box(Modifier.weight(1f))
        }
        if (count > 0) {
            Text(
                "$count programado${if (count != 1) "s" else ""}", color = C.accent, fontSize = 13.sp,
                modifier = Modifier.clickable(onClick = onOpenList).padding(horizontal = 8.dp, vertical = 12.dp),
            )
        }
    }
}

@Composable
private fun ReplyPreview(q: Message, onClose: () -> Unit) {
    Row(Modifier.fillMaxWidth().background(C.surface).padding(start = 12.dp), verticalAlignment = Alignment.CenterVertically) {
        Column(Modifier.weight(1f).padding(vertical = 6.dp)) {
            Text(if (q.fromMe) "Tú" else q.senderName.orEmpty(), color = C.accent, fontSize = 13.sp, maxLines = 1)
            Text(Db.preview(q.mediaType, q.text), color = C.text2, fontSize = 13.sp, maxLines = 2, overflow = TextOverflow.Ellipsis)
        }
        IconButton(onClick = onClose) { Icon(Icons.Default.Close, "Cancelar respuesta") }
    }
}

/** La foto recién tomada, antes de enviarla: se puede agregar un texto o sacarla de nuevo. */
@Composable
private fun PhotoPreview(
    path: String, initialCaption: String, scheduled: Boolean, modifier: Modifier, onCancel: () -> Unit, onRetake: () -> Unit, onSend: (String) -> Unit,
) {
    var caption by rememberSaveable(path) { mutableStateOf(initialCaption) }
    BackHandler(onBack = onCancel)
    Column(modifier.fillMaxSize().background(Color.Black)) {
        AsyncImage(
            model = File(path), contentDescription = "Foto tomada", contentScale = ContentScale.Fit,
            modifier = Modifier.weight(1f).fillMaxWidth(),
        )
        Row(Modifier.fillMaxWidth().background(C.surface).padding(4.dp), verticalAlignment = Alignment.Bottom) {
            IconButton(onClick = onCancel) { Icon(Icons.Default.Close, "Descartar foto") }
            IconButton(onClick = onRetake) { Icon(Icons.Default.Replay, "Sacar otra foto") }
            TextField(
                value = caption, onValueChange = { caption = it }, placeholder = { Text("Agregá un texto") },
                modifier = Modifier.weight(1f).heightIn(max = 140.dp), shape = RoundedCornerShape(22.dp),
                colors = TextFieldDefaults.colors(
                    focusedIndicatorColor = Color.Transparent, unfocusedIndicatorColor = Color.Transparent,
                    focusedContainerColor = C.raised, unfocusedContainerColor = C.raised,
                ),
            )
            IconButton(onClick = { onSend(caption) }, modifier = Modifier.background(C.accent, CircleShape)) {
                if (scheduled) Icon(Icons.Default.Schedule, "Programar foto", tint = C.onAccent)
                else Icon(Icons.AutoMirrored.Filled.Send, "Enviar foto", tint = C.onAccent)
            }
        }
    }
}

@Composable
private fun Composer(
    draft: String, onDraft: (String) -> Unit, scope: CoroutineScope, scheduled: Boolean, onChooseSchedule: () -> Unit,
    scheduleIsPast: () -> Boolean, onToggleAi: () -> Unit, onCamera: () -> Unit, onPickMedia: () -> Unit,
    onPickDocument: () -> Unit, onSend: () -> Unit, onSendVoice: (Recording) -> Unit, onRecordingState: (Boolean) -> Unit,
    onError: (String) -> Unit,
) {
    val context = LocalContext.current
    var recorder by remember { mutableStateOf<VoiceRecorder?>(null) }
    var attachMenu by remember { mutableStateOf(false) }

    fun startRecording() {
        try {
            VoicePlayer.release()
            recorder = VoiceRecorder().also { it.start(scope) }
            onRecordingState(true)
        } catch (e: Exception) {
            recorder = null
            onError("No se pudo grabar: ${e.message}")
        }
    }
    val askMic = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
        if (granted) startRecording() else onError("Hace falta el permiso de micrófono para grabar notas de voz.")
    }
    DisposableEffect(Unit) { onDispose { recorder?.cancel() } }

    val rec = recorder
    if (rec != null) {
        val seconds by rec.seconds.collectAsStateWithLifecycle()
        val paused by rec.paused.collectAsStateWithLifecycle()
        Row(Modifier.fillMaxWidth().background(C.surface).padding(6.dp), verticalAlignment = Alignment.CenterVertically) {
            IconButton(onClick = {
                rec.cancel()
                recorder = null
                onRecordingState(false)
            }) { Icon(Icons.Default.Delete, "Descartar", tint = C.danger) }
            Box(Modifier.size(10.dp).background(if (paused) C.text3 else C.danger, CircleShape))
            Text(formatSeconds(seconds), color = C.text, modifier = Modifier.padding(start = 8.dp).weight(1f))
            IconButton(onClick = { rec.togglePause() }) {
                Icon(if (paused) Icons.Default.Mic else Icons.Default.Pause, if (paused) "Seguir" else "Pausar")
            }
            IconButton(
                onClick = onSendRecording@{
                    if (scheduleIsPast()) return@onSendRecording   // sigue grabando: la nota no se pierde mientras se elige otra hora
                    val r = rec.stop()
                    recorder = null
                    onRecordingState(false)
                    if (r != null) onSendVoice(r) else onError("La grabación fue demasiado corta.")
                },
                modifier = Modifier.background(C.accent, CircleShape),
            ) {
                if (scheduled) Icon(Icons.Default.Schedule, "Programar nota de voz", tint = C.onAccent)
                else Icon(Icons.AutoMirrored.Filled.Send, "Enviar nota de voz", tint = C.onAccent)
            }
        }
        return
    }

    Row(Modifier.fillMaxWidth().background(C.surface).padding(4.dp), verticalAlignment = Alignment.Bottom) {
        IconButton(onClick = onToggleAi) { Icon(Icons.Default.AutoAwesome, "Redactor con IA", tint = C.ember) }
        TextField(
            value = draft, onValueChange = onDraft, placeholder = { Text("Mensaje") },
            modifier = Modifier.weight(1f).heightIn(max = 160.dp), shape = RoundedCornerShape(22.dp),
            colors = TextFieldDefaults.colors(
                focusedIndicatorColor = Color.Transparent, unfocusedIndicatorColor = Color.Transparent,
                focusedContainerColor = C.raised, unfocusedContainerColor = C.raised,
            ),
        )
        Box {
            IconButton(onClick = { attachMenu = true }) { Icon(Icons.Default.AttachFile, "Adjuntar") }
            DropdownMenu(expanded = attachMenu, onDismissRequest = { attachMenu = false }) {
                DropdownMenuItem(text = { Text("Sacar una foto") }, onClick = {
                    attachMenu = false
                    onCamera()
                })
                DropdownMenuItem(text = { Text("Foto o video") }, onClick = {
                    attachMenu = false
                    onPickMedia()
                })
                DropdownMenuItem(text = { Text("Documento") }, onClick = {
                    attachMenu = false
                    onPickDocument()
                })
                DropdownMenuItem(text = { Text("Programar envío") }, onClick = {
                    attachMenu = false
                    onChooseSchedule()
                })
            }
        }
        if (draft.isBlank()) {
            IconButton(onClick = onCamera) { Icon(Icons.Default.PhotoCamera, "Sacar una foto") }
            IconButton(
                onClick = {
                    val ok = ContextCompat.checkSelfPermission(context, Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED
                    if (ok) startRecording() else askMic.launch(Manifest.permission.RECORD_AUDIO)
                },
                modifier = Modifier.background(C.accent, CircleShape),
            ) { Icon(Icons.Default.Mic, "Grabar nota de voz", tint = C.onAccent) }
        } else {
            IconButton(onClick = onSend, modifier = Modifier.background(C.accent, CircleShape)) {
                if (scheduled) Icon(Icons.Default.Schedule, "Programar", tint = C.onAccent)
                else Icon(Icons.AutoMirrored.Filled.Send, "Enviar", tint = C.onAccent)
            }
        }
    }
}

/** Redactor: reformula el borrador o, si está vacío, sugiere una respuesta. */
@Composable
private fun AiPanel(jid: String, contact: String, draft: String, onResult: (String) -> Unit, onError: (String) -> Unit) {
    val scope = rememberCoroutineScope()
    var provider by remember { mutableStateOf(Prefs.provider) }
    var model by remember(provider) { mutableStateOf(AiService.defaultModel(provider)) }
    var models by remember(provider) { mutableStateOf(AiService.fallbackModels(provider)) }
    var mode by remember { mutableStateOf(Prefs.getString("polish_mode", ReplyPolisher.DEFAULT_MODE)) }
    var extra by remember { mutableStateOf("") }
    var useProfile by remember { mutableStateOf(Prefs.useProfileInReplies) }
    var busy by remember { mutableStateOf(false) }
    LaunchedEffect(provider) { models = withContext(Dispatchers.IO) { AiService.listModels(provider) } }

    fun generate() {
        busy = true
        Prefs.provider = provider
        Prefs.setModel(provider, model)
        Prefs.putString("polish_mode", mode)
        Prefs.useProfileInReplies = useProfile
        scope.launch {
            val out = withContext(Dispatchers.IO) {
                val recent = Db.getMessages(jid, ReplyPolisher.CONTEXT_MESSAGES)
                val context = ReplyPolisher.formatContext(recent, contact)
                val profile = if (useProfile) AnalysisStore.replyProfile(jid) else ""
                when {
                    draft.isNotBlank() -> AiService.ask(ReplyPolisher.buildPolishPrompt(draft, context, mode, extra, profile), provider, model)
                    context.isBlank() -> "Error: no hay mensajes de texto en este chat para sugerir una respuesta."
                    else -> AiService.ask(ReplyPolisher.buildSuggestPrompt(context, mode, extra, profile), provider, model)
                }
            }
            busy = false
            if (AiService.isError(out)) onError(out) else onResult(ReplyPolisher.cleanReply(out))
        }
    }

    Column(Modifier.fillMaxWidth().heightIn(max = 280.dp).background(C.panel).verticalScroll(rememberScrollState()).padding(8.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Selector("IA", provider, AiService.PROVIDERS, { provider = it })
            Selector("Modelo", model, models, { model = it })
        }
        Selector("Objetivo", mode, ReplyPolisher.MODES.keys.toList(), { mode = it }, display = { ReplyPolisher.MODES[it]?.first ?: it })
        Field(extra, { extra = it }, "Indicación privada (opcional)")
        Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.padding(vertical = 4.dp)) {
            Switch(checked = useProfile, onCheckedChange = { useProfile = it })
            Text("Usar mis notas y el último análisis", color = C.text2, fontSize = 13.sp, modifier = Modifier.padding(start = 8.dp))
        }
        Button(onClick = { generate() }, enabled = !busy, modifier = Modifier.fillMaxWidth()) {
            if (busy) CircularProgressIndicator(Modifier.size(18.dp), strokeWidth = 2.dp)
            else Text(if (draft.isNotBlank()) "Reformular mi borrador" else "Sugerir una respuesta")
        }
    }
}
