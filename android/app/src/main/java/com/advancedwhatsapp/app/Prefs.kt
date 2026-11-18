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

    /** Borrar las notificaciones de WhatsApp mientras AW está conectado (notify/WaNotificationSilencer). */
    var replaceWaNotifications: Boolean
        get() = p.getBoolean("replace_wa_notifications", false)
        set(v) = p.edit().putBoolean("replace_wa_notifications", v).apply()

    /** Vibración corta al tocar botones (además respeta el ajuste de vibración táctil del sistema). */
    var haptics: Boolean
        get() = p.getBoolean("haptics", true)
        set(v) = p.edit().putBoolean("haptics", v).apply()

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

    /** Leer con IA (descripción + texto) las fotos al descargarlas. Apagado por defecto: gasta cuota del proveedor. */
    var autoReadImages: Boolean
        get() = p.getBoolean("auto_read_images", false)
        set(v) = p.edit().putBoolean("auto_read_images", v).apply()

    /** Al terminar una nota de voz suena la siguiente del chat (si es otro audio ya bajado). */
    var audioAutoplay: Boolean
        get() = p.getBoolean("audio_autoplay", true)
        set(v) = p.edit().putBoolean("audio_autoplay", v).apply()

    /** Velocidad de los audios (1, 1.5 o 2): se recuerda entre notas y entre sesiones. */
    var audioSpeed: Float
        get() = p.getFloat("audio_speed", 1f)
        set(v) = p.edit().putFloat("audio_speed", v).apply()

    /** Tarjeta con título, descripción e imagen de los links. */
    var linkPreviews: Boolean
        get() = p.getBoolean("link_previews", true)
        set(v) = p.edit().putBoolean("link_previews", v).apply()

    /** Limpieza automática de multimedia bajada: más vieja que X días (0 = apagado) y/o tope en GB (0 = sin tope). */
    var mediaAutoDays: Int
        get() = p.getInt("media_auto_days", 0)
        set(v) = p.edit().putInt("media_auto_days", v).apply()
    var mediaAutoGb: Float
        get() = p.getFloat("media_auto_gb", 0f)
        set(v) = p.edit().putFloat("media_auto_gb", v).apply()
    var mediaAutoLast: Long
        get() = p.getLong("media_auto_last", 0L)
        set(v) = p.edit().putLong("media_auto_last", v).apply()

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
    /** Para las copias: ajustes que no son secretos (sin token, claves de IA ni contraseña de bloqueo), con tipos simples. */
    /** Buscar versiones nuevas en GitHub al abrir y cada 12 h (Ajustes). */
    var autoUpdate: Boolean
        get() = p.getBoolean("auto_update", true)
        set(v) = p.edit().putBoolean("auto_update", v).apply()

    /** Dueño/repo de GitHub de donde salen los releases. */
    var updateRepo: String
        get() = p.getString("update_repo", "") ?: ""
        set(v) = p.edit().putString("update_repo", v.trim()).apply()

    /** Última versión de la que ya se avisó con una notificación. */
    var updateNotified: String
        get() = p.getString("update_notified", "") ?: ""
        set(v) = p.edit().putString("update_notified", v).apply()

    fun exportNonSecret(): Map<String, Any> = p.all.filter { (k, v) ->
        k != "token" && !k.startsWith("key_") && !k.startsWith("lock_") && !k.startsWith("secret_") && k != "device_id" &&
            !k.startsWith("bk_") && !k.startsWith("sync_") && (v is Boolean || v is String || v is Int || v is Long)
    }.mapValues { (_, v) -> if (v is Int) v.toLong() else v!! }

    fun setRaw(k: String, v: Any) {
        val e = p.edit()
        when (v) {
            is Boolean -> e.putBoolean(k, v)
            is Long -> if (p.all[k] is Int) e.putInt(k, v.toInt()) else e.putLong(k, v)
            is String -> e.putString(k, v)
        }
        e.apply()
    }

    fun remove(k: String) = p.edit().remove(k).apply()

    fun getString(k: String, d: String = ""): String = p.getString(k, d) ?: d
    fun putString(k: String, v: String) = p.edit().putString(k, v).apply()
}
