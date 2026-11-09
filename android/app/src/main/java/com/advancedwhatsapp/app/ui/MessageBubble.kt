package com.advancedwhatsapp.app.ui

import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.combinedClickable
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
import androidx.compose.material3.Slider
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
import androidx.compose.ui.geometry.Offset
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
import coil.compose.AsyncImage
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
) {
    var menu by remember { mutableStateOf(false) }
    var showSummary by remember(m.id) { mutableStateOf(false) }
    val clipboard = LocalClipboardManager.current
    val revoked = m.mediaType == "revoked"

    Box(Modifier.fillMaxWidth(), contentAlignment = if (m.fromMe) Alignment.CenterEnd else Alignment.CenterStart) {
        Column(
            Modifier.widthIn(max = 310.dp)
                .background(if (m.fromMe) C.bubbleOut else C.bubbleIn, RoundedCornerShape(10.dp))
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
                "image", "sticker" -> ImageContent(m, busy, onOpenMedia)
                "audio" -> VoiceContent(m, busy, scope, onOpenMedia, onTranscribe)
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
                            showSummary = !showSummary
                            if (showSummary && m.summary == null) onSummarize(false)
                        }.padding(3.dp),
                    )
                }
                if (m.edited) Text("Editado", color = C.text3, fontSize = 11.sp)
                Text(timeOf(m.timestamp), color = C.text2, fontSize = 11.sp)
                if (m.fromMe && !revoked) StatusTicks(m.status)
            }
            if (showSummary) SummaryPanel(m, busy, onRedo = { onSummarize(true) })
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
                        showSummary = true
                        if (m.summary == null) onSummarize(false)
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

@Composable
private fun SummaryPanel(m: Message, busy: Boolean, onRedo: () -> Unit) {
    Column(Modifier.fillMaxWidth().padding(top = 4.dp).background(Color(0x33000000), RoundedCornerShape(6.dp)).padding(7.dp)) {
        when {
            busy -> Row(verticalAlignment = Alignment.CenterVertically) {
                CircularProgressIndicator(Modifier.size(14.dp), strokeWidth = 2.dp)
                Text("Analizando…", color = C.text2, fontSize = 13.sp, modifier = Modifier.padding(start = 8.dp))
            }
            m.summary != null -> {
                SelectionContainer {
                    Column {
                        MessageSummary.parse(m.summary).forEach { (title, body) ->
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
private fun VoiceContent(m: Message, busy: Boolean, scope: CoroutineScope, onDownload: () -> Unit, onTranscribe: () -> Unit) {
    val playing by VoicePlayer.playingId.collectAsStateWithLifecycle()
    val progress by VoicePlayer.progress.collectAsStateWithLifecycle()
    val position by VoicePlayer.positionMs.collectAsStateWithLifecycle()
    val speed by VoicePlayer.speed.collectAsStateWithLifecycle()
    val isThis = VoicePlayer.currentIdOf() == m.id
    val file = m.mediaPath?.takeIf { File(it).exists() }
    var seek by remember(m.id) { mutableFloatStateOf(0f) }

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
        Slider(
            value = if (isThis) progress else seek,
            onValueChange = {
                seek = it
                if (isThis) VoicePlayer.seek(m.id, it)
            },
            enabled = file != null, modifier = Modifier.weight(1f),
        )
        if (isThis) {
            Text(
                "${speed}x".replace(".0x", "x"), color = C.text2, fontSize = 12.sp,
                modifier = Modifier.clickable { VoicePlayer.nextSpeed() }.padding(6.dp),
            )
            Text(formatSeconds(position / 1000), color = C.text2, fontSize = 12.sp)
        }
    }
    val t = m.transcript?.trim().orEmpty()
    if (t.isNotEmpty()) {
        SelectionContainer { Text(t, color = C.text2, fontSize = 14.sp, fontStyle = FontStyle.Italic) }
    } else {
        TextButton(onClick = onTranscribe, enabled = !busy) { Text("Transcribir", fontSize = 12.sp) }
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
