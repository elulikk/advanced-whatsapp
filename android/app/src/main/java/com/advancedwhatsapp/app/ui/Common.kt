package com.advancedwhatsapp.app.ui

import android.content.Context
import android.content.Intent
import android.webkit.MimeTypeMap
import android.widget.Toast
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.core.content.FileProvider
import coil.compose.AsyncImage
import java.io.File
import java.text.SimpleDateFormat
import java.util.Calendar
import java.util.Date
import java.util.Locale

/** Paleta de la app de escritorio (app/ui/theme.py): negro con brasas y el verde de WhatsApp. */
object C {
    val rail = Color(0xFF120F0D)
    val panel = Color(0xFF181412)
    val surface = Color(0xFF201B19)
    val raised = Color(0xFF29221F)
    val border = Color(0xFF372D28)
    val chatBg = Color(0xFF0F0C0B)
    val bubbleIn = Color(0xFF28211E)
    val bubbleOut = Color(0xFF0C4437)
    val text = Color(0xFFE6E0DC)
    val text2 = Color(0xFF9A8E87)
    val text3 = Color(0xFF77695F)
    val accent = Color(0xFF1FA37F)
    val accentDim = Color(0xFF16332B)
    val onAccent = Color(0xFF0B1114)
    val ember = Color(0xFFFF7A18)
    val emberDim = Color(0xFF3A1F10)
    val link = Color(0xFF53BDEB)
    val danger = Color(0xFFE5534B)
    val warn = Color(0xFFD9A33A)
}

private val colors = darkColorScheme(
    primary = C.accent, onPrimary = C.onAccent, primaryContainer = C.accentDim, onPrimaryContainer = C.text,
    secondary = C.ember, onSecondary = C.onAccent, secondaryContainer = C.emberDim, onSecondaryContainer = C.text,
    background = C.panel, onBackground = C.text, surface = C.surface, onSurface = C.text,
    surfaceVariant = C.raised, onSurfaceVariant = C.text2, outline = C.border, outlineVariant = C.border,
    error = C.danger, surfaceContainer = C.surface, surfaceContainerHigh = C.raised, surfaceContainerHighest = C.raised,
    surfaceContainerLow = C.panel,
)

@Composable
fun AppTheme(content: @Composable () -> Unit) = MaterialTheme(colorScheme = colors, content = content)

@Composable
fun Avatar(name: String, path: String?, size: Dp = 48.dp) {
    val file = path?.let { File(it) }?.takeIf { it.exists() }
    if (file != null) {
        AsyncImage(
            model = file, contentDescription = null, contentScale = ContentScale.Crop,
            modifier = Modifier.size(size).clip(CircleShape),
        )
    } else {
        Box(Modifier.size(size).clip(CircleShape).background(C.raised), contentAlignment = Alignment.Center) {
            val initial = name.firstOrNull { it.isLetterOrDigit() }?.uppercase() ?: "?"
            Text(initial, color = C.text2, fontWeight = FontWeight.Medium, fontSize = (size.value * 0.4f).sp)
        }
    }
}

fun timeOf(ts: Long): String = SimpleDateFormat("HH:mm", Locale.getDefault()).format(Date(ts * 1000))

private fun sameDay(a: Calendar, b: Calendar) =
    a.get(Calendar.YEAR) == b.get(Calendar.YEAR) && a.get(Calendar.DAY_OF_YEAR) == b.get(Calendar.DAY_OF_YEAR)

private fun isYesterday(c: Calendar) = sameDay(c, Calendar.getInstance().apply { add(Calendar.DAY_OF_YEAR, -1) })

/** "11:30", "Ayer" o "05/11/26" para la lista de chats. */
fun chatTime(ts: Long): String {
    if (ts <= 0) return ""
    val c = Calendar.getInstance().apply { timeInMillis = ts * 1000 }
    return when {
        sameDay(c, Calendar.getInstance()) -> timeOf(ts)
        isYesterday(c) -> "Ayer"
        else -> SimpleDateFormat("dd/MM/yy", Locale.getDefault()).format(Date(ts * 1000))
    }
}

/** Separador de fecha dentro de una conversación. */
fun dayLabel(ts: Long): String {
    val c = Calendar.getInstance().apply { timeInMillis = ts * 1000 }
    return when {
        sameDay(c, Calendar.getInstance()) -> "Hoy"
        isYesterday(c) -> "Ayer"
        else -> SimpleDateFormat("d 'de' MMMM 'de' yyyy", Locale("es")).format(Date(ts * 1000))
    }
}

fun dayKey(ts: Long): String = SimpleDateFormat("yyyyMMdd", Locale.US).format(Date(ts * 1000))

fun toast(c: Context, text: String) = Toast.makeText(c, text, Toast.LENGTH_LONG).show()

/** Abre un archivo recibido o enviado con la app que corresponda. */
fun openFile(c: Context, path: String) {
    val file = File(path)
    val uri = FileProvider.getUriForFile(c, "${c.packageName}.files", file)
    val mime = MimeTypeMap.getSingleton().getMimeTypeFromExtension(file.extension.lowercase()) ?: "*/*"
    val i = Intent(Intent.ACTION_VIEW).setDataAndType(uri, mime).addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
    runCatching { c.startActivity(i) }.onFailure { toast(c, "No hay ninguna app para abrir este archivo.") }
}

/** Selector compacto: un botón con el valor actual que despliega las opciones. */
@Composable
fun Selector(label: String, value: String, options: List<String>, onSelect: (String) -> Unit, display: (String) -> String = { it }) {
    var open by remember { mutableStateOf(false) }
    Column {
        TextButton(onClick = { open = true }) {
            Text("$label: ${display(value)}", color = C.text, fontSize = 13.sp, maxLines = 1)
        }
        DropdownMenu(expanded = open, onDismissRequest = { open = false }) {
            options.forEach { o ->
                DropdownMenuItem(text = { Text(display(o)) }, onClick = {
                    open = false
                    onSelect(o)
                })
            }
        }
    }
}

@Composable
fun Field(value: String, onChange: (String) -> Unit, label: String, modifier: Modifier = Modifier.fillMaxWidth(), singleLine: Boolean = false, minLines: Int = 1) {
    OutlinedTextField(value = value, onValueChange = onChange, label = { Text(label) }, modifier = modifier, singleLine = singleLine, minLines = minLines)
}
