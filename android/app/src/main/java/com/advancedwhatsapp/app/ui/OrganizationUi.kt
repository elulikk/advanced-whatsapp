package com.advancedwhatsapp.app.ui

import android.content.Intent
import android.net.Uri
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.grid.GridCells
import androidx.compose.foundation.lazy.grid.LazyVerticalGrid
import androidx.compose.foundation.lazy.grid.itemsIndexed
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.filled.ArrowDownward
import androidx.compose.material.icons.filled.ArrowUpward
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material.icons.filled.Description
import androidx.compose.material.icons.filled.Edit
import androidx.compose.material.icons.filled.Image
import androidx.compose.material.icons.filled.Link
import androidx.compose.material.icons.filled.Mic
import androidx.compose.material.icons.filled.Videocam
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Checkbox
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilterChip
import androidx.compose.material3.Icon
import androidx.compose.material3.Scaffold
import androidx.compose.material3.ScrollableTabRow
import androidx.compose.material3.Switch
import androidx.compose.material3.Tab
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.key
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateMapOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import coil.compose.AsyncImage
import coil.request.ImageRequest
import com.advancedwhatsapp.app.data.Actions
import com.advancedwhatsapp.app.data.Chat
import com.advancedwhatsapp.app.data.ChatFilter
import com.advancedwhatsapp.app.data.ChatTab
import com.advancedwhatsapp.app.data.Db
import com.advancedwhatsapp.app.data.FolderRules
import com.advancedwhatsapp.app.data.GalleryLink
import com.advancedwhatsapp.app.data.Message
import com.advancedwhatsapp.app.data.Organization
import com.advancedwhatsapp.app.data.SmartFolder
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.sync.Semaphore
import kotlinx.coroutines.sync.withPermit
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.io.File
import java.util.Collections

// ─── Carpetas inteligentes ───────────────────────────────────────────────────────────────────────

/** Administrador de carpetas: crear, editar, ordenar y borrar. Aparecen como pestañas en la lista de chats. */
@Composable
fun FoldersDialog(onDismiss: () -> Unit) {
    var folders by remember { mutableStateOf(emptyList<SmartFolder>()) }
    var tabs by remember { mutableStateOf(emptyList<ChatTab>()) }
    var version by remember { mutableIntStateOf(0) }
    var editing by remember { mutableStateOf<SmartFolder?>(null) }
    var creating by remember { mutableStateOf(false) }
    val scope = rememberCoroutineScope()

    LaunchedEffect(version) {
        val loaded = withContext(Dispatchers.IO) { Organization.folders() to Db.getTabs() }
        folders = loaded.first
        tabs = loaded.second
    }
    fun change(work: () -> Unit) {
        scope.launch {
            withContext(Dispatchers.IO) { work() }
            version++
        }
    }

    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("Carpetas inteligentes") },
        text = {
            Column(Modifier.verticalScroll(rememberScrollState())) {
                Text(
                    "Se arman solas con reglas (pestaña, tipo de chat, no leídos, silenciados o contactos elegidos) y aparecen junto a las pestañas.",
                    color = C.text2, fontSize = 13.sp,
                )
                if (folders.isEmpty()) Text("Todavía no hay carpetas.", color = C.text3, modifier = Modifier.padding(vertical = 12.dp))
                val names = tabs.associate { it.id to it.name }
                folders.forEachIndexed { i, f ->
                    Row(Modifier.fillMaxWidth().padding(top = 8.dp), verticalAlignment = Alignment.CenterVertically) {
                        Column(Modifier.weight(1f)) {
                            Text(f.name, color = C.text, maxLines = 1, overflow = TextOverflow.Ellipsis)
                            Text(Organization.describe(f.rules, names), color = C.text3, fontSize = 12.sp, maxLines = 2, overflow = TextOverflow.Ellipsis)
                        }
                        IconButton(enabled = i > 0, onClick = { change { Organization.moveFolder(f.id, -1) } }) {
                            Icon(Icons.Default.ArrowUpward, "Subir ${f.name}", tint = C.text2)
                        }
                        IconButton(enabled = i < folders.size - 1, onClick = { change { Organization.moveFolder(f.id, 1) } }) {
                            Icon(Icons.Default.ArrowDownward, "Bajar ${f.name}", tint = C.text2)
                        }
                        IconButton(onClick = { editing = f }) { Icon(Icons.Default.Edit, "Editar ${f.name}", tint = C.text2) }
                        IconButton(onClick = { change { Organization.deleteFolder(f.id) } }) { Icon(Icons.Default.Delete, "Borrar ${f.name}", tint = C.danger) }
                    }
                }
                TextButton(onClick = { creating = true }, modifier = Modifier.align(Alignment.End)) { Text("Nueva carpeta") }
            }
        },
        confirmButton = { TextButton(onClick = onDismiss) { Text("Listo") } },
    )
    if (creating || editing != null) {
        val current = editing
        FolderEditor(
            initial = current, tabs = tabs,
            onSave = { name, rules ->
                change { if (current != null) Organization.updateFolder(current.id, name, rules) else Organization.createFolder(name, rules) }
                creating = false
                editing = null
            },
            onDismiss = { creating = false; editing = null },
        )
    }
}

@Composable
private fun ChipRow(options: List<Pair<String, String>>, selected: String, onSelect: (String) -> Unit) {
    Row(Modifier.horizontalScroll(rememberScrollState()), horizontalArrangement = Arrangement.spacedBy(6.dp)) {
        options.forEach { (value, label) ->
            FilterChip(selected = selected == value, onClick = { onSelect(value) }, label = { Text(label, maxLines = 1, fontSize = 12.sp) })
        }
    }
}

@Composable
private fun FolderEditor(initial: SmartFolder?, tabs: List<ChatTab>, onSave: (String, FolderRules) -> Unit, onDismiss: () -> Unit) {
    var name by remember { mutableStateOf(initial?.name.orEmpty()) }
    var kind by remember { mutableStateOf(initial?.rules?.kind ?: FolderRules.KIND_ANY) }
    var muted by remember { mutableStateOf(initial?.rules?.muted ?: FolderRules.MUTED_ANY) }
    var unread by remember { mutableStateOf(initial?.rules?.unread ?: false) }
    var pickedTabs by remember { mutableStateOf(initial?.rules?.tabs ?: emptySet()) }
    var picked by remember { mutableStateOf(initial?.rules?.jids ?: emptySet()) }
    var query by remember { mutableStateOf("") }
    var contacts by remember { mutableStateOf(emptyList<Chat>()) }
    LaunchedEffect(query) { contacts = withContext(Dispatchers.IO) { Db.getChats(query, ChatFilter.Inbox, 200) } }
    val rules = FolderRules(pickedTabs, kind, unread, muted, picked)

    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text(if (initial == null) "Nueva carpeta" else "Editar carpeta") },
        text = {
            Column(Modifier.verticalScroll(rememberScrollState()), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Field(name, { name = it }, "Nombre", singleLine = true)
                Text("Tipo de chat", color = C.text2, fontSize = 13.sp)
                ChipRow(
                    listOf(FolderRules.KIND_ANY to "Todos", FolderRules.KIND_INDIVIDUAL to "Individuales", FolderRules.KIND_GROUP to "Grupos"),
                    kind,
                ) { kind = it }
                Text("Notificaciones", color = C.text2, fontSize = 13.sp)
                ChipRow(
                    listOf(FolderRules.MUTED_ANY to "Todos", FolderRules.MUTED_ONLY to "Silenciados", FolderRules.MUTED_NONE to "Sin silenciar"),
                    muted,
                ) { muted = it }
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text("Solo con mensajes sin leer", color = C.text, fontSize = 14.sp, modifier = Modifier.weight(1f))
                    Switch(checked = unread, onCheckedChange = { unread = it })
                }
                if (tabs.isNotEmpty()) {
                    Text("Pestañas (alcanza con una)", color = C.text2, fontSize = 13.sp)
                    tabs.forEach { t ->
                        Row(Modifier.fillMaxWidth().clickable { pickedTabs = if (t.id in pickedTabs) pickedTabs - t.id else pickedTabs + t.id },
                            verticalAlignment = Alignment.CenterVertically) {
                            Checkbox(checked = t.id in pickedTabs, onCheckedChange = { pickedTabs = if (it) pickedTabs + t.id else pickedTabs - t.id })
                            Text(t.name, color = C.text, fontSize = 14.sp)
                        }
                    }
                }
                Text("Contactos elegidos (${picked.size}): entran siempre", color = C.text2, fontSize = 13.sp)
                Field(query, { query = it }, "Buscar contacto o grupo", singleLine = true)
                Box(Modifier.fillMaxWidth().height(190.dp)) {
                    LazyColumn {
                        itemsIndexed(contacts, key = { _, c -> c.jid }) { _, c ->
                            Row(Modifier.fillMaxWidth().clickable { picked = if (c.jid in picked) picked - c.jid else picked + c.jid },
                                verticalAlignment = Alignment.CenterVertically) {
                                Checkbox(checked = c.jid in picked, onCheckedChange = { picked = if (it) picked + c.jid else picked - c.jid })
                                Text(c.name, color = C.text, fontSize = 14.sp, maxLines = 1, overflow = TextOverflow.Ellipsis)
                            }
                        }
                    }
                }
            }
        },
        confirmButton = {
            TextButton(enabled = name.isNotBlank() && !rules.isEmpty, onClick = { onSave(name, rules) }) { Text("Guardar") }
        },
        dismissButton = { TextButton(onClick = onDismiss) { Text("Cancelar") } },
    )
}

// ─── Galería del chat ────────────────────────────────────────────────────────────────────────────

private const val GALLERY_PAGE = 60
private val downloadSlots = Semaphore(2)                          // a lo sumo 2 descargas a la vez
private val failedDownloads: MutableSet<String> = Collections.synchronizedSet(HashSet())   // no se reintentan en cada scroll

/**
 * Fotos y videos, documentos, links y audios de un chat. Se carga de a 60 al llegar cerca del final; las miniaturas las
 * decodifica Coil ya reducidas (con su caché) y las fotos que todavía no se bajaron se descargan al verlas.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun GalleryScreen(jid: String, onBack: () -> Unit) {
    var chatName by remember { mutableStateOf("") }
    var tab by rememberSaveable { mutableIntStateOf(0) }
    LaunchedEffect(jid) { chatName = withContext(Dispatchers.IO) { Db.getChat(jid)?.name.orEmpty() } }
    val kinds = listOf(
        Organization.GALLERY_MEDIA to "Fotos y videos", Organization.GALLERY_DOCS to "Documentos",
        Organization.GALLERY_LINKS to "Links", Organization.GALLERY_AUDIO to "Audios",
    )
    Scaffold(topBar = {
        TopAppBar(
            title = { Text("Galería · $chatName", maxLines = 1, overflow = TextOverflow.Ellipsis) },
            navigationIcon = { IconButton(onClick = onBack) { Icon(Icons.AutoMirrored.Filled.ArrowBack, "Volver") } },
        )
    }) { pad ->
        Column(Modifier.padding(pad).fillMaxSize().background(C.panel)) {
            ScrollableTabRow(selectedTabIndex = tab, edgePadding = 0.dp) {
                kinds.forEachIndexed { i, k ->
                    Tab(selected = tab == i, onClick = { tab = i }, text = { Text(k.second, maxLines = 1) })
                }
            }
            key(tab) { GalleryTab(jid, kinds[tab].first) }
        }
    }
}

@Composable
private fun GalleryTab(jid: String, kind: String) {
    val c = LocalContext.current
    val scope = rememberCoroutineScope()
    val messages = remember { mutableStateListOf<Message>() }
    val links = remember { mutableStateListOf<GalleryLink>() }
    val paths = remember { mutableStateMapOf<String, String>() }      // id de mensaje -> archivo ya bajado
    var offset by remember { mutableIntStateOf(0) }
    var done by remember { mutableStateOf(false) }
    var loading by remember { mutableStateOf(false) }
    var viewer by remember { mutableStateOf<String?>(null) }

    fun loadMore() {
        if (loading || done) return
        loading = true
        scope.launch {
            val page = withContext(Dispatchers.IO) { Organization.gallery(jid, kind, GALLERY_PAGE, offset) }
            messages.addAll(page.messages)
            links.addAll(page.links)
            offset += page.read
            if (page.read < GALLERY_PAGE) done = true
            loading = false
        }
    }
    LaunchedEffect(Unit) { loadMore() }

    /** Abre el archivo del mensaje; si todavía no se bajó, lo baja ahora (bajo demanda). */
    fun open(m: Message) {
        val local = (paths[m.id] ?: m.mediaPath)?.takeIf { File(it).exists() }
        fun show(path: String) { if (m.mediaType == "image") viewer = path else openFile(c, path, m.text.ifEmpty { null }) }
        if (local != null) { show(local); return }
        toast(c, "Descargando…")
        scope.launch {
            runCatching { withContext(Dispatchers.IO) { Actions.downloadMedia(m) } }
                .onSuccess { paths[m.id] = it; show(it) }
                .onFailure { toast(c, it.message ?: "No se pudo descargar el archivo.") }
        }
    }

    if (messages.isEmpty() && links.isEmpty() && done) {
        Box(Modifier.fillMaxSize().padding(32.dp), contentAlignment = Alignment.Center) {
            Text("No hay nada en esta sección.", color = C.text2)
        }
    }
    when (kind) {
        Organization.GALLERY_MEDIA -> LazyVerticalGrid(columns = GridCells.Adaptive(96.dp), modifier = Modifier.fillMaxSize()) {
            itemsIndexed(messages, key = { _, m -> m.id }) { i, m ->
                if (i >= messages.size - 12) LaunchedEffect(messages.size) { loadMore() }
                MediaCell(m, paths[m.id]) { open(m) }
            }
        }
        Organization.GALLERY_LINKS -> LazyColumn(contentPadding = PaddingValues(bottom = 24.dp)) {
            itemsIndexed(links) { i, l ->
                if (i >= links.size - 10) LaunchedEffect(links.size) { loadMore() }
                Row(
                    Modifier.fillMaxWidth().clickable {
                        runCatching { c.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(l.url)).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)) }
                    }.padding(horizontal = 14.dp, vertical = 10.dp),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Icon(Icons.Default.Link, null, tint = C.accent)
                    Column(Modifier.padding(start = 12.dp)) {
                        Text(l.url, color = C.link, fontSize = 14.sp, maxLines = 2, overflow = TextOverflow.Ellipsis)
                        Text(dayLabel(l.timestamp), color = C.text3, fontSize = 12.sp)
                    }
                }
            }
        }
        else -> LazyColumn(contentPadding = PaddingValues(bottom = 24.dp)) {
            itemsIndexed(messages, key = { _, m -> m.id }) { i, m ->
                if (i >= messages.size - 10) LaunchedEffect(messages.size) { loadMore() }
                val isAudio = kind == Organization.GALLERY_AUDIO
                Row(
                    Modifier.fillMaxWidth().clickable { open(m) }.padding(horizontal = 14.dp, vertical = 10.dp),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Icon(if (isAudio) Icons.Default.Mic else Icons.Default.Description, null, tint = C.accent)
                    Column(Modifier.padding(start = 12.dp)) {
                        Text(
                            if (isAudio) "Nota de voz" else m.text.ifEmpty { "Documento" },
                            color = C.text, fontSize = 14.sp, maxLines = 1, overflow = TextOverflow.Ellipsis,
                        )
                        Text(dayLabel(m.timestamp) + " · " + timeOf(m.timestamp), color = C.text3, fontSize = 12.sp)
                        if (isAudio && !m.transcript.isNullOrBlank()) {
                            Text(m.transcript, color = C.text2, fontSize = 12.sp, maxLines = 2, overflow = TextOverflow.Ellipsis)
                        }
                    }
                }
            }
        }
    }
    viewer?.let { ImageViewer(it) { viewer = null } }
}

/** Una celda cuadrada: miniatura de la foto (la baja si hace falta) o ícono del video. */
@Composable
private fun MediaCell(m: Message, downloaded: String?, onClick: () -> Unit) {
    val ctx = LocalContext.current
    val file = (downloaded ?: m.mediaPath)?.let { File(it) }?.takeIf { it.exists() }
    val isImage = m.mediaType == "image"
    var downloading by remember(m.id) { mutableStateOf(false) }
    // Descarga bajo demanda: solo las fotos que llegan a verse (los videos se bajan al tocarlos)
    LaunchedEffect(m.id, file) {
        if (file == null && isImage && m.mediaRaw != null && m.id !in failedDownloads) {
            downloading = true
            downloadSlots.withPermit {
                runCatching { withContext(Dispatchers.IO) { Actions.downloadMedia(m) } }
                    .onFailure { failedDownloads.add(m.id) }
            }
            downloading = false
        }
    }
    Box(Modifier.aspectRatio(1f).padding(1.dp).background(C.raised).clickable(onClick = onClick), contentAlignment = Alignment.Center) {
        if (file != null && isImage) {
            AsyncImage(
                model = ImageRequest.Builder(ctx).data(file).size(256).build(), contentDescription = "Foto",
                contentScale = ContentScale.Crop, modifier = Modifier.fillMaxSize(),
            )
        } else if (downloading) {
            CircularProgressIndicator(Modifier.size(22.dp), strokeWidth = 2.dp)
        } else {
            Icon(if (isImage) Icons.Default.Image else Icons.Default.Videocam, if (isImage) "Foto" else "Video", tint = C.text3, modifier = Modifier.size(32.dp))
        }
    }
}
