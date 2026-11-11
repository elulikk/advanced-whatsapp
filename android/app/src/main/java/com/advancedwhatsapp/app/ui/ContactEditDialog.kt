package com.advancedwhatsapp.app.ui

import android.content.Intent
import android.provider.ContactsContract
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.advancedwhatsapp.app.data.ContactInfo
import com.advancedwhatsapp.app.data.Db
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

/**
 * Editar los datos de un contacto: nombre propio y notas. Se guardan solo en este teléfono y el nombre propio
 * prevalece sobre el que manda WhatsApp (en la lista de chats, en el chat y en las notificaciones).
 */
@Composable
fun ContactEditDialog(jid: String, onDismiss: () -> Unit) {
    val context = LocalContext.current
    var info by remember { mutableStateOf<ContactInfo?>(null) }
    var name by remember { mutableStateOf("") }
    var notes by remember { mutableStateOf("") }

    LaunchedEffect(jid) {
        val loaded = withContext(Dispatchers.IO) { Db.getContactInfo(jid) }
        info = loaded
        name = loaded?.customName.orEmpty()
        notes = loaded?.notes.orEmpty()
    }
    val current = info ?: return

    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("Editar contacto") },
        text = {
            Column {
                OutlinedTextField(
                    value = name, onValueChange = { name = it }, singleLine = true,
                    label = { Text("Nombre o apodo") },
                    placeholder = { Text(current.waName) },
                    supportingText = { Text("Vacío = el nombre que manda WhatsApp", fontSize = 12.sp) },
                    modifier = Modifier.fillMaxWidth(),
                )
                current.phone?.takeIf { it.isNotBlank() }?.let {
                    Text("Teléfono: +$it", fontSize = 13.sp, modifier = Modifier.padding(top = 8.dp))
                }
                OutlinedTextField(
                    value = notes, onValueChange = { notes = it }, minLines = 3, maxLines = 6,
                    label = { Text("Notas") },
                    modifier = Modifier.fillMaxWidth().padding(top = 10.dp),
                )
                current.phone?.takeIf { it.isNotBlank() }?.let { phone ->
                    TextButton(onClick = {
                        // Abre la agenda del teléfono para editar o agregar este número
                        val intent = Intent(Intent.ACTION_INSERT_OR_EDIT).apply {
                            type = ContactsContract.Contacts.CONTENT_ITEM_TYPE
                            putExtra(ContactsContract.Intents.Insert.PHONE, "+$phone")
                            if (name.isNotBlank()) putExtra(ContactsContract.Intents.Insert.NAME, name.trim())
                        }
                        runCatching { context.startActivity(intent) }
                    }) { Text("Abrir en Contactos del teléfono") }
                }
            }
        },
        confirmButton = {
            TextButton(onClick = {
                Db.setContactInfo(jid, name, notes)
                onDismiss()
            }) { Text("Guardar") }
        },
        dismissButton = { TextButton(onClick = onDismiss) { Text("Cancelar") } },
    )
}
