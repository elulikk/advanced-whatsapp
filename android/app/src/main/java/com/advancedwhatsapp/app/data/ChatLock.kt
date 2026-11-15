package com.advancedwhatsapp.app.data

import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import com.advancedwhatsapp.app.Prefs
import java.security.MessageDigest
import java.security.SecureRandom
import javax.crypto.SecretKeyFactory
import javax.crypto.spec.PBEKeySpec

/**
 * Bloqueo de chats con una contraseña global de la app. De la contraseña solo se guarda un hash PBKDF2 con sal
 * (nunca el texto). Qué chats están bloqueados se guarda en [Prefs]. "Desbloqueado" es estado de sesión, en memoria:
 * el chat queda abierto mientras se lo mira y se vuelve a cerrar al salir de él o tras un rato en segundo plano.
 * Los estados son de Compose, así las pantallas se actualizan solas.
 *
 * Además: chats **ocultos** (un chat bloqueado que no aparece en la lista ni en la búsqueda hasta escribir el código secreto,
 * guardado también como hash PBKDF2) y el **bloqueo de la app entera** (misma contraseña, al abrir y al volver tras X minutos).
 */
object ChatLock {
    const val MIN_PASSWORD_LEN = 4
    const val LOCKED_PREVIEW = "Chat bloqueado"
    const val LOCKED_NOTIFICATION = "Nuevo mensaje"
    private const val ITERATIONS = 120_000
    private const val AWAY_RELOCK_MS = 60_000L

    private const val K_HASH = "lock_hash"
    private const val K_SALT = "lock_salt"
    private const val K_ITERS = "lock_iters"
    private const val K_CHATS = "lock_chats"
    private const val K_BIO = "lock_biometric"
    private const val K_HIDDEN = "lock_hidden"
    private const val K_S_HASH = "lock_secret_hash"
    private const val K_S_SALT = "lock_secret_salt"
    private const val K_S_ITERS = "lock_secret_iters"
    const val MIN_SECRET_LEN = 3
    private const val MIN_APP_AWAY_MS = 10_000L   // un selector de fotos o la cámara no cuentan como "salir de la app"

    var locked by mutableStateOf(emptySet<String>())
        private set
    var open by mutableStateOf(emptySet<String>())
        private set
    var hidden by mutableStateOf(emptySet<String>())
        private set
    /** Los chats ocultos se ven en esta sesión (se escribió el código secreto o se mostraron desde Ajustes). */
    var revealed by mutableStateOf(false)
        private set
    /** La app entera está desbloqueada en esta sesión (solo importa si el bloqueo de la app está activado). */
    var appUnlocked by mutableStateOf(false)
        private set
    private var hasPw by mutableStateOf(false)
    private var hasSecret by mutableStateOf(false)
    private var loaded = false
    private var leftAt = 0L

    private fun ensureLoaded() {
        if (loaded) return
        loaded = true
        locked = Prefs.getString(K_CHATS).split('\n').filter { it.isNotEmpty() }.toSet()
        hasPw = Prefs.getString(K_HASH).isNotEmpty()
        hidden = Prefs.getString(K_HIDDEN).split('\n').filter { it.isNotEmpty() }.toSet()
        hasSecret = Prefs.getString(K_S_HASH).isNotEmpty()
    }

    /** Cargar antes de dibujar pantallas (no escribir estados de Compose en plena composición). */
    fun load() = ensureLoaded()

    fun isLocked(jid: String): Boolean { ensureLoaded(); return jid in locked }

    /** ¿Se puede ver el contenido? No está bloqueado o ya se desbloqueó en esta sesión. */
    fun isOpen(jid: String): Boolean { ensureLoaded(); return jid !in locked || jid in open }

    fun hasPassword(): Boolean { ensureLoaded(); return hasPw }

    // ── Contraseña ──

    fun hash(password: String, salt: ByteArray, iterations: Int = ITERATIONS): String {
        val spec = PBEKeySpec(password.toCharArray(), salt, iterations, 256)
        return SecretKeyFactory.getInstance("PBKDF2WithHmacSHA256").generateSecret(spec).encoded.toHex()
    }

    /** Crea o cambia la contraseña. false si es demasiado corta. */
    fun setPassword(password: String): Boolean {
        ensureLoaded()
        if (password.trim().length < MIN_PASSWORD_LEN) return false
        val salt = ByteArray(16).also { SecureRandom().nextBytes(it) }
        Prefs.putString(K_SALT, salt.toHex())
        Prefs.putString(K_ITERS, ITERATIONS.toString())
        Prefs.putString(K_HASH, hash(password, salt))
        hasPw = true
        return true
    }

    fun verify(password: String): Boolean {
        ensureLoaded()
        val stored = Prefs.getString(K_HASH)
        val salt = Prefs.getString(K_SALT).hexToBytes() ?: return false
        if (stored.isEmpty()) return false
        val iters = Prefs.getString(K_ITERS).toIntOrNull() ?: ITERATIONS
        return MessageDigest.isEqual(hash(password, salt, iters).toByteArray(), stored.toByteArray())
    }

    /** Quita la contraseña y desbloquea todos los chats: sin contraseña no hay nada que proteger. */
    fun removePassword() {
        ensureLoaded()
        listOf(K_HASH, K_SALT, K_ITERS, K_CHATS, K_HIDDEN, K_S_HASH, K_S_SALT, K_S_ITERS).forEach { Prefs.putString(it, "") }
        hasPw = false
        hasSecret = false
        locked = emptySet()
        open = emptySet()
        hidden = emptySet()
        revealed = false
        Prefs.appLockEnabled = false   // sin contraseña no hay con qué pedir el desbloqueo
    }

    /** Desbloquear con la huella (opcional; la contraseña siempre funciona). Por defecto activada. */
    var biometric: Boolean
        get() = Prefs.getString(K_BIO, "1") != "0"
        set(v) = Prefs.putString(K_BIO, if (v) "1" else "0")

    // ── Chats ──

    fun lock(jid: String): Boolean {
        ensureLoaded()
        if (!hasPw) return false
        locked = locked + jid
        open = open - jid
        save()
        return true
    }

    fun unlock(jid: String) {
        ensureLoaded()
        locked = locked - jid
        open = open - jid
        if (jid in hidden) { hidden = hidden - jid; saveHidden() }   // sin bloqueo no hay nada que ocultar
        save()
    }

    /** Para copias y sincronización: chats bloqueados y el hash de la contraseña (hash, sal, iteraciones), si hay. */
    fun syncExport(): Pair<Set<String>, Triple<String, String, Int>?> {
        ensureLoaded()
        val hash = Prefs.getString(K_HASH)
        val pw = if (hash.isEmpty()) null else Triple(hash, Prefs.getString(K_SALT), Prefs.getString(K_ITERS).toIntOrNull() ?: ITERATIONS)
        return locked to pw
    }

    /** Aplica lo que vino de otro dispositivo. `pw` = nueva contraseña (hash, sal, iteraciones); `removePw` la quita. */
    fun syncApply(lock: Set<String>, unlock: Set<String>, pw: Triple<String, String, Int>?, removePw: Boolean) {
        ensureLoaded()
        if (removePw) {
            listOf(K_HASH, K_SALT, K_ITERS).forEach { Prefs.putString(it, "") }
            hasPw = false
        } else if (pw != null) {
            Prefs.putString(K_HASH, pw.first)
            Prefs.putString(K_SALT, pw.second)
            Prefs.putString(K_ITERS, pw.third.toString())
            hasPw = true
        }
        locked = (locked + lock) - unlock
        open = open - unlock
        save()
    }

    fun markOpen(jid: String) { if (jid in locked) open = open + jid }
    fun relock(jid: String) { open = open - jid }
    fun lockAll() { open = emptySet(); revealed = false }

    /** La app pasó a segundo plano / volvió: tras un rato afuera se vuelve a pedir la contraseña. */
    fun appLeft() { leftAt = System.currentTimeMillis() }

    fun appReturned() {
        ensureLoaded()
        if (leftAt != 0L) {
            val away = System.currentTimeMillis() - leftAt
            if (away > AWAY_RELOCK_MS) lockAll()
            if (Prefs.appLockEnabled && away >= maxOf(Prefs.appLockMinutes * 60_000L, MIN_APP_AWAY_MS)) appUnlocked = false
        }
        leftAt = 0L
    }

    // ── Bloqueo de la app entera ──

    /** ¿Hay que mostrar el pedido de contraseña sobre toda la app? */
    fun appLocked(): Boolean { ensureLoaded(); return Prefs.appLockEnabled && hasPw && !appUnlocked }

    fun unlockApp() { appUnlocked = true }

    // ── Chats ocultos ──

    fun isHidden(jid: String): Boolean { ensureLoaded(); return jid in hidden }

    /** ¿Debe esconderse de la lista y la búsqueda? Oculto y sin haber escrito el código. */
    fun hiddenNow(jid: String): Boolean = isHidden(jid) && !revealed

    fun hasSecretCode(): Boolean { ensureLoaded(); return hasSecret }

    /** Crea o cambia el código secreto. false si es muy corto. */
    fun setSecretCode(code: String): Boolean {
        ensureLoaded()
        val clean = code.trim()
        if (clean.length < MIN_SECRET_LEN) return false
        val salt = ByteArray(16).also { SecureRandom().nextBytes(it) }
        Prefs.putString(K_S_SALT, salt.toHex())
        Prefs.putString(K_S_ITERS, ITERATIONS.toString())
        Prefs.putString(K_S_HASH, hash(clean, salt))
        hasSecret = true
        return true
    }

    fun verifySecret(code: String): Boolean {
        ensureLoaded()
        val stored = Prefs.getString(K_S_HASH)
        val salt = Prefs.getString(K_S_SALT).hexToBytes() ?: return false
        if (stored.isEmpty() || code.isBlank()) return false
        val iters = Prefs.getString(K_S_ITERS).toIntOrNull() ?: ITERATIONS
        return MessageDigest.isEqual(hash(code.trim(), salt, iters).toByteArray(), stored.toByteArray())
    }

    /** Quita el código y deja de ocultar: los chats ocultos pasan a ser solo bloqueados. */
    fun removeSecretCode() {
        ensureLoaded()
        listOf(K_S_HASH, K_S_SALT, K_S_ITERS, K_HIDDEN).forEach { Prefs.putString(it, "") }
        hasSecret = false
        hidden = emptySet()
        revealed = false
    }

    /** Solo se puede ocultar un chat bloqueado y con código secreto creado. */
    fun setHidden(jid: String, on: Boolean): Boolean {
        ensureLoaded()
        if (on && !(jid in locked && hasSecret)) return false
        hidden = if (on) hidden + jid else hidden - jid
        saveHidden()
        return true
    }

    fun reveal() { revealed = true }
    fun conceal() { revealed = false }

    /**
     * Lo escrito en el buscador: si es el código secreto, muestra u oculta los chats ocultos. Calcula un hash
     * (PBKDF2): llamar desde un hilo de fondo. Devuelve "revealed", "concealed" o null (no era el código).
     */
    fun trySecret(text: String): String? {
        if (!hasSecretCode() || text.trim().length < MIN_SECRET_LEN || !verifySecret(text)) return null
        return if (revealed) { revealed = false; "concealed" } else { revealed = true; "revealed" }
    }

    private fun saveHidden() = Prefs.putString(K_HIDDEN, hidden.joinToString("\n"))

    private fun save() = Prefs.putString(K_CHATS, locked.joinToString("\n"))

    private fun ByteArray.toHex() = joinToString("") { "%02x".format(it) }

    private fun String.hexToBytes(): ByteArray? =
        if (isEmpty() || length % 2 != 0) null
        else runCatching { ByteArray(length / 2) { substring(it * 2, it * 2 + 2).toInt(16).toByte() } }.getOrNull()
}
