package com.advancedwhatsapp.app.data

import android.content.Context
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.Canvas
import android.graphics.Paint
import android.graphics.Rect
import android.net.Uri
import android.os.Build
import android.provider.DocumentsContract
import android.provider.OpenableColumns
import com.advancedwhatsapp.app.App
import java.io.ByteArrayOutputStream
import java.io.File
import java.nio.file.Files
import java.nio.file.StandardCopyOption
import java.security.MessageDigest
import java.util.zip.ZipInputStream
import org.json.JSONArray
import org.json.JSONObject
import kotlin.math.max
import kotlin.math.min
import kotlin.math.roundToInt

data class Sticker(
    val id: String,
    val file: File,
    val pack: String,
    val fav: Boolean,
    val animated: Boolean,
    val added: Double,
    val used: Double,
)

data class StickerPack(val name: String, val count: Int, val cover: Sticker?)

/** Resultado de una importación: cuántos se agregaron, cuántos ya estaban, cuántos fallaron y cuántos WhatsApp ya no tiene. */
data class ImportResult(var added: Int = 0, var duplicates: Int = 0, var failed: Int = 0, var unavailable: Int = 0) {
    fun summary(): String {
        val parts = mutableListOf("$added agregado(s)")
        if (duplicates > 0) parts += "$duplicates repetido(s)"
        if (unavailable > 0) parts += "$unavailable ya no disponible(s) en WhatsApp"
        if (failed > 0) parts += "$failed con error"
        return "Listo: " + parts.joinToString(", ") + "."
    }
}

/**
 * Biblioteca local de stickers: `<datos>/media/stickers/<id>.webp` (512x512) y un índice JSON escrito de forma atómica,
 * con el mismo formato que la app de escritorio. El id es el hash del contenido: un sticker repetido no se duplica.
 * Bloqueante: llamar desde Dispatchers.IO.
 */
object StickerLibrary {
    const val DEFAULT_PACK = "Mis stickers"
    const val CHATS_PACK = "De mis chats"
    const val SIZE = 512
    private const val MAX_STATIC_BYTES = 100 * 1024
    private const val MAX_ANIMATED_BYTES = 500 * 1024
    private const val MAX_FILE_BYTES = 32 * 1024 * 1024
    private const val MAX_ENTRY_BYTES = 4 * 1024 * 1024
    private const val MAX_ARCHIVE_ENTRIES = 300
    private const val MAX_RECENT_EMOJIS = 32
    private const val MAX_TREE_DEPTH = 4
    private val IMAGE_EXTS = listOf(".webp", ".png", ".jpg", ".jpeg", ".gif", ".bmp")

    private val lock = Any()
    private var cache: JSONObject? = null

    private val dir: File get() = File(App.mediaDir, "stickers").apply { mkdirs() }
    private val indexFile: File get() = File(dir, "index.json")

    // ── Índice ──
    private fun load(): JSONObject {
        cache?.let { return it }
        val parsed = runCatching { JSONObject(indexFile.readText()) }.getOrNull()
        val idx = parsed?.takeIf { it.optJSONObject("stickers") != null } ?: JSONObject()
        if (idx.optJSONObject("stickers") == null) idx.put("stickers", JSONObject())
        if (idx.optJSONArray("packs") == null) idx.put("packs", JSONArray())
        if (idx.optJSONArray("recent_emojis") == null) idx.put("recent_emojis", JSONArray())
        if (idx.optJSONArray("imported_msgs") == null) idx.put("imported_msgs", JSONArray())
        val packs = idx.getJSONArray("packs")
        if ((0 until packs.length()).none { packs.getString(it) == DEFAULT_PACK }) {
            val withDefault = JSONArray().put(DEFAULT_PACK)
            for (i in 0 until packs.length()) withDefault.put(packs.getString(i))
            idx.put("packs", withDefault)
        }
        cache = idx
        return idx
    }

    private fun save(idx: JSONObject) {
        val tmp = File(dir, "index.json.tmp")
        tmp.writeText(idx.toString())
        runCatching {
            Files.move(tmp.toPath(), indexFile.toPath(), StandardCopyOption.REPLACE_EXISTING, StandardCopyOption.ATOMIC_MOVE)
        }.onFailure {
            Files.move(tmp.toPath(), indexFile.toPath(), StandardCopyOption.REPLACE_EXISTING)
        }
    }

    /** Para copias y sincronización (data/BackupSections.kt): el índice y la carpeta, bajo el mismo candado. */
    fun <T> withIndex(block: (idx: JSONObject, dir: File) -> T): T = synchronized(lock) { block(load(), dir) }

    fun saveIndex(idx: JSONObject) = synchronized(lock) { save(idx) }

    private fun toSticker(o: JSONObject) = Sticker(
        id = o.getString("id"), file = File(dir, o.getString("file")), pack = o.optString("pack", DEFAULT_PACK),
        fav = o.optBoolean("fav"), animated = o.optBoolean("animated"), added = o.optDouble("added", 0.0),
        used = o.optDouble("used", 0.0),
    )

    private fun all(): List<Sticker> = synchronized(lock) {
        val map = load().getJSONObject("stickers")
        val out = ArrayList<Sticker>(map.length())
        val keys = map.keys()
        while (keys.hasNext()) {
            val s = runCatching { toSticker(map.getJSONObject(keys.next())) }.getOrNull() ?: continue
            if (s.file.exists()) out += s
        }
        out
    }

    // ── Conversión ──
    private class Webp(val width: Int, val height: Int, val animated: Boolean)

    /** Lee las medidas y si es animado del encabezado de un .webp; null si no lo es. */
    private fun webpInfo(d: ByteArray): Webp? {
        if (d.size < 30 || String(d, 0, 4, Charsets.US_ASCII) != "RIFF" || String(d, 8, 4, Charsets.US_ASCII) != "WEBP") return null
        fun b(o: Int) = d[o].toInt() and 0xFF
        fun u24(o: Int) = b(o) or (b(o + 1) shl 8) or (b(o + 2) shl 16)
        return when (String(d, 12, 4, Charsets.US_ASCII)) {
            "VP8X" -> Webp(u24(24) + 1, u24(27) + 1, (b(20) and 0x02) != 0)
            "VP8L" -> {
                val bits = b(21) or (b(22) shl 8) or (b(23) shl 16) or (b(24) shl 24)
                Webp((bits and 0x3FFF) + 1, ((bits ushr 14) and 0x3FFF) + 1, false)
            }
            "VP8 " -> Webp((b(26) or (b(27) shl 8)) and 0x3FFF, (b(28) or (b(29) shl 8)) and 0x3FFF, false)
            else -> null
        }
    }

    /** Imagen cualquiera -> webp 512x512 con fondo transparente. Un webp que ya es 512x512 queda tal cual (conserva la animación). */
    fun toWebp512(data: ByteArray): Pair<ByteArray, Boolean>? {
        webpInfo(data)?.let { w ->
            if (w.width == SIZE && w.height == SIZE && data.size <= (if (w.animated) MAX_ANIMATED_BYTES else 4 * MAX_STATIC_BYTES)) {
                return data to w.animated
            }
        }
        val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
        BitmapFactory.decodeByteArray(data, 0, data.size, bounds)
        if (bounds.outWidth <= 0 || bounds.outHeight <= 0) return null
        val opts = BitmapFactory.Options().apply { inSampleSize = Photos.sampleSize(bounds.outWidth, bounds.outHeight, SIZE) }
        val src = BitmapFactory.decodeByteArray(data, 0, data.size, opts) ?: return null
        val scale = min(SIZE.toFloat() / src.width, SIZE.toFloat() / src.height)
        val w = max(1, (src.width * scale).roundToInt())
        val h = max(1, (src.height * scale).roundToInt())
        val canvasBmp = Bitmap.createBitmap(SIZE, SIZE, Bitmap.Config.ARGB_8888)
        val left = (SIZE - w) / 2
        val top = (SIZE - h) / 2
        Canvas(canvasBmp).drawBitmap(src, null, Rect(left, top, left + w, top + h), Paint(Paint.FILTER_BITMAP_FLAG))
        src.recycle()
        @Suppress("DEPRECATION")
        val format = if (Build.VERSION.SDK_INT >= 30) Bitmap.CompressFormat.WEBP_LOSSY else Bitmap.CompressFormat.WEBP
        var out = ByteArray(0)
        for (q in intArrayOf(85, 65, 45, 30)) {
            val bos = ByteArrayOutputStream()
            canvasBmp.compress(format, q, bos)
            out = bos.toByteArray()
            if (out.size <= MAX_STATIC_BYTES) break
        }
        canvasBmp.recycle()
        return out to false
    }

    private fun sha1(b: ByteArray): String =
        MessageDigest.getInstance("SHA-1").digest(b).joinToString("") { "%02x".format(it) }.take(16)

    // ── Altas ──
    /** Agrega una imagen. Devuelve (sticker, yaEstaba), o null si no se pudo leer. */
    fun add(data: ByteArray, pack: String = DEFAULT_PACK, fav: Boolean = false): Pair<Sticker, Boolean>? {
        val (webp, animated) = toWebp512(data) ?: return null
        val id = sha1(webp)
        synchronized(lock) {
            val idx = load()
            val stickers = idx.getJSONObject("stickers")
            stickers.optJSONObject(id)?.let { o ->
                val s = toSticker(o)
                if (s.file.exists()) return s to true
            }
            val name = pack.trim().ifEmpty { DEFAULT_PACK }.take(60)
            val file = File(dir, "$id.webp")
            val tmp = File(dir, "$id.webp.tmp")
            tmp.writeBytes(webp)
            Files.move(tmp.toPath(), file.toPath(), StandardCopyOption.REPLACE_EXISTING)
            addPackName(idx, name)
            val rec = JSONObject().put("id", id).put("file", file.name).put("pack", name).put("fav", fav)
                .put("animated", animated).put("added", System.currentTimeMillis() / 1000.0).put("used", 0.0).put("uses", 0)
            stickers.put(id, rec)
            save(idx)
            return toSticker(rec) to false
        }
    }

    private fun addPackName(idx: JSONObject, name: String) {
        val packs = idx.getJSONArray("packs")
        if ((0 until packs.length()).none { packs.getString(it) == name }) packs.put(name)
    }

    private fun tally(res: ImportResult, r: Pair<Sticker, Boolean>?) {
        when {
            r == null -> res.failed++
            r.second -> res.duplicates++
            else -> res.added++
        }
    }

    /** «Agregar a mis stickers» sobre un mensaje ya descargado. */
    fun addFromMessage(m: Message, pack: String = DEFAULT_PACK): Pair<Sticker, Boolean>? {
        val f = m.mediaPath?.let { File(it) }?.takeIf { it.exists() && it.length() <= MAX_FILE_BYTES } ?: return null
        return add(f.readBytes(), pack)
    }

    private fun isImageName(name: String) = IMAGE_EXTS.any { name.lowercase().endsWith(it) }

    private fun displayName(cr: android.content.ContentResolver, uri: Uri): String =
        cr.query(uri, arrayOf(OpenableColumns.DISPLAY_NAME), null, null, null)?.use { c ->
            if (c.moveToFirst()) c.getString(0) else null
        } ?: "archivo"

    private fun readLimited(stream: java.io.InputStream, limit: Int): ByteArray? {
        val bos = ByteArrayOutputStream()
        val buf = ByteArray(16 * 1024)
        while (true) {
            val n = stream.read(buf)
            if (n < 0) break
            bos.write(buf, 0, n)
            if (bos.size() > limit) return null
        }
        return bos.toByteArray()
    }

    /** Importa un .wastickers (zip con webp + title.txt + icono de bandeja, que se ignora). */
    private fun importArchive(stream: java.io.InputStream, fallbackName: String, res: ImportResult) {
        val images = ArrayList<Pair<String, ByteArray>>()
        var title: String? = null
        var total = 0L
        ZipInputStream(stream).use { zip ->
            var entries = 0
            while (entries < MAX_ARCHIVE_ENTRIES) {
                val e = zip.nextEntry ?: break
                entries++
                if (e.isDirectory) continue
                val base = e.name.substringAfterLast('/').lowercase()
                val bytes = readLimited(zip, MAX_ENTRY_BYTES) ?: continue
                total += bytes.size
                if (total > 64L * 1024 * 1024) break
                if (base == "title.txt") title = String(bytes, Charsets.UTF_8).lines().firstOrNull()?.trim()?.take(60)
                else if (isImageName(base) && !base.startsWith("tray")) images += base to bytes
            }
        }
        val pack = title?.takeIf { it.isNotEmpty() } ?: fallbackName
        for ((_, bytes) in images) tally(res, add(bytes, pack))
    }

    /** Archivos sueltos elegidos en el selector (.webp .png .jpg .gif .wastickers). */
    fun importUris(ctx: Context, uris: List<Uri>, progress: (Int, Int) -> Unit = { _, _ -> }): ImportResult {
        val res = ImportResult()
        val cr = ctx.contentResolver
        uris.forEachIndexed { i, uri ->
            val name = displayName(cr, uri)
            runCatching {
                val low = name.lowercase()
                cr.openInputStream(uri)?.use { input ->
                    if (low.endsWith(".wastickers") || low.endsWith(".zip")) {
                        importArchive(input, name.substringBeforeLast('.'), res)
                    } else {
                        val bytes = readLimited(input, MAX_FILE_BYTES)
                        tally(res, if (bytes != null && isImageName(low)) add(bytes) else null)
                    }
                } ?: run { res.failed++ }
            }.onFailure { res.failed++ }
            progress(i + 1, uris.size)
        }
        return res
    }

    private class Doc(val id: String, val name: String, val isDir: Boolean)

    private fun children(cr: android.content.ContentResolver, tree: Uri, docId: String): List<Doc> {
        val uri = DocumentsContract.buildChildDocumentsUriUsingTree(tree, docId)
        val cols = arrayOf(
            DocumentsContract.Document.COLUMN_DOCUMENT_ID, DocumentsContract.Document.COLUMN_DISPLAY_NAME,
            DocumentsContract.Document.COLUMN_MIME_TYPE,
        )
        return cr.query(uri, cols, null, null, null)?.use { c ->
            buildList {
                while (c.moveToNext()) {
                    add(Doc(c.getString(0), c.getString(1).orEmpty(), c.getString(2) == DocumentsContract.Document.MIME_TYPE_DIR))
                }
            }
        }.orEmpty()
    }

    /**
     * Importa los paquetes que WhatsApp guarda en el teléfono (carpeta elegida con el selector de carpetas).
     * Cada subcarpeta es un paquete; los archivos sueltos van a un paquete con el nombre de la carpeta elegida.
     */
    fun importTree(ctx: Context, tree: Uri, progress: (Int, Int) -> Unit = { _, _ -> }): ImportResult {
        val res = ImportResult()
        val cr = ctx.contentResolver
        val rootId = DocumentsContract.getTreeDocumentId(tree)
        val rootName = runCatching {
            cr.query(DocumentsContract.buildDocumentUriUsingTree(tree, rootId),
                arrayOf(DocumentsContract.Document.COLUMN_DISPLAY_NAME), null, null, null)?.use { c ->
                if (c.moveToFirst()) c.getString(0) else null
            }
        }.getOrNull()?.takeIf { it.isNotBlank() } ?: "WhatsApp"
        // Primero se junta la lista de archivos (para poder informar el avance) y después se importan
        val files = ArrayList<Pair<String, String>>()   // id del documento, paquete
        var anonymous = 0
        fun walk(docId: String, pack: String, depth: Int) {
            for (d in children(cr, tree, docId)) {
                if (d.isDir) {
                    if (depth >= MAX_TREE_DEPTH) continue
                    val hashLike = d.name.length >= 16 && d.name.all { it.isLetterOrDigit() || it == '_' || it == '-' }
                    val sub = if (hashLike) "$rootName ${++anonymous}" else d.name
                    walk(d.id, sub, depth + 1)
                } else if (isImageName(d.name) && !d.name.lowercase().startsWith("tray")) {
                    files += d.id to pack
                }
            }
        }
        walk(rootId, rootName, 0)
        files.forEachIndexed { i, (docId, pack) ->
            runCatching {
                cr.openInputStream(DocumentsContract.buildDocumentUriUsingTree(tree, docId))?.use { input ->
                    val bytes = readLimited(input, MAX_FILE_BYTES)
                    tally(res, if (bytes != null) add(bytes, pack) else null)
                } ?: run { res.failed++ }
            }.onFailure { res.failed++ }
            progress(i + 1, files.size)
        }
        return res
    }

    /** Junta los stickers del historial (recibidos y enviados); los que no están en disco se bajan bajo demanda. */
    fun importFromChats(progress: (Int, Int) -> Unit = { _, _ -> }): ImportResult {
        val res = ImportResult()
        val done = synchronized(lock) {
            val arr = load().getJSONArray("imported_msgs")
            HashSet<String>().apply { for (i in 0 until arr.length()) add(arr.getString(i)) }
        }
        val rows = Db.stickerMessages().filter { it.id !in done }
        val newlyDone = ArrayList<String>()
        rows.forEachIndexed { i, m ->
            var path = m.mediaPath?.takeIf { File(it).exists() }
            if (path == null && m.mediaRaw != null) path = runCatching { Actions.downloadMedia(m) }.getOrNull()?.takeIf { File(it).exists() }
            if (path == null) {
                res.unavailable++
            } else {
                val bytes = runCatching { File(path).takeIf { it.length() <= MAX_FILE_BYTES }?.readBytes() }.getOrNull()
                val r = bytes?.let { add(it, CHATS_PACK) }
                tally(res, r)
                if (r != null) newlyDone += m.id
            }
            progress(i + 1, rows.size)
        }
        if (newlyDone.isNotEmpty()) synchronized(lock) {
            val idx = load()
            val arr = idx.getJSONArray("imported_msgs")
            newlyDone.forEach { arr.put(it) }
            save(idx)
        }
        return res
    }

    // ── Consulta ──
    fun list(pack: String? = null): List<Sticker> =
        all().filter { pack == null || it.pack == pack }.sortedByDescending { it.added }

    fun favorites(): List<Sticker> = all().filter { it.fav }.sortedByDescending { it.added }

    fun recents(limit: Int = 30): List<Sticker> = all().filter { it.used > 0 }.sortedByDescending { it.used }.take(limit)

    fun get(id: String): Sticker? = all().firstOrNull { it.id == id }

    fun count(): Int = synchronized(lock) { load().getJSONObject("stickers").length() }

    fun packs(): List<StickerPack> {
        val stickers = all()
        val names = synchronized(lock) { load().getJSONArray("packs").let { a -> List(a.length()) { a.getString(it) } } }
        return names.map { n ->
            val mine = stickers.filter { it.pack == n }.sortedByDescending { it.added }
            StickerPack(n, mine.size, mine.firstOrNull())
        }
    }

    // ── Cambios ──
    private fun update(id: String, change: (JSONObject) -> Unit): Boolean = synchronized(lock) {
        val idx = load()
        val rec = idx.getJSONObject("stickers").optJSONObject(id) ?: return false
        change(rec)
        save(idx)
        true
    }

    fun markUsed(id: String) {
        update(id) { it.put("used", System.currentTimeMillis() / 1000.0).put("uses", it.optInt("uses") + 1) }
    }

    /** Alterna favorito y devuelve el estado nuevo. */
    fun toggleFav(id: String): Boolean {
        var now = false
        update(id) {
            now = !it.optBoolean("fav")
            it.put("fav", now)
        }
        return now
    }

    fun moveToPack(id: String, pack: String): Boolean {
        val name = pack.trim().take(60)
        if (name.isEmpty()) return false
        synchronized(lock) {
            val idx = load()
            addPackName(idx, name)
            save(idx)
        }
        return update(id) { it.put("pack", name) }
    }

    fun createPack(name: String): Boolean = synchronized(lock) {
        val n = name.trim().take(60)
        val idx = load()
        val packs = idx.getJSONArray("packs")
        if (n.isEmpty() || (0 until packs.length()).any { packs.getString(it) == n }) return false
        packs.put(n)
        save(idx)
        true
    }

    fun deleteSticker(id: String): Boolean = synchronized(lock) {
        val idx = load()
        val rec = idx.getJSONObject("stickers").remove(id) as? JSONObject ?: return false
        File(dir, rec.optString("file")).delete()
        save(idx)
        true
    }

    /** Borra el paquete con sus stickers (el paquete por defecto solo se vacía). Devuelve cuántos borró. */
    fun deletePack(name: String): Int = synchronized(lock) {
        val idx = load()
        val map = idx.getJSONObject("stickers")
        val ids = ArrayList<String>()
        val keys = map.keys()
        while (keys.hasNext()) {
            val k = keys.next()
            if (map.getJSONObject(k).optString("pack") == name) ids += k
        }
        ids.forEach { k -> (map.remove(k) as? JSONObject)?.let { File(dir, it.optString("file")).delete() } }
        if (name != DEFAULT_PACK) {
            val packs = idx.getJSONArray("packs")
            val kept = JSONArray()
            for (i in 0 until packs.length()) if (packs.getString(i) != name) kept.put(packs.getString(i))
            idx.put("packs", kept)
        }
        save(idx)
        ids.size
    }

    // ── Emojis recientes (comparten el índice) ──
    fun recentEmojis(): List<String> = synchronized(lock) {
        val a = load().getJSONArray("recent_emojis")
        List(a.length()) { a.getString(it) }
    }

    fun addRecentEmoji(emoji: String): List<String> = synchronized(lock) {
        val idx = load()
        val a = idx.getJSONArray("recent_emojis")
        val list = (listOf(emoji) + List(a.length()) { a.getString(it) }.filter { it != emoji }).take(MAX_RECENT_EMOJIS)
        idx.put("recent_emojis", JSONArray(list))
        save(idx)
        list
    }
}
