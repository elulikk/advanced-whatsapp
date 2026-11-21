package com.advancedwhatsapp.app.ui

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.ContentCopy
import androidx.compose.material.icons.filled.DocumentScanner
import androidx.compose.material.icons.filled.ExpandLess
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material.icons.filled.ExpandMore
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalClipboardManager
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.advancedwhatsapp.app.ai.ImageReader
import com.advancedwhatsapp.app.data.Message
import java.io.File

/**
 * Bloque «Descripción / Texto» de la burbuja de una foto (lo que leyó la IA), plegable como la transcripción de los
 * audios, con botón para generarlo y para copiar cada parte. Solo se ofrece si la foto ya está en el teléfono.
 */
@Composable
fun ImageReadBlock(m: Message) {
    val working by ImageReader.working.collectAsStateWithLifecycle()
    val errors by ImageReader.errors.collectAsStateWithLifecycle()
    val desc = m.imageDesc?.trim().orEmpty()
    val ocr = m.imageOcr?.trim().orEmpty()
    val hasFile = m.mediaPath?.let { File(it).exists() } == true
    var open by remember(m.id) { mutableStateOf(false) }
    val clipboard = LocalClipboardManager.current

    when {
        m.id in working -> Row(Modifier.padding(vertical = 3.dp), verticalAlignment = Alignment.CenterVertically) {
            CircularProgressIndicator(Modifier.size(14.dp), strokeWidth = 2.dp)
            Text("  Leyendo imagen…", color = C.text2, fontSize = 12.sp)
        }
        desc.isNotEmpty() || ocr.isNotEmpty() -> Column(Modifier.fillMaxWidth().padding(bottom = 3.dp)) {
            Row(Modifier.fillMaxWidth().clickable { open = !open }, verticalAlignment = Alignment.CenterVertically) {
                Text("Descripción / Texto", color = C.text3, fontSize = 12.sp, fontWeight = FontWeight.Medium, modifier = Modifier.weight(1f))
                Icon(
                    if (open) Icons.Default.ExpandLess else Icons.Default.ExpandMore, if (open) "Plegar" else "Desplegar",
                    tint = C.text3, modifier = Modifier.size(18.dp),
                )
            }
            if (!open) {
                Text(desc.ifEmpty { ocr }, color = C.text2, fontSize = 13.sp, maxLines = 2, overflow = androidx.compose.ui.text.style.TextOverflow.Ellipsis)
            } else {
                for ((title, text) in listOf("Descripción" to desc, "Texto" to ocr)) {
                    if (text.isEmpty()) continue
                    Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        Text(title.uppercase(), color = C.accent, fontSize = 11.sp, fontWeight = FontWeight.Medium)
                        BubbleButton("Copiar", Icons.Default.ContentCopy) { clipboard.setText(AnnotatedString(text)) }
                    }
                    SelectionContainer { Text(text, color = C.text, fontSize = 14.sp, modifier = Modifier.padding(bottom = 4.dp)) }
                }
                if (hasFile) {
                    BubbleButton("Rehacer", Icons.Default.Refresh, modifier = Modifier.padding(bottom = 4.dp)) { ImageReader.readNow(m.id) }
                }
            }
        }
        !hasFile -> Unit   // nunca se baja una foto solo para leerla
        else -> {
            errors[m.id]?.let { Text("No se pudo leer la imagen: $it", color = C.text2, fontSize = 12.sp) }
            BubbleButton(
                if (errors[m.id] != null) "Reintentar" else "Leer imagen", Icons.Default.DocumentScanner,
                modifier = Modifier.padding(top = 4.dp, bottom = 4.dp),
            ) { ImageReader.readNow(m.id) }
        }
    }
}
