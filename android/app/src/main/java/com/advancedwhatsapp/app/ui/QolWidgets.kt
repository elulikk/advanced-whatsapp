package com.advancedwhatsapp.app.ui

import android.graphics.Bitmap
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalUriHandler
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import coil.compose.AsyncImage
import com.advancedwhatsapp.app.Prefs
import com.advancedwhatsapp.app.data.LinkPreviews
import com.advancedwhatsapp.app.data.Thumbs
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.io.File

/**
 * Foto del chat desde la caché de miniaturas (memoria + disco): se pinta al instante si ya estaba y, si no, se arma en
 * un hilo de fondo. Si la imagen no se puede reducir, cae a Coil con la original.
 */
@Composable
fun ThumbImage(path: String, modifier: Modifier, onClick: () -> Unit) {
    var bitmap by remember(path) { mutableStateOf<Bitmap?>(Thumbs.peek(path)) }
    var failed by remember(path) { mutableStateOf(false) }
    LaunchedEffect(path) {
        if (bitmap == null) {
            val loaded = withContext(Dispatchers.IO) { Thumbs.load(path) }
            if (loaded == null) failed = true else bitmap = loaded
        }
    }
    val b = bitmap
    when {
        b != null -> {
            val image = remember(b) { b.asImageBitmap() }
            Image(bitmap = image, contentDescription = "Imagen", contentScale = ContentScale.FillWidth, modifier = modifier.clickable(onClick = onClick))
        }
        failed -> AsyncImage(
            model = File(path), contentDescription = "Imagen", contentScale = ContentScale.FillWidth,
            modifier = modifier.clickable(onClick = onClick),
        )
        else -> Box(modifier.height(160.dp).background(C.raised, RoundedCornerShape(6.dp)).clickable(onClick = onClick))
    }
}

/** Tarjeta con imagen, título y descripción del primer link del mensaje (nada si no hay, está apagada o aún no llegó). */
@Composable
fun LinkCard(text: String) {
    if (!Prefs.linkPreviews) return
    val url = remember(text) { LinkPreviews.firstUrl(text) } ?: return
    var preview by remember(url) { mutableStateOf(LinkPreviews.peek(url)) }
    LaunchedEffect(url) {
        if (preview == null) preview = withContext(Dispatchers.IO) { LinkPreviews.load(url) }
    }
    val p = preview ?: return
    val uri = LocalUriHandler.current
    val open = { runCatching { uri.openUri(url) }; Unit }
    Column(
        Modifier.fillMaxWidth().padding(top = 4.dp, bottom = 2.dp)
            .clip(RoundedCornerShape(6.dp)).background(Color(0x33000000)).clickable(onClick = open),
    ) {
        p.imagePath?.let { ThumbImage(it, Modifier.fillMaxWidth().heightIn(max = 170.dp), open) }
        Column(Modifier.padding(horizontal = 8.dp, vertical = 6.dp)) {
            if (p.title.isNotEmpty()) {
                Text(p.title, color = C.text, fontSize = 14.sp, fontWeight = FontWeight.SemiBold, maxLines = 2, overflow = TextOverflow.Ellipsis)
            }
            if (p.description.isNotEmpty()) {
                Text(p.description, color = C.text2, fontSize = 12.sp, maxLines = 3, overflow = TextOverflow.Ellipsis)
            }
            Text(LinkPreviews.domainOf(url), color = C.text3, fontSize = 11.sp, maxLines = 1)
        }
    }
}
