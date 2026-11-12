package com.advancedwhatsapp.app.ui

import android.app.NotificationManager
import android.content.Intent
import android.net.Uri
import android.os.PowerManager
import android.provider.Settings
import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.combinedClickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material.icons.filled.DonutLarge
import androidx.compose.material.icons.filled.Edit
import androidx.compose.material.icons.filled.Folder
import androidx.compose.material.icons.filled.NotificationsOff
import androidx.compose.material.icons.filled.Psychology
import androidx.compose.material.icons.filled.Schedule
import androidx.compose.material.icons.filled.PushPin
import androidx.compose.material.icons.filled.Search
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Badge
import androidx.compose.material3.BadgedBox
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilterChip
import androidx.compose.material3.FloatingActionButton
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.material.icons.filled.Lock
import com.advancedwhatsapp.app.data.ChatLock
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.graphics.Color
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
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.compose.LocalLifecycleOwner
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.repeatOnLifecycle
import com.advancedwhatsapp.app.App
import com.advancedwhatsapp.app.AppState
import com.advancedwhatsapp.app.Prefs
import com.advancedwhatsapp.app.data.Actions
import com.advancedwhatsapp.app.data.Chat
import com.advancedwhatsapp.app.data.ChatFilter
import com.advancedwhatsapp.app.data.ChatTab
import com.advancedwhatsapp.app.data.Db
import com.advancedwhatsapp.app.data.Organization
import com.advancedwhatsapp.app.data.SmartFolder
import com.advancedwhatsapp.app.data.Statuses
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.collectLatest
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun ChatListScreen(
    onOpen: (String) -> Unit, onSettings: () -> Unit, onStatuses: () -> Unit, onLink: () -> Unit, onNewChat: () -> Unit, onSelfAnalysis: () -> Unit,
    onScheduled: () -> Unit,
) {
    val conn by AppState.connection.collectAsStateWithLifecycle()
    var search by remember { mutableStateOf("") }
    var searching by remember { mutableStateOf(false) }
    var selected by rememberSaveable { mutableStateOf(KEY_ALL) }
    var chats by remember { mutableStateOf(emptyList<Chat>()) }
    var tabs by remember { mutableStateOf(emptyList<ChatTab>()) }
    var unreadGroups by remember { mutableIntStateOf(0) }
    var managingTabs by remember { mutableStateOf(false) }
    var folders by remember { mutableStateOf(emptyList<SmartFolder>()) }
    var folderUnread by remember { mutableStateOf(emptyMap<Long, Int>()) }
    var archivedCount by remember { mutableIntStateOf(0) }
    var managingFolders by remember { mutableStateOf(false) }
    val owner = LocalLifecycleOwner.current
    val scope = rememberCoroutineScope()
    val context = LocalContext.current

    suspend fun reload() {
        withContext(Dispatchers.IO) {
            val filter = filterFor(selected)
            val loadedChats = Db.getChats(search, filter)
            val loadedTabs = Db.getTabs()
            val groups = Db.unreadGroups()
            val loadedFolders = Organization.folders()
            val counts = loadedFolders.associate { it.id to Organization.folderUnread(it) }
            val archived = Organization.archivedCount()
            withContext(Dispatchers.Main) {
                chats = loadedChats
                tabs = loadedTabs
                unreadGroups = groups
                folders = loadedFolders
                folderUnread = counts
                archivedCount = archived
                // La pestaña o carpeta abierta se borró
                if (filter is ChatFilter.Tab && loadedTabs.none { it.id == filter.id }) selected = KEY_ALL
                if (filter is ChatFilter.Folder && loadedFolders.none { it.id == filter.id }) selected = KEY_ALL
            }
        }
    }
    // Ocultar o mostrar chats ocultos también recarga la lista
    LaunchedEffect(search, selected, ChatLock.revealed, ChatLock.hidden) { reload() }
    // Código secreto en el buscador: muestra (o vuelve a ocultar) los chats ocultos
    LaunchedEffect(search) {
        if (search.trim().length >= ChatLock.MIN_SECRET_LEN && ChatLock.hasSecretCode()) {
            delay(400)   // el hash tarda: no por cada tecla
            val res = withContext(Dispatchers.Default) { ChatLock.trySecret(search) }
            if (res != null) {
                search = ""
                toast(context, if (res == "revealed") "Chats ocultos visibles" else "Chats ocultos de nuevo escondidos")
            }
        }
    }
    // Archivado automático de los chats sin actividad (apagado por defecto)
    LaunchedEffect(owner) {
        owner.lifecycle.repeatOnLifecycle(Lifecycle.State.STARTED) {
            while (true) {
                withContext(Dispatchers.IO) { Organization.autoArchive() }
                delay(30 * 60 * 1000L)
            }
        }
    }
    LaunchedEffect(owner, search, selected) {
        owner.lifecycle.repeatOnLifecycle(Lifecycle.State.STARTED) {
            reload()
            Db.changes.collectLatest {
                delay(250)   // el historial llega en tandas: una sola recarga
                reload()
            }
        }
    }
    // Cuántos contactos tienen estados sin ver; el gateway recién responde cuando terminó de arrancar
    var unseenStatuses by remember { mutableIntStateOf(0) }
    LaunchedEffect(owner, conn.isConnected) {
        owner.lifecycle.repeatOnLifecycle(Lifecycle.State.STARTED) {
            AppState.statusEvents.collectLatest {
                delay(300)
                unseenStatuses = withContext(Dispatchers.IO) {
                    runCatching { Statuses.list().count { it.unseen > 0 } }.getOrDefault(unseenStatuses)
                }
            }
        }
    }

    Scaffold(
        topBar = {
            TopAppBar(
                title = {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        androidx.compose.foundation.Image(
                            painter = androidx.compose.ui.res.painterResource(com.advancedwhatsapp.app.R.mipmap.ic_launcher),
                            contentDescription = "Logo", modifier = Modifier.size(30.dp),
                        )
                        Text("Advanced WhatsApp", color = C.ember, fontWeight = FontWeight.SemiBold, modifier = Modifier.padding(start = 8.dp))
                    }
                },
                actions = {
                    IconButton(onClick = { searching = !searching; if (!searching) search = "" }) { Icon(Icons.Default.Search, "Buscar") }
                    IconButton(onClick = onScheduled) { Icon(Icons.Default.Schedule, "Mensajes programados") }
                    IconButton(onClick = onSelfAnalysis) { Icon(Icons.Default.Psychology, "Análisis interno") }
                    IconButton(onClick = onSettings) { Icon(Icons.Default.Settings, "Ajustes") }
                },
            )
        },
        floatingActionButton = {
            FloatingActionButton(onClick = onNewChat, containerColor = C.accent, contentColor = C.onAccent) {
                Icon(Icons.Default.Add, "Nuevo chat")
            }
        },
    ) { pad ->
        Column(Modifier.padding(pad).fillMaxSize().background(C.panel)) {
            ConnectionBanner(conn.status, conn.label, onLink)
            NotificationsBanner()
            BatteryBanner()
            if (searching) {
                OutlinedTextField(
                    value = search, onValueChange = { search = it }, placeholder = { Text("Buscar chats") }, singleLine = true,
                    modifier = Modifier.fillMaxWidth().padding(horizontal = 12.dp, vertical = 6.dp),
                )
            }
            Row(Modifier.padding(end = 4.dp), verticalAlignment = Alignment.CenterVertically) {
                LazyRow(
                    Modifier.weight(1f), contentPadding = PaddingValues(horizontal = 12.dp),
                    horizontalArrangement = Arrangement.spacedBy(8.dp), verticalAlignment = Alignment.CenterVertically,
                ) {
                    item(KEY_ALL) { TabChip("Todos", 0, selected == KEY_ALL) { selected = KEY_ALL } }
                    item(KEY_UNREAD) { TabChip("No leídos", 0, selected == KEY_UNREAD) { selected = KEY_UNREAD } }
                    item(KEY_GROUPS) { TabChip("Grupos", unreadGroups, selected == KEY_GROUPS) { selected = KEY_GROUPS } }
                    items(tabs, key = { tabKey(it.id) }) { t ->
                        TabChip(t.name, t.unread, selected == tabKey(t.id)) { selected = tabKey(t.id) }
                    }
                    item("nueva") {
                        IconButton(onClick = { managingTabs = true }) { Icon(Icons.Default.Add, "Administrar pestañas", tint = C.text2) }
                    }
                    // Carpetas inteligentes (por reglas) y Archivados, después de las pestañas
                    items(folders, key = { folderKey(it.id) }) { f ->
                        TabChip(f.name, folderUnread[f.id] ?: 0, selected == folderKey(f.id)) { selected = folderKey(f.id) }
                    }
                    if (archivedCount > 0 || selected == KEY_ARCHIVED) {
                        item(KEY_ARCHIVED) { TabChip("Archivados", archivedCount, selected == KEY_ARCHIVED) { selected = KEY_ARCHIVED } }
                    }
                    item("carpetas") {
                        IconButton(onClick = { managingFolders = true }) { Icon(Icons.Default.Folder, "Carpetas inteligentes", tint = C.text2) }
                    }
                }
                // Un ícono y no un botón con texto: el ancho de la fila queda para las pestañas
                IconButton(onClick = onStatuses) {
                    BadgedBox(badge = { if (unseenStatuses > 0) Badge(containerColor = C.accent, contentColor = C.onAccent) { Text("$unseenStatuses") } }) {
                        Icon(Icons.Default.DonutLarge, "Estados", tint = if (unseenStatuses > 0) C.accent else C.text2)
                    }
                }
            }
            if (chats.isEmpty()) {
                Box(Modifier.fillMaxSize().padding(32.dp), contentAlignment = Alignment.Center) {
                    Text(
                        when {
                            search.isNotBlank() -> "Ningún chat coincide con la búsqueda."
                            selected == KEY_UNREAD -> "No tenés chats sin leer."
                            selected == KEY_GROUPS -> "Todavía no hay grupos."
                            selected == KEY_ARCHIVED -> "No hay chats archivados."
                            selected.startsWith("carpeta:") -> "Ningún chat cumple las reglas de esta carpeta."
                            selected != KEY_ALL -> "Esta pestaña está vacía. Mantené apretado un chat y elegí \"Mover a\"."
                            conn.isConnected -> "Todavía no hay chats. Si recién vinculaste, el historial tarda unos minutos en llegar."
                            else -> "Sin chats todavía."
                        },
                        color = C.text2, textAlign = TextAlign.Center,
                    )
                }
            } else {
                LazyColumn(contentPadding = PaddingValues(bottom = 88.dp)) {
                    items(chats, key = { it.jid }) { chat ->
                        ChatRow(chat, tabs, onClick = { onOpen(chat.jid) }, onMarkRead = {
                            scope.launch(Dispatchers.IO) { Actions.markRead(chat.jid) }
                        }, onMove = { tabId ->
                            scope.launch(Dispatchers.IO) { Db.moveChatToTab(chat.jid, tabId) }
                        })
                    }
                }
            }
        }
    }
    if (managingFolders) FoldersDialog(onDismiss = { managingFolders = false })
    if (managingTabs) {
        TabsDialog(
            tabs = tabs,
            onCreate = { name -> scope.launch(Dispatchers.IO) { Db.createTab(name) } },
            onRename = { id, name -> scope.launch(Dispatchers.IO) { Db.renameTab(id, name) } },
            onDelete = { id -> scope.launch(Dispatchers.IO) { Db.deleteTab(id) } },
            onDismiss = { managingTabs = false },
        )
    }
}

private const val KEY_ALL = "todos"
private const val KEY_UNREAD = "no_leidos"
private const val KEY_GROUPS = "grupos"
private const val KEY_ARCHIVED = "archivados"
private fun tabKey(id: Long) = "pestana:$id"
private fun folderKey(id: Long) = "carpeta:$id"

private fun filterFor(key: String): ChatFilter = when {
    key == KEY_UNREAD -> ChatFilter.Unread
    key == KEY_GROUPS -> ChatFilter.Groups
    key == KEY_ARCHIVED -> ChatFilter.Archived
    key == KEY_ALL -> ChatFilter.Inbox
    key.startsWith("carpeta:") -> key.substringAfter(':').toLongOrNull()?.let { ChatFilter.Folder(it) } ?: ChatFilter.Inbox
    else -> key.substringAfter(':').toLongOrNull()?.let { ChatFilter.Tab(it) } ?: ChatFilter.Inbox
}

@Composable
private fun TabChip(name: String, unread: Int, selected: Boolean, onClick: () -> Unit) {
    FilterChip(selected = selected, onClick = onClick, label = { Text(if (unread > 0) "$name ($unread)" else name, maxLines = 1) })
}

/** Crear, renombrar y borrar pestañas. Marketplaces viene con la app y no se toca. */
@Composable
private fun TabsDialog(
    tabs: List<ChatTab>, onCreate: (String) -> Unit, onRename: (Long, String) -> Unit, onDelete: (Long) -> Unit, onDismiss: () -> Unit,
) {
    var name by remember { mutableStateOf("") }
    var editing by remember { mutableStateOf<ChatTab?>(null) }
    val others = tabs.filter { it.id != editing?.id }.map { it.name }
    val valid = Db.isValidTabName(name, others)

    fun submit() {
        val e = editing
        if (e != null) onRename(e.id, name) else onCreate(name)
        name = ""
        editing = null
    }

    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("Pestañas") },
        text = {
            Column(Modifier.heightIn(max = 380.dp).verticalScroll(rememberScrollState())) {
                OutlinedTextField(
                    value = name, onValueChange = { name = it }, singleLine = true,
                    label = { Text(if (editing != null) "Nuevo nombre" else "Nombre de la pestaña nueva") },
                    modifier = Modifier.fillMaxWidth(),
                )
                TextButton(onClick = { submit() }, enabled = valid, modifier = Modifier.align(Alignment.End)) {
                    Text(if (editing != null) "Guardar" else "Crear")
                }
                tabs.forEach { t ->
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Text(t.name, color = C.text, maxLines = 1, overflow = TextOverflow.Ellipsis, modifier = Modifier.weight(1f))
                        if (!t.isBuiltIn) {
                            IconButton(onClick = { editing = t; name = t.name }) { Icon(Icons.Default.Edit, "Renombrar ${t.name}", tint = C.text2) }
                            IconButton(onClick = {
                                if (editing?.id == t.id) { editing = null; name = "" }
                                onDelete(t.id)
                            }) { Icon(Icons.Default.Delete, "Borrar ${t.name}", tint = C.danger) }
                        }
                    }
                }
                Text("Al borrar una pestaña, sus chats vuelven a Todos o a Grupos.", color = C.text3, fontSize = 12.sp, modifier = Modifier.padding(top = 6.dp))
            }
        },
        confirmButton = { TextButton(onClick = onDismiss) { Text("Listo") } },
    )
}

@Composable
private fun ConnectionBanner(status: String, label: String, onLink: () -> Unit) {
    if (status == "connected") return
    val needsLink = status == "qr_ready" || status == "logged_out"
    Text(
        if (needsLink) "$label. Tocá acá." else label,
        color = if (needsLink) C.ember else C.text2, fontSize = 13.sp,
        modifier = Modifier.fillMaxWidth().background(C.raised).clickable(enabled = needsLink, onClick = onLink)
            .padding(horizontal = 14.dp, vertical = 8.dp),
    )
}

/** Si Android tiene apagados los avisos de la app o de los mensajes, no llega ninguno y no hay otra señal de por qué. */
@Composable
private fun NotificationsBanner() {
    val c = LocalContext.current
    val nm = c.getSystemService(NotificationManager::class.java)
    fun blocked() = Prefs.notifications && (!nm.areNotificationsEnabled() ||
        nm.getNotificationChannel(App.CH_MESSAGES)?.importance == NotificationManager.IMPORTANCE_NONE)
    var off by remember { mutableStateOf(false) }
    val owner = LocalLifecycleOwner.current
    LaunchedEffect(owner) {
        owner.lifecycle.repeatOnLifecycle(Lifecycle.State.RESUMED) { off = blocked() }
    }
    if (!off) return
    Text(
        "Las notificaciones están apagadas: no te van a avisar de los mensajes. Tocá acá para activarlas.",
        color = C.warn, fontSize = 13.sp,
        modifier = Modifier.fillMaxWidth().background(C.emberDim).clickable {
            c.startActivity(Intent(Settings.ACTION_APP_NOTIFICATION_SETTINGS).putExtra(Settings.EXTRA_APP_PACKAGE, c.packageName))
        }.padding(horizontal = 14.dp, vertical = 8.dp),
    )
}

/** Sin esta excepción, Android corta la conexión al rato de apagar la pantalla. */
@Composable
private fun BatteryBanner() {
    val c = LocalContext.current
    val pm = c.getSystemService(PowerManager::class.java)
    var ignoring by remember { mutableStateOf(pm.isIgnoringBatteryOptimizations(c.packageName)) }
    val owner = LocalLifecycleOwner.current
    LaunchedEffect(owner) {
        owner.lifecycle.repeatOnLifecycle(Lifecycle.State.RESUMED) { ignoring = pm.isIgnoringBatteryOptimizations(c.packageName) }
    }
    if (ignoring) return
    Text(
        "Para recibir mensajes con la pantalla apagada, permití que la app funcione en segundo plano. Tocá acá.",
        color = C.warn, fontSize = 13.sp,
        modifier = Modifier.fillMaxWidth().background(C.emberDim).clickable {
            c.startActivity(Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS, Uri.parse("package:${c.packageName}")))
        }.padding(horizontal = 14.dp, vertical = 8.dp),
    )
}

@OptIn(ExperimentalFoundationApi::class)
@Composable
private fun ChatRow(chat: Chat, tabs: List<ChatTab>, onClick: () -> Unit, onMarkRead: () -> Unit, onMove: (Long) -> Unit) {
    var menu by remember { mutableStateOf(false) }
    var lockDialog by remember { mutableStateOf("") }   // "", "new" (crear contraseña y bloquear), "ask" (pedirla para desbloquear)
    val locked = ChatLock.isLocked(chat.jid)
    var editing by remember { mutableStateOf(false) }
    if (editing) ContactEditDialog(chat.jid) { editing = false }
    Box {
        Row(
            Modifier.fillMaxWidth().combinedClickable(onClick = onClick, onLongClick = { menu = true })
                .alpha(if (locked) 0.55f else 1f)   // chat bloqueado: fila apagada, como algo todavía no desbloqueado
                .padding(horizontal = 14.dp, vertical = 10.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Box(contentAlignment = Alignment.Center) {
                Avatar(chat.name, chat.avatarPath)
                if (locked) {
                    Box(Modifier.size(48.dp).background(Color(0xAA000000), CircleShape), contentAlignment = Alignment.Center) {
                        Icon(Icons.Default.Lock, "Chat bloqueado", tint = Color.White, modifier = Modifier.size(22.dp))
                    }
                }
            }
            Column(Modifier.padding(start = 12.dp).weight(1f)) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text(chat.name, color = C.text, fontWeight = FontWeight.Medium, maxLines = 1, overflow = TextOverflow.Ellipsis, modifier = Modifier.weight(1f))
                    Text(chatTime(chat.lastTime), color = if (chat.unread != 0) C.accent else C.text3, fontSize = 12.sp)
                }
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text(if (locked) ChatLock.LOCKED_PREVIEW else chat.lastText, color = C.text2, fontSize = 14.sp, maxLines = 1, overflow = TextOverflow.Ellipsis, modifier = Modifier.weight(1f))
                    if (chat.isMuted) Icon(Icons.Default.NotificationsOff, "Silenciado", tint = C.text3, modifier = Modifier.size(16.dp).padding(start = 2.dp))
                    if (chat.pinned) Icon(Icons.Default.PushPin, "Fijado", tint = C.text3, modifier = Modifier.size(16.dp).padding(start = 2.dp))
                    if (chat.unread != 0) {
                        Box(Modifier.padding(start = 6.dp).background(C.accent, CircleShape).padding(horizontal = 7.dp, vertical = 1.dp)) {
                            Text(if (chat.unread > 0) chat.unread.toString() else " ", color = C.onAccent, fontSize = 12.sp, fontWeight = FontWeight.Bold)
                        }
                    }
                }
            }
        }
        DropdownMenu(expanded = menu, onDismissRequest = { menu = false }) {
            if (!chat.isGroup) {
                DropdownMenuItem(text = { Text("Editar contacto") }, onClick = {
                    menu = false
                    editing = true
                })
            }
            tabs.filter { it.id != chat.tabId }.forEach { t ->
                DropdownMenuItem(text = { Text("Mover a ${t.name}") }, onClick = {
                    menu = false
                    onMove(t.id)
                })
            }
            if (chat.tabId != 0L) {
                DropdownMenuItem(text = { Text(if (chat.isGroup) "Volver a Grupos" else "Volver a Todos") }, onClick = {
                    menu = false
                    onMove(0)
                })
            }
            DropdownMenuItem(text = { Text(if (locked) "Desbloquear chat" else "Bloquear chat") }, onClick = {
                menu = false
                if (locked) lockDialog = "ask"
                else if (ChatLock.hasPassword()) ChatLock.lock(chat.jid)
                else lockDialog = "new"
            })
            if (locked) {
                val hiddenChat = ChatLock.isHidden(chat.jid)
                DropdownMenuItem(text = { Text(if (hiddenChat) "Dejar de ocultar chat" else "Ocultar chat (se busca con el código)") }, onClick = {
                    menu = false
                    if (hiddenChat) ChatLock.setHidden(chat.jid, false)
                    else if (ChatLock.hasSecretCode()) ChatLock.setHidden(chat.jid, true)
                    else lockDialog = "secret"
                })
            }
            DropdownMenuItem(text = { Text(if (chat.archived) "Sacar de Archivados" else "Archivar") }, onClick = {
                menu = false
                Organization.setArchived(chat.jid, !chat.archived)
            })
            val silentRead = Organization.isSilentRead(chat.jid)
            DropdownMenuItem(text = { Text(if (silentRead) "Lectura invisible: activada" else "Lectura invisible (sin tildes azules)") }, onClick = {
                menu = false
                Organization.setSilentRead(chat.jid, !silentRead)
            })
            DropdownMenuItem(text = { Text(if (chat.pinned) "Desfijar" else "Fijar") }, onClick = {
                menu = false
                Db.setPinned(chat.jid, !chat.pinned)
            })
            if (chat.isMuted) {
                DropdownMenuItem(text = { Text("Quitar silencio") }, onClick = {
                    menu = false
                    Db.setMuted(chat.jid, 0)
                })
            } else {
                val now = System.currentTimeMillis() / 1000
                listOf("Silenciar 8 horas" to now + 8 * 3600, "Silenciar 1 semana" to now + 7 * 86400, "Silenciar siempre" to -1L).forEach { (t, until) ->
                    DropdownMenuItem(text = { Text(t) }, onClick = {
                        menu = false
                        Db.setMuted(chat.jid, until)
                    })
                }
            }
            if (chat.unread != 0) {
                DropdownMenuItem(text = { Text("Marcar como leído") }, onClick = {
                    menu = false
                    onMarkRead()
                })
            }
        }
        when (lockDialog) {
            "new" -> NewPasswordDialog(onDone = { lockDialog = ""; ChatLock.lock(chat.jid) }, onDismiss = { lockDialog = "" })
            "ask" -> AskPasswordDialog("Desbloquear chat", onOk = { lockDialog = ""; ChatLock.unlock(chat.jid) }, onDismiss = { lockDialog = "" })
            "secret" -> NewSecretDialog(onDone = { lockDialog = ""; ChatLock.setHidden(chat.jid, true) }, onDismiss = { lockDialog = "" })
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun NewChatScreen(onBack: () -> Unit, onOpen: (String) -> Unit) {
    var query by remember { mutableStateOf("") }
    var contacts by remember { mutableStateOf(emptyList<com.advancedwhatsapp.app.data.Contact>()) }
    LaunchedEffect(query) { contacts = withContext(Dispatchers.IO) { Db.getContacts(query) } }
    val digits = query.filter(Char::isDigit)

    Scaffold(topBar = {
        TopAppBar(title = { Text("Nuevo chat") }, navigationIcon = {
            IconButton(onClick = onBack) { Icon(Icons.AutoMirrored.Filled.ArrowBack, "Volver") }
        })
    }) { pad ->
        Column(Modifier.padding(pad).fillMaxSize()) {
            OutlinedTextField(
                value = query, onValueChange = { query = it }, singleLine = true,
                label = { Text("Nombre o número con código de país") },
                modifier = Modifier.fillMaxWidth().padding(12.dp),
            )
            LazyColumn {
                if (digits.length >= 8) {
                    item {
                        Row(
                            Modifier.fillMaxWidth().clickable {
                                val jid = "$digits@s.whatsapp.net"
                                Db.openChat(jid, null, digits)
                                onOpen(jid)
                            }.padding(14.dp),
                        ) { Text("Escribirle a +$digits", color = C.accent) }
                    }
                }
                items(contacts, key = { it.jid }) { c ->
                    Row(
                        Modifier.fillMaxWidth().clickable {
                            Db.openChat(c.jid, c.name, c.phone)
                            onOpen(c.jid)
                        }.padding(horizontal = 14.dp, vertical = 10.dp),
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        Avatar(c.name, c.avatarPath, 42.dp)
                        Column(Modifier.padding(start = 12.dp)) {
                            Text(c.name, color = C.text)
                            c.phone?.let { Text("+$it", color = C.text2, fontSize = 13.sp) }
                        }
                    }
                }
            }
        }
    }
}
