package com.advancedwhatsapp.app.ui

import android.app.DatePickerDialog
import android.content.Intent
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Checkbox
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.RadioButton
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.core.content.FileProvider
import com.advancedwhatsapp.app.data.ChatExport
import com.advancedwhatsapp.app.data.ChatLock
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.util.Calendar

/**
 * «Exportar chat…»: contraseña (si el chat está bloqueado) → opciones → exportación en segundo plano con progreso →
 * resultado con «Compartir». El archivo queda en Descargas/AdvancedWhatsapp.
 */
@Composable
fun ExportChatFlow(jid: String, chatName: String, onDismiss: () -> Unit) {
    val c = LocalContext.current
    val scope = rememberCoroutineScope()
    // 0 = contraseña, 1 = opciones, 2 = exportando, 3 = listo
    var step by remember { mutableStateOf(if (ChatLock.isLocked(jid)) 0 else 1) }
    var format by remember { mutableStateOf(ChatExport.Format.HTML) }
    var range by remember { mutableStateOf("all") }
    var from by remember { mutableStateOf<Long?>(null) }
    var to by remember { mutableStateOf<Long?>(null) }
    var media by remember { mutableStateOf(true) }
    var download by remember { mutableStateOf(false) }
    var transcripts by remember { mutableStateOf(true) }
    var summaries by remember { mutableStateOf(false) }
    var deleted by remember { mutableStateOf(true) }
    var progress by remember { mutableFloatStateOf(0f) }
    var counts by remember { mutableStateOf("") }
    var result by remember { mutableStateOf<ChatExport.Result?>(null) }
    var error by remember { mutableStateOf<String?>(null) }
    var job by remember { mutableStateOf<Job?>(null) }
    var cancelFlag by remember { mutableStateOf(false) }

    fun pickDate(initial: Long?, onPick: (Long) -> Unit) {
        val cal = Calendar.getInstance().apply { if (initial != null) timeInMillis = initial * 1000 }
        DatePickerDialog(c, { _, y, m, d ->
            val picked = Calendar.getInstance().apply { clear(); set(y, m, d) }
            onPick(picked.timeInMillis / 1000)
        }, cal.get(Calendar.YEAR), cal.get(Calendar.MONTH), cal.get(Calendar.DAY_OF_MONTH)).show()
    }

    fun start() {
        val (since, until) = ChatExport.rangeFor(range, System.currentTimeMillis() / 1000, from, to)
        val opts = ChatExport.Options(
            format = format, since = since, until = until, includeMedia = media, downloadMissing = media && download,
            transcripts = transcripts, summaries = summaries, deleted = deleted,
        )
        step = 2
        cancelFlag = false
        job = scope.launch {
            try {
                val res = withContext(Dispatchers.IO) {
                    ChatExport.run(jid, chatName, opts, progress = { done, total ->
                        progress = if (total > 0) done.toFloat() / total else 1f
                        counts = "$done de $total mensajes"
                    }, cancelled = { cancelFlag })
                }
                if (res.cancelled) onDismiss() else { result = res; step = 3 }
            } catch (e: Exception) {
                error = e.message ?: "Error desconocido"
                step = 3
            }
        }
    }

    when (step) {
        0 -> AskPasswordDialog("Este chat está bloqueado. Ingresá la contraseña para exportarlo", onOk = { step = 1 }, onDismiss = onDismiss)
        1 -> AlertDialog(
            onDismissRequest = onDismiss,
            title = { Text("Exportar chat") },
            text = {
                Column(Modifier.verticalScroll(rememberScrollState()), verticalArrangement = Arrangement.spacedBy(2.dp)) {
                    Text("Formato", fontSize = 12.sp, color = C.text2)
                    listOf(ChatExport.Format.HTML to "HTML (burbujas, imágenes incrustadas)", ChatExport.Format.PDF to "PDF", ChatExport.Format.TXT to "TXT (como WhatsApp)").forEach { (f, label) ->
                        OptionRow(label, selected = format == f, radio = true) { format = f }
                    }
                    Text("Mensajes", fontSize = 12.sp, color = C.text2, modifier = Modifier.padding(top = 8.dp))
                    listOf("all" to "Todo el chat", "month" to "Último mes", "custom" to "Entre fechas").forEach { (k, label) ->
                        OptionRow(label, selected = range == k, radio = true) { range = k }
                    }
                    if (range == "custom") Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        TextButton(onClick = { pickDate(from) { from = it } }) { Text("Desde: " + (from?.let { ChatExport.dayText(it) } ?: "elegir")) }
                        TextButton(onClick = { pickDate(to) { to = it } }) { Text("Hasta: " + (to?.let { ChatExport.dayText(it) } ?: "elegir")) }
                    }
                    Text("Contenido", fontSize = 12.sp, color = C.text2, modifier = Modifier.padding(top = 8.dp))
                    OptionRow("Incluir multimedia ya descargada", media) { media = it }
                    if (media) OptionRow("Descargar lo que falte (usa datos y puede tardar)", download) { download = it }
                    OptionRow("Transcripciones de audios", transcripts) { transcripts = it }
                    OptionRow("Resúmenes de IA", summaries) { summaries = it }
                    OptionRow("Mensajes eliminados y ediciones conservados", deleted) { deleted = it }
                }
            },
            confirmButton = {
                TextButton(enabled = range != "custom" || (from != null || to != null), onClick = { start() }) { Text("Exportar") }
            },
            dismissButton = { TextButton(onClick = onDismiss) { Text("Cancelar") } },
        )
        2 -> AlertDialog(
            onDismissRequest = {},
            title = { Text("Exportando…") },
            text = {
                Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    LinearProgressIndicator(progress = { progress }, modifier = Modifier.fillMaxWidth())
                    Text(counts.ifEmpty { "Preparando…" }, fontSize = 13.sp, color = C.text2)
                }
            },
            confirmButton = { TextButton(onClick = { cancelFlag = true }) { Text("Cancelar") } },
        )
        else -> {
            val res = result
            AlertDialog(
                onDismissRequest = onDismiss,
                title = { Text(if (error != null) "No se pudo exportar" else "Chat exportado") },
                text = {
                    Text(
                        error ?: buildString {
                            append("${res?.messages ?: 0} mensajes.")
                            if (res?.savedToDownloads == true) append("\nGuardado en Descargas/AdvancedWhatsapp:\n${res.name}")
                            else append("\nNo se pudo copiar a Descargas; podés compartir el archivo.")
                            if ((res?.mediaMissing ?: 0) > 0) append("\n${res?.mediaMissing} archivo(s) no estaban descargados y quedaron afuera.")
                        },
                        fontSize = 14.sp,
                    )
                },
                confirmButton = {
                    if (res != null) TextButton(onClick = {
                        runCatching {
                            val uri = FileProvider.getUriForFile(c, "${c.packageName}.files", res.file)
                            val send = Intent(Intent.ACTION_SEND).setType(ChatExport.Format.entries.first { f -> res.name.endsWith(f.ext) }.mime)
                                .putExtra(Intent.EXTRA_STREAM, uri).addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
                            c.startActivity(Intent.createChooser(send, "Compartir chat exportado"))
                        }.onFailure { toast(c, "No se pudo compartir: ${it.message}") }
                    }) { Text("Compartir") }
                },
                dismissButton = { TextButton(onClick = onDismiss) { Text("Cerrar") } },
            )
        }
    }
}

@Composable
private fun OptionRow(label: String, selected: Boolean, radio: Boolean = false, onChange: (Boolean) -> Unit) {
    Row(Modifier.fillMaxWidth().clickable { onChange(if (radio) true else !selected) }, verticalAlignment = Alignment.CenterVertically) {
        if (radio) RadioButton(selected, onClick = { onChange(true) }) else Checkbox(selected, onCheckedChange = onChange)
        Text(label, fontSize = 14.sp, color = C.text)
    }
}
