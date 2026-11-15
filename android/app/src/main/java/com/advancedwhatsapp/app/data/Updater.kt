package com.advancedwhatsapp.app.data

import android.content.Intent
import android.net.Uri
import android.os.Build
import android.provider.Settings
import androidx.core.content.FileProvider
import com.advancedwhatsapp.app.App
import com.advancedwhatsapp.app.BuildConfig
import com.advancedwhatsapp.app.Prefs
import okhttp3.OkHttpClient
import okhttp3.Request
import org.json.JSONArray
import java.io.File
import java.security.MessageDigest
import java.util.concurrent.TimeUnit

data class UpdateInfo(
    val version: String, val tag: String, val notes: String, val pageUrl: String,
    val assetName: String, val assetUrl: String, val assetSize: Long, val sha256Url: String,
)

class UpdateException(message: String) : Exception(message)

/**
 * Actualizaciones desde GitHub Releases (mismo repo y convención que la PC, ver docs/ARQUITECTURA.md):
 * release con tag `android-v<versión>` y asset `AdvancedWhatsapp-Android-<versión>.apk` (+ `.apk.sha256` opcional).
 * Todo es bloqueante: se llama desde hilos, nunca desde el principal.
 */
object Updater {
    const val DEFAULT_REPO = "elulikk/advanced-whatsapp"
    const val TAG_PREFIX = "android-v"
    const val CHECK_INTERVAL_MS = 12 * 3600 * 1000L
    private const val APK_MIME = "application/vnd.android.package-archive"
    private val ASSET = Regex("^AdvancedWhatsapp-Android-(\\d+\\.\\d+\\.\\d+(?:[-+][0-9A-Za-z.\\-+]*)?)\\.apk$")

    private val http = OkHttpClient.Builder()
        .connectTimeout(15, TimeUnit.SECONDS).readTimeout(30, TimeUnit.SECONDS).build()

    // ── Lógica pura (probada en UpdaterTest) ──

    /** Versión del nombre de un asset de APK, o null si no es el esperado. */
    fun versionOfAsset(name: String): String? = ASSET.matchEntire(name)?.groupValues?.get(1)

    /** Mayor, menor, parche; una versión final le gana a su pre-release (1.0.0 > 1.0.0-beta). */
    private fun parse(v: String): List<Long>? {
        val m = Regex("(\\d+)\\.(\\d+)(?:\\.(\\d+))?(?:-([0-9A-Za-z.\\-]+))?").find(v) ?: return null
        val pre = m.groupValues[4]
        val nums = listOf(1, 2, 3).map { m.groupValues[it].toLongOrNull() ?: 0L }
        return nums + (if (pre.isEmpty()) 1L else 0L)
    }

    fun isNewer(candidate: String, current: String): Boolean {
        val a = parse(candidate) ?: return false
        val b = parse(current) ?: return false
        for (i in a.indices) if (a[i] != b[i]) return a[i] > b[i]
        // Misma base y ambas pre-release: se comparan como texto (beta.2 > beta.1)
        val pa = Regex("-([0-9A-Za-z.\\-]+)").find(candidate)?.groupValues?.get(1).orEmpty()
        val pb = Regex("-([0-9A-Za-z.\\-]+)").find(current)?.groupValues?.get(1).orEmpty()
        return pa.isNotEmpty() && pa > pb
    }

    fun parseSha256(text: String): String =
        Regex("\\b([0-9a-fA-F]{64})\\b").find(text)?.groupValues?.get(1)?.lowercase().orEmpty()

    fun normalizeRepo(repo: String): String {
        val r = repo.trim().removePrefix("https://github.com/").removePrefix("http://github.com/")
            .trim('/').removeSuffix(".git")
        return if (Regex("[\\w.\\-]+/[\\w.\\-]+").matches(r)) r else ""
    }

    // ── Red ──

    val repo: String get() = normalizeRepo(Prefs.updateRepo).ifEmpty { DEFAULT_REPO }

    private fun get(url: String): okhttp3.Response = http.newCall(
        Request.Builder().url(url).header("User-Agent", "AdvancedWhatsapp-Updater")
            .header("Accept", "application/vnd.github+json").build()
    ).execute()

    /** La versión nueva (si la hay) o null. Lanza UpdateException con un mensaje listo para mostrar. */
    fun check(): UpdateInfo? {
        val body = try {
            get("https://api.github.com/repos/$repo/releases?per_page=30").use { r ->
                when {
                    r.code == 404 -> throw UpdateException(
                        "No se encontraron releases: hace falta que el repositorio $repo sea público para buscar actualizaciones.")
                    r.code == 403 || r.code == 429 -> throw UpdateException("GitHub limitó las consultas por ahora. Probá más tarde.")
                    !r.isSuccessful -> throw UpdateException("GitHub respondió con el error ${r.code}.")
                    else -> r.body?.string().orEmpty()
                }
            }
        } catch (e: UpdateException) {
            throw e
        } catch (e: Exception) {
            throw UpdateException("No se pudo conectar con GitHub. Revisá tu conexión a internet.")
        }
        val releases = try { JSONArray(body) } catch (e: Exception) { throw UpdateException("La respuesta de GitHub no se pudo leer.") }
        var best: UpdateInfo? = null
        for (i in 0 until releases.length()) {
            val rel = releases.optJSONObject(i) ?: continue
            if (rel.optBoolean("draft") || rel.optBoolean("prerelease")) continue
            val tag = rel.optString("tag_name")
            if (!tag.startsWith(TAG_PREFIX)) continue
            val assets = rel.optJSONArray("assets") ?: continue
            var apk: org.json.JSONObject? = null
            var version = ""
            for (j in 0 until assets.length()) {
                val a = assets.optJSONObject(j) ?: continue
                versionOfAsset(a.optString("name"))?.let { apk = a; version = it }
                if (apk != null) break
            }
            val a = apk ?: continue
            val url = a.optString("browser_download_url")
            if (url.isEmpty()) continue
            var sha = ""
            for (j in 0 until assets.length()) {
                val s = assets.optJSONObject(j) ?: continue
                if (s.optString("name").equals(a.optString("name") + ".sha256", ignoreCase = true)) sha = s.optString("browser_download_url")
            }
            if (best == null || isNewer(version, best.version)) {
                best = UpdateInfo(version, tag, rel.optString("body").trim(), rel.optString("html_url"),
                    a.optString("name"), url, a.optLong("size"), sha)
            }
        }
        return best?.takeIf { isNewer(it.version, BuildConfig.VERSION_NAME) }
    }

    private val dir: File get() = File(App.instance.cacheDir, "updates").apply { mkdirs() }

    /** Baja el APK a la caché de la app, verifica tamaño y SHA-256 (si el release lo trae) y devuelve el archivo. */
    fun download(info: UpdateInfo, progress: (Long, Long) -> Unit): File {
        dir.listFiles()?.forEach { it.delete() }
        val expected = if (info.sha256Url.isEmpty()) "" else try {
            get(info.sha256Url).use { parseSha256(it.body?.string().orEmpty()) }
        } catch (e: Exception) { "" }
        if (info.sha256Url.isNotEmpty() && expected.isEmpty()) throw UpdateException("No se pudo leer el archivo de verificación (.sha256).")
        val dest = File(dir, info.assetName)
        val part = File(dir, info.assetName + ".part")
        try {
            get(info.assetUrl).use { r ->
                if (!r.isSuccessful) throw UpdateException("No se pudo bajar la actualización (error ${r.code}).")
                val body = r.body ?: throw UpdateException("La descarga vino vacía.")
                val total = body.contentLength().takeIf { it > 0 } ?: info.assetSize
                var done = 0L
                var last = 0L
                body.byteStream().use { input ->
                    part.outputStream().use { out ->
                        val buf = ByteArray(64 * 1024)
                        while (true) {
                            val n = input.read(buf)
                            if (n < 0) break
                            out.write(buf, 0, n)
                            done += n
                            val now = System.currentTimeMillis()
                            if (now - last > 150) { last = now; progress(done, total) }
                        }
                    }
                }
                progress(done, total)
            }
            if (info.assetSize > 0 && part.length() != info.assetSize) throw UpdateException("La descarga quedó incompleta.")
            if (expected.isNotEmpty() && sha256(part) != expected) throw UpdateException("La descarga está dañada: no coincide el SHA-256 del release.")
            if (!part.renameTo(dest)) throw UpdateException("No se pudo guardar la actualización.")
            return dest
        } catch (e: UpdateException) {
            part.delete(); throw e
        } catch (e: Exception) {
            part.delete(); throw UpdateException("No se pudo bajar la actualización: ${e.message}")
        }
    }

    private fun sha256(f: File): String {
        val md = MessageDigest.getInstance("SHA-256")
        f.inputStream().use { i -> val b = ByteArray(1 shl 16); while (true) { val n = i.read(b); if (n < 0) break; md.update(b, 0, n) } }
        return md.digest().joinToString("") { "%02x".format(it) }
    }

    fun canInstall(): Boolean = Build.VERSION.SDK_INT < 26 || App.instance.packageManager.canRequestPackageInstalls()

    /** Pantalla de Android para habilitar «instalar apps desconocidas» de Advanced WhatsApp. */
    fun askInstallPermission() {
        runCatching {
            App.instance.startActivity(
                Intent(Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES, Uri.parse("package:${App.instance.packageName}"))
                    .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
        }
    }

    /** Abre el instalador del sistema con el APK bajado (FileProvider de la caché). */
    fun install(apk: File) {
        val c = App.instance
        val uri = FileProvider.getUriForFile(c, "${c.packageName}.files", apk)
        c.startActivity(Intent(Intent.ACTION_VIEW).setDataAndType(uri, APK_MIME)
            .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_ACTIVITY_NEW_TASK))
    }

    // ── Estado compartido con la pantalla ──
    @Volatile var available: UpdateInfo? = null

    /** Busca (al abrir y cada 12 h): guarda el resultado y avisa con una notificación una vez por versión. */
    fun autoCheck() {
        if (!Prefs.autoUpdate) return
        val info = try { check() } catch (e: UpdateException) { return }
        available = info
        if (info != null && Prefs.updateNotified != info.version) {
            Prefs.updateNotified = info.version
            com.advancedwhatsapp.app.notify.Notifier.updateAvailable(info.version)
        }
    }
}
