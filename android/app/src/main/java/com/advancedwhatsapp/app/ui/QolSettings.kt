package com.advancedwhatsapp.app.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.advancedwhatsapp.app.Prefs
import com.advancedwhatsapp.app.data.MediaCleanup
import com.advancedwhatsapp.app.data.Thumbs
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

/** Ajustes de calidad de vida: audios en cadena, previews de links, almacenamiento (ver y liberar multimedia) y estadísticas. */
@Composable
fun QolSettingsSection() {
    val scope = rememberCoroutineScope()
    var autoplay by remember { mutableStateOf(Prefs.audioAutoplay) }
    var previews by remember { mutableStateOf(Prefs.linkPreviews) }
    var fwdAll by remember { mutableStateOf(Prefs.forwardButtonAll) }
    var doubleTap by remember { mutableStateOf(Prefs.doubleTapReact) }
    var quickEmoji by remember { mutableStateOf(Prefs.quickReaction) }
    var seconds by remember { mutableStateOf(Prefs.timeSeconds) }
    var pattern by remember { mutableStateOf(Prefs.chatPattern) }
    var usage by remember { mutableStateOf<MediaCleanup.Usage?>(null) }
    var days by remember { mutableStateOf(if (Prefs.mediaAutoDays > 0) Prefs.mediaAutoDays.toString() else "") }
    var gb by remember { mutableStateOf(if (Prefs.mediaAutoGb > 0f) Prefs.mediaAutoGb.toString() else "") }
    var auto by remember { mutableStateOf(Prefs.mediaAutoDays > 0 || Prefs.mediaAutoGb > 0f) }
    var message by remember { mutableStateOf<String?>(null) }
    var busy by remember { mutableStateOf(false) }
    var confirm by remember { mutableStateOf<List<MediaCleanup.Entry>?>(null) }
    var showStats by remember { mutableStateOf(false) }

    fun daysValue(): Int = days.trim().toIntOrNull()?.coerceIn(0, 3650) ?: 0
    fun gbValue(): Double = gb.trim().replace(',', '.').toDoubleOrNull()?.coerceIn(0.0, 500.0) ?: 0.0
    fun saveAuto(on: Boolean) {
        Prefs.mediaAutoDays = if (on) daysValue() else 0
        Prefs.mediaAutoGb = if (on) gbValue().toFloat() else 0f
    }
    fun refresh() {
        scope.launch { usage = withContext(Dispatchers.IO) { runCatching { MediaCleanup.usage() }.getOrNull() } }
    }
    LaunchedEffect(Unit) { refresh() }

    Text("Chat", color = C.ember, fontWeight = FontWeight.SemiBold, modifier = Modifier.padding(top = 10.dp))
    SwitchRow("Reproducir sola la siguiente nota de voz al terminar una", autoplay) { autoplay = it; Prefs.audioAutoplay = it }
    SwitchRow("Mostrar vista previa de los links (título, descripción e imagen)", previews) { previews = it; Prefs.linkPreviews = it }
    SwitchRow("Botón rápido de reenviar también en los mensajes de texto (siempre está en fotos, videos, audios, archivos y links)", fwdAll) {
        fwdAll = it; Prefs.forwardButtonAll = it
    }
    SwitchRow("Doble toque sobre un mensaje para reaccionar (otro doble toque la saca)", doubleTap) { doubleTap = it; Prefs.doubleTapReact = it }
    if (doubleTap) {
        Selector("Emoji del doble toque", quickEmoji, listOf("❤️", "👍", "😂", "😮", "😢", "🙏", "🔥", "👏"),
            { quickEmoji = it; Prefs.quickReaction = it })
    }
    SwitchRow("Mostrar los segundos en la hora de los mensajes", seconds) { seconds = it; Prefs.timeSeconds = it }
    SwitchRow("Textura de dibujitos de fondo en los chats", pattern) { pattern = it; Prefs.chatPattern = it }
    OutlinedButton(onClick = { showStats = true }, modifier = Modifier.fillMaxWidth()) { Text("Ver estadísticas de mis chats") }
    Text(
        "Widget: mantené apretado un lugar libre de la pantalla de inicio, elegí «Widgets» y agregá «Sin leer» para ver los chats pendientes.",
        color = C.text2, fontSize = 13.sp,
    )

    Text("Almacenamiento", color = C.ember, fontWeight = FontWeight.SemiBold, modifier = Modifier.padding(top = 10.dp))
    val u = usage
    if (u == null) {
        Text("Calculando…", color = C.text2, fontSize = 13.sp)
    } else {
        val types = u.byType.entries.sortedByDescending { it.value.bytes }
            .joinToString(" · ") { "${MediaCleanup.TYPE_LABELS[it.key] ?: it.key} ${MediaCleanup.formatSize(it.value.bytes)}" }
        Text(
            "${MediaCleanup.formatSize(u.totalBytes)} en ${u.totalFiles} archivos (se pueden liberar ${MediaCleanup.formatSize(u.removableBytes)}).\n" +
                types.ifEmpty { "Nada bajado todavía." },
            color = C.text, fontSize = 13.sp,
        )
        u.byChat.take(6).forEach {
            Text("${it.name} — ${MediaCleanup.formatSize(it.bytes)} (${it.count})", color = C.text2, fontSize = 12.sp)
        }
    }
    Text(
        "Se borran los archivos bajados más viejos; los mensajes quedan y al abrirlos se vuelven a bajar. Lo que enviaste desde acá no se toca.",
        color = C.text2, fontSize = 13.sp,
    )
    Row(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalAlignment = Alignment.CenterVertically) {
        OutlinedTextField(
            value = days, onValueChange = { days = it.filter(Char::isDigit).take(4); if (auto) saveAuto(true) },
            label = { Text("Más viejo que (días)") }, singleLine = true, modifier = Modifier.weight(1f),
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
        )
        OutlinedTextField(
            value = gb, onValueChange = { gb = it.filter { c -> c.isDigit() || c == '.' || c == ',' }.take(5); if (auto) saveAuto(true) },
            label = { Text("Tope (GB)") }, singleLine = true, modifier = Modifier.weight(1f),
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Decimal),
        )
    }
    OutlinedButton(
        enabled = !busy, modifier = Modifier.fillMaxWidth(),
        onClick = {
            if (daysValue() == 0 && gbValue() == 0.0) {
                message = "Poné una antigüedad en días o un tope en GB."
            } else {
                busy = true
                scope.launch {
                    val plan = withContext(Dispatchers.IO) { MediaCleanup.plan(MediaCleanup.scan(), daysValue(), gbValue()) }
                    busy = false
                    if (plan.isEmpty()) message = "No hay nada para borrar con esos valores." else confirm = plan
                }
            }
        },
    ) { Text(if (busy) "Calculando…" else "Liberar espacio ahora") }
    SwitchRow("Hacerlo solo, una vez por día, con estos valores", auto) { auto = it; saveAuto(it) }
    message?.let { Text(it, color = C.text2, fontSize = 13.sp) }
    Text("Caché de miniaturas: ${MediaCleanup.formatSize(Thumbs.diskBytes())} (se poda sola a 300 MB).", color = C.text3, fontSize = 12.sp)

    confirm?.let { plan ->
        AlertDialog(
            onDismissRequest = { confirm = null },
            title = { Text("Liberar espacio") },
            text = { Text("Se van a borrar ${plan.size} archivos (${MediaCleanup.formatSize(plan.sumOf { it.size })}). Se vuelven a bajar al abrirlos.") },
            confirmButton = {
                TextButton(onClick = {
                    confirm = null
                    busy = true
                    scope.launch {
                        val res = withContext(Dispatchers.IO) { MediaCleanup.apply(plan) }
                        busy = false
                        message = "Listo: ${res.files} archivos, ${MediaCleanup.formatSize(res.bytes)} liberados." +
                            if (res.errors > 0) " (${res.errors} no se pudieron borrar)" else ""
                        refresh()
                    }
                }) { Text("Borrar", color = C.danger) }
            },
            dismissButton = { TextButton(onClick = { confirm = null }) { Text("Cancelar") } },
        )
    }
    if (showStats) StatsDialog(onDismiss = { showStats = false })
}

@Composable
private fun SwitchRow(label: String, value: Boolean, onChange: (Boolean) -> Unit) {
    Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.fillMaxWidth()) {
        Text(label, color = C.text, fontSize = 14.sp, modifier = Modifier.weight(1f).padding(end = 10.dp))
        Switch(checked = value, onCheckedChange = onChange)
    }
}
