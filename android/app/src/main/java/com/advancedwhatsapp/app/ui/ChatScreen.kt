package com.advancedwhatsapp.app.ui

import android.Manifest
import android.content.ActivityNotFoundException
import android.content.ClipboardManager
import android.content.pm.PackageManager
import android.net.Uri
import androidx.activity.compose.BackHandler
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.PickVisualMediaRequest
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.clickable
import androidx.compose.foundation.combinedClickable
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
import androidx.compose.material.icons.filled.Check
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.ContentPaste
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material.icons.filled.Mic
import androidx.compose.material.icons.filled.Pause
import androidx.compose.material.icons.filled.PhotoCamera
import androidx.compose.material.icons.filled.Psychology
import androidx.compose.material.icons.filled.Replay
import androidx.compose.material.icons.filled.Schedule
import androidx.compose.material3.Button
import androidx.compose.material3.Checkbox
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
import androidx.compose.material3.LocalTextStyle
import androidx.compose.material3.TextField
import androidx.compose.material3.TextFieldDefaults
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateMapOf
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.hapticfeedback.HapticFeedbackType
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalHapticFeedback
import androidx.compose.ui.platform.LocalSoftwareKeyboardController
import androidx.compose.ui.text.TextRange
import androidx.compose.ui.text.input.TextFieldValue
import androidx.compose.runtime.MutableState
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.interaction.PressInteraction
import androidx.compose.foundation.content.MediaType
import androidx.compose.foundation.content.consume
import androidx.compose.foundation.content.contentReceiver
import androidx.compose.foundation.content.hasMediaType
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.foundation.text.input.TextFieldLineLimits
import androidx.compose.foundation.text.input.TextFieldState
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.runtime.snapshotFlow
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.material.icons.filled.EmojiEmotions
import androidx.compose.material.icons.filled.Keyboard
import com.advancedwhatsapp.app.data.Sticker
import com.advancedwhatsapp.app.data.StickerLibrary
import kotlin.reflect.KProperty
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.core.content.ContextCompat
import androidx.core.content.FileProvider
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
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
import com.advancedwhatsapp.app.ai.SmartFeatures
import com.advancedwhatsapp.app.data.Actions
import com.advancedwhatsapp.app.data.Chat
import com.advancedwhatsapp.app.data.Db
import com.advancedwhatsapp.app.data.Downloads
import com.advancedwhatsapp.app.data.Extras
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
fun ChatScreen(jid: String, onBack: () -> Unit, onAnalysis: () -> Unit, onScheduled: () -> Unit, onExtras: (String) -> Unit = {}) {
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    val owner = LocalLifecycleOwner.current
    val snackbar = remember { SnackbarHostState() }
    var chat by remember { mutableStateOf<Chat?>(null) }
    var messages by remember { mutableStateOf(emptyList<Message>()) }
    var limit by remember { mutableIntStateOf(PAGE) }
    var replyTo by remember { mutableStateOf<Message?>(null) }
    // El borrador guarda también el cursor: los emojis se insertan donde está. `draft` es solo su texto.
    val draftState = remember { mutableStateOf(TextFieldValue("")) }
    val draftText = remember { DraftText(draftState) }
    var draft by draftText
    var emojiOpen by rememberSaveable { mutableStateOf(false) }
    val keyboard = LocalSoftwareKeyboardController.current
    var showAi by remember { mutableStateOf(false) }
    // Con hora elegida, lo próximo que se mande queda programado en vez de salir ya
    var scheduleAt by rememberSaveable { mutableStateOf<Long?>(null) }
    var scheduledCount by remember { mutableIntStateOf(0) }
    var askRepeat by remember { mutableStateOf(false) }   // tras elegir la hora: ¿se repite?
    var viewerImage by remember { mutableStateOf<String?>(null) }
    val busyIds = remember { mutableStateOf(setOf<String>()) }
    // Transcripciones y síntesis desplegadas: por mensaje, mientras dure esta pantalla (la lista recicla las burbujas)
    val openTranscripts = remember { mutableStateMapOf<String, Boolean>() }
    val openSummaries = remember { mutableStateMapOf<String, Boolean>() }
    // IA extra: traducciones por mensaje («…» mientras se traduce), fecha detectada a confirmar y sin leer al abrir el chat
    val translations = remember { mutableStateMapOf<String, String>() }
    var reminderFor by remember { mutableStateOf<Pair<Message, SmartFeatures.Detected>?>(null) }
    val missedUnread = remember(jid) { runCatching { Db.getChat(jid)?.unread ?: 0 }.getOrDefault(0) }   // antes de marcarlo leído
    var transcribing by remember { mutableStateOf(false) }
    var clipHasImage by remember { mutableStateOf(false) }
    var clipDismissed by remember { mutableStateOf(0L) }
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

    fun toggleTranslation(m: Message) {
        val current = translations[m.id]
        if (current == "…") return
        if (current != null) {
            translations.remove(m.id)
            return
        }
        translations[m.id] = "…"
        scope.launch(Dispatchers.IO) {
            val (p, model) = SmartFeatures.chooseAi()
            val res = runCatching { SmartFeatures.translate(SmartFeatures.text(m), SmartFeatures.translateLang, p, model) }
                .getOrElse { "" to "Error: ${it.message}" }
            withContext(Dispatchers.Main) {
                translations[m.id] = if (res.second.isEmpty()) res.first else "Error: ${res.second.removePrefix("Error:").trim()}"
            }
        }
    }

    fun toggleEmojiPanel() {
        if (emojiOpen) {
            emojiOpen = false
            keyboard?.show()
        } else {
            keyboard?.hide()   // el panel ocupa el lugar del teclado
            emojiOpen = true
        }
    }

    fun insertEmoji(e: String) {
        val v = draftState.value
        val a = v.selection.min.coerceIn(0, v.text.length)
        val b = v.selection.max.coerceIn(a, v.text.length)
        draftState.value = TextFieldValue(v.text.replaceRange(a, b, e), TextRange(a + e.length))
    }

    BackHandler(enabled = emojiOpen) { emojiOpen = false }

    fun chooseScheduleTime() = pickDateTime(context, scheduleAt) { at ->
        if (at > nowSec()) {
            scheduleAt = at
            askRepeat = true
        } else {
            notifyError("Elegí una fecha y hora futuras.")
        }
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

    fun sendSticker(s: Sticker) {
        if (scheduleIsPast()) return
        val at = scheduleAt
        val quoted = replyTo
        replyTo = null
        scheduleAt = null
        scope.launch(Dispatchers.IO) {
            try {
                Actions.sendSticker(jid, s, quoted, at)
                confirmScheduled(at)
            } catch (e: Exception) {
                withContext(Dispatchers.Main) {
                    replyTo = quoted
                    scheduleAt = at
                    notifyError(e.message)
                }
            }
        }
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

    // Imagen en el portapapeles: se ofrece pegarla como si se hubiera adjuntado
    DisposableEffect(owner) {
        val cm = ContextCompat.getSystemService(context, ClipboardManager::class.java)
        fun check() {
            val d = runCatching { cm?.primaryClipDescription }.getOrNull()
            clipHasImage = d != null && d.hasMimeType("image/*") && d.timestamp != clipDismissed
        }
        check()
        val listener = ClipboardManager.OnPrimaryClipChangedListener { check() }
        cm?.addPrimaryClipChangedListener(listener)
        val observer = LifecycleEventObserver { _, e -> if (e == Lifecycle.Event.ON_RESUME) check() }
        owner.lifecycle.addObserver(observer)
        onDispose {
            cm?.removePrimaryClipChangedListener(listener)
            owner.lifecycle.removeObserver(observer)
        }
    }

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

    fun dismissClipImage() {
        val cm = ContextCompat.getSystemService(context, ClipboardManager::class.java)
        clipDismissed = runCatching { cm?.primaryClipDescription?.timestamp }.getOrNull() ?: 0L
        clipHasImage = false
    }

    /** Imagen pegada (portapapeles o la sugerencia "Pegar" del teclado): misma vista previa que una foto recién sacada. */
    fun pasteImageUri(uri: Uri?) {
        if (uri == null) {
            notifyError("No se pudo leer la imagen del portapapeles.")
            return
        }
        runTask {
            val (file, _) = Actions.importUri(uri)
            try {
                Photos.prepare(file)
            } catch (e: Exception) {
                file.delete()
                throw e
            }
            withContext(Dispatchers.Main) { photoToSend = file.path }
        }
    }

    /** Pega la imagen del portapapeles: abre la misma vista previa que una foto recién sacada. */
    fun pasteClipImage() {
        val cm = ContextCompat.getSystemService(context, ClipboardManager::class.java)
        val uri = runCatching { cm?.primaryClip?.getItemAt(0)?.uri }.getOrNull()
        dismissClipImage()
        pasteImageUri(uri)
    }

    /** Dictado: transcribe la grabación, la emprolija si se pidió y deja el texto en el campo, sin enviar. */
    fun dictate(rec: Recording, polish: Boolean) {
        transcribing = true
        val contact = chat?.name.orEmpty()
        scope.launch(Dispatchers.IO) {
            try {
                var text = AiService.transcribe(rec.file).trim()
                if (text.isEmpty()) throw IllegalStateException("No se reconoció voz en la grabación.")
                var polishError: String? = null
                if (polish) {
                    val provider = Prefs.provider
                    val mode = Prefs.getString("polish_mode", ReplyPolisher.DEFAULT_MODE)
                    val recent = Db.getMessages(jid, ReplyPolisher.CONTEXT_MESSAGES)
                    val profile = if (Prefs.useProfileInReplies) AnalysisStore.replyProfile(jid) else ""
                    val prompt = ReplyPolisher.buildPolishPrompt(text, ReplyPolisher.formatContext(recent, contact), mode, "", profile)
                    val out = AiService.ask(prompt, provider, AiService.defaultModel(provider))
                    if (AiService.isError(out)) polishError = out else text = ReplyPolisher.cleanReply(out)
                }
                val result = text
                val failed = polishError?.lines()?.first()
                withContext(Dispatchers.Main) {
                    draft = if (draft.isBlank()) result else draft.trimEnd() + " " + result
                    if (failed != null) notifyError("No se pudo emprolijar, queda el dictado tal cual: $failed")
                }
            } catch (e: Exception) {
                withContext(Dispatchers.Main) { notifyError(e.message) }
            } finally {
                rec.file.delete()
                withContext(Dispatchers.Main) { transcribing = false }
            }
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

    var editingContact by remember { mutableStateOf(false) }
    if (editingContact) ContactEditDialog(jid) { editingContact = false }
    Scaffold(
        snackbarHost = { SnackbarHost(snackbar) },
        topBar = {
            TopAppBar(
                navigationIcon = { IconButton(onClick = onBack) { Icon(Icons.AutoMirrored.Filled.ArrowBack, "Volver") } },
                title = {
                    Row(
                        Modifier.clickable(enabled = chat?.isGroup == false) { editingContact = true },   // tocar el nombre: editar contacto
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
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
                    ChatExtrasMenu(jid, onNav = onExtras, onMessage = { notifyError(it) })
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
            MissedBanner(jid, chat?.name.orEmpty(), chat?.isGroup == true, missedUnread)
            PinBar(
                pins = remember(messages) { messages.filter { it.pinnedAt > 0 }.sortedByDescending { it.pinnedAt } },
                onGo = { pin ->
                    val idx = messages.asReversed().indexOfFirst { it.id == pin.id }
                    if (idx >= 0) scope.launch { listState.animateScrollToItem(idx) }
                },
                onUnpin = { pin -> scope.launch(Dispatchers.IO) { Extras.pin(pin, false) } },
            )
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
                            transcriptOpen = openTranscripts[m.id] == true,
                            onToggleTranscript = { openTranscripts[m.id] = openTranscripts[m.id] != true },
                            summaryOpen = openSummaries[m.id] == true,
                            onSetSummaryOpen = { openSummaries[m.id] = it },
                            onReact = { emoji -> runTask { Actions.react(m, emoji) } },
                            onAutoDownload = { scope.launch(Dispatchers.IO) { runCatching { Actions.downloadMedia(m) } } },
                            onSaveSticker = {
                                scope.launch(Dispatchers.IO) {
                                    val r = runCatching { StickerLibrary.addFromMessage(m) }.getOrNull()
                                    snackbar.showSnackbar(
                                        when {
                                            r == null -> "No se pudo agregar el sticker."
                                            r.second -> "Ese sticker ya estaba en tus stickers."
                                            else -> "Agregado a tus stickers."
                                        },
                                    )
                                }
                            },
                            onDelete = { runTask { Actions.deleteForEveryone(m) } },
                            onOpenMedia = {
                                withBusy(m.id) {
                                    // Una imagen que todavía no estaba bajada solo se descarga (queda en la burbuja y en Descargas);
                                    // la pantalla completa se abre recién al tocarla ya descargada
                                    val yaDescargada = m.mediaPath?.let { File(it).exists() } == true
                                    val path = Actions.downloadMedia(m)
                                    // Lo descargado también queda en la carpeta pública Descargas del teléfono
                                    if (m.mediaType == "document" || m.mediaType == "video" || m.mediaType == "image") {
                                        Downloads.saveCopy(File(path), Downloads.displayName(m.text, File(path)))
                                    }
                                    if (m.mediaType == "image" && !yaDescargada) return@withBusy
                                    withContext(Dispatchers.Main) {
                                        if (m.mediaType == "image" || m.mediaType == "sticker") viewerImage = path
                                        else if (m.mediaType != "audio") openFile(context, path, m.text)
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
                            translation = translations[m.id], onTranslate = { toggleTranslation(m) },
                            onHideTranslation = { translations.remove(m.id) },
                            reminder = if (SmartFeatures.dateRemindersOn) SmartFeatures.futureDatetime(m) else null,
                            onReminder = { SmartFeatures.futureDatetime(m)?.let { found -> reminderFor = m to found } },
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
            if (transcribing) {
                Row(Modifier.fillMaxWidth().background(C.surface).padding(horizontal = 12.dp, vertical = 8.dp), verticalAlignment = Alignment.CenterVertically) {
                    CircularProgressIndicator(Modifier.size(16.dp), strokeWidth = 2.dp)
                    Text("Transcribiendo el dictado…", color = C.text2, fontSize = 13.sp, modifier = Modifier.padding(start = 8.dp))
                }
            }
            if (clipHasImage && photoToSend == null) {
                Row(Modifier.fillMaxWidth().background(C.surface).padding(start = 12.dp), verticalAlignment = Alignment.CenterVertically) {
                    Icon(Icons.Default.ContentPaste, null, tint = C.accent, modifier = Modifier.size(18.dp))
                    Text(
                        "Pegar imagen del portapapeles", color = C.text, fontSize = 13.sp,
                        modifier = Modifier.weight(1f).clickable { pasteClipImage() }.padding(start = 8.dp, top = 12.dp, bottom = 12.dp),
                    )
                    IconButton(onClick = { dismissClipImage() }) { Icon(Icons.Default.Close, "Ignorar") }
                }
            }
            AiExtraBar(jid, chat?.name.orEmpty(), messages, draft, onUseText = { draft = it }, onError = { notifyError(it) })
            ScheduleBar(scheduleAt, scheduledCount, onChange = { chooseScheduleTime() }, onClear = { scheduleAt = null; Extras.nextRepeat = null }, onOpenList = onScheduled)
            TemplateChips(draft) { t ->
                val full = Extras.applyTemplate(draft, t)
                draftState.value = TextFieldValue(full, TextRange(full.length))
            }
            Composer(
                draft = draftState.value,
                onDraft = { v ->
                    // «/atajo» + espacio: se reemplaza por la plantilla guardada
                    val expanded = if (v.selection.collapsed && v.selection.end == v.text.length) Extras.expandAfterSpace(v.text) else null
                    draftState.value = if (expanded != null) TextFieldValue(expanded, TextRange(expanded.length)) else v
                },
                scope = scope, scheduled = scheduleAt != null,
                emojiOpen = emojiOpen, onToggleEmoji = { toggleEmojiPanel() },
                onFieldTap = {
                    if (emojiOpen) {
                        emojiOpen = false
                        keyboard?.show()
                    }
                },
                onChooseSchedule = { chooseScheduleTime() }, scheduleIsPast = { scheduleIsPast() },
                onToggleAi = { showAi = !showAi },
                onCamera = { openCamera() },
                onPasteImage = { uri ->
                    dismissClipImage()
                    pasteImageUri(uri)
                },
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
                onDictate = { rec, polish -> dictate(rec, polish) },
                onRecordingState = { runTask { Actions.chatPresence(jid, state = if (it) "recording" else "paused") } },
                onError = { notifyError(it) },
            )
            if (emojiOpen) {
                EmojiStickerPanel(
                    onEmoji = { insertEmoji(it) }, onSticker = { sendSticker(it) },
                    onMessage = { text -> scope.launch { snackbar.showSnackbar(text) } },
                )
            }
        }
    }
    viewerImage?.let { ImageViewer(it) { viewerImage = null } }
    reminderFor?.let { (rm, found) ->
        ReminderDialog(
            rm, found, chat?.name.orEmpty(),
            onSchedule = { at ->
                scheduleAt = at
                scope.launch { snackbar.showSnackbar("Escribí el mensaje: sale a la hora elegida.") }
            },
            onDismiss = { reminderFor = null },
        )
    }
    if (askRepeat) {
        RepeatDialog(
            onChoose = { rep ->
                Extras.nextRepeat = rep
                askRepeat = false
            },
            onDismiss = {
                Extras.nextRepeat = null
                askRepeat = false
            },
        )
    }
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

@OptIn(ExperimentalFoundationApi::class)
@Composable
private fun Composer(
    draft: TextFieldValue, onDraft: (TextFieldValue) -> Unit, emojiOpen: Boolean, onToggleEmoji: () -> Unit, onFieldTap: () -> Unit,
    scope: CoroutineScope, scheduled: Boolean, onChooseSchedule: () -> Unit,
    scheduleIsPast: () -> Boolean, onToggleAi: () -> Unit, onCamera: () -> Unit, onPasteImage: (Uri) -> Unit, onPickMedia: () -> Unit,
    onPickDocument: () -> Unit, onSend: () -> Unit, onSendVoice: (Recording) -> Unit, onDictate: (Recording, Boolean) -> Unit, onRecordingState: (Boolean) -> Unit,
    onError: (String) -> Unit,
) {
    val context = LocalContext.current
    val haptic = LocalHapticFeedback.current
    // El campo usa TextFieldState: es el único que acepta imágenes del teclado (la sugerencia "Pegar" con la miniatura).
    // Se sincroniza en los dos sentidos con `draft`, que sigue siendo lo que usa el resto de la pantalla.
    val fieldState = remember { TextFieldState(draft.text, draft.selection) }
    val currentDraft by rememberUpdatedState(draft)
    val currentOnDraft by rememberUpdatedState(onDraft)
    LaunchedEffect(draft) {
        if (fieldState.text.toString() != draft.text || fieldState.selection != draft.selection) {
            fieldState.edit {
                replace(0, length, draft.text)
                selection = draft.selection
            }
        }
    }
    LaunchedEffect(fieldState) {
        snapshotFlow { TextFieldValue(fieldState.text.toString(), fieldState.selection) }.collect {
            if (it.text != currentDraft.text || it.selection != currentDraft.selection) currentOnDraft(it)
        }
    }
    var recorder by remember { mutableStateOf<VoiceRecorder?>(null) }
    // Dictado: la grabación no se manda, se transcribe y el texto va al campo de mensaje
    var dictation by remember { mutableStateOf(false) }
    var pendingDictation by remember { mutableStateOf(false) }
    var polish by remember { mutableStateOf(Prefs.getString("dictate_polish") == "1") }
    var attachMenu by remember { mutableStateOf(false) }

    fun startRecording(dict: Boolean) {
        try {
            VoicePlayer.release()
            recorder = VoiceRecorder().also { it.start(scope) }
            dictation = dict
            if (!dict) onRecordingState(true)
        } catch (e: Exception) {
            recorder = null
            onError("No se pudo grabar: ${e.message}")
        }
    }
    val askMic = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
        if (granted) startRecording(pendingDictation) else onError("Hace falta el permiso de micrófono para grabar notas de voz.")
    }
    DisposableEffect(Unit) { onDispose { recorder?.cancel() } }

    fun beginRecording(dict: Boolean) {
        val ok = ContextCompat.checkSelfPermission(context, Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED
        if (ok) startRecording(dict) else {
            pendingDictation = dict
            askMic.launch(Manifest.permission.RECORD_AUDIO)
        }
    }

    // La pantalla no se apaga mientras se graba
    KeepScreenOn(recorder != null)

    val rec = recorder
    if (rec != null) {
        val seconds by rec.seconds.collectAsStateWithLifecycle()
        val paused by rec.paused.collectAsStateWithLifecycle()
        Row(Modifier.fillMaxWidth().background(C.surface).padding(6.dp), verticalAlignment = Alignment.CenterVertically) {
            IconButton(onClick = {
                rec.cancel()
                recorder = null
                if (!dictation) onRecordingState(false)
            }) { Icon(Icons.Default.Delete, "Descartar", tint = C.danger) }
            Box(Modifier.size(10.dp).background(if (paused) C.text3 else C.danger, CircleShape))
            Text(formatSeconds(seconds), color = C.text, modifier = Modifier.padding(start = 8.dp).weight(1f))
            if (dictation) {
                Row(
                    Modifier.clickable {
                        polish = !polish
                        Prefs.putString("dictate_polish", if (polish) "1" else "0")
                    },
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Checkbox(checked = polish, onCheckedChange = null, modifier = Modifier.padding(horizontal = 6.dp))
                    Text("Emprolijar", color = C.text2, fontSize = 13.sp)
                }
            }
            IconButton(onClick = { rec.togglePause() }) {
                Icon(if (paused) Icons.Default.Mic else Icons.Default.Pause, if (paused) "Seguir" else "Pausar")
            }
            IconButton(
                onClick = onSendRecording@{
                    if (dictation) {
                        val r = rec.stop()
                        recorder = null
                        if (r != null) onDictate(r, polish) else onError("La grabación fue demasiado corta.")
                        return@onSendRecording
                    }
                    if (scheduleIsPast()) return@onSendRecording   // sigue grabando: la nota no se pierde mientras se elige otra hora
                    val r = rec.stop()
                    recorder = null
                    onRecordingState(false)
                    if (r != null) onSendVoice(r) else onError("La grabación fue demasiado corta.")
                },
                modifier = Modifier.background(C.accent, CircleShape),
            ) {
                if (dictation) Icon(Icons.Default.Check, "Transcribir dictado", tint = C.onAccent)
                else if (scheduled) Icon(Icons.Default.Schedule, "Programar nota de voz", tint = C.onAccent)
                else Icon(Icons.AutoMirrored.Filled.Send, "Enviar nota de voz", tint = C.onAccent)
            }
        }
        return
    }

    Row(Modifier.fillMaxWidth().background(C.surface).padding(4.dp), verticalAlignment = Alignment.Bottom) {
        IconButton(onClick = onToggleAi) { Icon(Icons.Default.AutoAwesome, "Redactor con IA", tint = C.ember) }
        IconButton(onClick = onToggleEmoji) {
            Icon(if (emojiOpen) Icons.Default.Keyboard else Icons.Default.EmojiEmotions, if (emojiOpen) "Teclado" else "Emojis y stickers", tint = C.text2)
        }
        // Tocar el campo con el panel abierto lo cierra y vuelve el teclado
        val fieldSource = remember { MutableInteractionSource() }
        LaunchedEffect(fieldSource) {
            fieldSource.interactions.collect { if (it is PressInteraction.Release) onFieldTap() }
        }
        BasicTextField(
            state = fieldState,
            interactionSource = fieldSource,
            lineLimits = TextFieldLineLimits.MultiLine(maxHeightInLines = 6),
            textStyle = LocalTextStyle.current.copy(color = C.text, fontSize = 16.sp),
            cursorBrush = SolidColor(C.accent),
            modifier = Modifier.weight(1f).heightIn(min = 48.dp, max = 160.dp)
                .background(C.raised, RoundedCornerShape(22.dp))
                .contentReceiver { content ->
                    if (!content.hasMediaType(MediaType.Image)) return@contentReceiver content
                    var image: Uri? = null
                    val rest = content.consume { item ->
                        val u = item.uri
                        if (u != null && image == null) { image = u; true } else false
                    }
                    image?.let(onPasteImage)
                    rest
                },
            decorator = { inner ->
                Box(Modifier.padding(horizontal = 16.dp, vertical = 13.dp), contentAlignment = Alignment.CenterStart) {
                    if (fieldState.text.isEmpty()) Text("Mensaje", color = C.text3, fontSize = 16.sp)
                    inner()
                }
            },
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
                DropdownMenuItem(text = { Text("Dictar a texto") }, onClick = {
                    attachMenu = false
                    beginRecording(true)
                })
                DropdownMenuItem(text = { Text("Programar envío") }, onClick = {
                    attachMenu = false
                    onChooseSchedule()
                })
            }
        }
        if (draft.text.isBlank()) {
            IconButton(onClick = onCamera) { Icon(Icons.Default.PhotoCamera, "Sacar una foto") }
            // Toque: nota de voz. Mantener apretado: dictar a texto.
            Box(
                Modifier.size(48.dp).clip(CircleShape).background(C.accent).combinedClickable(
                    onClick = { beginRecording(false) },
                    onLongClick = {
                        haptic.performHapticFeedback(HapticFeedbackType.LongPress)
                        beginRecording(true)
                    },
                ),
                contentAlignment = Alignment.Center,
            ) { Icon(Icons.Default.Mic, "Grabar nota de voz (mantené apretado para dictar)", tint = C.onAccent) }
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

/** `draft` como texto plano sobre un TextFieldValue (que además guarda el cursor). Escribir el texto deja el cursor al final. */
private class DraftText(val state: MutableState<TextFieldValue>) {
    operator fun getValue(thisRef: Any?, property: KProperty<*>): String = state.value.text
    operator fun setValue(thisRef: Any?, property: KProperty<*>, value: String) {
        state.value = TextFieldValue(value, TextRange(value.length))
    }
}
