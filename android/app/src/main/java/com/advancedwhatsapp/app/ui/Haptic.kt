package com.advancedwhatsapp.app.ui

import android.os.Build
import android.view.HapticFeedbackConstants
import android.view.View
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalView
import com.advancedwhatsapp.app.Prefs

/** Respuesta táctil de la app: un toque corto al apretar botones, uno más marcado al confirmar (empezar a grabar, enviar). */
object Haptics {
    fun tap(view: View) {
        if (Prefs.haptics) view.performHapticFeedback(HapticFeedbackConstants.VIRTUAL_KEY)
    }

    fun confirm(view: View) {
        if (!Prefs.haptics) return
        view.performHapticFeedback(if (Build.VERSION.SDK_INT >= 30) HapticFeedbackConstants.CONFIRM else HapticFeedbackConstants.LONG_PRESS)
    }

    fun reject(view: View) {
        if (!Prefs.haptics) return
        view.performHapticFeedback(if (Build.VERSION.SDK_INT >= 30) HapticFeedbackConstants.REJECT else HapticFeedbackConstants.LONG_PRESS)
    }
}

/**
 * Igual que el IconButton de Material, pero vibra al tocarlo. Las pantallas de este paquete lo usan en lugar del original
 * (no importan androidx.compose.material3.IconButton), así todos los botones de ícono responden al toque.
 */
@Composable
fun IconButton(onClick: () -> Unit, modifier: Modifier = Modifier, enabled: Boolean = true, content: @Composable () -> Unit) {
    val view = LocalView.current
    androidx.compose.material3.IconButton(
        onClick = {
            Haptics.tap(view)
            onClick()
        },
        modifier = modifier, enabled = enabled, content = content,
    )
}
