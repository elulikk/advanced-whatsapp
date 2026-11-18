package com.advancedwhatsapp.app.ui

import android.net.Uri
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.combinedClickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.grid.GridCells
import androidx.compose.foundation.lazy.grid.LazyVerticalGrid
import androidx.compose.foundation.lazy.grid.items
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Add
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.Icon
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TextField
import androidx.compose.material3.TextFieldDefaults
import androidx.compose.runtime.Composable
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
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import coil.compose.AsyncImage
import com.advancedwhatsapp.app.data.EmojiCategory
import com.advancedwhatsapp.app.data.Emojis
import com.advancedwhatsapp.app.data.ImportResult
import com.advancedwhatsapp.app.data.Sticker
import com.advancedwhatsapp.app.data.StickerLibrary
import com.advancedwhatsapp.app.data.StickerPack
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

private const val VIEW_RECENTS = "__recientes__"
private const val VIEW_FAVS = "__favoritos__"
private const val VIEW_ALL = "__todos__"

/** Carpetas donde WhatsApp guarda los paquetes de stickers en el teléfono (sugerencia inicial del selector de carpetas). */
private fun waFolder(path: String): Uri =
    Uri.parse("content://com.android.externalstorage.documents/document/primary%3A" + Uri.encode(path))

private val WHATSAPP_STICKERS = waFolder("Android/media/com.whatsapp/WhatsApp/Media/WhatsApp Stickers")
private val BUSINESS_STICKERS = waFolder("Android/media/com.whatsapp.w4b/WhatsApp Business/Media/WhatsApp Business Stickers")

/**
 * Panel de emojis y stickers que ocupa el lugar del teclado (como en WhatsApp).
 * Emojis: recientes, categorías y buscador. Stickers: recientes, favoritos, todos y paquetes, con importación.
 */
@OptIn(ExperimentalFoundationApi::class)
@Composable
fun EmojiStickerPanel(onEmoji: (String) -> Unit, onSticker: (Sticker) -> Unit, onMessage: (String) -> Unit, modifier: Modifier = Modifier) {
    var tab by rememberSaveable { mutableIntStateOf(0) }
    Column(modifier.fillMaxWidth().height(300.dp).background(C.surface)) {
        Row(Modifier.fillMaxWidth().padding(horizontal = 8.dp, vertical = 4.dp), horizontalArrangement = Arrangement.spacedBy(6.dp)) {
            PanelTab("Emojis", tab == 0) { tab = 0 }
            PanelTab("Stickers", tab == 1) { tab = 1 }
        }
        if (tab == 0) EmojiTab(onEmoji) else StickerTab(onSticker, onMessage)
    }
}

@Composable
private fun PanelTab(text: String, selected: Boolean, onClick: () -> Unit) {
    Text(
        text, color = if (selected) C.text else C.text2, fontSize = 14.sp,
        modifier = Modifier.background(if (selected) C.raised else Color.Transparent, RoundedCornerShape(14.dp))
            .clickable(onClick = onClick).padding(horizontal = 14.dp, vertical = 6.dp),
    )
}

@Composable
private fun EmojiTab(onEmoji: (String) -> Unit) {
    var cats by remember { mutableStateOf<List<EmojiCategory>?>(null) }
    var recents by remember { mutableStateOf(emptyList<String>()) }
    var query by remember { mutableStateOf("") }
    var cat by rememberSaveable { mutableIntStateOf(0) }   // 0 = recientes
    LaunchedEffect(Unit) {
        withContext(Dispatchers.IO) {
            val c = Emojis.categories          // se parsea recién acá, la primera vez
            val r = StickerLibrary.recentEmojis()
            withContext(Dispatchers.Main) {
                cats = c
                recents = r
                if (r.isEmpty() && cat == 0) cat = 1
            }
        }
    }
    val categories = cats ?: return
    val shown = when {
        query.isNotBlank() -> Emojis.search(query)
        cat == 0 -> recents
        else -> categories.getOrNull(cat - 1)?.items?.map { it.first }.orEmpty()
    }
    Column(Modifier.fillMaxSize()) {
        TextField(
            value = query, onValueChange = { query = it }, singleLine = true,
            placeholder = { Text("Buscar emoji (ej: corazón, risa, pizza)", fontSize = 13.sp) },
            modifier = Modifier.fillMaxWidth().padding(horizontal = 8.dp), shape = RoundedCornerShape(20.dp),
            colors = TextFieldDefaults.colors(
                focusedIndicatorColor = Color.Transparent, unfocusedIndicatorColor = Color.Transparent,
                focusedContainerColor = C.raised, unfocusedContainerColor = C.raised,
            ),
        )
        LazyRow(Modifier.fillMaxWidth().padding(horizontal = 4.dp, vertical = 2.dp)) {
            val icons = listOf("🕘") + categories.map { it.icon }
            items(icons.size) { i ->
                Text(
                    icons[i], fontSize = 20.sp,
                    modifier = Modifier.padding(horizontal = 2.dp)
                        .background(if (i == cat && query.isBlank()) C.raised else Color.Transparent, RoundedCornerShape(8.dp))
                        .clickable {
                            query = ""
                            cat = i
                        }.padding(horizontal = 8.dp, vertical = 4.dp),
                )
            }
        }
        if (shown.isEmpty()) {
            val msg = if (query.isNotBlank()) "Sin resultados" else "Todavía no usaste emojis: elegí una categoría"
            Text(msg, color = C.text2, fontSize = 13.sp, modifier = Modifier.padding(16.dp))
        }
        LazyVerticalGrid(columns = GridCells.Adaptive(44.dp), modifier = Modifier.fillMaxSize().padding(horizontal = 4.dp)) {
            items(shown) { e ->
                Text(
                    e, fontSize = 26.sp, textAlign = TextAlign.Center,
                    modifier = Modifier.clickable {
                        recents = (listOf(e) + recents.filter { it != e }).take(32)
                        onEmoji(e)
                        // Se guarda en segundo plano: tocar emojis seguidos no espera al disco
                        Thread { runCatching { StickerLibrary.addRecentEmoji(e) } }.start()
                    }.padding(vertical = 6.dp),
                )
            }
        }
    }
}

@OptIn(ExperimentalFoundationApi::class)
@Composable
private fun StickerTab(onSticker: (Sticker) -> Unit, onMessage: (String) -> Unit) {
    val ctx = LocalContext.current
    val scope = rememberCoroutineScope()
    var view by rememberSaveable { mutableStateOf(VIEW_RECENTS) }
    var version by remember { mutableIntStateOf(0) }
    var stickers by remember { mutableStateOf(emptyList<Sticker>()) }
    var packs by remember { mutableStateOf(emptyList<StickerPack>()) }
    var total by remember { mutableIntStateOf(-1) }
    var status by remember { mutableStateOf("") }
    var importing by remember { mutableStateOf(false) }
    var menu by remember { mutableStateOf(false) }
    var newPackDialog by remember { mutableStateOf(false) }
    var stickerMenu by remember { mutableStateOf<Sticker?>(null) }

    LaunchedEffect(view, version) {
        val (list, p, n) = withContext(Dispatchers.IO) {
            val l = when (view) {
                VIEW_RECENTS -> StickerLibrary.recents()
                VIEW_FAVS -> StickerLibrary.favorites()
                VIEW_ALL -> StickerLibrary.list()
                else -> StickerLibrary.list(view)
            }
            Triple(l, StickerLibrary.packs(), StickerLibrary.count())
        }
        stickers = list
        packs = p
        total = n
    }

    fun runImport(job: (progress: (Int, Int) -> Unit) -> ImportResult) {
        if (importing) return
        importing = true
        status = "Importando…"
        scope.launch(Dispatchers.IO) {
            val r = runCatching {
                job { done, all -> if (done == all || done % 5 == 0) status = "Importando… $done de $all" }
            }
            withContext(Dispatchers.Main) {
                importing = false
                status = r.fold({ it.summary() }, { "No se pudo importar: ${it.message}" })
                version++
            }
        }
    }

    val pickTree = rememberLauncherForActivityResult(ActivityResultContracts.OpenDocumentTree()) { uri ->
        if (uri != null) runImport { StickerLibrary.importTree(ctx, uri, it) }
    }
    val pickFiles = rememberLauncherForActivityResult(ActivityResultContracts.OpenMultipleDocuments()) { uris ->
        if (uris.isNotEmpty()) runImport { StickerLibrary.importUris(ctx, uris, it) }
    }

    Column(Modifier.fillMaxSize()) {
        Row(Modifier.fillMaxWidth().padding(start = 4.dp), verticalAlignment = Alignment.CenterVertically) {
            LazyRow(Modifier.weight(1f)) {
                val chips = listOf(VIEW_RECENTS to "🕘 Recientes", VIEW_FAVS to "⭐ Favoritos", VIEW_ALL to "Todos") +
                    packs.map { it.name to "${it.name} (${it.count})" }
                items(chips) { (key, label) ->
                    Text(
                        label, color = if (view == key) C.text else C.text2, fontSize = 13.sp, maxLines = 1,
                        modifier = Modifier.padding(horizontal = 2.dp)
                            .background(if (view == key) C.raised else Color.Transparent, RoundedCornerShape(14.dp))
                            .clickable { view = key }.padding(horizontal = 10.dp, vertical = 6.dp),
                    )
                }
            }
            Box {
                IconButton(onClick = { menu = true }, enabled = !importing) { Icon(Icons.Default.Add, "Importar stickers y paquetes") }
                DropdownMenu(expanded = menu, onDismissRequest = { menu = false }) {
                    DropdownMenuItem(text = { Text("Importar de WhatsApp") }, onClick = {
                        menu = false
                        onMessage("En la carpeta que se abre, tocá «Usar esta carpeta».")
                        pickTree.launch(WHATSAPP_STICKERS)
                    })
                    DropdownMenuItem(text = { Text("Importar de WhatsApp Business") }, onClick = {
                        menu = false
                        onMessage("En la carpeta que se abre, tocá «Usar esta carpeta».")
                        pickTree.launch(BUSINESS_STICKERS)
                    })
                    DropdownMenuItem(text = { Text("Importar stickers de mis chats") }, onClick = {
                        menu = false
                        runImport { StickerLibrary.importFromChats(it) }
                    })
                    DropdownMenuItem(text = { Text("Importar archivos (.webp .png .wastickers)") }, onClick = {
                        menu = false
                        pickFiles.launch(arrayOf("*/*"))
                    })
                    DropdownMenuItem(text = { Text("Nuevo paquete…") }, onClick = {
                        menu = false
                        newPackDialog = true
                    })
                    if (view != VIEW_RECENTS && view != VIEW_FAVS && view != VIEW_ALL) {
                        DropdownMenuItem(text = { Text("Eliminar este paquete", color = C.danger) }, onClick = {
                            menu = false
                            val name = view
                            view = VIEW_RECENTS
                            scope.launch(Dispatchers.IO) {
                                StickerLibrary.deletePack(name)
                                withContext(Dispatchers.Main) { version++ }
                            }
                        })
                    }
                }
            }
        }
        if (status.isNotEmpty()) Text(status, color = C.text2, fontSize = 12.sp, modifier = Modifier.padding(horizontal = 12.dp, vertical = 2.dp))
        if (stickers.isEmpty() && total >= 0) {
            val msg = when {
                total == 0 -> "Todavía no tenés stickers. Con el botón + podés importar los de WhatsApp, los de tus chats o archivos."
                view == VIEW_RECENTS -> "Los stickers que envíes aparecen acá."
                view == VIEW_FAVS -> "Mantené apretado un sticker para marcarlo como favorito."
                else -> "Este paquete está vacío."
            }
            Text(msg, color = C.text2, fontSize = 13.sp, modifier = Modifier.padding(16.dp))
        }
        LazyVerticalGrid(columns = GridCells.Adaptive(82.dp), modifier = Modifier.fillMaxSize().padding(horizontal = 4.dp)) {
            items(stickers, key = { it.id }) { s ->
                Box {
                    AsyncImage(
                        model = s.file, contentDescription = "Sticker", contentScale = ContentScale.Fit,
                        modifier = Modifier.size(82.dp).padding(3.dp).combinedClickable(
                            onClick = { onSticker(s) },
                            onLongClick = { stickerMenu = s },
                        ),
                    )
                    DropdownMenu(expanded = stickerMenu?.id == s.id, onDismissRequest = { stickerMenu = null }) {
                        DropdownMenuItem(text = { Text(if (s.fav) "Quitar de favoritos" else "Agregar a favoritos") }, onClick = {
                            stickerMenu = null
                            scope.launch(Dispatchers.IO) {
                                StickerLibrary.toggleFav(s.id)
                                withContext(Dispatchers.Main) { version++ }
                            }
                        })
                        packs.filter { it.name != s.pack }.take(8).forEach { p ->
                            DropdownMenuItem(text = { Text("Mover a «${p.name}»") }, onClick = {
                                stickerMenu = null
                                scope.launch(Dispatchers.IO) {
                                    StickerLibrary.moveToPack(s.id, p.name)
                                    withContext(Dispatchers.Main) { version++ }
                                }
                            })
                        }
                        DropdownMenuItem(text = { Text("Eliminar de mis stickers", color = C.danger) }, onClick = {
                            stickerMenu = null
                            scope.launch(Dispatchers.IO) {
                                StickerLibrary.deleteSticker(s.id)
                                withContext(Dispatchers.Main) { version++ }
                            }
                        })
                    }
                }
            }
        }
    }

    if (newPackDialog) {
        var name by remember { mutableStateOf("") }
        AlertDialog(
            onDismissRequest = { newPackDialog = false },
            title = { Text("Nuevo paquete") },
            text = { OutlinedTextField(value = name, onValueChange = { name = it }, singleLine = true, label = { Text("Nombre") }) },
            confirmButton = {
                TextButton(onClick = {
                    val n = name.trim()
                    newPackDialog = false
                    if (n.isNotEmpty()) scope.launch(Dispatchers.IO) {
                        StickerLibrary.createPack(n)
                        withContext(Dispatchers.Main) {
                            view = n.take(60)
                            version++
                        }
                    }
                }) { Text("Crear") }
            },
            dismissButton = { TextButton(onClick = { newPackDialog = false }) { Text("Cancelar") } },
        )
    }
}
