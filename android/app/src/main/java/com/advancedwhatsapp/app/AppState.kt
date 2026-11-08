package com.advancedwhatsapp.app

import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.update
import org.json.JSONObject

data class Connection(
    val status: String = "starting",
    val phone: String? = null,
    val pushName: String? = null,
    val qrDataUrl: String? = null,
) {
    val isConnected get() = status == "connected"
    val label: String
        get() = when (status) {
            "connected" -> "Conectado" + (pushName?.takeIf { it.isNotEmpty() }?.let { " como $it" } ?: "")
            "qr_ready", "logged_out" -> "Falta vincular la cuenta"
            "starting" -> "Arrancando…"
            else -> "Conectando…"
        }
}

data class Presence(val state: String, val lastSeen: Long?)

/** Estado en memoria compartido entre el servicio y las pantallas. */
object AppState {
    val connection = MutableStateFlow(Connection())
    val presence = MutableStateFlow<Map<String, Presence>>(emptyMap())

    /** Sube cada vez que aparece o se borra un estado de un contacto: las pantallas vuelven a pedir la lista. */
    val statusEvents = MutableStateFlow(0)

    /** Cuántas veces se cortó un intento de vinculación: el código que se estaba mostrando ya no sirve. */
    val pairingCuts = MutableStateFlow(0)

    /** Chat abierto con la app al frente: no se notifica ni se cuenta como sin leer. */
    @Volatile var activeChat: String? = null
    val foreground = MutableStateFlow(false)
    val inForeground: Boolean get() = foreground.value

    private val lock = Any()

    fun updateConnection(d: JSONObject) = synchronized(lock) {
        val status = d.optString("status").ifEmpty { return }
        if (d.optBoolean("pairing_cut")) pairingCuts.value++
        val cur = connection.value
        val qr = if (d.isNull("qr_data_url")) null else d.optString("qr_data_url").ifEmpty { null }
        connection.value = Connection(
            status = status,
            phone = d.optString("phone").takeUnless { d.isNull("phone") || it.isEmpty() } ?: cur.phone.takeIf { status == "connected" },
            pushName = d.optString("push_name").takeUnless { d.isNull("push_name") || it.isEmpty() } ?: cur.pushName.takeIf { status == "connected" },
            qrDataUrl = qr ?: cur.qrDataUrl.takeIf { status == "qr_ready" },
        )
    }

    fun markGatewayDown() = synchronized(lock) {
        val cur = connection.value
        if (cur.status != "starting") connection.value = Connection(status = "disconnected")
    }

    fun statusChanged() = statusEvents.update { it + 1 }

    fun updatePresence(d: JSONObject) {
        val jid = d.optString("chat_jid").ifEmpty { return }
        val p = Presence(d.optString("presence"), if (d.isNull("last_seen")) null else d.optLong("last_seen"))
        synchronized(lock) { presence.value = presence.value + (jid to p) }
    }
}
