# Changelog

## 2026-11-11 — Android 0.3.1

### Arreglos
- Sacar una foto y enviarla fallaba con `ENOENT ... /tmp/...-enc`: en Android Node no tomaba la carpeta temporal
  de la app; ahora el gateway usa siempre una carpeta propia (y la vuelve a crear si Android vacía la caché).

### Nuevo
- La sugerencia **"Pegar"** del teclado (con la miniatura de la imagen copiada) funciona en el campo de mensaje:
  abre la vista previa de envío como una foto recién sacada.

## 2026-11-10 — Android 0.3.0 / PC

### Nuevo
- **Stickers** (PC y Android): panel con favoritos, recientes y paquetes; envío de stickers; "Agregar a mis stickers"
  desde un sticker recibido. Importación desde WhatsApp (en Android, carpeta de stickers de WhatsApp y WhatsApp Business),
  desde los stickers de tus chats y desde archivos .webp/.png/.wastickers.
- **Emojis** (PC y Android): selector con categorías, buscador y recientes; en Android reemplaza al teclado.
- **Dictado a texto** (PC y Android): graba, transcribe y deja el texto en el campo para revisarlo, con opción de
  emprolijarlo con IA. La nota de voz normal sigue estando.
- **Transcripción automática de audios** (PC y Android): los audios que llegan y los que mandás se transcriben solos.
  Activada por defecto; se apaga en Ajustes ("Transcribir audios automáticamente").
- **Bloqueo de chats con contraseña** (PC y Android, con huella en Android): candado, fila oscurecida, vista previa
  oculta y notificaciones sin contenido.
- **Editar contactos** (PC y Android): nombre propio, apodo y notas, que prevalecen sobre el nombre de WhatsApp.
- **Pegar imágenes del portapapeles** en el campo de mensaje (PC: también archivos copiados desde el Explorador).
- **Notificar mensajes de grupos** (Ajustes, PC y Android): apagado por defecto; así los grupos no avisan ni suman en el
  contador del ícono.

### Cambios
- Transcripciones y síntesis IA plegables dentro de las burbujas.
- Estados de contactos en silencio por defecto; tocar el nombre de quien lo subió abre el chat con esa persona.
- Logo de la llama junto al título de la app.
- Android: barra de reproducción de audios rediseñada (progreso, posición y tiempos; se puede arrastrar).
- Android: los archivos descargados van a la carpeta **Descargas** (`Download/AdvancedWhatsapp`).

### Arreglos
- El contador de no leídos del ícono (barra de tareas, bandeja, título y launcher de Android) ahora baja al leer y no
  suma los chats silenciados.
- Android: la pantalla ya no se apaga mientras grabás o escuchás un audio.
- Android: los APK y documentos recibidos se abren al tocarlos (con permiso para instalar APKs).
