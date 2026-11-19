package com.advancedwhatsapp.app.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
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
import androidx.compose.material.icons.automirrored.filled.Forward
import androidx.compose.material.icons.automirrored.filled.Send
import androidx.compose.material3.Checkbox
import androidx.compose.material3.FloatingActionButton
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.window.Dialog
import androidx.compose.ui.window.DialogProperties
import com.advancedwhatsapp.app.data.Chat
import com.advancedwhatsapp.app.data.Db
import com.advancedwhatsapp.app.data.Message
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.withContext

/** Botón redondo de reenvío al costado de la burbuja, como en WhatsApp. */
@Composable
fun QuickForwardButton(onClick: () -> Unit) {
    Box(
        Modifier.padding(horizontal = 6.dp).size(32.dp).clip(CircleShape).background(C.raised).clickable(onClick = onClick),
        contentAlignment = Alignment.Center,
    ) {
        Icon(Icons.AutoMirrored.Filled.Forward, "Reenviar", tint = C.text2, modifier = Modifier.size(18.dp))
    }
}

/**
 * Elegir a qué chats reenviar (uno o varios, como en WhatsApp). Los chats salen en el orden de la lista
 * (más recientes primero) y se pueden buscar por nombre o número. [onSend] recibe los jids elegidos.
 */
@Composable
fun ForwardDialog(message: Message, onDismiss: () -> Unit, onSend: (List<String>) -> Unit) {
    var query by remember { mutableStateOf("") }
    var chats by remember { mutableStateOf(emptyList<Chat>()) }
    val picked = remember { mutableStateListOf<Chat>() }

    LaunchedEffect(query) {
        if (query.isNotBlank()) delay(150)   // al tipear, que no consulte por cada letra
        chats = withContext(Dispatchers.IO) { runCatching { Db.getChats(query.trim(), limit = 200) }.getOrDefault(emptyList()) }
    }

    Dialog(onDismissRequest = onDismiss, properties = DialogProperties(usePlatformDefaultWidth = false)) {
        Surface(Modifier.fillMaxSize(), color = C.panel) {
            Box {
                Column {
                    Row(Modifier.padding(4.dp), verticalAlignment = Alignment.CenterVertically) {
                        IconButton(onClick = onDismiss) { Icon(Icons.AutoMirrored.Filled.ArrowBack, "Volver") }
                        Column(Modifier.weight(1f)) {
                            Text("Reenviar a…", color = C.text, fontSize = 18.sp, fontWeight = FontWeight.Medium)
                            Text(
                                Db.preview(message.mediaType, message.text).ifEmpty { "Mensaje" }, color = C.text3, fontSize = 12.sp,
                                maxLines = 1, overflow = TextOverflow.Ellipsis,
                            )
                        }
                    }
                    OutlinedTextField(
                        value = query, onValueChange = { query = it }, singleLine = true,
                        placeholder = { Text("Buscar chat o número") },
                        modifier = Modifier.fillMaxWidth().padding(horizontal = 12.dp, vertical = 4.dp),
                    )
                    if (picked.isNotEmpty()) {
                        Text(
                            picked.joinToString(", ") { it.name }, color = C.accent, fontSize = 13.sp, maxLines = 2,
                            overflow = TextOverflow.Ellipsis, modifier = Modifier.padding(horizontal = 16.dp, vertical = 4.dp),
                        )
                    }
                    LazyColumn(Modifier.fillMaxWidth()) {
                        items(chats, key = { it.jid }) { c ->
                            val on = picked.any { it.jid == c.jid }
                            Row(
                                Modifier.fillMaxWidth().clickable {
                                    if (on) picked.removeAll { it.jid == c.jid } else picked.add(c)
                                }.padding(horizontal = 12.dp, vertical = 6.dp),
                                verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(10.dp),
                            ) {
                                Avatar(c.name, c.avatarPath, size = 42.dp)
                                Column(Modifier.weight(1f)) {
                                    Text(c.name, color = C.text, fontSize = 15.sp, maxLines = 1, overflow = TextOverflow.Ellipsis)
                                    if (c.isGroup) Text("Grupo", color = C.text3, fontSize = 12.sp)
                                }
                                Checkbox(checked = on, onCheckedChange = null)
                            }
                        }
                        item { Box(Modifier.size(80.dp)) }   // que el botón de enviar no tape el último chat
                    }
                }
                if (picked.isNotEmpty()) {
                    FloatingActionButton(
                        onClick = { onSend(picked.map { it.jid }) },
                        containerColor = C.accent, modifier = Modifier.align(Alignment.BottomEnd).padding(20.dp),
                    ) {
                        Row(Modifier.padding(horizontal = 14.dp), verticalAlignment = Alignment.CenterVertically) {
                            if (picked.size > 1) Text("${picked.size}", color = C.onAccent, fontWeight = FontWeight.Bold, modifier = Modifier.padding(end = 6.dp))
                            Icon(Icons.AutoMirrored.Filled.Send, "Reenviar", tint = C.onAccent)
                        }
                    }
                }
            }
        }
    }
}
