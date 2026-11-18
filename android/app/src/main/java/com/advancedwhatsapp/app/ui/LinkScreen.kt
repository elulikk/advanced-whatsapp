package com.advancedwhatsapp.app.ui

import android.graphics.BitmapFactory
import android.util.Base64
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Tab
import androidx.compose.material3.TabRow
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.platform.LocalClipboardManager
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.advancedwhatsapp.app.AppState
import com.advancedwhatsapp.app.data.Actions
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun LinkScreen(onBack: () -> Unit) {
    val conn by AppState.connection.collectAsStateWithLifecycle()
    var tab by remember { mutableIntStateOf(0) }

    Scaffold(topBar = {
        TopAppBar(
            title = { Text("Vincular cuenta") },
            navigationIcon = { IconButton(onClick = onBack) { Icon(Icons.AutoMirrored.Filled.ArrowBack, "Volver") } },
        )
    }) { pad ->
        Column(Modifier.padding(pad).fillMaxSize().verticalScroll(rememberScrollState())) {
            TabRow(selectedTabIndex = tab) {
                Tab(selected = tab == 0, onClick = { tab = 0 }, text = { Text("Con código") })
                Tab(selected = tab == 1, onClick = { tab = 1 }, text = { Text("Con QR") })
            }
            if (tab == 0) CodeTab() else QrTab(conn.qrDataUrl)
        }
    }
}

@Composable
private fun CodeTab() {
    var phone by remember { mutableStateOf("") }
    var code by remember { mutableStateOf<String?>(null) }
    var error by remember { mutableStateOf<String?>(null) }
    var busy by remember { mutableStateOf(false) }
    val scope = rememberCoroutineScope()
    val clipboard = LocalClipboardManager.current
    val cuts by AppState.pairingCuts.collectAsStateWithLifecycle()

    LaunchedEffect(cuts) {
        if (code != null) {
            code = null
            error = "El código venció sin usarse. Pedí otro."
        }
    }

    Column(Modifier.padding(20.dp), verticalArrangement = Arrangement.spacedBy(14.dp)) {
        Text(
            "Usá esta opción si tu WhatsApp está en este mismo celular. Poné el número de la cuenta que vas a vincular: " +
                "si tenés WhatsApp y WhatsApp Business, el de la app donde vas a escribir el código.",
            color = C.text2,
        )
        OutlinedTextField(
            value = phone, onValueChange = { phone = it.filter { c -> c.isDigit() || c == '+' || c == ' ' } },
            label = { Text("Tu número con código de país") }, placeholder = { Text("5491112345678") },
            singleLine = true, modifier = Modifier.fillMaxWidth(),
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Phone),
        )
        Button(
            enabled = !busy && phone.count(Char::isDigit) >= 8, modifier = Modifier.fillMaxWidth(),
            onClick = {
                busy = true
                error = null
                code = null
                scope.launch {
                    try {
                        code = withContext(Dispatchers.IO) { Actions.requestPairingCode(phone) }
                    } catch (e: Exception) {
                        error = e.message
                    }
                    busy = false
                }
            },
        ) { Text(if (busy) "Pidiendo código…" else "Obtener código") }
        if (busy) CircularProgressIndicator(Modifier.align(Alignment.CenterHorizontally))
        error?.let { Text(it, color = C.danger) }
        code?.let { c ->
            val shown = if (c.length == 8) c.substring(0, 4) + "-" + c.substring(4) else c
            Text(
                shown, fontFamily = FontFamily.Monospace, fontSize = 36.sp, fontWeight = FontWeight.Bold, color = C.ember,
                modifier = Modifier.align(Alignment.CenterHorizontally),
            )
            OutlinedButton(onClick = { clipboard.setText(AnnotatedString(c)) }, modifier = Modifier.fillMaxWidth()) { Text("Copiar código") }
            Text(
                "1. Abrí WhatsApp y andá a Dispositivos vinculados.\n" +
                    "2. Tocá Vincular un dispositivo.\n" +
                    "3. Elegí «Vincular con el número de teléfono».\n" +
                    "4. Escribí este código. Pedí uno solo: cada código nuevo anula el anterior.",
                color = C.text,
            )
        }
    }
}

@Composable
private fun QrTab(dataUrl: String?) {
    Column(Modifier.padding(20.dp).fillMaxWidth(), horizontalAlignment = Alignment.CenterHorizontally, verticalArrangement = Arrangement.spacedBy(14.dp)) {
        Text("Usá esta opción si tu WhatsApp está en otro celular: escaneá el código con ese celular.", color = C.text2)
        val bitmap = remember(dataUrl) {
            dataUrl?.substringAfter("base64,", "")?.takeIf { it.isNotEmpty() }?.let { b64 ->
                runCatching {
                    val bytes = Base64.decode(b64, Base64.DEFAULT)
                    BitmapFactory.decodeByteArray(bytes, 0, bytes.size)?.asImageBitmap()
                }.getOrNull()
            }
        }
        if (bitmap != null) {
            Image(
                bitmap, "Código QR",
                Modifier.size(280.dp).clip(RoundedCornerShape(12.dp)).background(Color.White).padding(6.dp),
            )
            Text("Dispositivos vinculados › Vincular un dispositivo", color = C.text)
        } else {
            CircularProgressIndicator()
            Text("Generando el código…", color = C.text2)
        }
    }
}
