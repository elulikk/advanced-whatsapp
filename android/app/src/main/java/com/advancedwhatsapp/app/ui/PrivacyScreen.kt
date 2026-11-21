package com.advancedwhatsapp.app.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.advancedwhatsapp.app.Prefs
import com.advancedwhatsapp.app.data.Actions
import com.advancedwhatsapp.app.gateway.GatewayClient
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject

private val LAST_SEEN = listOf("all", "contacts", "contact_blacklist", "none")
private val LAST_SEEN_LABEL = mapOf(
    "all" to "Todos", "contacts" to "Mis contactos", "contact_blacklist" to "Mis contactos, excepto…", "none" to "Nadie",
)
private val ONLINE = listOf("all", "match_last_seen")
private val ONLINE_LABEL = mapOf("all" to "Todos", "match_last_seen" to "Los mismos que ven mi última vez")
private val CALL_MODES = listOf("off", "unknown", "blocklist", "allowlist")
private val CALL_LABEL = mapOf(
    "off" to "No rechazar ninguna",
    "unknown" to "Rechazar números no agendados",
    "blocklist" to "Rechazar solo los de la lista",
    "allowlist" to "Rechazar todas menos la lista",
)
private val CALL_LIST_HINT = mapOf(
    "unknown" to "Los números de la lista suenan aunque no estén agendados.",
    "blocklist" to "Estos números no te pueden llamar.",
    "allowlist" to "Solo estos números te pueden llamar.",
)

/**
 * Modo fantasma, privacidad de la cuenta (vale también en la app oficial), filtro de llamadas, estados eliminados y
 * avisos de borrados y de vistas. El filtro y los estados eliminados los aplica el gateway (igual que en la PC).
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun PrivacyScreen(onBack: () -> Unit) {
    val scope = rememberCoroutineScope()
    var ghost by remember { mutableStateOf(Prefs.ghostMode) }
    var receipts by remember { mutableStateOf(Prefs.readReceipts) }
    var statusReceipts by remember { mutableStateOf(Prefs.statusReceipts) }
    var notifyDeleted by remember { mutableStateOf(Prefs.notifyDeleted) }
    var notifyViews by remember { mutableStateOf(Prefs.notifyStatusViews) }

    // Privacidad de la cuenta (null = todavía no se leyó o no hay conexión)
    var account by remember { mutableStateOf<JSONObject?>(null) }
    var accountNote by remember { mutableStateOf("Leyendo la privacidad de la cuenta…") }
    // Ajustes del gateway
    var gwLoaded by remember { mutableStateOf(false) }
    var keepStatuses by remember { mutableStateOf(true) }
    var callMode by remember { mutableStateOf("off") }
    var callList by remember { mutableStateOf("") }
    var callNote by remember { mutableStateOf("") }

    fun loadAccount(note: String = "") {
        scope.launch {
            val res = withContext(Dispatchers.IO) { runCatching { GatewayClient.get("/privacy") } }
            account = res.getOrNull()
            accountNote = if (res.isSuccess) note else note.ifEmpty { "Conectá WhatsApp para ver y cambiar estos ajustes." }
        }
    }

    fun saveAccount(field: String, value: String) {
        accountNote = "Guardando en WhatsApp…"
        scope.launch {
            val res = withContext(Dispatchers.IO) { runCatching { GatewayClient.post("/privacy", JSONObject().put(field, value)) } }
            // Se relee siempre: WhatsApp devuelve el valor real (si falló, vuelve a lo que había)
            loadAccount(res.exceptionOrNull()?.let { "No se pudo cambiar: ${it.message}" } ?: "Guardado.")
        }
    }

    fun applyGateway(d: JSONObject) {
        keepStatuses = d.optBoolean("keep_deleted_statuses", true)
        val calls = d.optJSONObject("calls")
        callMode = calls?.optString("mode")?.takeIf { it in CALL_MODES } ?: "off"
        val list = calls?.optJSONArray("list")
        callList = if (list == null) "" else List(list.length()) { list.optString(it) }.joinToString("\n")
        gwLoaded = true
    }

    fun saveGateway(body: JSONObject, okNote: String) {
        scope.launch {
            val res = withContext(Dispatchers.IO) { runCatching { GatewayClient.post("/gateway-prefs", body) } }
            res.getOrNull()?.let { applyGateway(it) }
            callNote = res.exceptionOrNull()?.let { "No se pudo guardar: ${it.message}" } ?: okNote
        }
    }

    LaunchedEffect(Unit) {
        loadAccount()
        withContext(Dispatchers.IO) { runCatching { GatewayClient.get("/gateway-prefs") } }
            .onSuccess { applyGateway(it) }
            .onFailure { callNote = "El servicio de WhatsApp no responde: probá de nuevo en unos segundos." }
    }

    Scaffold(topBar = {
        TopAppBar(title = { Text("Privacidad y llamadas") }, navigationIcon = {
            IconButton(onClick = onBack) { Icon(Icons.AutoMirrored.Filled.ArrowBack, "Volver") }
        })
    }) { pad ->
        Column(
            Modifier.padding(pad).fillMaxSize().background(C.panel).verticalScroll(rememberScrollState()).padding(14.dp),
            verticalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            Title("Modo fantasma")
            Switch2("Activar el modo fantasma", ghost) {
                ghost = it
                Prefs.ghostMode = it
                // Se aplica ya: fantasma = fuera de línea; al salir, en línea (la app está al frente)
                scope.launch(Dispatchers.IO) { Actions.setPresence(true) }
            }
            Hint("Desde esta app no se mandan tildes azules, ni «escribiendo…», ni figurás «en línea», y los estados que mires no avisan. La app oficial sigue su propio ajuste.")
            Switch2("Enviar confirmación de lectura (tildes azules)", receipts && !ghost, enabled = !ghost) { receipts = it; Prefs.readReceipts = it }
            Switch2("Al ver un estado, avisar al contacto que lo viste", statusReceipts && !ghost, enabled = !ghost) {
                statusReceipts = it; Prefs.statusReceipts = it
            }

            Title("Privacidad de la cuenta (también en la app oficial)")
            val acc = account
            Selector("Quién ve mi última vez", acc?.optString("last").orEmpty(), if (acc == null) emptyList() else LAST_SEEN,
                { saveAccount("last", it) }, { LAST_SEEN_LABEL[it] ?: "—" })
            Selector("Quién ve cuándo estoy en línea", acc?.optString("online").orEmpty(), if (acc == null) emptyList() else ONLINE,
                { saveAccount("online", it) }, { ONLINE_LABEL[it] ?: "—" })
            Switch2("Tildes azules en toda la cuenta", acc?.optString("readreceipts") != "none", enabled = acc != null) {
                saveAccount("readreceipts", if (it) "all" else "none")
            }
            Hint("Es el mismo ajuste de WhatsApp › Privacidad y es recíproco: si ocultás tu última vez o tus tildes azules, tampoco ves las de los demás. «Mis contactos, excepto…» usa la lista que armaste en la app oficial.")
            if (accountNote.isNotEmpty()) Hint(accountNote)

            Title("Filtro de llamadas")
            Selector("Llamadas", callMode, if (gwLoaded) CALL_MODES else emptyList(), { callMode = it }, { CALL_LABEL[it] ?: it })
            if (callMode != "off") {
                Hint(CALL_LIST_HINT[callMode].orEmpty())
                Field(callList, { callList = it }, "Números, uno por línea (con código de país y de área)", minLines = 3)
            }
            OutlinedButton(
                onClick = {
                    val numbers = callList.lines().map { l -> l.filter { it.isDigit() } }.filter { it.length >= 6 }
                    callNote = "Guardando…"
                    saveGateway(
                        JSONObject().put("calls", JSONObject().put("mode", callMode).put("list", JSONArray(numbers))),
                        "Filtro guardado.",
                    )
                },
                enabled = gwLoaded, modifier = Modifier.fillMaxWidth(),
            ) { Text("Guardar filtro") }
            if (callNote.isNotEmpty()) Hint(callNote)
            Hint("Una llamada filtrada se corta sola en todos tus dispositivos (como tocar «Rechazar») y queda anotada en el chat.")

            Title("Borrados y estados")
            Switch2("Conservar los estados que el contacto elimina", keepStatuses, enabled = gwLoaded) {
                keepStatuses = it
                saveGateway(JSONObject().put("keep_deleted_statuses", it), "")
            }
            Hint("Siguen en Estados hasta las 24 h, marcados «Lo eliminó», y se pueden guardar.")
            Switch2("Avisar cuando alguien elimina un mensaje que me mandó", notifyDeleted) { notifyDeleted = it; Prefs.notifyDeleted = it }
            Switch2("Avisar cuando alguien ve uno de mis estados", notifyViews) { notifyViews = it; Prefs.notifyStatusViews = it }
            Hint("Estos avisos van sin sonido, en el canal «Borrados y vistas de estados»; se cambia desde los ajustes de notificaciones de Android.")
        }
    }
}

@Composable
private fun Title(text: String) {
    Text(text, color = C.ember, fontWeight = FontWeight.SemiBold, modifier = Modifier.padding(top = 10.dp))
}

@Composable
private fun Hint(text: String) {
    if (text.isNotEmpty()) Text(text, color = C.text2, fontSize = 13.sp)
}

@Composable
private fun Switch2(label: String, value: Boolean, enabled: Boolean = true, onChange: (Boolean) -> Unit) {
    Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.fillMaxWidth()) {
        Text(label, color = if (enabled) C.text else C.text3, fontSize = 14.sp, modifier = Modifier.weight(1f).padding(end = 10.dp))
        Switch(checked = value, onCheckedChange = onChange, enabled = enabled)
    }
}
