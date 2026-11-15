package com.advancedwhatsapp.app.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
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
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.advancedwhatsapp.app.BuildConfig
import com.advancedwhatsapp.app.Prefs
import com.advancedwhatsapp.app.data.UpdateException
import com.advancedwhatsapp.app.data.UpdateInfo
import com.advancedwhatsapp.app.data.Updater
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

/** Ajustes > Actualizaciones: casilla de búsqueda automática, botón de buscar, novedades y descarga con progreso. */
@Composable
fun UpdateSettings() {
    val scope = rememberCoroutineScope()
    val context = LocalContext.current
    var auto by remember { mutableStateOf(Prefs.autoUpdate) }
    var info by remember { mutableStateOf<UpdateInfo?>(Updater.available) }
    var status by remember { mutableStateOf("") }
    var busy by remember { mutableStateOf(false) }
    var progress by remember { mutableStateOf<Float?>(null) }

    Text("Actualizaciones", color = C.ember, fontWeight = FontWeight.SemiBold, modifier = Modifier.padding(top = 10.dp))
    Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.fillMaxWidth()) {
        Text("Buscar actualizaciones automáticamente", color = C.text, fontSize = 14.sp, modifier = Modifier.weight(1f).padding(end = 10.dp))
        Switch(checked = auto, onCheckedChange = { auto = it; Prefs.autoUpdate = it })
    }
    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        OutlinedButton(enabled = !busy, onClick = {
            busy = true
            status = "Buscando…"
            scope.launch {
                try {
                    val found = withContext(Dispatchers.IO) { Updater.check() }
                    Updater.available = found
                    info = found
                    status = if (found == null) "Tenés la última versión (${BuildConfig.VERSION_NAME})." else "Hay una versión nueva: ${found.version}"
                } catch (e: UpdateException) {
                    status = e.message.orEmpty()
                }
                busy = false
            }
        }) { Text("Buscar actualizaciones") }
        val found = info
        if (found != null) {
            OutlinedButton(enabled = !busy, onClick = {
                if (!Updater.canInstall()) {
                    toast(context, "Permití instalar desde Advanced WhatsApp y volvé a tocar «Actualizar».")
                    Updater.askInstallPermission()
                    return@OutlinedButton
                }
                busy = true
                progress = 0f
                status = "Bajando ${found.version}…"
                scope.launch {
                    try {
                        val apk = withContext(Dispatchers.IO) {
                            Updater.download(found) { done, total -> if (total > 0) progress = done.toFloat() / total }
                        }
                        status = "Listo: confirmá la instalación en la pantalla del sistema."
                        Updater.install(apk)
                    } catch (e: UpdateException) {
                        status = e.message.orEmpty()
                    } catch (e: Exception) {
                        status = "No se pudo instalar: ${e.message}"
                    }
                    progress = null
                    busy = false
                }
            }) { Text("Actualizar a ${found.version}") }
        }
    }
    progress?.let { LinearProgressIndicator(progress = { it }, modifier = Modifier.fillMaxWidth()) }
    if (status.isNotEmpty()) Text(status, color = C.text2, fontSize = 13.sp)
    info?.let {
        Column(Modifier.fillMaxWidth().heightIn(max = 180.dp).verticalScroll(rememberScrollState())) {
            Text("Novedades de la versión ${it.version}", color = C.text, fontSize = 13.sp, fontWeight = FontWeight.SemiBold)
            Text(it.notes.ifEmpty { "Sin notas." }, color = C.text2, fontSize = 13.sp)
        }
    }
    Text("Las versiones salen de GitHub Releases (${Updater.repo}).", color = C.text3, fontSize = 12.sp)
}
