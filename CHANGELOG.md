# Changelog

## Sin publicar — calidad de vida y performance (PC y Android)

### Nuevo
- **Audios**: la velocidad elegida (1x / 1.5x / 2x; en PC también las de broma) se recuerda entre notas y sesiones, y al
  terminar una nota suena sola la siguiente del chat si es otro audio ya bajado (se apaga en Ajustes).
- **Vista previa de links** (PC y Android): título, descripción e imagen. Se usa el preview que trae el mensaje de WhatsApp;
  si no, el gateway lo baja (`GET /link-preview`, timeouts de 3-4 s, sin dependencias) y se guarda en caché local. Al enviar
  un texto con link, el gateway manda el preview (`linkPreview` de Baileys; la miniatura solo si la imagen ya es JPEG chica).
- **Atajos de teclado en PC**: marcar leído (Ctrl+Shift+R), silenciar 8 h / reactivar (Ctrl+Shift+M), siguiente chat sin leer
  (Ctrl+Shift+U), chat siguiente/anterior (Alt+↓/↑), archivar (Ctrl+E), fijar (Ctrl+Shift+P), estadísticas (Ctrl+Shift+E) y
  la ventana **Atajos** (Ctrl+/).
- **Widget de Android «Sin leer»**: chats con mensajes sin leer (sin los silenciados; los bloqueados no muestran el texto);
  tocar uno lo abre. RemoteViews, sin dependencias nuevas.
- **Estadísticas** (PC: Ctrl+Shift+E o Ajustes > Almacenamiento y chat; Android: Ajustes): con quién hablás más, mensajes por día
  y por hora, y tiempo de respuesta promedio tuyo y de los demás, por período. Índice por fecha para filtrar rápido.
- **Limpieza de multimedia** (Ajustes de PC y Android): cuánto ocupa lo bajado por tipo y por chat, borrar lo más viejo que X días
  o por encima de X GB (se vuelve a bajar al abrirlo; lo enviado desde acá no se toca) y limpieza automática diaria opcional.

### Performance
- **Caché de miniaturas** en ambas apps (memoria LRU + disco): las fotos del chat ya no se decodifican enteras en cada
  redibujado; en PC además los avatares de la lista usan una LRU compartida. Medido en PC con 40 fotos de 12 MP: decodificar
  las originales ~2400 ms; con miniaturas en caché ~90 ms (la primera vez, generarlas ~700 ms).
- Estadísticas: índice `idx_messages_ts`; con 300 mil mensajes, el período «último mes» pasó de ~127 ms a ~65 ms y el
  historial completo se calcula en ~2,3 s en un hilo aparte. El tiempo de respuesta usa una sola pasada (funciones de ventana en PC;
  recorrido ordenado en Android, donde SQLite 3.22 no las tiene).
- Paginación de mensajes: en un chat de 100 mil mensajes `get_messages` tarda ~2 ms con 40, ~12 ms con 2000 y ~58 ms con 10 mil:
  el cuello de botella es rearmar el HTML/lista, no la consulta.

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
