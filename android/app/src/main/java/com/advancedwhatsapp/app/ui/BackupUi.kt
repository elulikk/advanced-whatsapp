package com.advancedwhatsapp.app.ui

import android.net.Uri
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Checkbox
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.advancedwhatsapp.app.data.Backup
import com.advancedwhatsapp.app.data.BackupException
import com.advancedwhatsapp.app.data.SecretStore
import com.advancedwhatsapp.app.data.Sync
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONObject
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

private fun fmt(ms: Long): String = if (ms <= 0) "nunca" else SimpleDateFormat("dd/MM/yyyy HH:mm", Locale.getDefault()).format(Date(ms))

/** Pide una contraseña (dos veces si `confirm`). `extra` agrega una casilla opcional (devuelve su estado en onOk). */
@Composable
private fun PasswordDialog(
    title: String, confirm: Boolean, extra: String? = null, onOk: (password: String, extra: Boolean) -> Unit, onDismiss: () -> Unit,
) {
    var pw by remember { mutableStateOf("") }
    var again by remember { mutableStateOf("") }
    var flag by remember { mutableStateOf(false) }
    val problem = when {
        confirm && pw.isNotEmpty() && pw.length < 6 -> "Usá al menos 6 caracteres."
        confirm && again.isNotEmpty() && again != pw -> "Las contraseñas no coinciden."
        else -> null
    }
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text(title) },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                OutlinedTextField(pw, { pw = it }, label = { Text("Contraseña") }, singleLine = true, visualTransformation = PasswordVisualTransformation())
                if (confirm) {
                    OutlinedTextField(again, { again = it }, label = { Text("Repetila") }, singleLine = true, visualTransformation = PasswordVisualTransformation())
                }
                problem?.let { Text(it, color = C.danger, fontSize = 13.sp) }
                if (extra != null) {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Checkbox(flag, { flag = it })
                        Text(extra, color = C.text, fontSize = 13.sp)
                    }
                }
            }
        },
        confirmButton = {
            TextButton(enabled = pw.isNotEmpty() && (!confirm || (problem == null && again == pw)), onClick = { onOk(pw, flag) }) { Text("Aceptar") }
        },
        dismissButton = { TextButton(onClick = onDismiss) { Text("Cancelar") } },
    )
}

/**
 * Sección «Copias y sincronización» de Ajustes: copia cifrada .awbackup (manual y automática), sincronización con la PC.
 * Los archivos van por el selector de Android (SAF): no hacen falta permisos de almacenamiento.
 */
@Composable
fun BackupSettings() {
    val c = LocalContext.current
    val scope = rememberCoroutineScope()
    var dialog by remember { mutableStateOf("") }          // create | restore | auto | sync
    var createPw by remember { mutableStateOf("") }
    var createSecrets by remember { mutableStateOf(false) }
    var restoreUri by remember { mutableStateOf<Uri?>(null) }
    var restorePw by remember { mutableStateOf("") }
    var restoreInfo by remember { mutableStateOf<JSONObject?>(null) }
    var busy by remember { mutableStateOf(false) }
    var status by remember { mutableStateOf("") }
    var autoOn by remember { mutableStateOf(Backup.autoEnabled) }
    var autoDays by remember { mutableStateOf(Backup.autoDays) }
    var autoKeep by remember { mutableStateOf(Backup.autoKeep) }
    var autoSecrets by remember { mutableStateOf(Backup.autoSecrets) }
    var syncOn by remember { mutableStateOf(Sync.enabled) }
    var syncInfo by remember { mutableStateOf(Sync.lastTimes()) }

    val createLauncher = rememberLauncherForActivityResult(ActivityResultContracts.CreateDocument("application/octet-stream")) { uri ->
        if (uri == null) return@rememberLauncherForActivityResult
        busy = true
        status = "Creando la copia…"
        scope.launch {
            val err = withContext(Dispatchers.IO) {
                try {
                    c.contentResolver.openOutputStream(uri, "wt")!!.use { Backup.create(it, createPw, createSecrets) }
                    null
                } catch (e: Exception) {
                    e.message ?: "Error desconocido"
                }
            }
            busy = false
            createPw = ""
            status = err?.let { "No se pudo crear la copia: $it" } ?: "Copia creada."
        }
    }
    val restoreLauncher = rememberLauncherForActivityResult(ActivityResultContracts.OpenDocument()) { uri ->
        if (uri != null) {
            restoreUri = uri
            dialog = "restore"
        }
    }

    fun runRestore(replace: Boolean) {
        val uri = restoreUri ?: return
        busy = true
        status = "Restaurando…"
        scope.launch {
            val res = withContext(Dispatchers.IO) {
                try {
                    Result.success(c.contentResolver.openInputStream(uri)!!.use { Backup.restore(it, restorePw, replace) })
                } catch (e: Exception) {
                    Result.failure(e)
                }
            }
            busy = false
            restorePw = ""
            res.onSuccess {
                status = "Listo: ${it.db["messages"] ?: 0} mensajes y ${it.db["chats"] ?: 0} chats agregados; las secciones se combinaron."
                autoOn = Backup.autoEnabled
            }.onFailure { status = "No se pudo restaurar: ${it.message}" }
        }
    }

    Text("Copias y sincronización", color = C.ember, fontWeight = FontWeight.SemiBold, modifier = Modifier.padding(top = 10.dp))
    Text(
        "Una copia cifrada (.awbackup) guarda tus chats, nombres y notas de contactos, chats bloqueados, stickers, mensajes programados y " +
            "ajustes. El mismo archivo se restaura en Advanced WhatsApp de la PC, y al revés. Las claves de IA solo se incluyen si lo elegís.",
        color = C.text2, fontSize = 13.sp,
    )
    OutlinedButton(enabled = !busy, onClick = { dialog = "create" }, modifier = Modifier.fillMaxWidth()) { Text("Crear copia de seguridad…") }
    OutlinedButton(enabled = !busy, onClick = { restoreLauncher.launch(arrayOf("*/*")) }, modifier = Modifier.fillMaxWidth()) { Text("Restaurar copia…") }
    if (status.isNotEmpty()) Text(status, color = C.text2, fontSize = 13.sp)

    Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.fillMaxWidth()) {
        Text("Copia automática en Descargas/AdvancedWhatsapp/Copias", color = C.text, fontSize = 14.sp, modifier = Modifier.weight(1f).padding(end = 10.dp))
        Switch(checked = autoOn, onCheckedChange = {
            if (it && SecretStore.get(Backup.AUTO_SECRET).isEmpty()) {
                dialog = "auto"
            } else {
                autoOn = it
                Backup.autoEnabled = it
            }
        })
    }
    if (autoOn) {
        Selector("Cada", autoDays.toString(), listOf("1", "3", "7", "15", "30"), { autoDays = it.toInt(); Backup.autoDays = autoDays }, { "$it días" })
        Selector("Guardar las últimas", autoKeep.toString(), listOf("2", "3", "5", "10", "20"), { autoKeep = it.toInt(); Backup.autoKeep = autoKeep }, { "$it copias" })
        Row(verticalAlignment = Alignment.CenterVertically) {
            Checkbox(autoSecrets, { autoSecrets = it; Backup.autoSecrets = it })
            Text("Incluir las claves de API de IA", color = C.text, fontSize = 13.sp)
        }
        Text("Última copia automática: ${fmt(Backup.autoLast)}", color = C.text2, fontSize = 13.sp)
        OutlinedButton(onClick = { dialog = "auto" }, modifier = Modifier.fillMaxWidth()) { Text("Cambiar la contraseña de la copia automática…") }
    }

    Text("Sincronizar con la PC", color = C.ember, fontWeight = FontWeight.SemiBold, modifier = Modifier.padding(top = 10.dp))
    Text(
        "Mantiene iguales en el celular y en la PC los tags, chats bloqueados, nombres y notas de contactos, stickers y algunas " +
            "preferencias (gana el último cambio). Viaja cifrado por tu chat «Mi número», sin servidores de terceros. En las dos apps " +
            "hay que activarlo y poner la misma clave. Los mensajes no se sincronizan.",
        color = C.text2, fontSize = 13.sp,
    )
    Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.fillMaxWidth()) {
        Text("Sincronizar automáticamente", color = C.text, fontSize = 14.sp, modifier = Modifier.weight(1f).padding(end = 10.dp))
        Switch(checked = syncOn, onCheckedChange = {
            if (it && !Sync.hasPassword()) {
                dialog = "sync"
            } else {
                syncOn = it
                Sync.enabled = it
            }
        })
    }
    OutlinedButton(onClick = { dialog = "sync" }, modifier = Modifier.fillMaxWidth()) { Text("Clave de sincronización…") }
    OutlinedButton(
        enabled = !busy && Sync.hasPassword(),
        onClick = {
            busy = true
            status = "Sincronizando…"
            Sync.enabled = true
            syncOn = true
            scope.launch {
                val err = withContext(Dispatchers.IO) { Sync.sendNow(full = true) }
                busy = false
                syncInfo = Sync.lastTimes()
                status = err ?: "Sincronización enviada a la PC."
            }
        },
        modifier = Modifier.fillMaxWidth(),
    ) { Text("Sincronizar ahora") }
    Text(
        "Último envío: ${fmt(syncInfo.first * 1000)} · última recepción: ${fmt(syncInfo.second * 1000)}" +
            if (Sync.hasPassword()) "" else ". Falta elegir la clave.",
        color = C.text2, fontSize = 13.sp,
    )

    when (dialog) {
        "create" -> PasswordDialog(
            "Contraseña de la copia", confirm = true, extra = "Incluir las claves de API de IA",
            onOk = { pw, secrets ->
                createPw = pw
                createSecrets = secrets
                dialog = ""
                createLauncher.launch("AdvancedWhatsapp-" + SimpleDateFormat("yyyyMMdd", Locale.US).format(Date()) + Backup.EXT)
            },
            onDismiss = { dialog = "" },
        )
        "restore" -> PasswordDialog(
            "Contraseña de la copia", confirm = false,
            onOk = { pw, _ ->
                val uri = restoreUri
                dialog = ""
                if (uri != null) {
                    restorePw = pw
                    busy = true
                    status = "Abriendo la copia…"
                    scope.launch {
                        val res = withContext(Dispatchers.IO) {
                            try {
                                Result.success(c.contentResolver.openInputStream(uri)!!.use { Backup.inspect(it, pw) })
                            } catch (e: Exception) {
                                Result.failure(e)
                            }
                        }
                        busy = false
                        res.onSuccess { restoreInfo = it; status = ""; dialog = "restore_mode" }
                            .onFailure { status = (it as? BackupException)?.message ?: "No se pudo abrir la copia." }
                    }
                }
            },
            onDismiss = { dialog = "" },
        )
        "restore_mode" -> {
            val info = restoreInfo
            val platform = info?.optJSONObject("device")?.optString("platform")
            AlertDialog(
                onDismissRequest = { dialog = "" },
                title = { Text("Restaurar copia") },
                text = {
                    Text(
                        "Copia del ${fmt(info?.optLong("created_at") ?: 0L)} (hecha en la ${if (platform == "pc") "PC" else "app del celular"}).\n\n" +
                            "«Combinar» agrega lo que falta y se queda con lo más nuevo de cada dato.\n" +
                            "«Reemplazar todo» deja la app igual a la copia: se pierde lo que hayas hecho después.",
                    )
                },
                confirmButton = { TextButton(onClick = { dialog = ""; runRestore(false) }) { Text("Combinar") } },
                dismissButton = {
                    Row {
                        TextButton(onClick = { dialog = ""; runRestore(true) }) { Text("Reemplazar todo", color = C.danger) }
                        TextButton(onClick = { dialog = "" }) { Text("Cancelar") }
                    }
                },
            )
        }
        "auto" -> PasswordDialog(
            "Contraseña de la copia automática", confirm = true,
            onOk = { pw, _ ->
                SecretStore.set(Backup.AUTO_SECRET, pw)
                autoOn = true
                Backup.autoEnabled = true
                dialog = ""
            },
            onDismiss = { dialog = "" },
        )
        "sync" -> PasswordDialog(
            "Clave de sincronización", confirm = true,
            onOk = { pw, _ ->
                Sync.setPassword(pw)
                dialog = ""
            },
            onDismiss = { dialog = "" },
        )
    }
}
