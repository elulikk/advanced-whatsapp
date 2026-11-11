package com.advancedwhatsapp.app.ui

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.combinedClickable
import androidx.compose.foundation.gestures.detectHorizontalDragGestures
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.gestures.detectTransformGestures
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.AutoAwesome
import androidx.compose.material.icons.filled.Description
import androidx.compose.material.icons.filled.Done
import androidx.compose.material.icons.filled.DoneAll
import androidx.compose.material.icons.filled.Download
import androidx.compose.material.icons.filled.ExpandLess
import androidx.compose.material.icons.filled.ExpandMore
import androidx.compose.material.icons.filled.Image
import androidx.compose.material.icons.filled.Pause
import androidx.compose.material.icons.filled.PlayArrow
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material.icons.filled.Videocam
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.CornerRadius
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalClipboardManager
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.LinkAnnotation
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.TextLinkStyles
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.font.FontStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextDecoration
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.text.withLink
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.window.Dialog
import androidx.compose.ui.window.DialogProperties
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import coil.ImageLoader
import coil.compose.AsyncImage
import coil.decode.ImageDecoderDecoder
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.ui.platform.LocalContext
import com.advancedwhatsapp.app.ai.MessageSummary
import com.advancedwhatsapp.app.data.Message
import kotlinx.coroutines.CoroutineScope
import org.json.JSONObject
import java.io.File

private val QUICK_REACTIONS = listOf("👍", "❤️", "😂", "😮", "😢", "🙏")
private val URL = Regex("https?://\\S+")

private fun linkify(text: String): AnnotatedString = buildAnnotatedString {
    var last = 0
    for (m in URL.findAll(text)) {
        append(text.substring(last, m.range.first))
        val url = m.value.trimEnd('.', ',', ')', ';')
        withLink(LinkAnnotation.Url(url, TextLinkStyles(SpanStyle(color = C.link, textDecoration = TextDecoration.Underline)))) { append(url) }
        last = m.range.first + url.length
    }
    append(text.substring(last))
}

@OptIn(ExperimentalFoundationApi::class)
@Composable
fun MessageBubble(
    m: Message, busy: Boolean, scope: CoroutineScope, showSender: Boolean, onReply: () -> Unit, onReact: (String) -> Unit, onDelete: () -> Unit,
    onOpenMedia: () -> Unit, onTranscribe: () -> Unit, onSummarize: (redo: Boolean) -> Unit,
    transcriptOpen: Boolean, onToggleTranscript: () -> Unit, summaryOpen: Boolean, onSetSummaryOpen: (Boolean) -> Unit,
    onAutoDownload: () -> Unit = {}, onSaveSticker: () -> Unit = {},
) {
    var menu by remember { mutableStateOf(false) }
    val clipboard = LocalClipboardManager.current
    val revoked = m.mediaType == "revoked"
    val stickerFile = if (m.mediaType == "sticker") m.mediaPath?.let { File(it) }?.takeIf { it.exists() } else null
    // Un sticker va suelto, sin burbuja, como en WhatsApp (con una cita sí lleva fondo)
    val bare = stickerFile != null && m.quotedText.isNullOrBlank()

    Box(Modifier.fillMaxWidth(), contentAlignment = if (m.fromMe) Alignment.CenterEnd else Alignment.CenterStart) {
        Column(
            Modifier.widthIn(max = 310.dp)
                .background(if (bare) Color.Transparent else if (m.fromMe) C.bubbleOut else C.bubbleIn, RoundedCornerShape(10.dp))
                .combinedClickable(onClick = {}, onLongClick = { menu = true })
                .padding(horizontal = 9.dp, vertical = 5.dp),
        ) {
            if (showSender && !m.senderName.isNullOrBlank()) {
                Text(m.senderName, color = C.accent, fontSize = 13.sp, fontWeight = FontWeight.Medium, maxLines = 1, overflow = TextOverflow.Ellipsis)
            }
            m.quotedText?.takeIf { it.isNotBlank() }?.let { q ->
                Column(Modifier.padding(bottom = 4.dp).fillMaxWidth().background(Color(0x33000000), RoundedCornerShape(6.dp)).padding(6.dp)) {
                    m.quotedSender?.takeIf { it.isNotBlank() }?.let { Text(it, color = C.accent, fontSize = 12.sp, maxLines = 1) }
                    Text(q, color = C.text2, fontSize = 13.sp, maxLines = 3, overflow = TextOverflow.Ellipsis)
                }
            }
            when (m.mediaType) {
                "image" -> ImageContent(m, busy, onOpenMedia)
                "sticker" -> StickerContent(m, stickerFile, busy, onOpenMedia, onAutoDownload)
                "audio" -> VoiceContent(m, busy, scope, onOpenMedia, onTranscribe, transcriptOpen, onToggleTranscript)
                "video" -> FileRow(Icons.Default.Videocam, "Video", m, busy, onOpenMedia, showCaptionAsName = false)
                "document" -> FileRow(Icons.Default.Description, m.text.ifEmpty { "Documento" }, m, busy, onOpenMedia, showCaptionAsName = true)
            }
            val caption = when {
                revoked -> ""
                m.mediaType == "document" || m.mediaType == "audio" -> ""
                m.mediaType == "location" -> "📍 ${m.text}".trim()
                m.mediaType == "contact" -> contactText(m)
                else -> m.text
            }
            if (revoked) {
                Text("🚫 Mensaje eliminado", color = C.text2, fontStyle = FontStyle.Italic, fontSize = 14.sp)
            } else if (caption.isNotEmpty()) {
                Text(linkify(caption), color = C.text, fontSize = 15.sp)
            }
            Row(Modifier.align(Alignment.End), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(4.dp)) {
                if (MessageSummary.canSummarize(m)) {
                    Icon(
                        Icons.Default.AutoAwesome, "Resumir con IA", tint = if (m.summary != null) C.ember else C.text3,
                        modifier = Modifier.size(22.dp).clickable {
                            if (m.summary == null) {
                                onSetSummaryOpen(true)
                                onSummarize(false)
                            } else {
                                onSetSummaryOpen(!summaryOpen)
                            }
                        }.padding(3.dp),
                    )
                }
                if (m.edited) Text("Editado", color = C.text3, fontSize = 11.sp)
                Text(timeOf(m.timestamp), color = C.text2, fontSize = 11.sp)
                if (m.fromMe && !revoked) StatusTicks(m.status)
            }
            if (summaryOpen || m.summary != null) {
                SummaryPanel(m, busy, summaryOpen, onToggle = { onSetSummaryOpen(!summaryOpen) }, onRedo = { onSummarize(true) })
            }
        }
        DropdownMenu(expanded = menu, onDismissRequest = { menu = false }) {
            if (!revoked) {
                Row(Modifier.padding(horizontal = 8.dp)) {
                    QUICK_REACTIONS.forEach { e ->
                        Text(e, fontSize = 22.sp, modifier = Modifier.clickable {
                            menu = false
                            onReact(e)
                        }.padding(5.dp))
                    }
                }
                DropdownMenuItem(text = { Text("Responder") }, onClick = {
                    menu = false
                    onReply()
                })
                if (m.text.isNotEmpty()) {
                    DropdownMenuItem(text = { Text("Copiar") }, onClick = {
                        menu = false
                        clipboard.setText(AnnotatedString(m.text))
                    })
                }
                if (MessageSummary.canSummarize(m)) {
                    DropdownMenuItem(text = { Text("Resumir con IA") }, onClick = {
                        menu = false
                        onSetSummaryOpen(true)
                        if (m.summary == null) onSummarize(false)
                    })
                }
                if (stickerFile != null) {
                    DropdownMenuItem(text = { Text("Agregar a mis stickers") }, onClick = {
                        menu = false
                        onSaveSticker()
                    })
                }
                if (m.mediaType == "audio") {
                    DropdownMenuItem(text = { Text("Transcribir") }, onClick = {
                        menu = false
                        onTranscribe()
                    })
                }
                if (m.fromMe) {
                    DropdownMenuItem(text = { Text("Eliminar para todos", color = C.danger) }, onClick = {
                        menu = false
                        onDelete()
                    })
                }
            }
        }
    }
    if (m.reactions.isNotEmpty()) {
        Box(Modifier.fillMaxWidth(), contentAlignment = if (m.fromMe) Alignment.CenterEnd else Alignment.CenterStart) {
            Text(
                m.reactions.distinct().joinToString(" "), fontSize = 13.sp,
                modifier = Modifier.padding(horizontal = 8.dp).background(C.surface, RoundedCornerShape(10.dp)).padding(horizontal = 6.dp, vertical = 1.dp),
            )
        }
    }
}

private fun contactText(m: Message): String {
    val first = runCatching { JSONObject(m.mediaRaw ?: "").optJSONArray("contacts")?.optJSONObject(0) }.getOrNull()
    val phone = first?.optString("phone").orEmpty()
    return "👤 ${m.text.ifEmpty { first?.optString("name").orEmpty() }}" + if (phone.isNotEmpty()) "\n+$phone" else ""
}

@Composable
private fun StatusTicks(status: String) {
    when (status) {
        "read" -> Icon(Icons.Default.DoneAll, "Leído", tint = C.link, modifier = Modifier.size(15.dp))
        "delivered" -> Icon(Icons.Default.DoneAll, "Entregado", tint = C.text2, modifier = Modifier.size(15.dp))
        else -> Icon(Icons.Default.Done, "Enviado", tint = C.text2, modifier = Modifier.size(15.dp))
    }
}

/** Síntesis IA plegable: encabezado con flecha y, plegada, una línea de adelanto. */
@Composable
private fun SummaryPanel(m: Message, busy: Boolean, open: Boolean, onToggle: () -> Unit, onRedo: () -> Unit) {
    val sections = remember(m.summary) { m.summary?.let { MessageSummary.parse(it) }.orEmpty() }
    Column(Modifier.fillMaxWidth().padding(top = 4.dp).background(Color(0x33000000), RoundedCornerShape(6.dp)).padding(7.dp)) {
        Row(Modifier.fillMaxWidth().clickable(onClick = onToggle), verticalAlignment = Alignment.CenterVertically) {
            Text("Síntesis IA", color = C.ember, fontSize = 12.sp, fontWeight = FontWeight.SemiBold, modifier = Modifier.weight(1f))
            Icon(if (open) Icons.Default.ExpandLess else Icons.Default.ExpandMore, if (open) "Plegar" else "Desplegar", tint = C.text3, modifier = Modifier.size(18.dp))
        }
        when {
            busy -> Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.padding(top = 3.dp)) {
                CircularProgressIndicator(Modifier.size(14.dp), strokeWidth = 2.dp)
                Text("Analizando…", color = C.text2, fontSize = 13.sp, modifier = Modifier.padding(start = 8.dp))
            }
            m.summary != null && !open ->
                Text(sections.firstOrNull()?.second.orEmpty(), color = C.text2, fontSize = 13.sp, maxLines = 1, overflow = TextOverflow.Ellipsis)
            m.summary != null -> {
                SelectionContainer {
                    Column {
                        sections.forEach { (title, body) ->
                            Text(title, color = C.ember, fontSize = 12.sp, fontWeight = FontWeight.SemiBold)
                            Text(body, color = C.text, fontSize = 14.sp, modifier = Modifier.padding(bottom = 4.dp))
                        }
                    }
                }
                Icon(
                    Icons.Default.Refresh, "Rehacer", tint = C.text2,
                    modifier = Modifier.align(Alignment.End).size(24.dp).clickable(onClick = onRedo).padding(3.dp),
                )
            }
            else -> Text("Sin resumen. Mantené apretado el mensaje para pedirlo de nuevo.", color = C.text2, fontSize = 13.sp)
        }
    }
}

/** Los stickers que llegan se bajan solos (pesan poco), una sola vez por sesión; los animados se mueven. */
private val autoTried: MutableSet<String> = java.util.Collections.synchronizedSet(HashSet())

@Composable
private fun StickerContent(m: Message, file: File?, busy: Boolean, onOpen: () -> Unit, onAutoDownload: () -> Unit) {
    LaunchedEffect(m.id, file == null) {
        if (file == null && m.mediaRaw != null && autoTried.add(m.id)) onAutoDownload()
    }
    if (file != null) {
        AsyncImage(
            model = file, imageLoader = animatedImageLoader(), contentDescription = "Sticker", contentScale = ContentScale.Fit,
            modifier = Modifier.size(150.dp).clickable(onClick = onOpen),
        )
    } else {
        Row(
            Modifier.size(width = 150.dp, height = 90.dp).background(C.raised, RoundedCornerShape(6.dp)).clickable(enabled = !busy, onClick = onOpen),
            verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.Center,
        ) {
            if (busy) CircularProgressIndicator(Modifier.size(24.dp), strokeWidth = 2.dp)
            else Text("🎨 Sticker", color = C.text2, fontSize = 13.sp)
        }
    }
}

private var animatedLoader: ImageLoader? = null

/** Cargador de Coil que anima los .webp animados (API 28+; la app pide 29). Se crea una vez. */
@Composable
private fun animatedImageLoader(): ImageLoader {
    val ctx = LocalContext.current
    return animatedLoader ?: ImageLoader.Builder(ctx.applicationContext)
        .components { add(ImageDecoderDecoder.Factory()) }.build().also { animatedLoader = it }
}

@Composable
private fun ImageContent(m: Message, busy: Boolean, onOpen: () -> Unit) {
    val file = m.mediaPath?.let { File(it) }?.takeIf { it.exists() }
    if (file != null) {
        AsyncImage(
            model = file, contentDescription = "Imagen", contentScale = ContentScale.FillWidth,
            modifier = Modifier.fillMaxWidth().heightIn(max = 320.dp).padding(bottom = 3.dp).clickable(onClick = onOpen),
        )
    } else {
        Row(
            Modifier.fillMaxWidth().height(90.dp).background(C.raised, RoundedCornerShape(6.dp)).clickable(enabled = !busy, onClick = onOpen).padding(10.dp),
            verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.Center,
        ) {
            if (busy) CircularProgressIndicator(Modifier.size(24.dp))
            else {
                Icon(Icons.Default.Image, null, tint = C.text2)
                Text("Tocá para descargar", color = C.text2, fontSize = 13.sp, modifier = Modifier.padding(start = 8.dp))
            }
        }
    }
}

@Composable
private fun FileRow(icon: androidx.compose.ui.graphics.vector.ImageVector, title: String, m: Message, busy: Boolean, onOpen: () -> Unit, showCaptionAsName: Boolean) {
    val have = m.mediaPath?.let { File(it).exists() } == true
    Row(
        Modifier.fillMaxWidth().padding(bottom = 3.dp).background(C.raised, RoundedCornerShape(6.dp)).clickable(enabled = !busy, onClick = onOpen).padding(10.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Icon(icon, null, tint = C.text2)
        Text(title, color = C.text, fontSize = 14.sp, maxLines = if (showCaptionAsName) 2 else 1, overflow = TextOverflow.Ellipsis, modifier = Modifier.padding(horizontal = 8.dp).weight(1f))
        when {
            busy -> CircularProgressIndicator(Modifier.size(18.dp), strokeWidth = 2.dp)
            !have -> Icon(Icons.Default.Download, "Descargar", tint = C.text2)
        }
    }
}

@Composable
private fun VoiceContent(
    m: Message, busy: Boolean, scope: CoroutineScope, onDownload: () -> Unit, onTranscribe: () -> Unit,
    transcriptOpen: Boolean, onToggleTranscript: () -> Unit,
) {
    // Solo cambia al empezar/pausar; el progreso lo lee únicamente la burbuja que está sonando
    val playing by VoicePlayer.playingId.collectAsStateWithLifecycle()
    val isThis = VoicePlayer.currentIdOf() == m.id
    val file = m.mediaPath?.takeIf { File(it).exists() }
    val totalSec = remember(m.id) { audioSeconds(m) }
    val barColor = if (m.fromMe) BAR_OUT else C.accent

    // La pantalla no se apaga mientras suena este audio
    KeepScreenOn(playing == m.id)

    Row(verticalAlignment = Alignment.CenterVertically) {
        IconButton(onClick = {
            if (file != null) runCatching { VoicePlayer.toggle(m.id, file, scope) } else onDownload()
        }) {
            when {
                busy -> CircularProgressIndicator(Modifier.size(20.dp), strokeWidth = 2.dp)
                file == null -> Icon(Icons.Default.Download, "Descargar audio")
                playing == m.id -> Icon(Icons.Default.Pause, "Pausar")
                else -> Icon(Icons.Default.PlayArrow, "Reproducir")
            }
        }
        if (isThis) {
            LiveAudioBar(m.id, barColor, totalSec, Modifier.weight(1f))
        } else {
            Column(Modifier.weight(1f)) {
                AudioBar(0f, enabled = false, active = barColor, onSeek = {})
                AudioTimes("0:00", if (totalSec > 0) formatSeconds(totalSec) else null)
            }
        }
    }
    val t = m.transcript?.trim().orEmpty()
    if (t.isNotEmpty()) {
        TranscriptBlock(t, transcriptOpen, onToggleTranscript)
    } else {
        TextButton(onClick = onTranscribe, enabled = !busy) { Text("Transcribir", fontSize = 12.sp) }
    }
}

private val BAR_OUT = Color(0xFF8FE9CF)

/** Duración declarada por WhatsApp en el audio recibido; 0 si no se conoce (por ejemplo, los que mandé yo). */
private fun audioSeconds(m: Message): Int {
    val raw = m.mediaRaw ?: return 0
    return runCatching {
        JSONObject(raw).optJSONObject("message")?.optJSONObject("audioMessage")?.optInt("seconds") ?: 0
    }.getOrDefault(0)
}

/** La barra del audio que está sonando: lee el progreso, así los demás audios no se redibujan cada 120 ms. */
@Composable
private fun LiveAudioBar(id: String, active: Color, totalSec: Int, modifier: Modifier) {
    val progress by VoicePlayer.progress.collectAsStateWithLifecycle()
    val position by VoicePlayer.positionMs.collectAsStateWithLifecycle()
    val duration by VoicePlayer.durationMs.collectAsStateWithLifecycle()
    val speed by VoicePlayer.speed.collectAsStateWithLifecycle()
    val total = if (duration > 0) duration / 1000 else totalSec
    Column(modifier) {
        AudioBar(progress, enabled = true, active = active, onSeek = { VoicePlayer.seek(id, it) })
        AudioTimes(formatSeconds(position / 1000), if (total > 0) formatSeconds(total) else null) {
            Text(
                "${speed}x".replace(".0x", "x"), color = C.text2, fontSize = 12.sp,
                modifier = Modifier.clickable { VoicePlayer.nextSpeed() }.padding(horizontal = 6.dp, vertical = 2.dp),
            )
        }
    }
}

@Composable
private fun AudioTimes(elapsed: String, total: String?, trailing: @Composable () -> Unit = {}) {
    Row(Modifier.fillMaxWidth().padding(horizontal = 4.dp), verticalAlignment = Alignment.CenterVertically) {
        Text(if (total != null) "$elapsed / $total" else elapsed, color = C.text2, fontSize = 11.sp, modifier = Modifier.weight(1f))
        trailing()
    }
}

private val THUMB_RADIUS = 8.dp

/**
 * Barra de reproducción: riel fino, tramo ya escuchado con el color de acento y un punto en la posición actual.
 * Se toca para saltar y se arrastra para buscar; el audio salta recién al soltar.
 */
@Composable
private fun AudioBar(progress: Float, enabled: Boolean, active: Color, onSeek: (Float) -> Unit, modifier: Modifier = Modifier) {
    var drag by remember { mutableStateOf<Float?>(null) }
    val shown = (drag ?: progress).coerceIn(0f, 1f)
    val track = C.text3.copy(alpha = 0.45f)
    val color = if (enabled) active else C.text3
    Canvas(
        modifier.fillMaxWidth().height(28.dp)
            .pointerInput(enabled) {
                if (!enabled) return@pointerInput
                val pad = THUMB_RADIUS.toPx()
                detectTapGestures { o -> onSeek(((o.x - pad) / (size.width - 2 * pad)).coerceIn(0f, 1f)) }
            }
            .pointerInput(enabled) {
                if (!enabled) return@pointerInput
                val pad = THUMB_RADIUS.toPx()
                fun at(x: Float) = ((x - pad) / (size.width - 2 * pad)).coerceIn(0f, 1f)
                detectHorizontalDragGestures(
                    onDragStart = { o -> drag = at(o.x) },
                    onDragEnd = {
                        drag?.let(onSeek)
                        drag = null
                    },
                    onDragCancel = { drag = null },
                    onHorizontalDrag = { change, _ ->
                        change.consume()
                        drag = at(change.position.x)
                    },
                )
            },
    ) {
        val h = 4.dp.toPx()
        val pad = THUMB_RADIUS.toPx()
        val w = size.width - 2 * pad
        val cy = size.height / 2
        drawRoundRect(track, Offset(pad, cy - h / 2), Size(w, h), CornerRadius(h / 2, h / 2))
        if (shown > 0f) drawRoundRect(color, Offset(pad, cy - h / 2), Size(w * shown, h), CornerRadius(h / 2, h / 2))
        drawCircle(color, if (drag != null) pad else pad * 0.8f, Offset(pad + w * shown, cy))
    }
}

/** Transcripción plegable: 2 líneas hasta que se toca; las cortas se muestran enteras. */
@Composable
private fun TranscriptBlock(text: String, open: Boolean, onToggle: () -> Unit) {
    if (text.length <= 70 && '\n' !in text) {
        SelectionContainer { Text(text, color = C.text2, fontSize = 14.sp, fontStyle = FontStyle.Italic) }
        return
    }
    Column(Modifier.fillMaxWidth().clickable(onClick = onToggle)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text("Transcripción", color = C.text3, fontSize = 12.sp, fontWeight = FontWeight.Medium, modifier = Modifier.weight(1f))
            Icon(if (open) Icons.Default.ExpandLess else Icons.Default.ExpandMore, if (open) "Plegar" else "Desplegar", tint = C.text3, modifier = Modifier.size(18.dp))
        }
        if (open) SelectionContainer { Text(text, color = C.text2, fontSize = 14.sp, fontStyle = FontStyle.Italic) }
        else Text(text, color = C.text2, fontSize = 14.sp, fontStyle = FontStyle.Italic, maxLines = 2, overflow = TextOverflow.Ellipsis)
    }
}

/** Foto a pantalla completa, con pellizco para acercar. */
@Composable
fun ImageViewer(path: String, onClose: () -> Unit) {
    Dialog(onDismissRequest = onClose, properties = DialogProperties(usePlatformDefaultWidth = false)) {
        var scale by remember { mutableFloatStateOf(1f) }
        var offset by remember { mutableStateOf(Offset.Zero) }
        Box(
            Modifier.fillMaxSize().background(Color.Black)
                .pointerInput(Unit) {
                    detectTransformGestures { _, pan, zoom, _ ->
                        scale = (scale * zoom).coerceIn(1f, 6f)
                        offset = if (scale == 1f) Offset.Zero else offset + pan
                    }
                }
                .clickable(onClick = onClose),
            contentAlignment = Alignment.Center,
        ) {
            AsyncImage(
                model = File(path), contentDescription = "Imagen", contentScale = ContentScale.Fit,
                modifier = Modifier.fillMaxSize().graphicsLayer {
                    scaleX = scale
                    scaleY = scale
                    translationX = offset.x
                    translationY = offset.y
                },
            )
        }
    }
}
