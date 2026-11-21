package com.advancedwhatsapp.app.ui

import android.content.Context
import android.content.Intent
import android.net.Uri
import androidx.compose.foundation.layout.Column
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.Forward
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.ContentCopy
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material.icons.filled.Share
import androidx.compose.material.icons.filled.Star
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.core.content.FileProvider
import com.advancedwhatsapp.app.data.Actions
import com.advancedwhatsapp.app.data.Db
import com.advancedwhatsapp.app.data.Downloads
import com.advancedwhatsapp.app.data.Message
import java.io.File
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/**
 * Selección múltiple de mensajes en un chat (tocar y mantener → «Seleccionar», después tocar otros):
 * copiar, compartir con otras apps, reenviar, destacar y eliminar varios a la vez.
 */
object MessageSelection {
    /** Se puede compartir: texto o un archivo que se baja si hace falta. */
    private val SHAREABLE = setOf("text", "image", "video", "audio", "document", "sticker")

    /** Texto para copiar: uno solo va tal cual; varios llevan fecha, hora y quién lo mandó, como WhatsApp. */
    fun copyText(list: List<Message>, chatName: String): String {
        val withText = list.filter { it.text.isNotBlank() && it.mediaType != "revoked" }
        if (withText.size == 1) return withText[0].text
        val fmt = SimpleDateFormat("dd/MM/yy HH:mm", Locale.getDefault())
        return withText.joinToString("\n") { m ->
            val who = if (m.fromMe) "Yo" else m.senderName?.takeIf { it.isNotBlank() } ?: chatName
            "[${fmt.format(Date(m.timestamp * 1000))}] $who: ${m.text}"
        }
    }

    fun canShare(list: List<Message>): Boolean = list.any { it.mediaType in SHAREABLE && (it.mediaType != "text" || it.text.isNotBlank()) }

    /**
     * Arma el Intent para compartir con otras apps (bloqueante: baja los archivos que falten, llamar desde IO).
     * Los textos van juntos en EXTRA_TEXT y los archivos como adjuntos.
     */
    fun buildShareIntent(context: Context, list: List<Message>, chatName: String): Intent {
        val files = mutableListOf<Uri>()
        val mimes = mutableSetOf<String>()
        val texts = mutableListOf<Message>()
        for (m in list) {
            when {
                m.mediaType == "text" -> if (m.text.isNotBlank()) texts.add(m)
                m.mediaType in SHAREABLE -> {
                    val f = File(Actions.downloadMedia(m))
                    files.add(FileProvider.getUriForFile(context, "${context.packageName}.files", f))
                    mimes.add(Downloads.mimeOf(Downloads.displayName(m.text, f)))
                    if ((m.mediaType == "image" || m.mediaType == "video") && m.text.isNotBlank()) texts.add(m)   // epígrafes
                }
            }
        }
        val text = if (texts.isEmpty()) null else copyText(texts, chatName)
        val type = when {
            files.isEmpty() -> "text/plain"
            mimes.size == 1 -> mimes.first()
            mimes.all { it.startsWith("image/") } -> "image/*"
            else -> "*/*"
        }
        val send = when (files.size) {
            0 -> Intent(Intent.ACTION_SEND)
            1 -> Intent(Intent.ACTION_SEND).putExtra(Intent.EXTRA_STREAM, files[0])
            else -> Intent(Intent.ACTION_SEND_MULTIPLE).putParcelableArrayListExtra(Intent.EXTRA_STREAM, ArrayList(files))
        }.setType(type).addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
        if (text != null) send.putExtra(Intent.EXTRA_TEXT, text)
        if (files.isNotEmpty()) send.clipData = android.content.ClipData.newRawUri("", files[0]).apply {
            files.drop(1).forEach { addItem(android.content.ClipData.Item(it)) }
        }
        return Intent.createChooser(send, "Compartir").addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
    }

    /** «Eliminar para todos» solo vale si todos son míos y ninguno estaba ya eliminado. */
    fun canDeleteForEveryone(list: List<Message>): Boolean =
        list.isNotEmpty() && list.all { it.fromMe && it.mediaType != "revoked" && !it.id.startsWith("call_") }

    fun forwardable(list: List<Message>): List<Message> = list.filter { Actions.canForward(it) }

    fun forwardPreview(list: List<Message>): String =
        if (list.size == 1) Db.preview(list[0].mediaType, list[0].text).ifEmpty { "Mensaje" } else "${list.size} mensajes"
}

/** Barra de arriba mientras hay mensajes elegidos (reemplaza la del chat). */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun SelectionTopBar(
    count: Int, canCopy: Boolean, canShare: Boolean, canForward: Boolean, allStarred: Boolean,
    onClose: () -> Unit, onCopy: () -> Unit, onShare: () -> Unit, onForward: () -> Unit, onStar: () -> Unit, onDelete: () -> Unit,
) {
    TopAppBar(
        navigationIcon = { IconButton(onClick = onClose) { Icon(Icons.Default.Close, "Cancelar selección") } },
        title = { Text("$count") },
        actions = {
            IconButton(onClick = onStar) { Icon(Icons.Default.Star, if (allStarred) "Quitar destacado" else "Destacar", tint = if (allStarred) C.ember else C.text2) }
            if (canCopy) IconButton(onClick = onCopy) { Icon(Icons.Default.ContentCopy, "Copiar") }
            if (canShare) IconButton(onClick = onShare) { Icon(Icons.Default.Share, "Compartir") }
            if (canForward) IconButton(onClick = onForward) { Icon(Icons.AutoMirrored.Filled.Forward, "Reenviar") }
            IconButton(onClick = onDelete) { Icon(Icons.Default.Delete, "Eliminar", tint = C.danger) }
        },
    )
}

/** Eliminar los elegidos: para mí (solo de este celular) o para todos (si son todos míos). */
@Composable
fun DeleteSelectedDialog(count: Int, forEveryone: Boolean, onForMe: () -> Unit, onForEveryone: () -> Unit, onDismiss: () -> Unit) {
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text(if (count == 1) "¿Eliminar el mensaje?" else "¿Eliminar $count mensajes?") },
        text = {
            Text(
                if (forEveryone) "«Para todos» los borra también en el celular de la otra persona."
                else "Se borran solo de este celular: en WhatsApp y en tus otros dispositivos siguen estando.",
            )
        },
        confirmButton = {
            Column {
                if (forEveryone) TextButton(onClick = onForEveryone) { Text("Eliminar para todos", color = C.danger) }
                TextButton(onClick = onForMe) { Text("Eliminar para mí", color = C.danger) }
                TextButton(onClick = onDismiss) { Text("Cancelar") }
            }
        },
    )
}
