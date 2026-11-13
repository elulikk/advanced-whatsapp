package com.advancedwhatsapp.app.ui

import android.content.Context
import android.net.Uri
import android.provider.OpenableColumns
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material.icons.filled.MoreVert
import androidx.compose.material.icons.filled.PushPin
import androidx.compose.material.icons.filled.Star
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.Checkbox
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.RadioButton
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.advancedwhatsapp.app.App
import com.advancedwhatsapp.app.data.Chat
import com.advancedwhatsapp.app.data.Db
import com.advancedwhatsapp.app.data.Extras
import com.advancedwhatsapp.app.data.Message
import com.advancedwhatsapp.app.data.PollView
import com.advancedwhatsapp.app.data.StarredItem
import com.advancedwhatsapp.app.data.Template
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

// Mensajería extra (Android): respuestas automáticas, plantillas, destacados, encuestas, envío a varios, fijados,
// eliminados/ediciones y repetición de programados. Equivale a app/ui/extras_dialogs.py en la PC.

private val DAY_LABELS = listOf("Dom", "Lun", "Mar", "Mié", "Jue", "Vie", "Sáb")

@Composable
private fun Pill(text: String, selected: Boolean, onClick: () -> Unit) {
    Text(
        text, color = if (selected) C.text else C.text2, fontSize = 13.sp,
        modifier = Modifier.background(if (selected) C.accentDim else C.raised, RoundedCornerShape(14.dp))
            .clickable(onClick = onClick).padding(horizontal = 10.dp, vertical = 6.dp),
    )
}

@Composable
private fun ExtrasToggle(label: String, value: Boolean, onChange: (Boolean) -> Unit) {
    Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
        Text(label, color = C.text, modifier = Modifier.weight(1f))
        Switch(checked = value, onCheckedChange = onChange)
    }
}

private fun dateText(ms: Long): String = SimpleDateFormat("dd/MM/yyyy HH:mm", Locale.getDefault()).format(Date(ms))

// ─── Pantalla contenedora ────────────────────────────────────────────────────

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun ExtrasHost(section: String, onBack: () -> Unit, onNav: (String) -> Unit, onOpenChat: (String) -> Unit) {
    val title = when (section) {
        "autoreply" -> "Respuestas automáticas"
        "templates" -> "Plantillas y atajos"
        "starred" -> "Mensajes destacados"
        "sendmany" -> "Enviar a varios"
        else -> "Mensajería"
    }
    Scaffold(topBar = {
        TopAppBar(title = { Text(title) }, navigationIcon = {
            IconButton(onClick = onBack) { Icon(Icons.AutoMirrored.Filled.ArrowBack, "Volver") }
        })
    }) { pad ->
        Box(Modifier.padding(pad).fillMaxSize().background(C.panel)) {
            when (section) {
                "autoreply" -> AutoReplyScreen()
                "templates" -> TemplatesScreen()
                "starred" -> StarredScreen(onOpenChat)
                "sendmany" -> SendManyScreen(onDone = onBack)
                else -> ExtrasHome(onNav)
            }
        }
    }
}

@Composable
private fun ExtrasHome(onNav: (String) -> Unit) {
    var keep by remember { mutableStateOf(Extras.keepDeleted) }
    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(14.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
        ExtrasToggle("Conservar lo que se elimina y las versiones anteriores de lo que se edita", keep) {
            keep = it
            Extras.keepDeleted = it
        }
        Text(
            "En la burbuja aparece «Ver original» en un mensaje eliminado y «Editado» con el historial de versiones. " +
                "Solo se guarda en este teléfono, desde que se activa el ajuste.",
            color = C.text2, fontSize = 13.sp,
        )
        OutlinedButton(onClick = { onNav("autoreply") }, modifier = Modifier.fillMaxWidth()) { Text("Respuestas automáticas") }
        Text("Por horario o con un modo manual («Estoy manejando»). Funcionan con la app cerrada.", color = C.text2, fontSize = 13.sp)
        OutlinedButton(onClick = { onNav("templates") }, modifier = Modifier.fillMaxWidth()) { Text("Plantillas y atajos") }
        Text("Escribí /atajo en un chat para insertar un texto guardado.", color = C.text2, fontSize = 13.sp)
        OutlinedButton(onClick = { onNav("starred") }, modifier = Modifier.fillMaxWidth()) { Text("Mensajes destacados") }
        OutlinedButton(onClick = { onNav("sendmany") }, modifier = Modifier.fillMaxWidth()) { Text("Enviar a varios") }
        Text("Un mismo mensaje o archivo a muchos contactos, por separado y con pausas entre envíos.", color = C.text2, fontSize = 13.sp)
    }
}

// ─── Elegir contactos ────────────────────────────────────────────────────────

@Composable
private fun ChatPickerDialog(title: String, selected: Set<String>, onDone: (List<Chat>) -> Unit, onDismiss: () -> Unit) {
    var search by remember { mutableStateOf("") }
    var chats by remember { mutableStateOf(emptyList<Chat>()) }
    val picked = remember { mutableStateListOf<String>().also { it.addAll(selected) } }
    val known = remember { HashMap<String, Chat>() }
    LaunchedEffect(search) {
        val list = withContext(Dispatchers.IO) { Db.getChats(search, null, 300) }
        list.forEach { known[it.jid] = it }
        chats = list
    }
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text(title) },
        text = {
            Column {
                Field(search, { search = it }, "Buscar contacto", singleLine = true)
                LazyColumn(Modifier.height(320.dp)) {
                    items(chats.size, key = { chats[it].jid }) { i ->
                        val c = chats[i]
                        Row(
                            Modifier.fillMaxWidth().clickable { if (c.jid in picked) picked.remove(c.jid) else picked.add(c.jid) },
                            verticalAlignment = Alignment.CenterVertically,
                        ) {
                            Checkbox(checked = c.jid in picked, onCheckedChange = { if (it) picked.add(c.jid) else picked.remove(c.jid) })
                            Text(c.name.ifEmpty { c.phone.orEmpty() }, color = C.text, maxLines = 1, overflow = TextOverflow.Ellipsis)
                        }
                    }
                }
                Text("${picked.size} seleccionado(s)", color = C.text2, fontSize = 12.sp)
            }
        },
        confirmButton = { TextButton(onClick = { onDone(picked.mapNotNull { known[it] ?: Db.getChat(it) }) }) { Text("Elegir") } },
        dismissButton = { TextButton(onClick = onDismiss) { Text("Cancelar") } },
    )
}

// ─── Respuestas automáticas ──────────────────────────────────────────────────

@Composable
private fun AutoReplyScreen() {
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    var cfg by remember { mutableStateOf<JSONObject?>(null) }
    var error by remember { mutableStateOf<String?>(null) }
    var editIndex by remember { mutableIntStateOf(-1) }
    var editRule by remember { mutableStateOf<JSONObject?>(null) }

    LaunchedEffect(Unit) {
        withContext(Dispatchers.IO) { runCatching { Extras.autoReplyConfig() } }
            .onSuccess { cfg = it }.onFailure { error = it.message ?: "No se pudo leer la configuración" }
    }

    /** Guarda en el gateway y refresca la pantalla con una copia (así Compose se entera del cambio). */
    fun persist(next: JSONObject) {
        cfg = JSONObject(next.toString())
        scope.launch(Dispatchers.IO) {
            runCatching { Extras.saveAutoReply(next) }.onFailure {
                withContext(Dispatchers.Main) { toast(context, it.message ?: "No se pudo guardar") }
            }
        }
    }

    val c = cfg
    if (c == null) {
        Box(Modifier.fillMaxSize().padding(24.dp), contentAlignment = Alignment.Center) {
            Text(error ?: "Cargando…", color = C.text2)
        }
        return
    }
    val rules = c.optJSONArray("rules") ?: JSONArray()
    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(14.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
        ExtrasToggle("Responder automáticamente los mensajes que lleguen", c.optBoolean("enabled")) {
            persist(JSONObject(c.toString()).put("enabled", it))
        }
        Text(
            "Se responde solo a mensajes nuevos, con una pausa corta, aunque la app esté en segundo plano. " +
                "Cada contacto recibe como máximo una respuesta por regla en el tiempo que elijas.",
            color = C.text2, fontSize = 13.sp,
        )
        for (i in 0 until rules.length()) {
            val r = rules.getJSONObject(i)
            val manual = r.optString("kind") == "manual"
            val active = c.optString("active_rule") == r.optString("id")
            Column(Modifier.fillMaxWidth().background(C.surface, RoundedCornerShape(10.dp)).padding(10.dp)) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text(
                        r.optString("name") + if (active) " · ACTIVADO" else "", color = if (active) C.accent else C.text,
                        fontWeight = FontWeight.Medium, modifier = Modifier.weight(1f),
                    )
                    Switch(checked = r.optBoolean("enabled", true), onCheckedChange = { on ->
                        val next = JSONObject(c.toString())
                        next.getJSONArray("rules").getJSONObject(i).put("enabled", on)
                        persist(next)
                    })
                }
                Text(r.optString("text"), color = C.text2, fontSize = 13.sp, maxLines = 2, overflow = TextOverflow.Ellipsis)
                Text(Extras.describeRule(r), color = C.text3, fontSize = 12.sp)
                Row {
                    if (manual) {
                        TextButton(onClick = {
                            val next = JSONObject(c.toString())
                            if (active) next.put("active_rule", JSONObject.NULL) else next.put("active_rule", r.optString("id")).put("enabled", true)
                            // El gateway guarda el modo y lo enciende; se manda la configuración entera
                            persist(next)
                        }) { Text(if (active) "Apagar este modo" else "Encender este modo") }
                    }
                    TextButton(onClick = {
                        editIndex = i
                        editRule = JSONObject(r.toString())
                    }) { Text("Editar") }
                    TextButton(onClick = {
                        val next = JSONObject(c.toString())
                        val arr = JSONArray()
                        val old = next.getJSONArray("rules")
                        for (k in 0 until old.length()) if (k != i) arr.put(old.getJSONObject(k))
                        next.put("rules", arr)
                        if (active) next.put("active_rule", JSONObject.NULL)
                        persist(next)
                    }) { Text("Quitar", color = C.danger) }
                }
            }
        }
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            OutlinedButton(onClick = { editIndex = -1; editRule = Extras.newRule(false) }) { Text("Agregar horario") }
            OutlinedButton(onClick = { editIndex = -1; editRule = Extras.newRule(true) }) { Text("Agregar modo manual") }
        }
    }
    editRule?.let { rule ->
        RuleDialog(rule, onDismiss = { editRule = null }, onSave = { saved ->
            val next = JSONObject(c.toString())
            val arr = next.optJSONArray("rules") ?: JSONArray()
            if (editIndex in 0 until arr.length()) arr.put(editIndex, saved) else arr.put(saved)
            next.put("rules", arr)
            editRule = null
            persist(next)
        })
    }
}

private val TIME = Regex("^([01]?\\d|2[0-3]):[0-5]\\d$")

@Composable
private fun RuleDialog(rule: JSONObject, onDismiss: () -> Unit, onSave: (JSONObject) -> Unit) {
    val context = LocalContext.current
    val manual = rule.optString("kind") == "manual"
    var name by remember { mutableStateOf(rule.optString("name")) }
    var text by remember { mutableStateOf(rule.optString("text")) }
    val days = remember {
        mutableStateListOf<Int>().also { l -> rule.optJSONArray("days")?.let { a -> for (k in 0 until a.length()) l.add(a.getInt(k)) } }
    }
    var from by remember { mutableStateOf(rule.optString("from", "22:00")) }
    var to by remember { mutableStateOf(rule.optString("to", "08:00")) }
    var individual by remember { mutableStateOf(rule.optBoolean("individual_only", true)) }
    var cooldown by remember { mutableStateOf(rule.optInt("cooldown_hours", 12).toString()) }
    var exclude by remember { mutableStateOf(rule.optJSONArray("exclude") ?: JSONArray()) }
    var only by remember { mutableStateOf(rule.optJSONArray("only") ?: JSONArray()) }
    var picker by remember { mutableStateOf("") }

    fun ids(a: JSONArray): Set<String> = (0 until a.length()).map { a.getString(it) }.toSet()

    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text(if (manual) "Modo manual" else "Respuesta por horario") },
        text = {
            Column(Modifier.verticalScroll(rememberScrollState()), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Field(name, { name = it }, "Nombre", singleLine = true)
                Field(text, { text = it }, "Mensaje que se responde", minLines = 2)
                if (!manual) {
                    Text("Días (sin marcar = todos)", color = C.text2, fontSize = 12.sp)
                    Row(horizontalArrangement = Arrangement.spacedBy(4.dp)) {
                        DAY_LABELS.forEachIndexed { d, label ->
                            Pill(label, d in days) { if (d in days) days.remove(d) else days.add(d) }
                        }
                    }
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        Field(from, { from = it }, "Desde (HH:mm)", Modifier.weight(1f), singleLine = true)
                        Field(to, { to = it }, "Hasta (HH:mm)", Modifier.weight(1f), singleLine = true)
                    }
                    Text("Si «hasta» es menor que «desde», la franja cruza la medianoche.", color = C.text3, fontSize = 12.sp)
                }
                ExtrasToggle("Solo en chats individuales (no en grupos)", individual) { individual = it }
                Field(cooldown, { cooldown = it.filter { ch -> ch.isDigit() }.take(3) }, "No responder dos veces al mismo contacto en (horas)", singleLine = true)
                OutlinedButton(onClick = { picker = "exclude" }, modifier = Modifier.fillMaxWidth()) { Text("Excepciones: ${exclude.length()} contacto(s)") }
                OutlinedButton(onClick = { picker = "only" }, modifier = Modifier.fillMaxWidth()) {
                    Text("Responder solo a: ${if (only.length() == 0) "todos" else only.length().toString()}")
                }
                Text("Excepciones: nunca se les responde. «Solo a»: si elegís contactos, se responde únicamente a ellos.", color = C.text3, fontSize = 12.sp)
            }
        },
        confirmButton = {
            TextButton(onClick = {
                when {
                    text.isBlank() -> toast(context, "Escribí el texto de la respuesta.")
                    !manual && !(TIME.matches(from.trim()) && TIME.matches(to.trim())) -> toast(context, "El horario no es válido.")
                    else -> onSave(
                        JSONObject(rule.toString()).put("name", name.trim().ifEmpty { "Respuesta" }).put("text", text.trim())
                            .put("days", JSONArray(days.sorted())).put("from", from.trim()).put("to", to.trim())
                            .put("individual_only", individual).put("cooldown_hours", cooldown.toIntOrNull() ?: 0)
                            .put("exclude", exclude).put("only", only),
                    )
                }
            }) { Text("Guardar") }
        },
        dismissButton = { TextButton(onClick = onDismiss) { Text("Cancelar") } },
    )
    if (picker.isNotEmpty()) {
        val current = if (picker == "exclude") exclude else only
        ChatPickerDialog(
            if (picker == "exclude") "Excepciones (no responder)" else "Responder solo a", ids(current),
            onDone = { chats ->
                val arr = JSONArray().also { a -> chats.forEach { a.put(it.jid) } }
                if (picker == "exclude") exclude = arr else only = arr
                picker = ""
            },
            onDismiss = { picker = "" },
        )
    }
}

// ─── Plantillas ──────────────────────────────────────────────────────────────

@Composable
private fun TemplatesScreen() {
    val context = LocalContext.current
    val items = remember { mutableStateListOf<Template>().also { it.addAll(Extras.templates()) } }
    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(14.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
        Text("Escribí /atajo en cualquier chat y un espacio: se reemplaza por el texto guardado (o tocá la sugerencia).", color = C.text2, fontSize = 13.sp)
        items.forEachIndexed { i, t ->
            Column(Modifier.fillMaxWidth().background(C.surface, RoundedCornerShape(10.dp)).padding(10.dp)) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Field(t.shortcut, { items[i] = t.copy(shortcut = it.filter { ch -> ch.isLetterOrDigit() || ch == '_' || ch == '-' }) },
                        "Atajo (sin la barra)", Modifier.weight(1f), singleLine = true)
                    IconButton(onClick = { items.removeAt(i) }) { Icon(Icons.Default.Delete, "Quitar", tint = C.danger) }
                }
                Field(t.text, { items[i] = t.copy(text = it) }, "Texto", minLines = 2)
            }
        }
        OutlinedButton(onClick = {
            var n = 1
            while (items.any { it.shortcut == "atajo$n" }) n++
            items.add(Template("atajo$n", ""))
        }) { Text("Agregar plantilla") }
        Button(onClick = {
            if (Extras.saveTemplates(items.toList())) toast(context, "Plantillas guardadas") else toast(context, "No se pudieron guardar")
        }, modifier = Modifier.fillMaxWidth()) { Text("Guardar") }
    }
}

/** Sugerencias de plantillas sobre el teclado mientras se escribe «/algo». */
@Composable
fun TemplateChips(draft: String, onPick: (Template) -> Unit) {
    val found = remember(draft) { Extras.suggestions(draft) }
    if (found.isEmpty()) return
    LazyRow(Modifier.fillMaxWidth().background(C.surface).padding(horizontal = 8.dp, vertical = 6.dp), horizontalArrangement = Arrangement.spacedBy(6.dp)) {
        items(found.size) { i ->
            val t = found[i]
            Text(
                "/${t.shortcut} · ${t.text.replace('\n', ' ').take(28)}", color = C.text, fontSize = 13.sp, maxLines = 1,
                modifier = Modifier.background(C.accentDim, RoundedCornerShape(14.dp)).clickable { onPick(t) }.padding(horizontal = 10.dp, vertical = 6.dp),
            )
        }
    }
}

// ─── Destacados ──────────────────────────────────────────────────────────────

@Composable
private fun StarredScreen(onOpenChat: (String) -> Unit) {
    var search by remember { mutableStateOf("") }
    var list by remember { mutableStateOf(emptyList<StarredItem>()) }
    var reload by remember { mutableIntStateOf(0) }
    LaunchedEffect(search, reload) { list = withContext(Dispatchers.IO) { Db.starredMessages(search) } }
    Column(Modifier.fillMaxSize()) {
        Field(search, { search = it }, "Buscar en los destacados", Modifier.fillMaxWidth().padding(12.dp), singleLine = true)
        if (list.isEmpty()) {
            Box(Modifier.fillMaxSize().padding(32.dp), contentAlignment = Alignment.Center) {
                Text("Sin destacados. Mantené apretado un mensaje › Destacar.", color = C.text2)
            }
        }
        LazyColumn(Modifier.fillMaxSize()) {
            items(list.size, key = { list[it].message.id }) { i ->
                val s = list[i]
                val m = s.message
                Row(Modifier.fillMaxWidth().clickable { onOpenChat(m.chatJid) }.padding(start = 14.dp, top = 8.dp, bottom = 8.dp), verticalAlignment = Alignment.CenterVertically) {
                    Column(Modifier.weight(1f)) {
                        Text("${s.chatName} · ${if (m.fromMe) "Tú" else m.senderName.orEmpty()} · ${dateText(m.timestamp * 1000)}", color = C.accent, fontSize = 12.sp, maxLines = 1, overflow = TextOverflow.Ellipsis)
                        Text(
                            (m.text.ifEmpty { m.transcript.orEmpty() }).ifEmpty { Db.preview(m.mediaType, "") },
                            color = C.text, fontSize = 14.sp, maxLines = 3, overflow = TextOverflow.Ellipsis,
                        )
                    }
                    IconButton(onClick = {
                        Db.setStarred(m.id, false)
                        reload++
                    }) { Icon(Icons.Default.Star, "Quitar destacado", tint = C.ember) }
                }
            }
        }
    }
}

// ─── Enviar a varios ─────────────────────────────────────────────────────────

private fun copyPicked(context: Context, uri: Uri): Triple<File, String, String?>? = runCatching {
    var name = "archivo"
    context.contentResolver.query(uri, null, null, null, null)?.use { c ->
        val idx = c.getColumnIndex(OpenableColumns.DISPLAY_NAME)
        if (idx >= 0 && c.moveToFirst()) name = c.getString(idx) ?: name
    }
    val mime = context.contentResolver.getType(uri)
    val out = File(App.sentDir, "multi_${System.currentTimeMillis()}_${name.replace(Regex("[^\\w.-]"), "_")}")
    context.contentResolver.openInputStream(uri)!!.use { input -> out.outputStream().use { input.copyTo(it) } }
    Triple(out, name, mime)
}.getOrNull()

@Composable
private fun SendManyScreen(onDone: () -> Unit) {
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    var targets by remember { mutableStateOf(emptyList<Chat>()) }
    var text by remember { mutableStateOf("") }
    var file by remember { mutableStateOf<Triple<File, String, String?>?>(null) }
    var delay by remember { mutableStateOf("5") }
    var picking by remember { mutableStateOf(false) }
    var busy by remember { mutableStateOf(false) }
    val pickFile = rememberLauncherForActivityResult(ActivityResultContracts.OpenDocument()) { uri ->
        if (uri != null) scope.launch(Dispatchers.IO) { file = copyPicked(context, uri) }
    }
    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(14.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
        Text(
            "El mismo mensaje llega a cada contacto por separado (no es un grupo ni una lista de difusión), con una pausa entre envíos " +
                "para no parecer spam. Sale aunque cierres la app.",
            color = C.text2, fontSize = 13.sp,
        )
        OutlinedButton(onClick = { picking = true }, modifier = Modifier.fillMaxWidth()) {
            Text(if (targets.isEmpty()) "Elegir contactos…" else "Contactos elegidos: ${targets.size}")
        }
        Field(text, { text = it }, "Mensaje (o descripción del archivo)", minLines = 3)
        Row(verticalAlignment = Alignment.CenterVertically) {
            OutlinedButton(onClick = { pickFile.launch(arrayOf("*/*")) }) { Text(if (file == null) "Adjuntar archivo…" else "Cambiar archivo") }
            file?.let { Text(it.second, color = C.text2, fontSize = 13.sp, maxLines = 1, overflow = TextOverflow.Ellipsis, modifier = Modifier.padding(start = 8.dp)) }
        }
        Field(delay, { delay = it.filter { ch -> ch.isDigit() }.take(3) }, "Pausa mínima entre envíos (segundos, mínimo 3)", singleLine = true)
        Button(
            enabled = !busy, modifier = Modifier.fillMaxWidth(),
            onClick = {
                val f = file
                when {
                    targets.isEmpty() -> toast(context, "Elegí al menos un contacto.")
                    text.isBlank() && f == null -> toast(context, "Escribí un mensaje o adjuntá un archivo.")
                    else -> {
                        busy = true
                        val min = (delay.toIntOrNull() ?: 5).coerceAtLeast(3)
                        val kind = when {
                            f?.third?.startsWith("image/") == true -> "image"
                            f?.third?.startsWith("video/") == true -> "video"
                            else -> "document"
                        }
                        scope.launch(Dispatchers.IO) {
                            val r = runCatching { Extras.sendToMany(targets, text.trim(), f?.first, kind, f?.second, min, min + 7) }
                            withContext(Dispatchers.Main) {
                                busy = false
                                r.onSuccess {
                                    toast(context, "Empezó el envío a ${targets.size} contacto(s). Va saliendo de a uno.")
                                    onDone()
                                }.onFailure { toast(context, it.message ?: "No se pudo iniciar el envío") }
                            }
                        }
                    }
                }
            },
        ) { Text("Enviar") }
    }
    if (picking) {
        ChatPickerDialog("Enviar a…", targets.map { it.jid }.toSet(), onDone = { targets = it; picking = false }, onDismiss = { picking = false })
    }
}

// ─── Encuestas ───────────────────────────────────────────────────────────────

@Composable
private fun PollDialog(jid: String, onDismiss: () -> Unit, onResult: (String) -> Unit) {
    val scope = rememberCoroutineScope()
    var question by remember { mutableStateOf("") }
    val options = remember { mutableStateListOf("", "") }
    var multiple by remember { mutableStateOf(false) }
    var busy by remember { mutableStateOf(false) }
    AlertDialog(
        onDismissRequest = { if (!busy) onDismiss() },
        title = { Text("Crear encuesta") },
        text = {
            Column(Modifier.verticalScroll(rememberScrollState()), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                Field(question, { question = it.take(250) }, "Pregunta", singleLine = true)
                options.forEachIndexed { i, o -> Field(o, { options[i] = it.take(100) }, "Opción ${i + 1}", singleLine = true) }
                if (options.size < 12) TextButton(onClick = { options.add("") }) { Text("Agregar opción") }
                ExtrasToggle("Permitir elegir varias opciones", multiple) { multiple = it }
            }
        },
        confirmButton = {
            TextButton(enabled = !busy, onClick = {
                val clean = options.map { it.trim() }.filter { it.isNotEmpty() }.distinct()
                when {
                    question.isBlank() -> onResult("Escribí la pregunta.")
                    clean.size < 2 -> onResult("Poné entre 2 y 12 opciones distintas.")
                    else -> {
                        busy = true
                        scope.launch(Dispatchers.IO) {
                            val r = runCatching { Extras.sendPoll(jid, question.trim(), clean, multiple) }
                            withContext(Dispatchers.Main) {
                                busy = false
                                if (r.isSuccess) onDismiss() else onResult(r.exceptionOrNull()?.message ?: "No se pudo enviar la encuesta.")
                            }
                        }
                    }
                }
            }) { Text("Enviar") }
        },
        dismissButton = { TextButton(enabled = !busy, onClick = onDismiss) { Text("Cancelar") } },
    )
}

/** Cuerpo de una burbuja de encuesta: pregunta, opciones con barra y votos. */
@Composable
fun PollContent(m: Message) {
    val poll: PollView? = remember(m.pollJson) { Extras.parsePoll(m) }
    if (poll == null) {
        Text(m.text, color = C.text, fontSize = 15.sp)
        return
    }
    val top = poll.options.maxOf { it.votes }.coerceAtLeast(1)
    Column(verticalArrangement = Arrangement.spacedBy(5.dp)) {
        Text("📊 ${poll.name}", color = C.text, fontSize = 15.sp, fontWeight = FontWeight.Medium)
        Text(if (poll.multi) "Elegí las opciones que quieras" else "Elegí una opción", color = C.text3, fontSize = 11.sp)
        poll.options.forEach { o ->
            Column {
                Row {
                    Text(o.name + if (o.mine) " ✓" else "", color = C.text, fontSize = 14.sp, modifier = Modifier.weight(1f))
                    Text("${o.votes}", color = C.text, fontSize = 14.sp, fontWeight = FontWeight.Bold)
                }
                LinearProgressIndicator(progress = { o.votes.toFloat() / top }, modifier = Modifier.fillMaxWidth(), color = C.accent, trackColor = C.raised)
            }
        }
        Text("${poll.voters} voto${if (poll.voters != 1) "s" else ""}${if (poll.voters == 0) " todavía" else ""}", color = C.text3, fontSize = 11.sp)
    }
}

// ─── Menú del chat, fijados, original / historial ────────────────────────────

/** Botón de tres puntos del chat: encuesta, destacados y plantillas. */
@Composable
fun ChatExtrasMenu(jid: String, onNav: (String) -> Unit, onMessage: (String) -> Unit, more: @Composable (close: () -> Unit) -> Unit = {}) {
    var open by remember { mutableStateOf(false) }
    var poll by remember { mutableStateOf(false) }
    Box {
        IconButton(onClick = { open = true }) { Icon(Icons.Default.MoreVert, "Más") }
        DropdownMenu(expanded = open, onDismissRequest = { open = false }) {
            DropdownMenuItem(text = { Text("Crear encuesta") }, onClick = { open = false; poll = true })
            DropdownMenuItem(text = { Text("Mensajes destacados") }, onClick = { open = false; onNav("starred") })
            DropdownMenuItem(text = { Text("Plantillas y atajos") }, onClick = { open = false; onNav("templates") })
            DropdownMenuItem(text = { Text("Enviar a varios") }, onClick = { open = false; onNav("sendmany") })
            more { open = false }
        }
    }
    if (poll) PollDialog(jid, onDismiss = { poll = false }, onResult = onMessage)
}

/** Barra de mensajes fijados sobre la lista; tocar lleva al mensaje (y pasa al siguiente fijado). */
@Composable
fun PinBar(pins: List<Message>, onGo: (Message) -> Unit, onUnpin: (Message) -> Unit) {
    if (pins.isEmpty()) return
    var index by remember { mutableIntStateOf(0) }
    val i = index % pins.size
    val m = pins[i]
    Row(Modifier.fillMaxWidth().background(C.surface).padding(start = 12.dp), verticalAlignment = Alignment.CenterVertically) {
        Icon(Icons.Default.PushPin, null, tint = C.accent, modifier = Modifier.size(18.dp))
        Text(
            (if (pins.size > 1) "(${i + 1}/${pins.size}) " else "") + Db.preview(m.mediaType, m.text).replace('\n', ' '),
            color = C.text, fontSize = 13.sp, maxLines = 1, overflow = TextOverflow.Ellipsis,
            modifier = Modifier.weight(1f).clickable {
                onGo(m)
                index = i + 1
            }.padding(start = 8.dp, top = 10.dp, bottom = 10.dp),
        )
        IconButton(onClick = { onUnpin(m) }) { Icon(Icons.Default.Close, "Desfijar", tint = C.text2) }
    }
}

/** Contenido original de un mensaje eliminado, o sus versiones anteriores si se editó. */
@Composable
fun OriginalDialog(m: Message, edits: Boolean, onDismiss: () -> Unit) {
    var body by remember { mutableStateOf("") }
    LaunchedEffect(m.id, edits) {
        body = withContext(Dispatchers.IO) {
            if (edits) {
                val versions = Db.editsOf(m.id)
                val parts = versions.mapIndexed { i, v -> "Versión ${i + 1} (cambiada el ${dateText(v.second * 1000)}):\n${v.first}" } + "Actual:\n${m.text}"
                if (versions.isEmpty()) "No se guardaron versiones anteriores de este mensaje.\n\nActual:\n${m.text}" else parts.joinToString("\n\n")
            } else {
                val o = Db.revokedOriginal(m.id)
                if (o == null) "No se guardó el contenido de este mensaje (se eliminó antes de activar el ajuste)." else {
                    val kind = o.optString("media_type", "text")
                    val text = o.optString("text").ifEmpty { o.optString("transcript") }
                    if (kind == "text") text else "${Db.preview(kind, "")}\n$text".trim()
                }
            }
        }
    }
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text(if (edits) "Historial de ediciones" else "Mensaje eliminado") },
        text = { Column(Modifier.verticalScroll(rememberScrollState())) { Text(body, color = C.text, fontSize = 14.sp) } },
        confirmButton = { TextButton(onClick = onDismiss) { Text("Cerrar") } },
    )
}

// ─── Repetición de un mensaje programado ─────────────────────────────────────

/** Después de elegir fecha y hora: ¿se repite? Entrega la repetición ({type, days}) o null si no. */
@Composable
fun RepeatDialog(onChoose: (JSONObject?) -> Unit, onDismiss: () -> Unit) {
    val kinds = listOf("" to "No repetir", "daily" to "Todos los días", "weekly" to "Semanal", "monthly" to "Todos los meses", "yearly" to "Todos los años (cumpleaños)")
    var kind by remember { mutableStateOf("") }
    val days = remember { mutableStateListOf<Int>() }
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("Repetir") },
        text = {
            Column {
                kinds.forEach { (k, label) ->
                    Row(Modifier.fillMaxWidth().clickable { kind = k }, verticalAlignment = Alignment.CenterVertically) {
                        RadioButton(selected = kind == k, onClick = { kind = k })
                        Text(label, color = C.text)
                    }
                }
                if (kind == "weekly") {
                    Text("Días (sin marcar = el mismo día de la semana)", color = C.text2, fontSize = 12.sp, modifier = Modifier.padding(top = 6.dp))
                    Row(horizontalArrangement = Arrangement.spacedBy(3.dp), modifier = Modifier.padding(top = 4.dp)) {
                        DAY_LABELS.forEachIndexed { d, label -> Pill(label.take(2), d in days) { if (d in days) days.remove(d) else days.add(d) } }
                    }
                }
            }
        },
        confirmButton = { TextButton(onClick = { onChoose(if (kind.isEmpty()) null else Extras.repeatOf(kind, days.sorted())) }) { Text("Listo") } },
        dismissButton = { TextButton(onClick = onDismiss) { Text("Cancelar") } },
    )
}
