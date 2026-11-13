package com.advancedwhatsapp.app.ui

import android.content.Intent
import android.net.Uri
import android.os.PowerManager
import android.provider.Settings
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
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
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.advancedwhatsapp.app.AppState
import com.advancedwhatsapp.app.BuildConfig
import com.advancedwhatsapp.app.Prefs
import com.advancedwhatsapp.app.ai.AiService
import com.advancedwhatsapp.app.ai.AutoTranscriber
import com.advancedwhatsapp.app.ai.SmartFeatures
import com.advancedwhatsapp.app.data.Actions
import com.advancedwhatsapp.app.data.ChatLock
import com.advancedwhatsapp.app.gateway.GatewayService
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun SettingsScreen(onBack: () -> Unit, onExtras: () -> Unit = {}) {
    val c = LocalContext.current
    val scope = rememberCoroutineScope()
    val conn by AppState.connection.collectAsStateWithLifecycle()
    var confirmUnlink by remember { mutableStateOf(false) }
    var notifications by remember { mutableStateOf(Prefs.notifications) }
    var notifyGroups by remember { mutableStateOf(Prefs.notifyGroups) }
    var receipts by remember { mutableStateOf(Prefs.readReceipts) }
    var statusReceipts by remember { mutableStateOf(Prefs.statusReceipts) }
    var keepCpu by remember { mutableStateOf(Prefs.keepCpuAwake) }
    var autoTranscribe by remember { mutableStateOf(Prefs.autoTranscribe) }
    var provider by remember { mutableStateOf(Prefs.provider) }
    var lockDialog by remember { mutableStateOf("") }   // "new", "change" (pide la actual y luego la nueva), "remove"
    var biometricOn by remember { mutableStateOf(ChatLock.biometric) }

    Scaffold(topBar = {
        TopAppBar(title = { Text("Ajustes") }, navigationIcon = {
            IconButton(onClick = onBack) { Icon(Icons.AutoMirrored.Filled.ArrowBack, "Volver") }
        })
    }) { pad ->
        Column(
            Modifier.padding(pad).fillMaxSize().background(C.panel).verticalScroll(rememberScrollState()).padding(14.dp),
            verticalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            Section("WhatsApp")
            Text(conn.label, color = C.text)
            conn.phone?.let { Text("+$it", color = C.text2) }
            if (conn.isConnected) {
                OutlinedButton(onClick = { confirmUnlink = true }, modifier = Modifier.fillMaxWidth()) {
                    Text("Desvincular esta cuenta", color = C.danger)
                }
            }

            Section("Notificaciones y lectura")
            Toggle("Avisar de mensajes y llamadas", notifications) { notifications = it; Prefs.notifications = it }
            Toggle("Avisar de mensajes de grupos", notifyGroups) { notifyGroups = it; Prefs.notifyGroups = it }
            Toggle("Enviar confirmación de lectura (tildes azules)", receipts) { receipts = it; Prefs.readReceipts = it }
            Toggle("Al ver un estado, avisar al contacto que lo viste", statusReceipts) { statusReceipts = it; Prefs.statusReceipts = it }

            Section("Mensajería")
            Text(
                "Respuestas automáticas, plantillas con /atajo, destacados, envío a varios y qué hacer con los mensajes eliminados o editados.",
                color = C.text2, fontSize = 13.sp,
            )
            OutlinedButton(onClick = onExtras, modifier = Modifier.fillMaxWidth()) { Text("Mensajería extra") }

            Section("Chats bloqueados")
            Text(
                "Mantené apretado un chat de la lista para bloquearlo. Se ve apagado, sin vista previa, pide la contraseña (o la huella) para abrirse y sus notificaciones no muestran el mensaje.",
                color = C.text2, fontSize = 13.sp,
            )
            OutlinedButton(onClick = { lockDialog = if (ChatLock.hasPassword()) "change" else "new" }, modifier = Modifier.fillMaxWidth()) {
                Text(if (ChatLock.hasPassword()) "Cambiar contraseña de bloqueo" else "Crear contraseña de bloqueo")
            }
            if (ChatLock.hasPassword()) {
                if (biometricAvailable(c) || !ChatLock.biometric) {
                    Toggle("Abrir con huella", biometricOn) { biometricOn = it; ChatLock.biometric = it }
                }
                OutlinedButton(onClick = { lockDialog = "remove" }, modifier = Modifier.fillMaxWidth()) {
                    Text("Quitar contraseña y desbloquear todo", color = C.danger)
                }
            }

            Section("Segundo plano")
            Toggle("Mantener la conexión siempre activa (mensajes al instante, usa más batería)", keepCpu) {
                keepCpu = it
                Prefs.keepCpuAwake = it
                GatewayService.start(c, GatewayService.ACTION_APPLY_SETTINGS)
            }
            val pm = c.getSystemService(PowerManager::class.java)
            if (!pm.isIgnoringBatteryOptimizations(c.packageName)) {
                OutlinedButton(onClick = {
                    c.startActivity(Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS, Uri.parse("package:${c.packageName}")))
                }, modifier = Modifier.fillMaxWidth()) { Text("Permitir funcionar en segundo plano") }
            }

            Section("Inteligencia artificial")
            Selector("Proveedor preferido", provider, AiService.PROVIDERS, { provider = it; Prefs.provider = it })
            Text("El resumen de mensajes usa Claude Haiku si hay clave de Anthropic. Transcribir audios necesita clave de Groq u OpenAI.", color = C.text2, fontSize = 13.sp)
            Toggle("Transcribir audios automáticamente", autoTranscribe) {
                autoTranscribe = it
                Prefs.autoTranscribe = it
                if (it) AutoTranscriber.backlogOnce(force = true)
            }
            var quickReplies by remember { mutableStateOf(SmartFeatures.quickRepliesOn) }
            Toggle("Sugerir respuestas rápidas sobre el campo de mensaje", quickReplies) {
                quickReplies = it
                SmartFeatures.quickRepliesOn = it
            }
            var dateReminders by remember { mutableStateOf(SmartFeatures.dateRemindersOn) }
            Toggle("Detectar fechas en los mensajes (⏰ para crear un recordatorio)", dateReminders) {
                dateReminders = it
                SmartFeatures.dateRemindersOn = it
            }
            var translateLang by remember { mutableStateOf(SmartFeatures.translateLang) }
            Selector("Traducir mensajes a", translateLang, SmartFeatures.LANGUAGES, { translateLang = it; SmartFeatures.translateLang = it })
            AiService.PROVIDERS.forEach { ProviderCard(it) }

            Text("Versión ${BuildConfig.VERSION_NAME}", color = C.text3, fontSize = 12.sp)
        }
    }

    when (lockDialog) {
        "new" -> NewPasswordDialog(onDone = { lockDialog = "" }, onDismiss = { lockDialog = "" })
        "change" -> AskPasswordDialog("Contraseña actual", onOk = { lockDialog = "new" }, onDismiss = { lockDialog = "" })
        "remove" -> AskPasswordDialog("Ingresá la contraseña para quitarla", onOk = { ChatLock.removePassword(); lockDialog = "" }, onDismiss = { lockDialog = "" })
    }

    if (confirmUnlink) {
        AlertDialog(
            onDismissRequest = { confirmUnlink = false },
            title = { Text("¿Desvincular la cuenta?") },
            text = { Text("La app deja de recibir mensajes hasta que la vuelvas a vincular. Los chats guardados en el celular no se borran.") },
            confirmButton = {
                TextButton(onClick = {
                    confirmUnlink = false
                    scope.launch(Dispatchers.IO) {
                        try {
                            Actions.unlink()
                        } catch (e: Exception) {
                            withContext(Dispatchers.Main) { toast(c, e.message ?: "No se pudo desvincular.") }
                        }
                    }
                }) { Text("Desvincular", color = C.danger) }
            },
            dismissButton = { TextButton(onClick = { confirmUnlink = false }) { Text("Cancelar") } },
        )
    }
}

@Composable
private fun Section(title: String) {
    Text(title, color = C.ember, fontWeight = FontWeight.SemiBold, modifier = Modifier.padding(top = 10.dp))
}

@Composable
private fun Toggle(label: String, value: Boolean, onChange: (Boolean) -> Unit) {
    Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.fillMaxWidth()) {
        Text(label, color = C.text, fontSize = 14.sp, modifier = Modifier.weight(1f).padding(end = 10.dp))
        Switch(checked = value, onCheckedChange = onChange)
    }
}

@Composable
private fun ProviderCard(provider: String) {
    val scope = rememberCoroutineScope()
    var key by remember { mutableStateOf(Prefs.apiKey(provider)) }
    var model by remember { mutableStateOf(AiService.defaultModel(provider)) }
    var models by remember { mutableStateOf(AiService.fallbackModels(provider)) }
    var result by remember { mutableStateOf<String?>(null) }
    var busy by remember { mutableStateOf(false) }
    LaunchedEffect(key) {
        delay(800)   // no consultar por cada tecla
        if (key.isNotEmpty()) models = withContext(Dispatchers.IO) { AiService.listModels(provider) }
    }

    Column(Modifier.fillMaxWidth().background(C.surface, RoundedCornerShape(8.dp)).padding(10.dp)) {
        Text(provider, color = C.text, fontWeight = FontWeight.Medium)
        OutlinedTextField(
            value = key,
            onValueChange = {
                key = it.trim()
                Prefs.setApiKey(provider, it)
            },
            label = { Text("API key") }, singleLine = true, modifier = Modifier.fillMaxWidth(),
            visualTransformation = PasswordVisualTransformation(),
        )
        Row(verticalAlignment = Alignment.CenterVertically) {
            Selector("Modelo", model, models, {
                model = it
                Prefs.setModel(provider, it)
            })
        }
        OutlinedButton(
            enabled = key.isNotEmpty() && !busy,
            onClick = {
                busy = true
                result = null
                scope.launch {
                    val start = System.currentTimeMillis()
                    val out = withContext(Dispatchers.IO) { AiService.ask("Hola, responde únicamente con la palabra OK.", provider, model) }
                    busy = false
                    result = if (AiService.isError(out)) out else "✓ Funciona (${System.currentTimeMillis() - start} ms)"
                }
            },
        ) { Text(if (busy) "Probando…" else "Probar conexión") }
        result?.let { Text(it, fontSize = 13.sp, color = if (it.startsWith("✓")) C.accent else C.danger) }
    }
}
