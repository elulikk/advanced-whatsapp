package com.advancedwhatsapp.app.gateway

import android.app.Service
import android.content.Intent
import android.os.Binder
import android.os.IBinder
import android.os.Process
import android.system.Os
import android.util.Log
import com.advancedwhatsapp.app.App
import com.advancedwhatsapp.app.BuildConfig
import java.io.File
import java.util.zip.ZipInputStream
import kotlin.system.exitProcess

object NodeBridge {
    init {
        System.loadLibrary("node")
        System.loadLibrary("nodebridge")
    }

    external fun startNode(args: Array<String>): Int
}

/** Corre el gateway en el proceso :node. Si Node termina, el proceso muere y Android lo vuelve a levantar. */
class NodeService : Service() {
    private val binder = Binder()
    private var started = false

    override fun onBind(intent: Intent): IBinder {
        if (!started) {
            started = true
            start(intent.getStringExtra(EXTRA_TOKEN).orEmpty())
        }
        return binder
    }

    private fun start(token: String) {
        // V8 necesita una pila grande: el hilo por defecto de Android es chico
        Thread(null, {
            try {
                val dir = prepareGateway()
                File(App.dataDir, PID_FILE).writeText(Process.myPid().toString())
                Os.setenv("HOME", App.dataDir.path, true)
                Os.setenv("TMPDIR", cacheDir.path, true)
                Os.setenv("PORT", App.GATEWAY_PORT.toString(), true)
                Os.setenv("ADVANCEDWHATSAPP_TOKEN", token, true)
                Os.setenv("ADVANCEDWHATSAPP_DATA_DIR", App.dataDir.path, true)
                Os.setenv("ADVANCEDWHATSAPP_BUILD", BuildConfig.VERSION_NAME, true)
                Os.setenv("NODE_ENV", "production", true)
                val code = NodeBridge.startNode(
                    arrayOf("node", "--max-old-space-size=512", File(dir, "android_main.mjs").path)
                )
                Log.w(TAG, "Node terminó con código $code")
            } catch (e: Throwable) {
                Log.e(TAG, "No se pudo iniciar Node", e)
            }
            exitProcess(0)
        }, "node", 64L * 1024 * 1024).start()
    }

    /** Descomprime el gateway solo la primera vez y después de actualizar la app. */
    private fun prepareGateway(): File {
        val dir = File(filesDir, "gateway")
        val marker = File(dir, ".instalado")
        val stamp = packageManager.getPackageInfo(packageName, 0).lastUpdateTime.toString()
        if (marker.exists() && marker.readText() == stamp) return dir
        dir.deleteRecursively()
        dir.mkdirs()
        val root = dir.canonicalPath + File.separator
        assets.open("gateway.zip").use { raw ->
            ZipInputStream(raw.buffered()).use { zip ->
                while (true) {
                    val e = zip.nextEntry ?: break
                    val out = File(dir, e.name)
                    require(out.canonicalPath.startsWith(root)) { "Ruta inválida en el zip: ${e.name}" }
                    if (e.isDirectory) {
                        out.mkdirs()
                    } else {
                        out.parentFile?.mkdirs()
                        out.outputStream().use { zip.copyTo(it) }
                    }
                }
            }
        }
        marker.writeText(stamp)
        return dir
    }

    companion object {
        const val EXTRA_TOKEN = "token"
        const val PID_FILE = "node.pid"
        private const val TAG = "NodeService"
    }
}
