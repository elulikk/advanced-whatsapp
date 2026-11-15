package com.advancedwhatsapp.app.data

import android.graphics.BitmapFactory
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.RectF
import android.graphics.pdf.PdfDocument
import android.text.Layout
import android.text.StaticLayout
import android.text.TextPaint
import android.util.Base64
import com.advancedwhatsapp.app.App
import org.json.JSONObject
import java.io.BufferedWriter
import java.io.File
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/**
 * Exportar un chat a TXT (formato de WhatsApp), HTML (burbujas) o PDF. Igual que en PC (app/chat_export.py): los mensajes se leen
 * por páginas y se escriben a medida que llegan (memoria constante), no se baja nada salvo que se pida, y se puede cancelar.
 * La parte de armado (rangos, cuerpo del mensaje, líneas de TXT, HTML) es pura y está probada en ChatExportTest.
 * [run] es bloqueante: llamar desde Dispatchers.IO.
 */
object ChatExport {
    const val PAGE_SIZE = 500
    private const val EMBED_MAX_BYTES = 1_500_000L
    private const val DAY = 86_400L
    private val MEDIA_LABEL = mapOf(
        "image" to "imagen", "video" to "video", "audio" to "nota de voz", "document" to "documento", "sticker" to "sticker",
    )

    enum class Format(val ext: String, val mime: String) {
        TXT("txt", "text/plain"), HTML("html", "text/html"), PDF("pdf", "application/pdf"),
    }

    data class Options(
        val format: Format = Format.HTML,
        val since: Long? = null,           // segundos, inclusive
        val until: Long? = null,
        val includeMedia: Boolean = true,
        val downloadMissing: Boolean = false,
        val transcripts: Boolean = true,
        val summaries: Boolean = false,
        val deleted: Boolean = true,
        val myName: String = "Yo",
    )

    /** Contenido conservado de un mensaje eliminado por el remitente. */
    data class Original(val mediaType: String, val text: String, val transcript: String?)

    class Body(val kind: String, val text: String, val notes: List<String>, val file: String?, val label: String, val deleted: Boolean)

    data class Result(val file: File, val name: String, val savedToDownloads: Boolean, val messages: Int, val mediaMissing: Int, val cancelled: Boolean)

    // ── Piezas puras ──

    /** (desde, hasta) en segundos para "all" | "month" | "custom"; `hasta` se extiende hasta el final de ese día. */
    fun rangeFor(kind: String, nowSec: Long, fromSec: Long? = null, toSec: Long? = null): Pair<Long?, Long?> = when (kind) {
        "month" -> (nowSec - 30 * DAY) to null
        "custom" -> fromSec to toSec?.let { it + DAY - 1 }
        else -> null to null
    }

    private fun fmt(pattern: String, ts: Long) = SimpleDateFormat(pattern, Locale.US).format(Date(ts * 1000))
    fun txtDate(ts: Long) = fmt("dd/MM/yy HH:mm", ts)
    fun dayText(ts: Long) = fmt("dd/MM/yyyy", ts)
    fun hourText(ts: Long) = fmt("HH:mm", ts)

    fun senderOf(m: Message, myName: String, chatName: String): String =
        if (m.fromMe) myName else m.senderName?.trim().orEmpty().ifEmpty { chatName }.ifEmpty { m.senderJid?.substringBefore('@').orEmpty() }.ifEmpty { "?" }

    fun safeName(s: String): String = s.replace(Regex("[^\\p{L}\\p{N}._ -]"), "_").trim(' ', '.').take(80).ifEmpty { "chat" }

    /** Interpreta un mensaje. [file] es el archivo que se incluye (ya resuelto); [edits], las versiones anteriores. */
    fun bodyOf(m: Message, o: Options, original: Original? = null, edits: List<String> = emptyList(), file: String? = null, pollText: String? = null): Body {
        val orig = if (m.mediaType == "revoked" && o.deleted) original else null
        var kind = orig?.mediaType ?: m.mediaType
        var text = (orig?.text ?: m.text).trim()
        val notes = mutableListOf<String>()
        if (kind == "revoked") text = "🚫 Mensaje eliminado"
        val label = MEDIA_LABEL[kind] ?: when (kind) { "location" -> "ubicación"; "contact" -> "contacto"; else -> "" }
        if (pollText != null) { text = pollText; kind = "poll" }
        if (!m.quotedText.isNullOrBlank()) {
            val who = m.quotedSender?.trim().orEmpty()
            notes += "↪ " + (if (who.isNotEmpty()) "$who: " else "") + m.quotedText.trim().take(200)
        }
        val transcript = orig?.transcript ?: m.transcript
        if (o.transcripts && !transcript.isNullOrBlank()) notes += "🎙 Transcripción: ${transcript.trim()}"
        if (o.summaries && !m.summary.isNullOrBlank()) notes += "✨ Resumen IA: ${m.summary.trim()}"
        if (orig != null) notes.add(0, "🚫 Eliminado por el remitente (se conservó el contenido original)")
        if (o.deleted) edits.forEachIndexed { i, v -> notes += "✏ Versión anterior ${i + 1}: ${v.trim()}" }
        if (m.edited) notes += "✏ Mensaje editado"
        return Body(kind, text, notes, file, label, orig != null)
    }

    /** Líneas de un mensaje al estilo del export de WhatsApp: `dd/mm/aa hh:mm - Nombre: texto`. */
    fun txtLines(m: Message, body: Body, who: String): List<String> {
        val head = "${txtDate(m.timestamp)} - $who: "
        val main = when {
            body.file != null -> "${File(body.file).name} (archivo adjunto)" + if (body.text.isNotEmpty()) "\n${body.text}" else ""
            body.kind in MEDIA_LABEL -> "<Multimedia omitida>" + if (body.text.isNotEmpty()) "\n${body.text}" else ""
            else -> body.text
        }
        return (head + main).split("\n") + body.notes.flatMap { n -> n.split("\n").map { "    $it" } }
    }

    fun htmlEscape(s: String): String = buildString(s.length + 16) {
        for (c in s) when (c) {
            '&' -> append("&amp;"); '<' -> append("&lt;"); '>' -> append("&gt;"); '"' -> append("&quot;"); '\'' -> append("&#x27;")
            else -> append(c)
        }
    }

    private val CSS = """
body{margin:0;background:#0b141a;color:#e9edef;font:15px/1.4 system-ui,sans-serif}
header{background:#202c33;padding:14px 16px}header h1{margin:0;font-size:18px}header small{color:#8696a0}
main{max-width:860px;margin:0 auto;padding:12px 12px 40px}
.day{text-align:center;margin:18px 0 8px}.day span{background:#182229;color:#8696a0;border-radius:8px;padding:4px 12px;font-size:12px}
.b{max-width:80%;margin:3px 0;padding:6px 9px 4px;border-radius:9px;background:#202c33;word-wrap:break-word;overflow-wrap:anywhere;clear:both}
.b.me{background:#005c4b;margin-left:auto}.b .who{font-size:12.5px;font-weight:600;color:#53bdeb}
.b .t{white-space:pre-wrap}.b .n{font-size:12.5px;color:#aebac1;border-left:3px solid #8696a0;padding-left:7px;margin:4px 0;white-space:pre-wrap}
.b .n.del{border-color:#f15c6d;color:#f6a1ab}.b time{display:block;text-align:right;font-size:11px;color:#8696a0}
.b img{max-width:100%;border-radius:6px;display:block;margin:2px 0}.ph{color:#8696a0;font-style:italic}
""".trimIndent()

    fun htmlHead(title: String, subtitle: String): String =
        "<!doctype html>\n<html lang=\"es\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">" +
            "<title>${htmlEscape(title)}</title><style>$CSS</style></head><body><header><h1>${htmlEscape(title)}</h1>" +
            "<small>${htmlEscape(subtitle)}</small></header><main>\n"

    fun htmlDay(ts: Long) = "<div class=\"day\"><span>${dayText(ts)}</span></div>\n"
    const val HTML_FOOT = "</main></body></html>\n"

    /** [imageSrc] es la imagen ya incrustada (data URI), si corresponde. */
    fun htmlBubble(m: Message, body: Body, who: String, imageSrc: String?): String = buildString {
        append("<div class=\"b${if (m.fromMe) " me" else ""}\">")
        if (!m.fromMe) append("<div class=\"who\">${htmlEscape(who)}</div>")
        if (imageSrc != null) append("<img src=\"$imageSrc\" alt=\"${htmlEscape(body.label)}\">")
        else if (body.kind in MEDIA_LABEL) append("<div class=\"ph\">[${htmlEscape(body.label)} no incluido]</div>")
        if (body.text.isNotEmpty()) append("<div class=\"t\">${htmlEscape(body.text)}</div>")
        body.notes.forEach { append("<div class=\"n${if (it.startsWith("🚫")) " del" else ""}\">${htmlEscape(it)}</div>") }
        if (m.reactions.isNotEmpty()) append("<div class=\"ph\">${htmlEscape(m.reactions.joinToString(" "))}</div>")
        append("<time>${hourText(m.timestamp)}</time></div>\n")
    }

    // ── Exportación ──

    private fun pollText(m: Message): String? {
        val raw = m.pollJson ?: return null
        return runCatching {
            val j = JSONObject(raw)
            val opts = j.optJSONArray("options") ?: return null
            val parts = (0 until opts.length()).map { i ->
                val o = opts.get(i)
                if (o is JSONObject) {
                    val n = o.optString("name").ifEmpty { o.optString("text") }
                    val v = o.opt("votes")
                    val c = when (v) { is org.json.JSONArray -> v.length(); is Number -> v.toInt(); else -> 0 }
                    if (c > 0) "$n ($c)" else n
                } else o.toString()
            }
            "📊 ${j.optString("name").ifEmpty { m.text }}: " + parts.joinToString(" · ")
        }.getOrNull()
    }

    private fun originalOf(m: Message, o: Options): Original? {
        if (m.mediaType != "revoked" || !o.deleted || !m.hasOriginal) return null
        val j = Db.revokedOriginal(m.id) ?: return null
        return Original(j.optString("media_type", "text"), j.optString("text"), j.optString("transcript").ifEmpty { null })
    }

    /** Exporta el chat y deja una copia en Descargas/AdvancedWhatsapp. [progress] (hechos, total) se llama por página. */
    fun run(
        jid: String, chatName: String, o: Options,
        progress: (Int, Int) -> Unit = { _, _ -> },
        cancelled: () -> Boolean = { false },
    ): Result {
        val total = Db.countInRange(jid, o.since, o.until)
        val dir = File(App.instance.cacheDir, "exports").apply { mkdirs() }
        dir.listFiles()?.forEach { it.delete() }   // exportaciones anteriores: ya están en Descargas
        val name = "Chat con ${safeName(chatName)} (${fmt("yyyy-MM-dd HHmm", System.currentTimeMillis() / 1000)}).${o.format.ext}"
        val out = File(dir, name)
        var missing = 0
        var count = 0
        var wasCancelled = false
        val pdf = if (o.format == Format.PDF) PdfOut(chatName) else null
        val w: BufferedWriter? = if (pdf == null) out.bufferedWriter(Charsets.UTF_8) else null
        try {
            if (o.format == Format.HTML) w!!.write(htmlHead("Chat con $chatName", "$total mensajes · exportado el ${dayText(System.currentTimeMillis() / 1000)}"))
            var lastDay = ""
            var ts = Long.MIN_VALUE
            var rid = -1L
            var done = 0
            while (true) {
                if (cancelled()) { wasCancelled = true; break }
                val page = Db.exportPage(jid, o.since, o.until, ts, rid, PAGE_SIZE)
                if (page.isEmpty()) break
                for ((_, m) in page) {
                    if (m.mediaType == "document" && m.text.startsWith("awsync-")) continue
                    val file = resolveFile(m, o)
                    if (o.includeMedia && file == null && m.mediaType in MEDIA_LABEL) missing++
                    val edits = if (o.deleted && m.edited) Db.editsOf(m.id).map { it.first } else emptyList()
                    val body = bodyOf(m, o, originalOf(m, o), edits, file, pollText(m))
                    val who = senderOf(m, o.myName, chatName)
                    when (o.format) {
                        Format.TXT -> { w!!.write(txtLines(m, body, who).joinToString("\r\n")); w.write("\r\n") }
                        Format.HTML -> {
                            val day = dayText(m.timestamp)
                            if (day != lastDay) { w!!.write(htmlDay(m.timestamp)); lastDay = day }
                            w!!.write(htmlBubble(m, body, who, embedImage(file, body.kind)))
                        }
                        Format.PDF -> pdf!!.add(m, body, who)
                    }
                    count++
                }
                done += page.size
                ts = page.last().second.timestamp
                rid = page.last().first
                progress(minOf(done, total), total)
                if (page.size < PAGE_SIZE) break
            }
            if (o.format == Format.HTML) w!!.write(HTML_FOOT)
        } catch (e: Throwable) {
            runCatching { w?.close() }
            pdf?.abort()
            out.delete()
            throw e
        }
        w?.close()
        pdf?.finish(out)
        if (wasCancelled) {
            out.delete()
            return Result(out, name, false, count, missing, true)
        }
        return Result(out, name, Downloads.saveCopy(out, name), count, missing, false)
    }

    private fun resolveFile(m: Message, o: Options): String? {
        if (!o.includeMedia || m.mediaType !in MEDIA_LABEL) return null
        m.mediaPath?.takeIf { File(it).exists() }?.let { return it }
        if (o.downloadMissing && m.mediaRaw != null) {
            runCatching { Actions.downloadMedia(m) }.getOrNull()?.takeIf { File(it).exists() }?.let { return it }
        }
        return null
    }

    private fun embedImage(path: String?, kind: String): String? {
        if (path == null || kind !in listOf("image", "sticker")) return null
        val f = File(path)
        if (f.length() > EMBED_MAX_BYTES) return null
        return runCatching {
            val mime = Downloads.mimeOf(f.name).takeIf { it.startsWith("image/") } ?: "image/jpeg"
            "data:$mime;base64," + Base64.encodeToString(f.readBytes(), Base64.NO_WRAP)
        }.getOrNull()
    }

    private fun chunks(s: String, size: Int = 1200): List<String> = if (s.isEmpty()) emptyList() else s.chunked(size)

    /** PDF nativo (PdfDocument): dibuja las burbujas a mano y pasa de página sobre la marcha, sin juntar el chat en memoria. */
    private class PdfOut(private val title: String) {
        private val doc = PdfDocument()
        private val pageW = 595
        private val pageH = 842
        private val margin = 36f
        private val width = pageW - 2 * margin
        private var page: PdfDocument.Page? = null
        private var canvas: Canvas? = null
        private var y = 0f
        private var number = 0
        private var lastDay = ""
        private val text = TextPaint(Paint.ANTI_ALIAS_FLAG).apply { textSize = 11f; color = Color.rgb(17, 17, 17) }
        private val small = TextPaint(Paint.ANTI_ALIAS_FLAG).apply { textSize = 9f; color = Color.rgb(84, 101, 111) }
        private val bold = TextPaint(Paint.ANTI_ALIAS_FLAG).apply { textSize = 10f; isFakeBoldText = true; color = Color.rgb(2, 126, 181) }
        private val fill = Paint(Paint.ANTI_ALIAS_FLAG)

        init {
            newPage()
            val h = TextPaint(text).apply { textSize = 17f; isFakeBoldText = true }
            val l = layout("Chat con $title", h, width.toInt())
            draw(l, margin, y)
            y += l.height + 10
        }

        private fun newPage() {
            page?.let { doc.finishPage(it) }
            number++
            page = doc.startPage(PdfDocument.PageInfo.Builder(pageW, pageH, number).create())
            canvas = page!!.canvas
            y = margin
        }

        private fun layout(s: String, p: TextPaint, w: Int) =
            StaticLayout.Builder.obtain(s, 0, s.length, p, w).setAlignment(Layout.Alignment.ALIGN_NORMAL).build()

        private fun draw(l: StaticLayout, x: Float, top: Float) {
            canvas!!.save(); canvas!!.translate(x, top); l.draw(canvas); canvas!!.restore()
        }

        fun add(m: Message, body: Body, who: String) {
            val day = dayText(m.timestamp)
            if (day != lastDay) {
                lastDay = day
                val l = layout(day, small, width.toInt())
                if (y + l.height + 8 > pageH - margin) newPage()
                y += 6; draw(l, margin, y); y += l.height + 4
            }
            val bw = width * 0.78f
            val inner = (bw - 16f).toInt()
            val x = if (m.fromMe) margin + width - bw else margin
            val parts = mutableListOf<Pair<StaticLayout, Boolean>>()
            var t = body.text
            if (body.file != null && body.kind !in listOf("image", "sticker")) t = ("📎 ${File(body.file).name}\n$t").trim()
            else if (body.file == null && body.kind in MEDIA_LABEL) t = ("[${body.label} no incluido]\n$t").trim()
            chunks(t).forEach { parts += layout(it, text, inner) to false }
            body.notes.forEach { parts += layout(it, small, inner) to true }
            if (m.reactions.isNotEmpty()) parts += layout(m.reactions.joinToString(" "), small, inner) to true
            val bmp = if (body.file != null && body.kind in listOf("image", "sticker")) loadThumb(body.file, inner.coerceAtMost(220)) else null
            var first = true
            var idx = 0
            if (parts.isEmpty() && bmp == null) parts += layout(" ", text, inner) to false
            while (idx < parts.size || (first && bmp != null)) {
                val whoL = if (first && !m.fromMe) layout(who, bold, inner) else null
                var used = 12f + (whoL?.height ?: 0) + (if (first && bmp != null) bmp.height + 4f else 0f)
                val take = mutableListOf<StaticLayout>()
                while (idx < parts.size) {
                    val h = parts[idx].first.height + 3f
                    if (take.isNotEmpty() && y + used + h > pageH - margin) break
                    take += parts[idx].first; used += h; idx++
                }
                if (y > margin && y + used > pageH - margin) newPage()
                fill.color = if (m.fromMe) Color.rgb(217, 253, 211) else Color.rgb(240, 242, 245)
                canvas!!.drawRoundRect(RectF(x, y, x + bw, y + used), 7f, 7f, fill)
                var cy = y + 5f
                whoL?.let { draw(it, x + 8f, cy); cy += it.height }
                if (first && bmp != null) { canvas!!.drawBitmap(bmp, x + 8f, cy, null); cy += bmp.height + 4f; bmp.recycle() }
                take.forEach { draw(it, x + 8f, cy); cy += it.height + 3f }
                if (idx >= parts.size) canvas!!.drawText(hourText(m.timestamp), x + bw - 8f - small.measureText(hourText(m.timestamp)), y + used - 4f, small)
                y += used + 4f
                first = false
                if (idx >= parts.size) break
            }
        }

        private fun loadThumb(path: String, target: Int): android.graphics.Bitmap? = runCatching {
            val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
            BitmapFactory.decodeFile(path, bounds)
            if (bounds.outWidth <= 0) return null
            var sample = 1
            while (bounds.outWidth / (sample * 2) >= target) sample *= 2
            val raw = BitmapFactory.decodeFile(path, BitmapFactory.Options().apply { inSampleSize = sample }) ?: return null
            val scale = target.toFloat() / raw.width
            if (scale >= 1f) raw else android.graphics.Bitmap.createScaledBitmap(raw, target, (raw.height * scale).toInt().coerceAtLeast(1), true).also { raw.recycle() }
        }.getOrNull()

        fun finish(out: File) {
            page?.let { doc.finishPage(it) }
            out.outputStream().use { doc.writeTo(it) }
            doc.close()
        }

        fun abort() {
            runCatching { doc.close() }
        }
    }
}
