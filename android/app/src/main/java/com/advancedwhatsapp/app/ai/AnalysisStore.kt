package com.advancedwhatsapp.app.ai

import com.advancedwhatsapp.app.App
import com.advancedwhatsapp.app.Prefs
import org.json.JSONArray
import java.io.File
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

data class SavedAnalysis(val text: String, val date: String, val mode: String, val provider: String, val model: String)

/**
 * Una carpeta por sujeto, igual que en la PC: notas.txt (lo único que se envía a la IA como contexto),
 * historial.txt (solo para consultar; nunca se reenvía) y ultimo_<enfoque>.txt.
 */
object AnalysisStore {
    private const val SEP = "----------------------------------------"
    private val FOCUSES = mapOf("normal" to "Contacto", "interno" to "Análisis interno (yo)")

    private fun safe(jid: String) = jid.ifEmpty { "desconocido" }.substringBefore('@').replace(Regex("[^\\w.-]"), "_").ifEmpty { "desconocido" }
    private fun dir(jid: String) = File(App.analysisDir, safe(jid)).apply { mkdirs() }
    private fun stamp() = SimpleDateFormat("yyyy-MM-dd HH:mm", Locale.US).format(Date())

    fun loadNotes(jid: String) = runCatching { File(dir(jid), "notas.txt").readText() }.getOrDefault("")
    fun saveNotes(jid: String, text: String) = File(dir(jid), "notas.txt").writeText(text)
    fun loadHistory(jid: String) = runCatching { File(dir(jid), "historial.txt").readText() }.getOrDefault("")

    private fun appendHistory(jid: String, title: String, body: String) =
        File(dir(jid), "historial.txt").appendText("\n===== $title =====\n${body.trim()}\n")

    fun saveAnalysis(jid: String, name: String, text: String, focus: String, length: String, provider: String, model: String,
                     nMessages: Int, relation: String, searches: List<String>) {
        val date = stamp()
        val header = buildString {
            append("Sujeto: $name\nJID: $jid\nFecha: $date\nEnfoque: $focus\nModo: $length\n")
            append("Proveedor: $provider\nModelo: $model\nMensajes analizados: $nMessages\n")
            if (relation.isNotEmpty()) append("Relación: $relation\n")
            if (searches.isNotEmpty()) append("Búsquedas: ${searches.joinToString(" | ")}\n")
            append("$SEP\n")
        }
        File(dir(jid), "ultimo_$focus.txt").writeText(header + text.trim() + "\n")
        val detail = if (searches.isNotEmpty()) "Búsquedas" else Psych.LENGTHS[length] ?: length
        val kind = if (relation.isNotEmpty() && focus != "interno") " ($relation)" else ""
        appendHistory(jid, "$date · ${FOCUSES[focus]}$kind · $detail · $provider $model · $nMessages mensajes", text)
        if (searches.isNotEmpty()) appendHistory(jid, "$date · Preguntas de la búsqueda", searches.joinToString("\n") { "- $it" })
    }

    fun saveQuestion(jid: String, focus: String, question: String, answer: String) =
        appendHistory(jid, "${stamp()} · Pregunta (${FOCUSES[focus]})", "P: $question\n\nR: $answer")

    fun loadAnalysis(jid: String, focus: String): SavedAnalysis? {
        val raw = runCatching { File(dir(jid), "ultimo_$focus.txt").readText() }.getOrNull() ?: return null
        val i = raw.indexOf("$SEP\n")
        if (i < 0) return SavedAnalysis(raw.trim(), "", "short", "", "")
        val meta = raw.substring(0, i).lines().filter { ": " in it }.associate { it.substringBefore(": ") to it.substringAfter(": ") }
        return SavedAnalysis(raw.substring(i + SEP.length + 1).trim(), meta["Fecha"].orEmpty(), meta["Modo"] ?: "short",
            meta["Proveedor"].orEmpty(), meta["Modelo"].orEmpty())
    }

    /** Mis notas y el último análisis, para redactar respuestas. Nunca incluye el historial. */
    fun replyProfile(jid: String, notesLimit: Int = 1500, analysisLimit: Int = 2500): String {
        if (jid.isEmpty() || !File(App.analysisDir, safe(jid)).isDirectory) return ""
        val parts = mutableListOf<String>()
        loadNotes(jid).trim().takeIf { it.isNotEmpty() }?.let { parts += "Mis notas sobre esta persona:\n${it.takeLast(notesLimit)}" }
        loadAnalysis(jid, "normal")?.text?.trim()?.takeIf { it.isNotEmpty() }?.let {
            parts += "Lectura psicológica (orientativa, hecha por una IA; puede estar equivocada):\n${it.take(analysisLimit)}"
        }
        return parts.joinToString("\n\n")
    }

    fun loadKinds(): List<String> = Psych.KIND_HINTS.keys.toList() + customList("kinds").filter { it !in Psych.KIND_HINTS }

    fun rememberKind(kind: String) {
        val k = kind.trim()
        if (k.isEmpty() || loadKinds().any { it.equals(k, true) }) return
        setCustomList("kinds", customList("kinds") + k)
    }

    fun loadSearches(): List<String> = Psych.DEFAULT_SEARCHES + customList("searches").filter { it !in Psych.DEFAULT_SEARCHES }

    fun addSearch(text: String) {
        val t = text.trim()
        if (t.isNotEmpty() && t !in loadSearches()) setCustomList("searches", customList("searches") + t)
    }

    private fun customList(k: String): List<String> = runCatching {
        val a = JSONArray(Prefs.getString(k, "[]"))
        List(a.length()) { a.getString(it) }
    }.getOrDefault(emptyList())

    private fun setCustomList(k: String, v: List<String>) = Prefs.putString(k, JSONArray(v).toString())
}
