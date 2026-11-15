package com.advancedwhatsapp.app.data

import com.advancedwhatsapp.app.App
import java.text.Normalizer

class EmojiCategory(val name: String, val icon: String, val items: List<Pair<String, String>>)   // (emoji, palabras clave)

/** Lista de emojis liviana (assets/emojis.txt, la misma que la app de escritorio). Se lee la primera vez que se abre el selector. */
object Emojis {
    val categories: List<EmojiCategory> by lazy { parse(App.instance.assets.open("emojis.txt").bufferedReader().use { it.readText() }) }

    fun fold(s: String): String =
        Normalizer.normalize(s.lowercase(), Normalizer.Form.NFD).filter { Character.getType(it) != Character.NON_SPACING_MARK.toInt() }

    fun parse(text: String): List<EmojiCategory> {
        val out = ArrayList<EmojiCategory>()
        var name = ""
        var icon = ""
        var items = ArrayList<Pair<String, String>>()
        fun flush() {
            if (name.isNotEmpty() && items.isNotEmpty()) out += EmojiCategory(name, icon, items)
        }
        for (raw in text.lines()) {
            val line = raw.trim()
            if (line.isEmpty()) continue
            if (line.startsWith("#")) {
                flush()
                val body = line.substring(1).trim()
                name = body.substringBefore('|').trim()
                icon = body.substringAfter('|', "").trim()
                items = ArrayList()
            } else if (name.isNotEmpty()) {
                items += line.substringBefore(' ') to fold(line.substringAfter(' ', ""))
            }
        }
        flush()
        return out
    }

    /** Emojis cuyas palabras clave (o nombre de categoría) contienen todas las palabras buscadas. */
    fun search(query: String, limit: Int = 120): List<String> {
        val words = fold(query).split(' ').filter { it.isNotEmpty() }
        if (words.isEmpty()) return emptyList()
        val found = LinkedHashSet<String>()
        for (cat in categories) {
            val catFold = fold(cat.name)
            for ((emoji, keys) in cat.items) {
                val hay = "$keys $catFold"
                if (words.all { it in hay }) {
                    found += emoji
                    if (found.size >= limit) return found.toList()
                }
            }
        }
        return found.toList()
    }
}
