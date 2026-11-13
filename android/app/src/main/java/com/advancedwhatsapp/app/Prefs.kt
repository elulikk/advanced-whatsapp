package com.advancedwhatsapp.app

import android.content.Context
import android.content.SharedPreferences
import java.security.SecureRandom

/** Configuración de la app. Las claves de IA quedan en la carpeta privada de la app (sin copia de seguridad). */
object Prefs {
    private lateinit var p: SharedPreferences

    fun init(c: Context) {
        p = c.getSharedPreferences("config", Context.MODE_PRIVATE)
    }

    /** Token que comparten la app y el gateway: el puerto local lo puede tocar cualquier app del celular. */
    val token: String
        get() = p.getString("token", null) ?: run {
            val bytes = ByteArray(32).also { SecureRandom().nextBytes(it) }
            bytes.joinToString("") { "%02x".format(it) }.also { p.edit().putString("token", it).commit() }
        }

    fun apiKey(provider: String): String = p.getString("key_$provider", "")?.trim().orEmpty()
    fun setApiKey(provider: String, v: String) = p.edit().putString("key_$provider", v.trim()).apply()

    fun model(provider: String): String = p.getString("model_$provider", "").orEmpty()
    fun setModel(provider: String, v: String) = p.edit().putString("model_$provider", v).apply()

    var provider: String
        get() = p.getString("provider", "Gemini") ?: "Gemini"
        set(v) = p.edit().putString("provider", v).apply()

    var notifications: Boolean
        get() = p.getBoolean("notifications", true)
        set(v) = p.edit().putBoolean("notifications", v).apply()

    /** Grupos: apagado no avisan ni suman en el número del ícono (se ven igual en la pestaña Grupos). */
    var notifyGroups: Boolean
        get() = p.getBoolean("notify_groups", false)
        set(v) = p.edit().putBoolean("notify_groups", v).apply()

    var readReceipts: Boolean
        get() = p.getBoolean("read_receipts", true)
        set(v) = p.edit().putBoolean("read_receipts", v).apply()

    /** Al ver un estado, el contacto ve que lo viste. */
    var statusReceipts: Boolean
        get() = p.getBoolean("status_receipts", true)
        set(v) = p.edit().putBoolean("status_receipts", v).apply()

    /** Mantener despierto el procesador: los mensajes llegan al instante, a cambio de más batería. */
    var keepCpuAwake: Boolean
        get() = p.getBoolean("keep_cpu", true)
        set(v) = p.edit().putBoolean("keep_cpu", v).apply()

    /** Transcribir solas las notas de voz que llegan y las que mando (necesita clave de Groq u OpenAI). */
    var autoTranscribe: Boolean
        get() = p.getBoolean("auto_transcribe", true)
        set(v) = p.edit().putBoolean("auto_transcribe", v).apply()

    var useProfileInReplies: Boolean
        get() = p.getBoolean("use_profile", true)
        set(v) = p.edit().putBoolean("use_profile", v).apply()

    /** Pedir la contraseña de los chats bloqueados al abrir la app y al volver tras un rato en segundo plano. */
    var appLockEnabled: Boolean
        get() = p.getBoolean("app_lock", false)
        set(v) = p.edit().putBoolean("app_lock", v).apply()

    /** Minutos en segundo plano tras los que se vuelve a pedir (0 = en cuanto vuelve). */
    var appLockMinutes: Int
        get() = p.getInt("app_lock_min", 1)
        set(v) = p.edit().putInt("app_lock_min", v).apply()

    /** Días sin actividad para archivar solo un chat (0 = apagado). */
    var autoArchiveDays: Int
        get() = p.getInt("auto_archive_days", 0)
        set(v) = p.edit().putInt("auto_archive_days", v).apply()

    fun getString(k: String, d: String = ""): String = p.getString(k, d) ?: d
    fun putString(k: String, v: String) = p.edit().putString(k, v).apply()
}
