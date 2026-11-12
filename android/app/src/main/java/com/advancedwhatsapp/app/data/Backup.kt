package com.advancedwhatsapp.app.data

import android.content.ContentValues
import android.os.Build
import android.provider.MediaStore
import com.advancedwhatsapp.app.App
import com.advancedwhatsapp.app.Prefs
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.io.FileInputStream
import java.io.FileOutputStream
import java.io.InputStream
import java.io.OutputStream
import java.security.MessageDigest
import java.util.UUID
import java.util.zip.ZipEntry
import java.util.zip.ZipFile
import java.util.zip.ZipOutputStream

/** Un valor de una sección con el momento (ms) de su último cambio; `deleted` es una lápida. Mismo modelo que app/backup.py. */
data class Item(val t: Long, val v: Any? = null, val deleted: Boolean = false)

/**
 * Una sección de datos propios de la app: `clave -> valor` (valores JSON simples como tipos de Kotlin: String, Boolean, Long,
 * List, Map). Para que una función nueva entre en las copias y la sincronización alcanza con registrar su sección en
 * [Backup.register]. `apply` recibe clave -> valor nuevo (null = borrar) y devuelve las claves que pudo aplicar.
 */
class Section(
    val name: String,
    val label: String,
    val collect: () -> Map<String, Any?>,
    val apply: (Map<String, Any?>) -> Set<String>,
    val sync: Boolean = true,
    val secret: Boolean = false,
    val platforms: List<String>? = null,
    val files: ((String) -> Pair<String, File>?)? = null,
    val importFile: ((String, ByteArray) -> Unit)? = null,
    val order: Int = 50,
)

/** Resultado de aplicar un documento: por sección, cuántos ítems entraron y cuántos locales le ganaron al remoto. */
data class ApplyReport(val applied: Int, val localWins: Int)

/**
 * Copias de seguridad (`.awbackup`) y base de la sincronización con la PC. El formato (zip dentro de [AwCrypto]) y las reglas de
 * mezcla son idénticos a app/backup.py; se describen en docs/ARQUITECTURA.md («Copias y sincronización»).
 * Bloqueante: llamar desde Dispatchers.IO.
 */
object Backup {
    const val FORMAT = "awbackup"
    const val VERSION = 1
    const val PLATFORM = "android"
    const val BASELINE_T = 1L
    private const val TOMBSTONE_TTL_MS = 90L * 24 * 3600 * 1000
    private const val MAX_ENTRY_BYTES = 64L * 1024 * 1024
    const val EXT = ".awbackup"
    private val MERGE_TABLES = listOf("chats", "contacts", "messages", "reactions")

    private val registry = LinkedHashMap<String, Section>()
    private var loaded = false
    private val lock = Any()

    fun register(s: Section) { registry[s.name] = s }

    fun sections(syncOnly: Boolean = false, includeSecret: Boolean = true): List<Section> {
        ensureBuiltin()
        return registry.values.filter { (it.sync || !syncOnly) && (includeSecret || !it.secret) }.sortedWith(compareBy({ it.order }, { it.name }))
    }

    private fun ensureBuiltin() {
        if (loaded) return
        loaded = true
        BackupSections.registerAll()
    }

    // ── Valores canónicos y hash ──

    fun canonical(v: Any?): String = StringBuilder().also { writeCanonical(it, v) }.toString()

    private fun writeCanonical(sb: StringBuilder, v: Any?) {
        when (v) {
            null -> sb.append("null")
            is Boolean -> sb.append(if (v) "true" else "false")
            is Int, is Long -> sb.append(v.toString())
            is Number -> sb.append(v.toString())
            is String -> quote(sb, v)
            is Map<*, *> -> {
                sb.append('{')
                v.keys.map { it as String }.sorted().forEachIndexed { i, k ->
                    if (i > 0) sb.append(',')
                    quote(sb, k)
                    sb.append(':')
                    writeCanonical(sb, v[k])
                }
                sb.append('}')
            }
            is List<*> -> {
                sb.append('[')
                v.forEachIndexed { i, x ->
                    if (i > 0) sb.append(',')
                    writeCanonical(sb, x)
                }
                sb.append(']')
            }
            else -> quote(sb, v.toString())
        }
    }

    // Igual que json.dumps(ensure_ascii=False) de Python: solo se escapan comillas, barra y caracteres de control
    private fun quote(sb: StringBuilder, s: String) {
        sb.append('"')
        for (ch in s) {
            when {
                ch == '"' -> sb.append("\\\"")
                ch == '\\' -> sb.append("\\\\")
                ch == '\n' -> sb.append("\\n")
                ch == '\r' -> sb.append("\\r")
                ch == '\t' -> sb.append("\\t")
                ch == '\b' -> sb.append("\\b")
                ch == '\u000c' -> sb.append("\\f")
                ch < ' ' -> sb.append("\\u").append("%04x".format(ch.code))
                else -> sb.append(ch)
            }
        }
        sb.append('"')
    }

    fun valueHash(v: Any?): String =
        MessageDigest.getInstance("SHA-1").digest(canonical(v).toByteArray(Charsets.UTF_8)).joinToString("") { "%02x".format(it) }

    fun itemSig(i: Item): String = if (i.deleted) "" else valueHash(i.v)

    /** ¿Gana el ítem remoto? Más nuevo gana; en empate, la firma más grande (un valor le gana a un borrado). Igual en las dos puntas. */
    fun remoteWins(remote: Item, local: Item?): Boolean {
        if (local == null) return true
        if (remote.t != local.t) return remote.t > local.t
        return itemSig(remote) > itemSig(local)
    }

    /** Pura: devuelve (ítems remotos que ganan, cantidad de ítems locales que le ganan al remoto). */
    fun mergeItems(local: Map<String, Item>, remote: Map<String, Item>): Pair<Map<String, Item>, Int> {
        val winners = LinkedHashMap<String, Item>()
        for ((k, ri) in remote) if (remoteWins(ri, local[k])) winners[k] = ri
        val localWins = local.count { (k, li) -> remote[k]?.let { r -> remoteWins(li, r) } ?: true }
        return winners to localWins
    }

    /** Huella de qué cambió (secciones, claves, t y borrados), sin los valores. */
    fun digest(all: Map<String, Map<String, Item>>): String {
        val md = MessageDigest.getInstance("SHA-1")
        for (name in all.keys.sorted()) {
            val m = all.getValue(name)
            for (k in m.keys.sorted()) {
                val it = m.getValue(k)
                md.update("$name\u0000$k\u0000${it.t}\u0000${if (it.deleted) 1 else 0}\n".toByteArray(Charsets.UTF_8))
            }
        }
        return md.digest().joinToString("") { "%02x".format(it) }
    }

    // ── JSON <-> tipos de Kotlin ──

    fun fromJson(v: Any?): Any? = when {
        v == null || v == JSONObject.NULL -> null
        v is JSONObject -> v.keys().asSequence().associateWith { fromJson(v.get(it)) }
        v is JSONArray -> List(v.length()) { fromJson(v.get(it)) }
        v is Int -> v.toLong()
        else -> v
    }

    fun toJson(v: Any?): Any = when (v) {
        null -> JSONObject.NULL
        is Map<*, *> -> JSONObject().also { o -> v.forEach { (k, x) -> o.put(k as String, toJson(x)) } }
        is List<*> -> JSONArray().also { a -> v.forEach { a.put(toJson(it)) } }
        else -> v!!
    }

    fun itemsToJson(items: Map<String, Item>): JSONObject = JSONObject().put("items", JSONObject().also { o ->
        for ((k, i) in items) o.put(k, JSONObject().put("t", i.t).apply { if (i.deleted) put("d", 1) else put("v", toJson(i.v)) })
    })

    fun itemsFromJson(doc: JSONObject): Map<String, Item> {
        val o = doc.optJSONObject("items") ?: return emptyMap()
        val out = LinkedHashMap<String, Item>()
        for (k in o.keys()) {
            val i = o.optJSONObject(k) ?: continue
            if (!i.has("t")) continue
            out[k] = if (i.optInt("d") == 1) Item(i.getLong("t"), deleted = true) else Item(i.getLong("t"), fromJson(i.opt("v")))
        }
        return out
    }

    // ── Estado de sincronización (qué cambió desde la última lectura) ──

    private fun ensureState() {
        Db.raw().execSQL(
            "CREATE TABLE IF NOT EXISTS sync_state (section TEXT NOT NULL, key TEXT NOT NULL, h TEXT NOT NULL, " +
                "t INTEGER NOT NULL, d INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (section, key))",
        )
    }

    private fun nowMs() = System.currentTimeMillis()

    /** Ítems actuales de la sección con su `t`; registra lo que cambió (o desapareció) desde la última lectura. */
    fun localItems(sec: Section): Map<String, Item> = synchronized(lock) {
        val current = sec.collect()
        ensureState()
        val d = Db.raw()
        val rows = HashMap<String, Triple<String, Long, Int>>()
        d.rawQuery("SELECT key, h, t, d FROM sync_state WHERE section = ?", arrayOf(sec.name)).use { c ->
            while (c.moveToNext()) rows[c.getString(0)] = Triple(c.getString(1), c.getLong(2), c.getInt(3))
        }
        val first = rows.isEmpty()
        val now = nowMs()
        val items = LinkedHashMap<String, Item>()
        val upserts = ArrayList<Array<Any>>()
        for ((k, v) in current) {
            val h = valueHash(v)
            val st = rows[k]
            val t: Long
            if (st == null || st.first != h || st.third == 1) {
                t = if (first) BASELINE_T else now
                upserts += arrayOf<Any>(sec.name, k, h, t, 0)
            } else {
                t = st.second
            }
            items[k] = Item(t, v)
        }
        for ((k, st) in rows) {
            if (current.containsKey(k)) continue
            var t = st.second
            if (st.third == 0) {
                t = now
                upserts += arrayOf<Any>(sec.name, k, "", t, 1)
            }
            if (now - t < TOMBSTONE_TTL_MS) items[k] = Item(t, deleted = true)
        }
        if (upserts.isNotEmpty()) {
            d.beginTransaction()
            try {
                for (u in upserts) d.execSQL("INSERT OR REPLACE INTO sync_state (section, key, h, t, d) VALUES (?, ?, ?, ?, ?)", u)
                d.setTransactionSuccessful()
            } finally {
                d.endTransaction()
            }
        }
        d.execSQL("DELETE FROM sync_state WHERE section = ? AND d = 1 AND t < ?", arrayOf(sec.name, now - TOMBSTONE_TTL_MS))
        items
    }

    /** Aplica a la app los ítems remotos que ganan. Con `force` (reemplazo total) gana siempre el remoto y se borra lo local ajeno. */
    fun applyRemote(sec: Section, remote: Map<String, Item>, force: Boolean = false): ApplyReport = synchronized(lock) {
        val local = localItems(sec)
        val winners: Map<String, Item>
        val localWins: Int
        if (force) {
            val w = LinkedHashMap(remote)
            for ((k, i) in local) if (!w.containsKey(k) && !i.deleted) w[k] = Item(nowMs(), deleted = true)
            winners = w
            localWins = 0
        } else {
            val m = mergeItems(local, remote)
            winners = m.first
            localWins = m.second
        }
        val changes = winners.mapValues { (_, i) -> if (i.deleted) null else i.v }
        val done = if (changes.isEmpty()) emptySet() else sec.apply(changes)
        if (done.isNotEmpty()) {
            val current = sec.collect()
            val d = Db.raw()
            ensureState()
            d.beginTransaction()
            try {
                for (k in done) {
                    val t = winners.getValue(k).t
                    if (current.containsKey(k)) {
                        d.execSQL("INSERT OR REPLACE INTO sync_state (section, key, h, t, d) VALUES (?, ?, ?, ?, 0)", arrayOf(sec.name, k, valueHash(current[k]), t))
                    } else {
                        d.execSQL("INSERT OR REPLACE INTO sync_state (section, key, h, t, d) VALUES (?, ?, '', ?, 1)", arrayOf(sec.name, k, t))
                    }
                }
                d.setTransactionSuccessful()
            } finally {
                d.endTransaction()
            }
        }
        ApplyReport(done.size, localWins)
    }

    // ── Secciones que esta versión no conoce (p. ej. «tags» de la PC): se guardan tal cual y se reenvían ──

    private val opaqueFile: File get() = File(App.dataDir, "sync_opaque.json")

    private fun loadOpaque(): JSONObject = runCatching { JSONObject(opaqueFile.readText()) }.getOrDefault(JSONObject())

    private fun saveOpaque(o: JSONObject) {
        val tmp = File(App.dataDir, "sync_opaque.json.tmp")
        tmp.writeText(o.toString())
        tmp.renameTo(opaqueFile)
    }

    class Opaque(val name: String, val sync: Boolean, val items: Map<String, Item>)

    fun opaqueSections(): List<Opaque> = synchronized(lock) {
        val o = loadOpaque()
        o.keys().asSequence().map { n ->
            val s = o.getJSONObject(n)
            Opaque(n, s.optBoolean("sync"), itemsFromJson(s))
        }.toList()
    }

    private fun mergeOpaque(name: String, remote: Map<String, Item>, sync: Boolean, force: Boolean): ApplyReport = synchronized(lock) {
        val o = loadOpaque()
        val cur = o.optJSONObject(name)
        val local = if (cur != null) itemsFromJson(cur) else emptyMap()
        val merged = LinkedHashMap(local)
        var applied = 0
        var localWins = 0
        if (force) {
            merged.clear()
            merged.putAll(remote)
            applied = remote.size
        } else {
            val (winners, lw) = mergeItems(local, remote)
            merged.putAll(winners)
            applied = winners.size
            localWins = lw
        }
        o.put(name, itemsToJson(merged).put("sync", sync || (cur?.optBoolean("sync") == true)))
        saveOpaque(o)
        ApplyReport(applied, localWins)
    }

    // ── Documentos ──

    fun deviceId(): String {
        val cur = Prefs.getString("device_id")
        if (cur.length >= 16) return cur
        return UUID.randomUUID().toString().replace("-", "").also { Prefs.putString("device_id", it) }
    }

    private fun appVersion(): String = runCatching {
        App.instance.packageManager.getPackageInfo(App.instance.packageName, 0).versionName
    }.getOrNull().orEmpty()

    class Built(val items: Map<String, Map<String, Item>>, val filesSent: Map<String, List<String>>)

    /** Escribe el zip (sin cifrar). Mismo contenido que `build_zip` de la PC. */
    fun buildZip(
        out: File, kind: String, secs: List<Section>, includeDb: Boolean = false, includeOpaque: Boolean = true,
        fileFilter: ((Section, Map<String, Item>) -> List<String>)? = null, maxFilesBytes: Long? = null,
    ): Built {
        val all = LinkedHashMap<String, Map<String, Item>>()
        val sent = LinkedHashMap<String, List<String>>()
        val syncFlags = LinkedHashMap<String, Boolean>()
        ZipOutputStream(FileOutputStream(out).buffered()).use { zf ->
            fun put(name: String, bytes: ByteArray) {
                zf.putNextEntry(ZipEntry(name))
                zf.write(bytes)
                zf.closeEntry()
            }
            var budget = maxFilesBytes
            for (sec in secs) {
                val items = localItems(sec)
                all[sec.name] = items
                syncFlags[sec.name] = sec.sync
                put("sections/${sec.name}.json", itemsToJson(items).toString().toByteArray())
                val fileOf = sec.files
                if (fileOf != null) {
                    val keys = fileFilter?.invoke(sec, items) ?: items.filter { !it.value.deleted }.keys.toList()
                    val used = ArrayList<String>()
                    for (key in keys) {
                        val (arc, file) = fileOf(key) ?: continue
                        if (!file.exists() || (budget != null && file.length() > budget)) continue
                        zf.putNextEntry(ZipEntry("files/${sec.name}/$arc"))
                        FileInputStream(file).use { it.copyTo(zf) }
                        zf.closeEntry()
                        if (budget != null) budget -= file.length()
                        used += key
                    }
                    sent[sec.name] = used
                }
            }
            if (includeOpaque) {
                for (op in opaqueSections()) {
                    if (all.containsKey(op.name) || (kind == "sync" && !op.sync)) continue
                    all[op.name] = op.items
                    syncFlags[op.name] = op.sync
                    put("sections/${op.name}.json", itemsToJson(op.items).toString().toByteArray())
                }
            }
            if (includeDb) {
                val copy = File(App.instance.cacheDir, "awb_copy.db")
                copy.delete()
                Db.raw().execSQL("VACUUM INTO '${copy.absolutePath.replace("'", "''")}'")
                zf.putNextEntry(ZipEntry("db/magsapp.db"))
                FileInputStream(copy).use { it.copyTo(zf) }
                zf.closeEntry()
                copy.delete()
            }
            val sections = JSONObject()
            for ((n, items) in all) sections.put(n, JSONObject().put("count", items.size).put("sync", syncFlags[n] == true))
            val manifest = JSONObject()
                .put("format", FORMAT).put("version", VERSION).put("kind", kind).put("created_at", nowMs())
                .put("device", JSONObject().put("id", deviceId()).put("platform", PLATFORM).put("app_version", appVersion()))
                .put("sections", sections).put("has_db", includeDb)
            put("manifest.json", manifest.toString().toByteArray())
        }
        return Built(all, sent)
    }

    /** Abre un zip ya descifrado y valida el manifiesto. */
    fun openZip(file: File): Pair<ZipFile, JSONObject> {
        try {
            val zf = ZipFile(file)
            val entry = zf.getEntry("manifest.json") ?: throw BackupException("El contenido de la copia está dañado.")
            val manifest = JSONObject(zf.getInputStream(entry).readBytes().toString(Charsets.UTF_8))
            if (manifest.optString("format") != FORMAT) throw BackupException("No es una copia de Advanced WhatsApp.")
            if (manifest.optInt("version") > VERSION) throw BackupException("La copia es de una versión más nueva de Advanced WhatsApp. Actualizá la app.")
            return zf to manifest
        } catch (e: BackupException) {
            throw e
        } catch (e: Exception) {
            throw BackupException("El contenido de la copia está dañado.")
        }
    }

    private fun readEntry(zf: ZipFile, name: String, limit: Long = MAX_ENTRY_BYTES): ByteArray {
        val e = zf.getEntry(name) ?: throw BackupException("Falta $name en la copia.")
        if (e.size > limit) throw BackupException("La copia contiene un archivo demasiado grande.")
        return zf.getInputStream(e).readBytes()
    }

    /** Importa archivos y secciones de un documento ya abierto. Devuelve el informe por sección. */
    fun applyDocument(zf: ZipFile, manifest: JSONObject, force: Boolean = false, onlySync: Boolean = false, includeSecrets: Boolean = true): Map<String, ApplyReport> {
        val srcPlatform = manifest.optJSONObject("device")?.optString("platform")
        val names = java.util.Collections.list(zf.entries()).map { it.name }
        val secs = sections(syncOnly = onlySync, includeSecret = includeSecrets).associateBy { it.name }
        val report = LinkedHashMap<String, ApplyReport>()
        // 1) archivos primero (el .webp de un sticker tiene que estar antes de aplicar su ítem)
        for ((name, sec) in secs) {
            val imp = sec.importFile ?: continue
            val prefix = "files/$name/"
            for (entry in names) {
                if (!entry.startsWith(prefix) || entry.endsWith("/")) continue
                val rest = entry.removePrefix(prefix)
                if (rest.contains('/') || rest.contains('\\') || rest.startsWith(".")) continue
                runCatching { imp(rest, readEntry(zf, entry, 8L * 1024 * 1024)) }
            }
        }
        // 2) secciones conocidas, en orden
        for (sec in secs.values.sortedWith(compareBy({ it.order }, { it.name }))) {
            val entry = "sections/${sec.name}.json"
            if (entry !in names) continue
            if (sec.platforms != null && (srcPlatform !in sec.platforms || PLATFORM !in sec.platforms)) continue
            report[sec.name] = try {
                applyRemote(sec, itemsFromJson(JSONObject(readEntry(zf, entry).toString(Charsets.UTF_8))), force)
            } catch (e: BackupException) {
                throw e
            } catch (e: Exception) {
                ApplyReport(0, 0)
            }
        }
        // 3) secciones que esta versión no conoce: se guardan para reenviarlas (así los tags de la PC dan la vuelta por acá)
        val known = registry.keys
        for (entry in names) {
            if (!entry.startsWith("sections/") || !entry.endsWith(".json")) continue
            val name = entry.removePrefix("sections/").removeSuffix(".json")
            if (name in known || !Regex("^[A-Za-z0-9_\\-]+$").matches(name)) continue
            val syncFlag = manifest.optJSONObject("sections")?.optJSONObject(name)?.optBoolean("sync") == true
            if (onlySync && !syncFlag) continue
            report[name] = runCatching {
                mergeOpaque(name, itemsFromJson(JSONObject(readEntry(zf, entry).toString(Charsets.UTF_8))), syncFlag, force)
            }.getOrDefault(ApplyReport(0, 0))
        }
        return report
    }

    // ── Copias ──

    /** Escribe un `.awbackup` cifrado en `out`. */
    fun create(out: OutputStream, password: String, includeSecrets: Boolean = false, includeDb: Boolean = true) {
        if (password.isEmpty()) throw BackupException("Falta la contraseña de la copia.")
        val tmp = File(App.instance.cacheDir, "awb_${System.nanoTime()}.zip")
        try {
            buildZip(tmp, "backup", sections(includeSecret = includeSecrets), includeDb = includeDb)
            FileInputStream(tmp).use { AwCrypto.encrypt(it, out, password) }
        } finally {
            tmp.delete()
        }
    }

    private fun decryptToZip(input: InputStream, password: String): File {
        val tmp = File(App.instance.cacheDir, "awr_${System.nanoTime()}.zip")
        try {
            FileOutputStream(tmp).use { AwCrypto.decrypt(input, it, password) }
        } catch (e: Exception) {
            tmp.delete()
            throw e
        }
        return tmp
    }

    /** Abre la copia solo para mostrar qué trae (no cambia nada). */
    fun inspect(input: InputStream, password: String): JSONObject {
        val tmp = decryptToZip(input, password)
        try {
            val (zf, manifest) = openZip(tmp)
            zf.use { manifest.put("has_db", it.getEntry("db/magsapp.db") != null) }
            return manifest
        } finally {
            tmp.delete()
        }
    }

    class RestoreResult(val db: Map<String, Int>, val sections: Map<String, ApplyReport>)

    /** Restaura una copia. `replace = false` combina (gana lo más nuevo); `true` deja la app igual a la copia. */
    fun restore(input: InputStream, password: String, replace: Boolean = false, includeSecrets: Boolean = true): RestoreResult {
        val tmp = decryptToZip(input, password)
        try {
            val (zf, manifest) = openZip(tmp)
            val result = zf.use { z ->
                var stats = emptyMap<String, Int>()
                val dbEntry = z.getEntry("db/magsapp.db")
                if (dbEntry != null) {
                    val dbFile = File(App.instance.cacheDir, "awr_${System.nanoTime()}.db")
                    try {
                        z.getInputStream(dbEntry).use { s -> FileOutputStream(dbFile).use { o -> s.copyTo(o) } }
                        stats = mergeDb(dbFile, replace)
                    } finally {
                        dbFile.delete()
                    }
                }
                RestoreResult(stats, applyDocument(z, manifest, force = replace, includeSecrets = includeSecrets))
            }
            return result
        } finally {
            tmp.delete()
        }
    }

    /**
     * Une chats, contactos, mensajes y reacciones de la base de la copia: solo agrega lo que falta (nunca pisa un mensaje que ya
     * está). Con `replace` vacía antes esas tablas. Usa las columnas en común, así sirve entre PC y Android.
     */
    fun mergeDb(backupDb: File, replace: Boolean): Map<String, Int> {
        val d = Db.raw()
        val stats = LinkedHashMap<String, Int>()
        d.execSQL("ATTACH DATABASE ? AS bk", arrayOf(backupDb.absolutePath))
        val touched = ArrayList<String>()
        try {
            val bkTables = HashSet<String>()
            d.rawQuery("SELECT name FROM bk.sqlite_master WHERE type = 'table'", null).use { while (it.moveToNext()) bkTables += it.getString(0) }
            d.beginTransaction()
            try {
                if (replace) for (t in listOf("reactions", "messages", "contacts", "chats")) if (t in bkTables) d.execSQL("DELETE FROM main.$t")
                for (t in MERGE_TABLES) {
                    if (t !in bkTables) continue
                    val mainCols = ArrayList<String>()
                    d.rawQuery("PRAGMA main.table_info($t)", null).use { while (it.moveToNext()) mainCols += it.getString(1) }
                    val bkCols = HashSet<String>()
                    d.rawQuery("PRAGMA bk.table_info($t)", null).use { while (it.moveToNext()) bkCols += it.getString(1) }
                    val cols = mainCols.filter { it in bkCols }
                    if (cols.isEmpty()) continue
                    val colSql = cols.joinToString(", ")
                    if (t == "messages") {
                        d.execSQL("DROP TABLE IF EXISTS temp.new_msg_ids")
                        d.execSQL("CREATE TEMP TABLE new_msg_ids AS SELECT id FROM bk.messages WHERE id NOT IN (SELECT id FROM main.messages)")
                    }
                    val before = count(d, "main.$t")
                    d.execSQL("INSERT OR IGNORE INTO main.$t ($colSql) SELECT $colSql FROM bk.$t")
                    stats[t] = count(d, "main.$t") - (if (replace) 0 else before)
                    if (t == "messages") {
                        d.rawQuery("SELECT DISTINCT chat_jid FROM main.messages WHERE id IN (SELECT id FROM temp.new_msg_ids)", null)
                            .use { while (it.moveToNext()) touched += it.getString(0) }
                        if ("media_path" in cols) {
                            // Los archivos de otro equipo no existen acá: se vacía la ruta y se vuelven a bajar al abrirlos
                            val missing = ArrayList<String>()
                            d.rawQuery(
                                "SELECT id, media_path FROM main.messages WHERE id IN (SELECT id FROM temp.new_msg_ids) " +
                                    "AND media_path IS NOT NULL AND media_path != ''", null,
                            ).use { while (it.moveToNext()) if (!File(it.getString(1)).exists()) missing += it.getString(0) }
                            for (id in missing) d.execSQL("UPDATE main.messages SET media_path = NULL WHERE id = ?", arrayOf(id))
                        }
                    }
                }
                if (replace) d.rawQuery("SELECT jid FROM main.chats", null).use { while (it.moveToNext()) touched += it.getString(0) }
                d.setTransactionSuccessful()
            } finally {
                d.endTransaction()
            }
        } finally {
            runCatching { d.execSQL("DETACH DATABASE bk") }
        }
        Db.refreshAfterMerge(touched)
        return stats
    }

    private fun count(d: android.database.sqlite.SQLiteDatabase, table: String): Int =
        d.rawQuery("SELECT COUNT(*) FROM $table", null).use { if (it.moveToFirst()) it.getInt(0) else 0 }

    // ── Copia automática en Descargas con rotación ──

    private const val AUTO_FOLDER = "Download/AdvancedWhatsapp/Copias"
    const val AUTO_PREFIX = "AdvancedWhatsapp-auto-"

    var autoEnabled: Boolean
        get() = Prefs.getString("bk_auto") == "1"
        set(v) = Prefs.putString("bk_auto", if (v) "1" else "0")
    var autoDays: Int
        get() = Prefs.getString("bk_days").toIntOrNull() ?: 7
        set(v) = Prefs.putString("bk_days", v.toString())
    var autoKeep: Int
        get() = Prefs.getString("bk_keep").toIntOrNull() ?: 5
        set(v) = Prefs.putString("bk_keep", v.toString())
    var autoSecrets: Boolean
        get() = Prefs.getString("bk_secrets") == "1"
        set(v) = Prefs.putString("bk_secrets", if (v) "1" else "0")
    val autoLast: Long get() = Prefs.getString("bk_last").toLongOrNull() ?: 0L
    const val AUTO_SECRET = "backup_auto"

    /** Llamada periódica: hace la copia automática si está activada, hay contraseña y ya toca. */
    fun autoTick() {
        if (!autoEnabled || !autoBackupDue(autoLast, autoDays)) return
        val pw = SecretStore.get(AUTO_SECRET)
        if (pw.isEmpty()) return
        runAuto(pw, autoKeep, autoSecrets)
        Prefs.putString("bk_last", nowMs().toString())
    }

    fun autoBackupDue(lastMs: Long, everyDays: Int, now: Long = nowMs()): Boolean =
        now - lastMs >= maxOf(1, everyDays) * 86_400_000L - 60_000L

    /** Hace una copia en Descargas/AdvancedWhatsapp/Copias y deja solo las `keep` más nuevas. Devuelve el nombre. */
    fun runAuto(password: String, keep: Int, includeSecrets: Boolean): String {
        val name = AUTO_PREFIX + java.text.SimpleDateFormat("yyyyMMdd-HHmmss", java.util.Locale.US).format(java.util.Date()) + EXT
        val tmp = File(App.instance.cacheDir, "awa_${System.nanoTime()}$EXT")
        try {
            FileOutputStream(tmp).use { create(it, password, includeSecrets, includeDb = true) }
            val cr = App.instance.contentResolver
            val values = ContentValues().apply {
                put(MediaStore.Downloads.DISPLAY_NAME, name)
                put(MediaStore.Downloads.MIME_TYPE, "application/octet-stream")
                put(MediaStore.Downloads.RELATIVE_PATH, AUTO_FOLDER)
                put(MediaStore.Downloads.IS_PENDING, 1)
            }
            val uri = cr.insert(MediaStore.Downloads.EXTERNAL_CONTENT_URI, values) ?: throw BackupException("No se pudo crear la copia en Descargas.")
            try {
                (cr.openOutputStream(uri) ?: throw BackupException("No se pudo escribir la copia.")).use { o -> FileInputStream(tmp).use { it.copyTo(o) } }
                cr.update(uri, ContentValues().apply { put(MediaStore.Downloads.IS_PENDING, 0) }, null, null)
            } catch (e: Exception) {
                cr.delete(uri, null, null)
                throw e
            }
            rotate(keep)
            return name
        } finally {
            tmp.delete()
        }
    }

    private fun rotate(keep: Int) {
        val cr = App.instance.contentResolver
        val ids = ArrayList<Long>()
        cr.query(
            MediaStore.Downloads.EXTERNAL_CONTENT_URI, arrayOf(MediaStore.Downloads._ID),
            "${MediaStore.Downloads.DISPLAY_NAME} LIKE ? AND ${MediaStore.Downloads.RELATIVE_PATH} LIKE ?",
            arrayOf("$AUTO_PREFIX%$EXT", "$AUTO_FOLDER%"), "${MediaStore.Downloads.DATE_ADDED} DESC, ${MediaStore.Downloads._ID} DESC",
        )?.use { while (it.moveToNext()) ids += it.getLong(0) }
        for (id in ids.drop(maxOf(1, keep))) {
            runCatching { cr.delete(android.content.ContentUris.withAppendedId(MediaStore.Downloads.EXTERNAL_CONTENT_URI, id), null, null) }
        }
    }
}
