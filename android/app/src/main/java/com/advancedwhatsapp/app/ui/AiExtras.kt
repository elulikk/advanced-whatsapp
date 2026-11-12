package com.advancedwhatsapp.app.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
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
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.filled.AutoAwesome
import androidx.compose.material.icons.filled.Close
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableLongStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.window.Dialog
import androidx.compose.ui.window.DialogProperties
import com.advancedwhatsapp.app.Prefs
import com.advancedwhatsapp.app.ai.AnalysisStore
import com.advancedwhatsapp.app.ai.SmartFeatures
import com.advancedwhatsapp.app.data.Db
import com.advancedwhatsapp.app.data.Message
import com.advancedwhatsapp.app.data.Reminders
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/*
 * Parte visual de las funciones inteligentes extra (ver ai/SmartFeatures.kt):
 * «Lo que te perdiste», respuestas rápidas, traducir antes de enviar, recordatorio detectado, traducción de un mensaje
 * y búsqueda por significado. ChatScreen / ChatListScreen / MessageBubble solo las enganchan.
 */

/** Banner de un chat con muchos sin leer: un botón resume con IA lo que te perdiste. [unread] es la cantidad al abrirlo. */
@Composable
fun MissedBanner(jid: String, chatName: String, isGroup: Boolean, unread: Int) {
    var dismissed by remember(jid) { mutableStateOf(false) }
    var text by remember(jid) { mutableStateOf("") }
    var error by remember(jid) { mutableStateOf("") }
    var busy by remember(jid) { mutableStateOf(false) }
    val scope = rememberCoroutineScope()
    if (unread < SmartFeatures.MISSED_MIN_UNREAD || dismissed || !SmartFeatures.aiAvailable()) return

    fun run() {
        if (busy) return
        busy = true
        error = ""
        scope.launch(Dispatchers.IO) {
            val (p, model) = SmartFeatures.chooseAi()
            val res = runCatching { SmartFeatures.summarizeMissed(jid, chatName, isGroup, unread, p, model) }
                .getOrElse { "" to "Error: ${it.message}" }
            withContext(Dispatchers.Main) {
                busy = false
                text = res.first
                error = res.second
            }
        }
    }

    Column(Modifier.fillMaxWidth().background(C.surface).padding(start = 14.dp, end = 4.dp, top = 4.dp, bottom = 4.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Icon(Icons.Default.AutoAwesome, null, tint = C.ember, modifier = Modifier.size(18.dp))
            Text(
                if (text.isEmpty()) "Tenés $unread mensajes sin leer en este chat." else "Lo que te perdiste ($unread mensajes):",
                color = C.text, fontSize = 13.sp, modifier = Modifier.weight(1f).padding(start = 8.dp),
            )
            if (busy) CircularProgressIndicator(Modifier.size(16.dp), strokeWidth = 2.dp)
            else TextButton(onClick = { run() }) { Text(if (text.isEmpty()) "Resumir" else "Rehacer", color = C.ember) }
            IconButton(onClick = { dismissed = true }) { Icon(Icons.Default.Close, "Cerrar", tint = C.text2) }
        }
        if (error.isNotEmpty()) Text(error.take(200), color = C.danger, fontSize = 12.sp, modifier = Modifier.padding(start = 26.dp, bottom = 4.dp))
        if (text.isNotEmpty()) {
            Column(Modifier.heightIn(max = 260.dp).verticalScroll(rememberScrollState()).padding(start = 26.dp, end = 10.dp, bottom = 6.dp)) {
                SmartFeatures.parseMissed(text).forEach { (title, lines) ->
                    Text(title, color = C.ember, fontSize = 12.sp, fontWeight = FontWeight.SemiBold, modifier = Modifier.padding(top = 4.dp))
                    lines.forEach { Text(if (title == "RESUMEN") it else "• $it", color = C.text, fontSize = 14.sp) }
                }
            }
        }
    }
}

@Composable
private fun SmartChip(label: String, onClick: () -> Unit) {
    Text(
        label, color = C.text, fontSize = 13.sp, maxLines = 1, overflow = TextOverflow.Ellipsis,
        modifier = Modifier.clip(RoundedCornerShape(16.dp)).background(C.raised).clickable(onClick = onClick)
            .padding(horizontal = 12.dp, vertical = 7.dp),
    )
}

/**
 * Fila sobre el campo de mensaje: 2-3 respuestas rápidas al último mensaje recibido (con el estilo del perfil si hay) y,
 * si hay un borrador, «Traducir» al idioma elegido. Las respuestas se piden solas si el último mensaje trae una pregunta;
 * si no, con el botón ✨ (no se gasta una llamada). Caché por último mensaje.
 */
@Composable
fun AiExtraBar(jid: String, chatName: String, messages: List<Message>, draft: String, onUseText: (String) -> Unit, onError: (String) -> Unit) {
    val scope = rememberCoroutineScope()
    val last = messages.lastOrNull()
    val ai = SmartFeatures.aiAvailable()
    val suggest = ai && SmartFeatures.quickRepliesOn && SmartFeatures.shouldSuggest(last)
    val canTranslate = ai && draft.isNotBlank()
    var replies by remember(jid, last?.id) { mutableStateOf(last?.let { SmartFeatures.cachedQuickReplies(jid, it.id) }.orEmpty()) }
    var state by remember(jid, last?.id) { mutableStateOf("") }   // "", "busy" o "error:<texto>"
    var langMenu by remember { mutableStateOf(false) }
    var translating by remember { mutableStateOf(false) }

    fun request(force: Boolean) {
        val msgs = messages
        if (state == "busy" || msgs.isEmpty()) return
        state = "busy"
        scope.launch(Dispatchers.IO) {
            val (p, model) = SmartFeatures.chooseAi()
            val profile = if (Prefs.useProfileInReplies) AnalysisStore.replyProfile(jid) else ""
            val res = runCatching { SmartFeatures.quickReplies(jid, msgs, chatName, p, model, profile, force) }
                .getOrElse { emptyList<String>() to "Error: ${it.message}" }
            withContext(Dispatchers.Main) {
                replies = res.first
                state = if (res.second.isEmpty()) "" else "error:${res.second}"
            }
        }
    }

    androidx.compose.runtime.LaunchedEffect(jid, last?.id, suggest) {
        if (suggest && replies.isEmpty() && state.isEmpty() && last != null && SmartFeatures.looksLikeRequest(SmartFeatures.text(last))) request(false)
    }
    if (!suggest && !canTranslate) return

    Row(
        Modifier.fillMaxWidth().background(C.panel).horizontalScroll(rememberScrollState()).padding(horizontal = 8.dp, vertical = 4.dp),
        verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(6.dp),
    ) {
        if (suggest) {
            Icon(
                Icons.Default.AutoAwesome, "Sugerir respuestas con IA", tint = C.ember,
                modifier = Modifier.size(30.dp).clickable { request(true) }.padding(5.dp),
            )
            when {
                state == "busy" -> {
                    CircularProgressIndicator(Modifier.size(16.dp), strokeWidth = 2.dp)
                    Text("Pensando respuestas…", color = C.text3, fontSize = 12.sp)
                }
                state.startsWith("error:") -> Text(state.removePrefix("error:").removePrefix("Error:").trim().take(60), color = C.text3, fontSize = 12.sp)
                replies.isEmpty() -> Text("Respuestas rápidas con IA", color = C.text3, fontSize = 12.sp)
                else -> replies.forEach { r -> SmartChip(r) { onUseText(r) } }
            }
        }
        if (canTranslate) {
            Box {
                if (translating) {
                    Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                        CircularProgressIndicator(Modifier.size(16.dp), strokeWidth = 2.dp)
                        Text("Traduciendo…", color = C.text3, fontSize = 12.sp)
                    }
                } else {
                    SmartChip("🌐 Traducir") { langMenu = true }
                }
                DropdownMenu(expanded = langMenu, onDismissRequest = { langMenu = false }) {
                    SmartFeatures.LANGUAGES.forEach { lang ->
                        DropdownMenuItem(text = { Text(lang.replaceFirstChar { it.uppercase() }) }, onClick = {
                            langMenu = false
                            translating = true
                            val text = draft
                            scope.launch(Dispatchers.IO) {
                                val (p, model) = SmartFeatures.chooseAi()
                                val res = runCatching { SmartFeatures.translate(text, lang, p, model) }.getOrElse { "" to "Error: ${it.message}" }
                                withContext(Dispatchers.Main) {
                                    translating = false
                                    if (res.second.isEmpty()) onUseText(res.first) else onError(res.second)
                                }
                            }
                        })
                    }
                }
            }
        }
    }
}

/** Traducción de un mensaje, debajo de su texto. [text] es "…" mientras se traduce, o "Error: …". */
@Composable
fun TranslationPanel(text: String, onHide: () -> Unit) {
    Column(Modifier.fillMaxWidth().padding(top = 4.dp).background(Color(0x33000000), RoundedCornerShape(6.dp)).padding(7.dp)) {
        Text("🌐 Traducción · ${SmartFeatures.translateLang}", color = C.ember, fontSize = 12.sp, fontWeight = FontWeight.SemiBold)
        when {
            text == "…" -> Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.padding(top = 3.dp)) {
                CircularProgressIndicator(Modifier.size(14.dp), strokeWidth = 2.dp)
                Text("Traduciendo…", color = C.text2, fontSize = 13.sp, modifier = Modifier.padding(start = 8.dp))
            }
            text.startsWith("Error") -> Text(text.take(160), color = C.danger, fontSize = 13.sp)
            else -> androidx.compose.foundation.text.selection.SelectionContainer { Text(text, color = C.text, fontSize = 14.sp) }
        }
        if (text != "…") Text("Ocultar", color = C.link, fontSize = 12.sp, modifier = Modifier.clickable(onClick = onHide).padding(top = 3.dp))
    }
}

/** Qué hacer con una fecha/hora detectada: recordatorio local (notificación) o programar el próximo mensaje a esa hora. */
@Composable
fun ReminderDialog(m: Message, found: SmartFeatures.Detected, chatName: String, onSchedule: (Long) -> Unit, onDismiss: () -> Unit) {
    val context = LocalContext.current
    var ts by remember { mutableLongStateOf(found.ts) }
    val fmt = remember { SimpleDateFormat("EEEE d/M HH:mm", Locale("es")) }
    val snippet = SmartFeatures.text(m).let { if (it.length > 140) it.take(139) + "…" else it }
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("Fecha detectada") },
        text = {
            Column {
                Text("“$snippet”", color = C.text2, fontSize = 13.sp)
                Text(fmt.format(Date(ts * 1000)), color = C.text, fontSize = 17.sp, fontWeight = FontWeight.Medium, modifier = Modifier.padding(top = 10.dp))
                if (!found.hasTime) Text("El mensaje no dice la hora: ajustala.", color = C.text3, fontSize = 12.sp)
                TextButton(onClick = { pickDateTime(context, ts) { ts = it } }) { Text("Cambiar fecha y hora") }
            }
        },
        confirmButton = {
            Row {
                TextButton(onClick = {
                    onSchedule(ts)
                    onDismiss()
                }) { Text("Programar un mensaje") }
                TextButton(onClick = {
                    Reminders.add(ts, snippet, m.chatJid, chatName)
                    android.widget.Toast.makeText(context, "Recordatorio creado", android.widget.Toast.LENGTH_SHORT).show()
                    onDismiss()
                }) { Text("Recordármelo") }
            }
        },
        dismissButton = { TextButton(onClick = onDismiss) { Text("Cancelar") } },
    )
}

private class Hit(val m: Message, val chatName: String)

/** Búsqueda por significado: describí lo que buscás y la IA ordena los mensajes (y transcripciones de audios) parecidos. */
@Composable
fun AiSearchDialog(initial: String, onDismiss: () -> Unit, onOpen: (String) -> Unit) {
    val scope = rememberCoroutineScope()
    var query by remember { mutableStateOf(initial) }
    var hits by remember { mutableStateOf(emptyList<Hit>()) }
    var status by remember { mutableStateOf("Encuentra mensajes por significado, incluidas las transcripciones de los audios.") }
    var busy by remember { mutableStateOf(false) }
    val fmt = remember { SimpleDateFormat("dd/MM/yy HH:mm", Locale.getDefault()) }

    fun search() {
        val q = query.trim()
        if (busy || q.length < 3) return
        busy = true
        hits = emptyList()
        status = if (SmartFeatures.aiAvailable()) "Buscando…" else "No hay clave de IA: se busca solo por palabras."
        scope.launch(Dispatchers.IO) {
            val res = runCatching {
                val (p, model) = SmartFeatures.chooseAi()
                val (msgs, note) = SmartFeatures.semanticSearch(q, p, model)
                msgs.map { Hit(it, Db.getChat(it.chatJid)?.name.orEmpty()) } to note
            }.getOrElse { emptyList<Hit>() to "Error: ${it.message}" }
            withContext(Dispatchers.Main) {
                busy = false
                hits = res.first
                status = res.second.ifEmpty { if (res.first.isEmpty()) "No encontré nada parecido." else "${res.first.size} resultado(s). Tocá uno para abrir el chat." }
            }
        }
    }
    androidx.compose.runtime.LaunchedEffect(Unit) { if (initial.trim().length >= 3) search() }

    Dialog(onDismissRequest = onDismiss, properties = DialogProperties(usePlatformDefaultWidth = false)) {
        Surface(Modifier.fillMaxSize(), color = C.panel) {
            Column {
                Row(Modifier.padding(4.dp), verticalAlignment = Alignment.CenterVertically) {
                    IconButton(onClick = onDismiss) { Icon(Icons.AutoMirrored.Filled.ArrowBack, "Volver") }
                    OutlinedTextField(
                        value = query, onValueChange = { query = it }, singleLine = true, modifier = Modifier.weight(1f),
                        placeholder = { Text("Describí lo que buscás") },
                        trailingIcon = {
                            if (busy) CircularProgressIndicator(Modifier.size(20.dp), strokeWidth = 2.dp)
                            else IconButton(onClick = { search() }) { Icon(Icons.Default.AutoAwesome, "Buscar con IA", tint = C.ember) }
                        },
                        keyboardActions = androidx.compose.foundation.text.KeyboardActions(onSearch = { search() }),
                        keyboardOptions = androidx.compose.foundation.text.KeyboardOptions(imeAction = androidx.compose.ui.text.input.ImeAction.Search),
                    )
                }
                Text(status, color = C.text3, fontSize = 13.sp, modifier = Modifier.padding(horizontal = 16.dp, vertical = 6.dp))
                LazyColumn {
                    items(hits.size) { i ->
                        val h = hits[i]
                        val who = if (h.m.fromMe) "Yo" else h.m.senderName?.ifEmpty { null } ?: h.chatName
                        Column(Modifier.fillMaxWidth().clickable { onOpen(h.m.chatJid) }.padding(horizontal = 16.dp, vertical = 8.dp)) {
                            Text("${h.chatName} · ${fmt.format(Date(h.m.timestamp.let { if (it > 1_000_000_000_000L) it else it * 1000 }))}", color = C.text2, fontSize = 12.sp)
                            Text(
                                "$who: ${if (h.m.mediaType == "audio") "🎤 " else ""}${SmartFeatures.text(h.m)}",
                                color = C.text, fontSize = 14.sp, maxLines = 3, overflow = TextOverflow.Ellipsis,
                            )
                        }
                    }
                }
            }
        }
    }
}
