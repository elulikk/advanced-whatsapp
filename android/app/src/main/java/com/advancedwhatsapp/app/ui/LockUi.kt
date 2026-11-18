package com.advancedwhatsapp.app.ui

import android.app.Activity
import android.content.Context
import androidx.biometric.BiometricManager
import androidx.biometric.BiometricPrompt
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.filled.Fingerprint
import androidx.compose.material.icons.filled.Lock
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.core.content.ContextCompat
import androidx.fragment.app.FragmentActivity
import com.advancedwhatsapp.app.data.ChatLock

/** El celular tiene huella (u otra biometría fuerte) cargada y la opción está activa. */
fun biometricAvailable(c: Context): Boolean =
    ChatLock.biometric && BiometricManager.from(c).canAuthenticate(BiometricManager.Authenticators.BIOMETRIC_STRONG) ==
        BiometricManager.BIOMETRIC_SUCCESS

/** Pide la huella; si falla o se cancela, el usuario sigue con la contraseña. */
fun promptBiometric(
    activity: FragmentActivity, title: String = "Chat bloqueado", subtitle: String = "Usá tu huella para abrirlo", onSuccess: () -> Unit,
) {
    val prompt = BiometricPrompt(
        activity, ContextCompat.getMainExecutor(activity),
        object : BiometricPrompt.AuthenticationCallback() {
            override fun onAuthenticationSucceeded(result: BiometricPrompt.AuthenticationResult) = onSuccess()
        },
    )
    prompt.authenticate(
        BiometricPrompt.PromptInfo.Builder()
            .setTitle(title)
            .setSubtitle(subtitle)
            .setNegativeButtonText("Usar contraseña")
            .setAllowedAuthenticators(BiometricManager.Authenticators.BIOMETRIC_STRONG)
            .build()
    )
}

/**
 * Envuelve la pantalla de un chat: si está bloqueado, muestra el pedido de contraseña/huella en su lugar.
 * Al salir del chat (no al girar el celular) vuelve a cerrarse.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun LockGate(jid: String, onBack: () -> Unit, content: @Composable () -> Unit) {
    val c = LocalContext.current
    DisposableEffect(jid) {
        onDispose { if ((c as? Activity)?.isChangingConfigurations != true) ChatLock.relock(jid) }
    }
    if (ChatLock.isOpen(jid)) {
        content()
        return
    }

    var pw by remember { mutableStateOf("") }
    var wrong by remember { mutableStateOf(false) }
    val bio = remember { biometricAvailable(c) }
    val tryBio: () -> Unit = {
        (c as? FragmentActivity)?.let { a -> promptBiometric(a) { ChatLock.markOpen(jid) } }
    }
    LaunchedEffect(jid) { if (bio) tryBio() }
    val submit: () -> Unit = {
        if (ChatLock.verify(pw)) ChatLock.markOpen(jid) else { wrong = true; pw = "" }
    }

    Scaffold(topBar = {
        TopAppBar(title = { Text("Chat bloqueado") }, navigationIcon = {
            IconButton(onClick = onBack) { Icon(Icons.AutoMirrored.Filled.ArrowBack, "Volver") }
        })
    }) { pad ->
        Column(
            Modifier.padding(pad).fillMaxSize().background(C.panel).padding(24.dp),
            horizontalAlignment = Alignment.CenterHorizontally, verticalArrangement = Arrangement.Center,
        ) {
            Icon(Icons.Default.Lock, null, tint = C.text3, modifier = Modifier.size(56.dp))
            Text("Ingresá la contraseña para abrir este chat", color = C.text2, fontSize = 14.sp, modifier = Modifier.padding(top = 12.dp, bottom = 16.dp))
            OutlinedTextField(
                value = pw, onValueChange = { pw = it; wrong = false }, singleLine = true, label = { Text("Contraseña") },
                isError = wrong, supportingText = { if (wrong) Text("Contraseña incorrecta") },
                visualTransformation = PasswordVisualTransformation(),
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Password),
                modifier = Modifier.fillMaxWidth(),
            )
            Button(onClick = submit, enabled = pw.isNotEmpty(), modifier = Modifier.fillMaxWidth().padding(top = 8.dp)) { Text("Desbloquear") }
            if (bio) {
                OutlinedButton(onClick = tryBio, modifier = Modifier.fillMaxWidth().padding(top = 8.dp)) {
                    Icon(Icons.Default.Fingerprint, null, modifier = Modifier.size(20.dp).padding(end = 6.dp))
                    Text("Usar huella")
                }
            }
        }
    }
}

/** Crear una contraseña nueva (se escribe dos veces). */
@Composable
fun NewPasswordDialog(title: String = "Contraseña de bloqueo", onDone: () -> Unit, onDismiss: () -> Unit) {
    var a by remember { mutableStateOf("") }
    var b by remember { mutableStateOf("") }
    val error = when {
        a.isNotEmpty() && a.trim().length < ChatLock.MIN_PASSWORD_LEN -> "Mínimo ${ChatLock.MIN_PASSWORD_LEN} caracteres"
        b.isNotEmpty() && a != b -> "Las contraseñas no coinciden"
        else -> null
    }
    AlertDialog(
        onDismissRequest = onDismiss, title = { Text(title) },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                OutlinedTextField(a, { a = it }, singleLine = true, label = { Text("Contraseña nueva") }, visualTransformation = PasswordVisualTransformation())
                OutlinedTextField(
                    b, { b = it }, singleLine = true, label = { Text("Repetila") }, visualTransformation = PasswordVisualTransformation(),
                    isError = error != null, supportingText = { error?.let { Text(it) } },
                )
            }
        },
        confirmButton = {
            TextButton(enabled = error == null && a.isNotEmpty() && a == b, onClick = {
                if (ChatLock.setPassword(a)) onDone()
            }) { Text("Guardar") }
        },
        dismissButton = { TextButton(onClick = onDismiss) { Text("Cancelar") } },
    )
}

/** Pide la contraseña actual (para sacar el bloqueo a un chat, cambiar o quitar la contraseña). */
@Composable
fun AskPasswordDialog(title: String, onOk: () -> Unit, onDismiss: () -> Unit) {
    var pw by remember { mutableStateOf("") }
    var wrong by remember { mutableStateOf(false) }
    AlertDialog(
        onDismissRequest = onDismiss, title = { Text(title) },
        text = {
            OutlinedTextField(
                pw, { pw = it; wrong = false }, singleLine = true, label = { Text("Contraseña") },
                visualTransformation = PasswordVisualTransformation(), isError = wrong,
                supportingText = { if (wrong) Text("Contraseña incorrecta") },
            )
        },
        confirmButton = {
            TextButton(enabled = pw.isNotEmpty(), onClick = { if (ChatLock.verify(pw)) onOk() else { wrong = true; pw = "" } }) { Text("Aceptar") }
        },
        dismissButton = { TextButton(onClick = onDismiss) { Text("Cancelar") } },
    )
}

/**
 * Bloqueo de la app entera: se dibuja encima de todo (la navegación y los borradores quedan debajo, intactos)
 * hasta ingresar la contraseña de los chats bloqueados o usar la huella.
 */
@Composable
fun AppLockOverlay() {
    if (!ChatLock.appLocked()) return
    val c = LocalContext.current
    var pw by remember { mutableStateOf("") }
    var wrong by remember { mutableStateOf(false) }
    val bio = remember { biometricAvailable(c) }
    val tryBio: () -> Unit = {
        (c as? FragmentActivity)?.let { a ->
            promptBiometric(a, "Advanced WhatsApp", "Usá tu huella para entrar") { ChatLock.unlockApp() }
        }
    }
    LaunchedEffect(Unit) { if (bio) tryBio() }
    val submit: () -> Unit = { if (ChatLock.verify(pw)) ChatLock.unlockApp() else { wrong = true; pw = "" } }

    Box(
        Modifier.fillMaxSize().background(C.panel)
            .clickable(interactionSource = remember { MutableInteractionSource() }, indication = null) {},   // tapa la app: nada de abajo recibe toques
        contentAlignment = Alignment.Center,
    ) {
        Column(
            Modifier.fillMaxWidth().padding(24.dp),
            horizontalAlignment = Alignment.CenterHorizontally, verticalArrangement = Arrangement.Center,
        ) {
            Icon(Icons.Default.Lock, null, tint = C.text3, modifier = Modifier.size(56.dp))
            Text("Advanced WhatsApp está bloqueado", color = C.text, fontSize = 17.sp, modifier = Modifier.padding(top = 12.dp))
            Text("Ingresá la contraseña para continuar", color = C.text2, fontSize = 14.sp, modifier = Modifier.padding(top = 4.dp, bottom = 16.dp))
            OutlinedTextField(
                value = pw, onValueChange = { pw = it; wrong = false }, singleLine = true, label = { Text("Contraseña") },
                isError = wrong, supportingText = { if (wrong) Text("Contraseña incorrecta") },
                visualTransformation = PasswordVisualTransformation(),
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Password),
                modifier = Modifier.fillMaxWidth(),
            )
            Button(onClick = submit, enabled = pw.isNotEmpty(), modifier = Modifier.fillMaxWidth().padding(top = 8.dp)) { Text("Desbloquear") }
            if (bio) {
                OutlinedButton(onClick = tryBio, modifier = Modifier.fillMaxWidth().padding(top = 8.dp)) {
                    Icon(Icons.Default.Fingerprint, null, modifier = Modifier.size(20.dp).padding(end = 6.dp))
                    Text("Usar huella")
                }
            }
        }
    }
}

/** Crear el código secreto que muestra los chats ocultos (se escribe dos veces). */
@Composable
fun NewSecretDialog(onDone: () -> Unit, onDismiss: () -> Unit) {
    var a by remember { mutableStateOf("") }
    var b by remember { mutableStateOf("") }
    val error = when {
        a.isNotEmpty() && a.trim().length < ChatLock.MIN_SECRET_LEN -> "Mínimo ${ChatLock.MIN_SECRET_LEN} caracteres"
        b.isNotEmpty() && a.trim() != b.trim() -> "Los códigos no coinciden"
        else -> null
    }
    AlertDialog(
        onDismissRequest = onDismiss, title = { Text("Código secreto de chats ocultos") },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Text("Lo escribís en el buscador de la lista de chats y aparecen los chats ocultos.", color = C.text2, fontSize = 13.sp)
                OutlinedTextField(a, { a = it }, singleLine = true, label = { Text("Código nuevo") }, visualTransformation = PasswordVisualTransformation())
                OutlinedTextField(
                    b, { b = it }, singleLine = true, label = { Text("Repetilo") }, visualTransformation = PasswordVisualTransformation(),
                    isError = error != null, supportingText = { error?.let { Text(it) } },
                )
            }
        },
        confirmButton = {
            TextButton(enabled = error == null && a.isNotEmpty() && a.trim() == b.trim(), onClick = {
                if (ChatLock.setSecretCode(a)) onDone()
            }) { Text("Guardar") }
        },
        dismissButton = { TextButton(onClick = onDismiss) { Text("Cancelar") } },
    )
}
