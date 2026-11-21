package com.advancedwhatsapp.app.ui

import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.AttachFile
import androidx.compose.material.icons.outlined.Brush
import androidx.compose.material.icons.outlined.Cake
import androidx.compose.material.icons.outlined.Call
import androidx.compose.material.icons.outlined.CameraAlt
import androidx.compose.material.icons.outlined.ChatBubbleOutline
import androidx.compose.material.icons.outlined.Cloud
import androidx.compose.material.icons.outlined.EmojiEmotions
import androidx.compose.material.icons.outlined.FavoriteBorder
import androidx.compose.material.icons.outlined.Headphones
import androidx.compose.material.icons.outlined.Lightbulb
import androidx.compose.material.icons.outlined.LocalCafe
import androidx.compose.material.icons.outlined.LocalFireDepartment
import androidx.compose.material.icons.outlined.Mic
import androidx.compose.material.icons.outlined.MusicNote
import androidx.compose.material.icons.outlined.Pets
import androidx.compose.material.icons.outlined.Photo
import androidx.compose.material.icons.outlined.Schedule
import androidx.compose.material.icons.outlined.StarBorder
import androidx.compose.material.icons.outlined.WbSunny
import androidx.compose.runtime.Composable
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.drawBehind
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.ColorFilter
import androidx.compose.ui.graphics.drawscope.rotate
import androidx.compose.ui.graphics.drawscope.translate
import androidx.compose.ui.graphics.vector.rememberVectorPainter
import androidx.compose.ui.unit.dp

/** Dibujitos del fondo: íconos de línea de cosas de chat, repartidos en grilla con un poco de desorden. */
private val DOODLES = listOf(
    Icons.Outlined.ChatBubbleOutline, Icons.Outlined.FavoriteBorder, Icons.Outlined.MusicNote, Icons.Outlined.CameraAlt,
    Icons.Outlined.LocalFireDepartment, Icons.Outlined.StarBorder, Icons.Outlined.Mic, Icons.Outlined.LocalCafe,
    Icons.Outlined.EmojiEmotions, Icons.Outlined.Call, Icons.Outlined.Cloud, Icons.Outlined.Headphones,
    Icons.Outlined.Pets, Icons.Outlined.Schedule, Icons.Outlined.Lightbulb, Icons.Outlined.Photo,
    Icons.Outlined.WbSunny, Icons.Outlined.Cake, Icons.Outlined.AttachFile, Icons.Outlined.Brush,
)

/** Número pseudoaleatorio fijo por celda (0..1): el dibujo es siempre el mismo, no «baila» al redibujar. */
private fun cellRand(col: Int, row: Int, salt: Int): Float {
    var h = col * 73856093 xor row * 19349663 xor salt * 83492791
    h = (h xor (h ushr 13)) * 1274126177
    return ((h xor (h ushr 16)) and 0xFFFF) / 65535f
}

/**
 * Textura de fondo de los chats, al estilo de los garabatos de WhatsApp pero con dibujos propios:
 * muy tenue, para que las burbujas sigan siendo lo que se ve.
 */
@Composable
fun Modifier.chatPattern(): Modifier {
    val painters = DOODLES.map { rememberVectorPainter(it) }
    val tint = remember { ColorFilter.tint(C.text.copy(alpha = 0.07f)) }
    return this.drawBehind {
        val cell = 64.dp.toPx()
        val icon = 22.dp.toPx()
        val cols = (size.width / cell).toInt() + 2
        val rows = (size.height / cell).toInt() + 2
        for (r in 0 until rows) {
            val shift = if (r % 2 == 1) cell / 2 else 0f   // filas corridas: no se ve como una grilla
            for (c in -1 until cols) {
                val pick = (cellRand(c, r, 1) * painters.size).toInt().coerceIn(0, painters.size - 1)
                val scale = 0.85f + cellRand(c, r, 2) * 0.35f
                val s = icon * scale
                val x = c * cell + shift + (cell - s) * (0.2f + cellRand(c, r, 3) * 0.6f)
                val y = r * cell + (cell - s) * (0.2f + cellRand(c, r, 4) * 0.6f)
                val angle = (cellRand(c, r, 5) - 0.5f) * 50f
                translate(x, y) {
                    rotate(angle, pivot = Offset(s / 2, s / 2)) {
                        with(painters[pick]) { draw(Size(s, s), colorFilter = tint) }
                    }
                }
            }
        }
    }
}
