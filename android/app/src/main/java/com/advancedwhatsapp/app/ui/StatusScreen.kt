package com.advancedwhatsapp.app.ui

import android.graphics.BitmapFactory
import android.net.Uri
import android.os.SystemClock
import android.util.Base64
import android.widget.VideoView
import androidx.activity.compose.BackHandler
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.Mic
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.key
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableLongStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.compose.LocalLifecycleOwner
import androidx.lifecycle.repeatOnLifecycle
import coil.compose.AsyncImage
import com.advancedwhatsapp.app.AppState
import com.advancedwhatsapp.app.data.StatusContact
import com.advancedwhatsapp.app.data.StatusItem
import com.advancedwhatsapp.app.data.Statuses
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.collectLatest
import kotlinx.coroutines.withContext
import java.io.File
import kotlin.concurrent.thread

private const val ERROR_MS = 4_000L
private const val PLAYER_GRACE_MS = 3_000L   // por si el reproductor nunca avisa que terminó
private val DEFAULT_TEXT_BACKGROUND = Color(0xFF0C4437)

/** Una lista fija: los estados que llegan mientras mirás no cambian el orden bajo el dedo. */
private data class ViewerStart(val contacts: List<StatusContact>, val index: Int)

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun StatusListScreen(onBack: () -> Unit) {
    var contacts by remember { mutableStateOf<List<StatusContact>?>(null) }
    var viewer by remember { mutableStateOf<ViewerStart?>(null) }
    val owner = LocalLifecycleOwner.current

    LaunchedEffect(owner) {
        owner.lifecycle.repeatOnLifecycle(Lifecycle.State.STARTED) {
            AppState.statusEvents.collectLatest {
                if (contacts != null) delay(300)   // al sincronizar llegan muchos juntos: una sola recarga
                val fresh = withContext(Dispatchers.IO) { runCatching { Statuses.list() }.getOrNull() }
                contacts = fresh ?: contacts.orEmpty()
            }
        }
    }

    viewer?.let { start ->
        StatusViewer(start.contacts, start.index, onClose = {
            viewer = null
            AppState.statusChanged()
        })
        return
    }

    Scaffold(topBar = {
        TopAppBar(title = { Text("Estados") }, navigationIcon = {
            IconButton(onClick = onBack) { Icon(Icons.AutoMirrored.Filled.ArrowBack, "Volver") }
        })
    }) { pad ->
        val all = contacts
        Column(Modifier.padding(pad).fillMaxSize().background(C.panel)) {
            when {
                all == null -> Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) { CircularProgressIndicator() }
                all.isEmpty() -> Box(Modifier.fillMaxSize().padding(32.dp), contentAlignment = Alignment.Center) {
                    Text(
                        "No hay estados de tus contactos. Aparecen acá a medida que los publican y duran 24 horas.",
                        color = C.text2, textAlign = TextAlign.Center,
                    )
                }
                else -> {
                    val (recent, seen) = all.partition { it.unseen > 0 }
                    LazyColumn {
                        statusSection("Recientes", recent) { viewer = ViewerStart(recent, it) }
                        statusSection("Vistos", seen) { viewer = ViewerStart(seen, it) }
                    }
                }
            }
        }
    }
}

private fun androidx.compose.foundation.lazy.LazyListScope.statusSection(
    title: String, contacts: List<StatusContact>, onOpen: (Int) -> Unit,
) {
    if (contacts.isEmpty()) return
    item(key = title) {
        Text(title, color = C.ember, fontSize = 13.sp, fontWeight = FontWeight.SemiBold, modifier = Modifier.padding(start = 14.dp, top = 12.dp, bottom = 4.dp))
    }
    items(contacts.size, key = { contacts[it].sender }) { i -> StatusRow(contacts[i]) { onOpen(i) } }
}

@Composable
private fun StatusRow(c: StatusContact, onClick: () -> Unit) {
    Row(
        Modifier.fillMaxWidth().clickable(onClick = onClick).padding(horizontal = 14.dp, vertical = 8.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Box(Modifier.border(2.dp, if (c.unseen > 0) C.accent else C.text3, CircleShape).padding(3.dp)) {
            Avatar(c.name, c.avatarPath, 46.dp)
        }
        Column(Modifier.padding(start = 12.dp).weight(1f)) {
            Text(c.name, color = C.text, fontWeight = FontWeight.Medium, maxLines = 1, overflow = TextOverflow.Ellipsis)
            val count = if (c.items.size > 1) " · ${c.items.size} estados" else ""
            Text(statusTime(c.lastTime) + count, color = C.text2, fontSize = 13.sp)
        }
    }
}

private fun statusTime(ts: Long): String {
    val day = if (dayKey(ts) == dayKey(System.currentTimeMillis() / 1000)) "Hoy" else "Ayer"
    return "$day ${timeOf(ts)}"
}

@Composable
private fun StatusViewer(contacts: List<StatusContact>, startContact: Int, onClose: () -> Unit) {
    var contactIndex by remember { mutableIntStateOf(startContact) }
    var itemIndex by remember { mutableIntStateOf(Statuses.startIndex(contacts[startContact])) }
    var held by remember { mutableStateOf(false) }
    var fraction by remember { mutableFloatStateOf(0f) }
    val marked = remember { mutableSetOf<String>() }
    val lifecycleState by LocalLifecycleOwner.current.lifecycle.currentStateFlow.collectAsState()
    val paused = held || !lifecycleState.isAtLeast(Lifecycle.State.RESUMED)
    val contact = contacts[contactIndex]
    val item = contact.items[itemIndex]

    fun show(newContact: Int, newItem: Int) {
        contactIndex = newContact
        itemIndex = newItem
        fraction = 0f
    }

    fun next() {
        when {
            itemIndex < contacts[contactIndex].items.lastIndex -> show(contactIndex, itemIndex + 1)
            contactIndex < contacts.lastIndex -> show(contactIndex + 1, Statuses.startIndex(contacts[contactIndex + 1]))
            else -> onClose()
        }
    }

    fun previous() {
        when {
            itemIndex > 0 -> show(contactIndex, itemIndex - 1)
            contactIndex > 0 -> show(contactIndex - 1, contacts[contactIndex - 1].items.lastIndex)
        }
    }

    BackHandler(onBack = onClose)
    Box(Modifier.fillMaxSize().background(Color.Black)) {
        key(item.id) {
            StatusPage(
                item = item, paused = paused, onProgress = { fraction = it }, onFinished = { next() },
                onShown = {
                    if (!item.seen && marked.add(item.id)) thread { runCatching { Statuses.markSeen(item.id) } }
                },
            )
        }
        // Tocar a la izquierda vuelve, a la derecha avanza, y mantener apretado pausa
        Box(
            Modifier.fillMaxSize().pointerInput(Unit) {
                detectTapGestures(
                    onPress = {
                        held = true
                        tryAwaitRelease()
                        held = false
                    },
                    onTap = { if (it.x < size.width / 3f) previous() else next() },
                )
            },
        )
        Column(Modifier.fillMaxWidth().background(Color(0x66000000)).padding(8.dp)) {
            ProgressBars(contact.items.size, itemIndex, fraction)
            Row(Modifier.padding(top = 8.dp), verticalAlignment = Alignment.CenterVertically) {
                Avatar(contact.name, contact.avatarPath, 38.dp)
                Column(Modifier.padding(start = 10.dp).weight(1f)) {
                    Text(contact.name, color = Color.White, fontWeight = FontWeight.Medium, maxLines = 1, overflow = TextOverflow.Ellipsis)
                    Text(statusTime(item.timestamp), color = Color(0xCCFFFFFF), fontSize = 12.sp)
                }
                IconButton(onClick = onClose) { Icon(Icons.Default.Close, "Cerrar", tint = Color.White) }
            }
        }
    }
}

@Composable
private fun ProgressBars(count: Int, current: Int, fraction: Float) {
    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(3.dp)) {
        repeat(count) { i ->
            val fill = when {
                i < current -> 1f
                i == current -> fraction
                else -> 0f
            }
            Box(Modifier.weight(1f).height(3.dp).background(Color(0x55FFFFFF))) {
                Box(Modifier.fillMaxHeight().fillMaxWidth(fill).background(Color.White))
            }
        }
    }
}

/** Un estado en pantalla: baja el archivo, lo muestra, y avisa cuándo se vio y cuándo terminó. */
@Composable
private fun StatusPage(item: StatusItem, paused: Boolean, onProgress: (Float) -> Unit, onShown: () -> Unit, onFinished: () -> Unit) {
    val usesPlayer = item.type == "video" || item.type == "audio"
    val isText = item.type == "text"
    var path by remember { mutableStateOf<String?>(null) }
    var error by remember { mutableStateOf(if (!isText && !item.hasMedia) "Este estado no tiene archivo." else null) }
    var playerReady by remember { mutableStateOf(false) }
    var durationMs by remember { mutableLongStateOf(if (error != null) ERROR_MS else Statuses.durationMs(item)) }
    var finished by remember { mutableStateOf(false) }

    fun fail(text: String) {
        error = text
        durationMs = ERROR_MS
    }

    fun finish() {
        if (finished) return
        finished = true
        onFinished()
    }

    LaunchedEffect(Unit) {
        if (!item.hasMedia) return@LaunchedEffect
        try {
            path = withContext(Dispatchers.IO) { Statuses.media(item.id) }
        } catch (e: Exception) {
            fail(e.message ?: "No se pudo descargar este estado.")
        }
    }

    val shown = error == null && (isText || (path != null && (!usesPlayer || playerReady)))
    LaunchedEffect(shown) { if (shown) onShown() }

    // El tiempo corre solo con el estado a la vista; si no se pudo mostrar, el aviso queda un momento y se sigue
    val running = !paused && (shown || error != null)
    var elapsed by remember { mutableLongStateOf(0L) }
    LaunchedEffect(running) {
        if (!running) return@LaunchedEffect
        var last = SystemClock.uptimeMillis()
        while (true) {
            delay(40)
            val now = SystemClock.uptimeMillis()
            elapsed += now - last
            last = now
            onProgress((elapsed.toFloat() / durationMs).coerceAtMost(1f))
            if (elapsed >= durationMs + if (usesPlayer && error == null) PLAYER_GRACE_MS else 0L) break
        }
        finish()
    }

    val background = if (isText) parseColor(item.background) ?: DEFAULT_TEXT_BACKGROUND else Color.Black
    Box(Modifier.fillMaxSize().background(background), contentAlignment = Alignment.Center) {
        val file = path
        when {
            error != null -> Text(error.orEmpty(), color = Color.White, textAlign = TextAlign.Center, modifier = Modifier.padding(32.dp))
            isText -> Text(
                item.text, color = parseColor(item.textColor) ?: Color.White, textAlign = TextAlign.Center,
                fontSize = if (item.text.length < 90) 30.sp else 22.sp, lineHeight = if (item.text.length < 90) 38.sp else 30.sp,
                modifier = Modifier.padding(28.dp),
            )
            file == null -> {
                Thumbnail(item.thumb)
                CircularProgressIndicator(color = Color.White)
            }
            item.type == "image" -> AsyncImage(
                model = File(file), contentDescription = "Estado", contentScale = ContentScale.Fit, modifier = Modifier.fillMaxSize(),
            )
            else -> {
                StatusPlayer(
                    path = file, paused = paused,
                    onPrepared = { ms ->
                        if (ms > 0) durationMs = ms
                        playerReady = true
                    },
                    onCompletion = { finish() },
                    onError = { fail("No se pudo reproducir este estado.") },
                )
                if (item.type == "audio") Icon(Icons.Default.Mic, "Estado de audio", tint = Color.White, modifier = Modifier.size(96.dp))
                if (!playerReady) CircularProgressIndicator(color = Color.White)
            }
        }
        if (!isText && error == null && item.text.isNotBlank()) {
            Text(
                item.text, color = Color.White, textAlign = TextAlign.Center, fontSize = 16.sp,
                modifier = Modifier.align(Alignment.BottomCenter).fillMaxWidth().background(Color(0x88000000)).padding(14.dp),
            )
        }
    }
}

@Composable
private fun Thumbnail(base64: String?) {
    val bitmap = remember(base64) {
        base64?.let {
            runCatching {
                val bytes = Base64.decode(it, Base64.DEFAULT)
                BitmapFactory.decodeByteArray(bytes, 0, bytes.size)?.asImageBitmap()
            }.getOrNull()
        }
    } ?: return
    Image(bitmap, null, contentScale = ContentScale.Fit, modifier = Modifier.fillMaxSize())
}

/** Video o audio. VideoView sirve para los dos y no suma otra librería a un APK que ya pesa mucho. */
@Composable
private fun StatusPlayer(path: String, paused: Boolean, onPrepared: (Long) -> Unit, onCompletion: () -> Unit, onError: () -> Unit) {
    var ratio by remember { mutableStateOf<Float?>(null) }
    AndroidView(
        modifier = ratio?.let { Modifier.aspectRatio(it) } ?: Modifier.fillMaxSize(),
        factory = { c ->
            VideoView(c).apply {
                setOnPreparedListener { mp ->
                    if (mp.videoWidth > 0 && mp.videoHeight > 0) ratio = mp.videoWidth.toFloat() / mp.videoHeight
                    onPrepared(mp.duration.toLong())
                }
                setOnCompletionListener { onCompletion() }
                setOnErrorListener { _, _, _ ->
                    onError()
                    true
                }
                setVideoURI(Uri.fromFile(File(path)))
            }
        },
        update = { if (paused) it.pause() else it.start() },
        onRelease = { it.stopPlayback() },
    )
}

private fun parseColor(hex: String?): Color? =
    hex?.let { runCatching { Color(android.graphics.Color.parseColor(it)) }.getOrNull() }
