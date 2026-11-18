package com.advancedwhatsapp.app.ui

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.window.Dialog
import androidx.compose.ui.window.DialogProperties
import com.advancedwhatsapp.app.data.Stats
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

private val RECEIVED = Color(0xFF6B5F58)

/** Estadísticas de tus chats (pantalla completa, sin cambiar de ruta): con quién hablás más, por día y hora, y respuestas. */
@Composable
fun StatsDialog(onDismiss: () -> Unit) {
    Dialog(onDismissRequest = onDismiss, properties = DialogProperties(usePlatformDefaultWidth = false)) {
        Surface(Modifier.fillMaxSize(), color = C.panel) {
            StatsContent(onDismiss)
        }
    }
}

@Composable
private fun StatsContent(onBack: () -> Unit) {
    var period by remember { mutableStateOf(Stats.PERIODS[1]) }
    var report by remember { mutableStateOf<Stats.Report?>(null) }
    var error by remember { mutableStateOf<String?>(null) }
    LaunchedEffect(period) {
        report = null
        error = null
        runCatching { withContext(Dispatchers.IO) { Stats.report(period) } }
            .onSuccess { report = it }
            .onFailure { error = it.message ?: "No se pudieron calcular las estadísticas." }
    }

    Column(Modifier.fillMaxSize()) {
        Row(Modifier.fillMaxWidth().background(C.surface).padding(4.dp), verticalAlignment = Alignment.CenterVertically) {
            IconButton(onClick = onBack) { Icon(Icons.AutoMirrored.Filled.ArrowBack, "Volver") }
            Text("Estadísticas", color = C.text, fontSize = 18.sp, fontWeight = FontWeight.Medium)
        }
        Row(Modifier.fillMaxWidth().padding(horizontal = 12.dp, vertical = 8.dp), horizontalArrangement = Arrangement.spacedBy(6.dp)) {
            Stats.PERIODS.forEach { p ->
                val on = p.key == period.key
                Text(
                    p.label, color = if (on) C.onAccent else C.text2, fontSize = 13.sp,
                    modifier = Modifier.background(if (on) C.accent else C.raised, RoundedCornerShape(14.dp))
                        .clickable { period = p }.padding(horizontal = 12.dp, vertical = 6.dp),
                )
            }
        }
        val r = report
        when {
            error != null -> Text(error.orEmpty(), color = C.danger, modifier = Modifier.padding(16.dp))
            r == null -> Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) { CircularProgressIndicator() }
            else -> Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(horizontal = 14.dp, vertical = 6.dp)) {
                Text(
                    "${r.total} mensajes en ${r.chats} chats · ${r.sent} enviados · ${r.received} recibidos",
                    color = C.text2, fontSize = 13.sp,
                )
                Title("Tiempo de respuesta promedio")
                Text(
                    "Vos contestás en ${Stats.formatDuration(r.response.mineAvg)} (${r.response.mineCount} respuestas); " +
                        "los demás en ${Stats.formatDuration(r.response.theirsAvg)} (${r.response.theirsCount}).",
                    color = C.text, fontSize = 14.sp,
                )
                Text("Solo chats individuales; las pausas de más de 24 h no cuentan.", color = C.text3, fontSize = 11.sp)
                Title("Con quién hablás más")
                if (r.topChats.isEmpty()) Empty() else {
                    val top = r.topChats.first().total.coerceAtLeast(1)
                    r.topChats.forEach { c ->
                        Row(Modifier.fillMaxWidth().padding(vertical = 3.dp), verticalAlignment = Alignment.CenterVertically) {
                            Text(c.name, color = C.text, fontSize = 13.sp, maxLines = 1, overflow = TextOverflow.Ellipsis, modifier = Modifier.weight(0.42f))
                            Row(Modifier.weight(0.46f).height(10.dp)) {
                                if (c.sent > 0) Box(Modifier.weight(c.sent.toFloat() / top).fillMaxHeight().background(C.accent, RoundedCornerShape(3.dp)))
                                if (c.received > 0) Box(Modifier.weight(c.received.toFloat() / top).fillMaxHeight().background(RECEIVED, RoundedCornerShape(3.dp)))
                                val rest = 1f - c.total.toFloat() / top
                                if (rest > 0.001f) Box(Modifier.weight(rest))
                            }
                            Text("${c.total}", color = C.text2, fontSize = 12.sp, modifier = Modifier.weight(0.12f).padding(start = 6.dp))
                        }
                    }
                    Text("Verde: enviados · gris: recibidos", color = C.text3, fontSize = 11.sp)
                }
                Title("Mensajes por día")
                val days = if (r.byDay.size > 60) groupByMonth(r.byDay) else r.byDay.map { Stats.Bucket(it.label.substring(5), it.sent, it.received) }
                BarChart(days, labelEvery = (days.size / 8).coerceAtLeast(1))
                Title("Mensajes por hora del día")
                BarChart(r.byHour, labelEvery = 3)
                Box(Modifier.height(24.dp))
            }
        }
    }
}

private fun groupByMonth(days: List<Stats.Bucket>): List<Stats.Bucket> {
    val months = LinkedHashMap<String, IntArray>()
    for (d in days) {
        val a = months.getOrPut(d.label.substring(0, 7)) { IntArray(2) }
        a[0] += d.sent
        a[1] += d.received
    }
    return months.map { (k, v) -> Stats.Bucket(k.substring(2), v[0], v[1]) }
}

@Composable
private fun Title(text: String) {
    Text(text, color = C.ember, fontWeight = FontWeight.SemiBold, fontSize = 14.sp, modifier = Modifier.padding(top = 14.dp, bottom = 4.dp))
}

@Composable
private fun Empty() = Text("Sin mensajes en este período", color = C.text3, fontSize = 13.sp)

/** Barras apiladas (enviados abajo en verde, recibidos arriba en gris) con etiquetas cada `labelEvery` barras. */
@Composable
private fun BarChart(items: List<Stats.Bucket>, labelEvery: Int) {
    val max = items.maxOfOrNull { it.total } ?: 0
    if (max == 0) { Empty(); return }
    Column(Modifier.fillMaxWidth()) {
        Canvas(Modifier.fillMaxWidth().height(110.dp)) {
            val slot = size.width / items.size
            val bar = (slot * 0.7f).coerceAtLeast(1.5f)
            items.forEachIndexed { i, b ->
                val x = i * slot + (slot - bar) / 2
                val hRecv = size.height * b.received / max
                val hSent = size.height * b.sent / max
                drawRect(RECEIVED, Offset(x, size.height - hRecv), Size(bar, hRecv))
                drawRect(C.accent, Offset(x, size.height - hRecv - hSent), Size(bar, hSent))
            }
        }
        Row(Modifier.fillMaxWidth()) {
            items.forEachIndexed { i, b ->
                Box(Modifier.weight(1f), contentAlignment = Alignment.TopCenter) {
                    if (i % labelEvery == 0) Text(b.label, color = C.text3, fontSize = 9.sp, maxLines = 1, softWrap = false)
                }
            }
        }
    }
}
