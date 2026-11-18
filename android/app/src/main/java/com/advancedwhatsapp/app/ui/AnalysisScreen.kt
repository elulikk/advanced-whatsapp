package com.advancedwhatsapp.app.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.filled.ContentCopy
import androidx.compose.material.icons.filled.History
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilterChip
import androidx.compose.material3.Icon
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalClipboardManager
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.advancedwhatsapp.app.Prefs
import com.advancedwhatsapp.app.ai.AiService
import com.advancedwhatsapp.app.ai.AnalysisStore
import com.advancedwhatsapp.app.ai.Psych
import com.advancedwhatsapp.app.data.Db
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

/** Análisis psicológico de un contacto, o interno (de mí) si jid es Psych.SELF_JID. El análisis solo arranca si lo pedís. */
@OptIn(ExperimentalMaterial3Api::class, ExperimentalLayoutApi::class)
@Composable
fun AnalysisScreen(jid: String, onBack: () -> Unit) {
    val context = LocalContext.current
    val clipboard = LocalClipboardManager.current
    val scope = rememberCoroutineScope()
    val internal = jid == Psych.SELF_JID
    val focus = if (internal) "interno" else "normal"
    val name = remember(jid) { if (internal) "Yo" else Db.getChat(jid)?.name.orEmpty().ifEmpty { "Contacto" } }

    var provider by remember { mutableStateOf(Prefs.provider) }
    var model by remember(provider) { mutableStateOf(AiService.defaultModel(provider)) }
    var models by remember(provider) { mutableStateOf(AiService.fallbackModels(provider)) }
    var length by remember { mutableStateOf("short") }
    var count by remember { mutableIntStateOf(Psych.DEFAULT_MESSAGES) }
    var relation by remember { mutableStateOf(Prefs.getString("relation_$jid", Psych.DEFAULT_KIND)) }
    var kinds by remember { mutableStateOf(AnalysisStore.loadKinds()) }
    var searches by remember { mutableStateOf(AnalysisStore.loadSearches()) }
    var selected by remember { mutableStateOf(setOf<String>()) }
    var newSearch by remember { mutableStateOf("") }
    var notes by remember { mutableStateOf(AnalysisStore.loadNotes(jid)) }
    val saved = remember { AnalysisStore.loadAnalysis(jid, focus) }
    var analysis by remember { mutableStateOf(saved?.text.orEmpty()) }
    var stamp by remember { mutableStateOf(saved?.let { "${it.date} · ${it.provider} ${it.model}" }.orEmpty()) }
    var material by remember { mutableStateOf("") }
    var qa by remember { mutableStateOf(emptyList<Pair<String, String>>()) }
    var question by remember { mutableStateOf("") }
    var busy by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf<String?>(null) }
    var history by remember { mutableStateOf<String?>(null) }

    LaunchedEffect(provider) { models = withContext(Dispatchers.IO) { AiService.listModels(provider) } }

    /** Arma el material y cuenta los mensajes incluidos (y los chats, en el análisis interno). */
    fun buildMaterial(): Triple<String, Int, Int> {
        val budget = Psych.CHAR_BUDGET[provider] ?: 80_000
        if (internal) {
            val chats = Db.recentChatsWithMessages(40, count).map { (c, m) -> c.name to m }
            val s = Psych.formatSelfMaterial(chats, budget)
            return Triple(s.text, s.mine, s.chats)
        }
        val messages = Db.getMessages(jid, count)
        return Triple(Psych.formatConversation(messages, name).takeLast(budget), messages.size, 1)
    }

    fun run() {
        busy = true
        error = null
        AnalysisStore.saveNotes(jid, notes)
        Prefs.provider = provider
        Prefs.setModel(provider, model)
        if (!internal) {
            Prefs.putString("relation_$jid", relation)
            AnalysisStore.rememberKind(relation)
            kinds = AnalysisStore.loadKinds()
        }
        scope.launch {
            val qs = if (internal) emptyList() else selected.toList()
            val out = withContext(Dispatchers.IO) {
                val (text, n, chats) = buildMaterial()
                material = text
                if (text.isBlank()) return@withContext "Error: no hay mensajes para analizar." to n
                val prompt = Psych.buildPrompt(text, focus, length, name, n, chats, notes, relation, qs)
                val res = AiService.ask(prompt, provider, model)
                if (!AiService.isError(res)) AnalysisStore.saveAnalysis(jid, name, res, focus, length, provider, model, n, relation, qs)
                res to n
            }
            busy = false
            if (AiService.isError(out.first)) {
                error = out.first
            } else {
                analysis = out.first.trim()
                stamp = "$provider $model · ${out.second} mensajes"
                qa = emptyList()
            }
        }
    }

    fun ask() {
        val q = question.trim()
        if (q.isEmpty() || analysis.isEmpty()) return
        busy = true
        error = null
        scope.launch {
            val res = withContext(Dispatchers.IO) {
                if (material.isEmpty()) material = buildMaterial().first
                val out = AiService.ask(Psych.buildFollowupPrompt(material, focus, name, analysis, qa, q), provider, model)
                if (!AiService.isError(out)) AnalysisStore.saveQuestion(jid, focus, q, out)
                out
            }
            busy = false
            if (AiService.isError(res)) error = res
            else {
                qa = qa + (q to res.trim())
                question = ""
            }
        }
    }

    Scaffold(topBar = {
        TopAppBar(
            title = { Text(if (internal) "Análisis interno (yo)" else "Análisis: $name", maxLines = 1) },
            navigationIcon = { IconButton(onClick = onBack) { Icon(Icons.AutoMirrored.Filled.ArrowBack, "Volver") } },
            actions = {
                IconButton(onClick = { history = AnalysisStore.loadHistory(jid) }) { Icon(Icons.Default.History, "Historial") }
                if (analysis.isNotEmpty()) {
                    IconButton(onClick = {
                        clipboard.setText(AnnotatedString(Psych.buildExport(focus, name, analysis, qa, material)))
                        toast(context, "Copiado: pegalo en otro chatbot para seguir el análisis.")
                    }) { Icon(Icons.Default.ContentCopy, "Copiar para otro chatbot") }
                }
            },
        )
    }) { pad ->
        Column(
            Modifier.padding(pad).fillMaxSize().background(C.panel).verticalScroll(rememberScrollState()).padding(12.dp),
            verticalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            Row {
                Selector("IA", provider, AiService.PROVIDERS, { provider = it })
                Selector("Modelo", model, models, { model = it })
            }
            Row {
                Selector("Largo", length, Psych.LENGTHS.keys.toList(), { length = it }, display = { Psych.LENGTHS[it] ?: it })
                Selector(if (internal) "Mensajes por chat" else "Mensajes", count.toString(), Psych.MESSAGE_COUNTS.map { it.toString() }, { count = it.toInt() })
            }
            if (!internal) {
                Text("Tipo de relación", color = C.text2, fontSize = 13.sp)
                FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                    kinds.forEach { k -> FilterChip(selected = relation == k, onClick = { relation = k }, label = { Text(k) }) }
                }
                Field(relation, { relation = it }, "O escribí otro tipo", singleLine = true)
                Text("Búsquedas rápidas (opcional): si elegís alguna, se responden solo esas", color = C.text2, fontSize = 13.sp)
                FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                    searches.forEach { q ->
                        FilterChip(
                            selected = q in selected,
                            onClick = { selected = if (q in selected) selected - q else selected + q },
                            label = { Text(q, fontSize = 13.sp) },
                        )
                    }
                }
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Field(newSearch, { newSearch = it }, "Agregar pregunta propia", Modifier.weight(1f), singleLine = true)
                    TextButton(onClick = {
                        AnalysisStore.addSearch(newSearch)
                        searches = AnalysisStore.loadSearches()
                        selected = selected + newSearch.trim()
                        newSearch = ""
                    }, enabled = newSearch.isNotBlank()) { Text("Agregar") }
                }
            }
            Field(notes, { notes = it }, "Mis notas (se envían a la IA como contexto)", minLines = 2)
            Button(onClick = { run() }, enabled = !busy, modifier = Modifier.fillMaxWidth()) {
                if (busy) CircularProgressIndicator(Modifier.size(18.dp), strokeWidth = 2.dp)
                else Text(if (analysis.isEmpty()) "Analizar" else "Rehacer el análisis")
            }
            error?.let { Text(it, color = C.danger, fontSize = 14.sp) }
            if (analysis.isNotEmpty()) {
                Column(Modifier.fillMaxWidth().background(C.surface, RoundedCornerShape(8.dp)).padding(10.dp)) {
                    if (stamp.isNotEmpty()) Text(stamp, color = C.text3, fontSize = 12.sp, modifier = Modifier.padding(bottom = 4.dp))
                    SelectionContainer { Text(analysis, color = C.text, fontSize = 15.sp) }
                }
                qa.forEach { (q, a) ->
                    Column(Modifier.fillMaxWidth().background(C.surface, RoundedCornerShape(8.dp)).padding(10.dp)) {
                        Text(q, color = C.ember, fontWeight = FontWeight.SemiBold, fontSize = 14.sp)
                        SelectionContainer { Text(a, color = C.text, fontSize = 15.sp) }
                    }
                }
                Field(question, { question = it }, "Preguntar algo sobre este análisis")
                OutlinedButton(onClick = { ask() }, enabled = !busy && question.isNotBlank(), modifier = Modifier.fillMaxWidth()) { Text("Preguntar") }
            }
            Text("Es una lectura orientativa hecha por una IA, no un diagnóstico.", color = C.text3, fontSize = 12.sp)
        }
    }

    history?.let { h ->
        AlertDialog(
            onDismissRequest = { history = null },
            confirmButton = { TextButton(onClick = { history = null }) { Text("Cerrar") } },
            title = { Text("Historial") },
            text = {
                Column(Modifier.heightIn(max = 460.dp).verticalScroll(rememberScrollState())) {
                    SelectionContainer { Text(h.ifBlank { "Todavía no hay análisis guardados." }, fontSize = 13.sp) }
                }
            },
        )
    }
}
