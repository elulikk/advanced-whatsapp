# Changelog

## Sin publicar — Actualizaciones automáticas

- **PC y Android buscan versiones nuevas en GitHub Releases** (`elulikk/advanced-whatsapp`; si el repo es privado, Ajustes lo avisa)
  al abrir (con demora) y cada 12 h, con la casilla «Buscar actualizaciones automáticamente» y el botón «Buscar actualizaciones»
  (Ajustes > Acerca de en PC, Ajustes en Android) que muestran las notas del release.
- **PC**: barra discreta «Hay una versión nueva · Ver novedades · Actualizar» y notificación. Al aceptar baja el Setup con
  verificación de tamaño y SHA-256, cierra la app y el gateway y lo instala en silencio; la app se reabre sola. En desarrollo solo avisa.
- **Android**: notificación de versión nueva; baja el APK a la caché y abre el instalador del sistema.
- **Publicar**: `python tools/publicar_release.py pc|android [--publicar]` (tags `pc-v<ver>` y `android-v<ver>`; ver `docs/ARQUITECTURA.md`).

## 2026-11-12 — Android 0.4.0 / PC

### Nuevo: IA
- **Lo que te perdiste**: en un chat con 15 o más mensajes sin leer aparece un banner arriba con "Resumir"; la IA resume
  quién dijo qué, las decisiones y las preguntas para vos.
- **Respuestas rápidas sugeridas**: 2-3 chips sobre el campo de mensaje con respuestas cortas al último mensaje, en tu estilo
  (usa tus notas/perfil del contacto si existen). Se piden solas cuando el último mensaje trae una pregunta; si no, con el
  botón ✨. Caché por último mensaje. Se apagan en Ajustes.
- **Buscar con IA**: ✨ en el buscador de chats; encuentra mensajes por significado, incluidas las transcripciones de audios
  (sinónimos con IA + candidatos por texto + reranking: como máximo 2 llamadas por búsqueda, con caché).
- **Recordatorios detectados**: un ⏰ en los mensajes que mencionan una fecha/hora futura ("el jueves a las 8") para crear un
  recordatorio local (notificación) o programar el próximo mensaje a esa hora. Detección con regex, sin IA.
- **Traducción**: "Traducir" en el menú de un mensaje (al idioma configurado en Ajustes) y "Traducir antes de enviar" sobre el
  borrador.
- **Requiere respuesta**: los chats donde quedó una pregunta o pedido tuyo sin contestar muestran "↩" y hay un filtro
  "Sin responder".

### Nuevo: mensajería
- **Respuestas automáticas**: reglas por horario/días o "modo" manual ("Estoy manejando"), solo chats individuales,
  excepciones y lista blanca, y un límite de una respuesta por contacto cada X horas. Las ejecuta el gateway: andan con la ventana cerrada.
- **Plantillas / atajos**: escribí `/atajo` (y espacio, Tab o Enter en la PC; espacio o sugerencia en Android) y se inserta el texto guardado.
  Se guardan en `plantillas.json` dentro de la carpeta de datos.
- **Mensajes programados recurrentes**: diaria, semanal en los días elegidos, mensual o anual (cumpleaños).
- **Mensajes eliminados y editados**: se conserva el original ("Ver original") y las versiones anteriores ("Editado" con historial).
  Ajuste "Conservar lo que se elimina…", activado por defecto.
- **Encuestas**: crear (pregunta, opciones, selección múltiple) y ver las recibidas/enviadas con el conteo de votos al día.
- **Fijar y destacar**: barra de mensajes fijados arriba del chat (también en Android) y pantalla de destacados con buscador.
- **Enviar a varios**: el mismo mensaje o archivo a cada contacto por separado, con pausa al azar entre envíos.

### Nuevo: copias y sincronización
- **Copia de seguridad cifrada** (`.awbackup`, AES-256-GCM con clave PBKDF2): chats y mensajes, tags, chats bloqueados, nombres y notas
  de contactos, stickers, mensajes programados y ajustes. Restaurar combinando (gana lo más nuevo) o reemplazando todo. El mismo archivo
  se restaura en la otra app. Las claves de IA solo van si se elige. Copia automática programable con rotación de las últimas N.
- **Sincronizar PC y celular** por el chat «Mi número» (documento cifrado que la otra app procesa sin mostrarlo): tags, bloqueos, nombres y
  notas, stickers y preferencias; gana el último cambio por clave.
- **Enviar al celular** (PC): arrastrar o elegir archivos que llegan a «Mi número»; en Android, botón «Guardar en Descargas».
- Formato por secciones, fácil de extender (ver `docs/ARQUITECTURA.md`).

### Nuevo: calidad de vida
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


### Nuevo: organización y privacidad
- **Carpetas inteligentes**: píldoras bajo los filtros con reglas (tags/pestañas, individual o grupo, no leídos, silenciados)
  o contactos elegidos.
- **Archivar chats** (a mano o automático tras X días sin actividad, salvo los fijados); vuelven solos al llegar un mensaje.
- **Notas privadas por chat** y **galería por chat** (fotos y videos, documentos, links y audios).
- **Bloqueo de toda la app** con la misma contraseña (y huella en Android) al abrir y al volver tras X minutos.
- **Chats ocultos**: un chat bloqueado puede desaparecer de la lista; se muestra escribiendo un código secreto en el buscador.
- **Lectura invisible por chat**: leer sin mandar tildes azules.

### Performance
- **Caché de miniaturas** en ambas apps (memoria LRU + disco): las fotos del chat ya no se decodifican enteras en cada
  redibujado; en PC además los avatares de la lista usan una LRU compartida. Medido en PC con 40 fotos de 12 MP: decodificar
  las originales ~2400 ms; con miniaturas en caché ~90 ms (la primera vez, generarlas ~700 ms).
- Estadísticas: índice `idx_messages_ts`; con 300 mil mensajes, el período «último mes» pasó de ~127 ms a ~65 ms y el
  historial completo se calcula en ~2,3 s en un hilo aparte. El tiempo de respuesta usa una sola pasada (funciones de ventana en PC;
  recorrido ordenado en Android, donde SQLite 3.22 no las tiene).
- Paginación de mensajes: en un chat de 100 mil mensajes `get_messages` tarda ~2 ms con 40, ~12 ms con 2000 y ~58 ms con 10 mil:
  el cuello de botella es rearmar el HTML/lista, no la consulta.

### Arreglos
- Las **notas de voz** que mandabas no se podían escuchar del otro lado: el gateway ahora reempaqueta el OGG/Opus (páginas,
  duración y fin de stream correctos) antes de enviarlo.
- **Descargar una imagen** ya no la abre en pantalla completa: se baja, queda en el chat y se guarda una copia en
  Descargas/AdvancedWhatsapp (PC y Android).

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
