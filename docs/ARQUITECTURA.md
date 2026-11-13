# Arquitectura: gateway Node <-> app Python

```
 WhatsApp (servidores)
        ^  WebSocket cifrado (Baileys)
        |
 +------+---------------------+   HTTP 127.0.0.1:5016 (control)    +---------------------------+
 | gateway/server.js (Node)   | <-------------------------------- | app/gateway_manager.py     |
 |  Express + Baileys         |                                    |  (cliente HTTP + proceso)  |
 |  colas: estados, mensajes  | --------------------------------> | app/webhook_server.py      |
 |  programados, outbox       |   HTTP 127.0.0.1:5015 (eventos)    |  -> señales Qt -> app/ui   |
 +----------------------------+                                    +---------------------------+
                                                                          |
                                                                  SQLite ~/.magsapp/magsapp.db
```

Dos canales HTTP locales, ambos solo en `127.0.0.1` y ambos con el header `X-AdvancedWhatsapp-Token`
(token de `~/.magsapp/gateway.token`, que `GatewayManager` pasa al gateway en `ADVANCEDWHATSAPP_TOKEN`):

- **Control (Python -> gateway)**, puerto `5016` (`PORT`, `ADVANCEDWHATSAPP_GATEWAY_PORT`): la UI pide enviar, reaccionar, descargar, etc.
- **Eventos (gateway -> Python)**, puerto `5015` (`ADVANCEDWHATSAPP_WEBHOOK_PORT`): el gateway avisa de todo lo que pasa en WhatsApp.

## Ciclo de vida del gateway

1. `MainWindow.__init__` (`app/ui/main_window.py`) inicia `WebhookServer` y, en un hilo, `GatewayManager.start()` seguido de
   `start_watchdog()` (el webhook sube primero para no perder eventos).
2. `GatewayManager.start()` mira si ya hay un gateway respondiendo en `GET /status`. Si su `build` (huella SHA-1 de
   `server.js` + `package.json`) coincide, lo adopta; si no, lo apaga con `POST /shutdown` y lanza otro.
3. Arranca `node server.js` con estas variables: `PORT`, `PYTHON_WEBHOOK_URL`, `PYTHON_CONNECTION_URL`, `AUTH_DIR`, `MEDIA_DIR`,
   `ADVANCEDWHATSAPP_TOKEN`, `ADVANCEDWHATSAPP_BUILD`, `ADVANCEDWHATSAPP_PARENT_PID`. En desarrollo corre desde `~/.magsapp/gateway/`
   (copia de `gateway/`); en la versión portable, desde `gateway/` con `node_modules` ya instalado.
4. Un hilo vigilante revisa cada 10 s: si el proceso murió o dejó de responder dos veces seguidas, lo reinicia.
5. El gateway comprueba cada 10 s que el proceso padre (`ADVANCEDWHATSAPP_PARENT_PID`) siga vivo; si no, se apaga solo.
6. Al conectar a WhatsApp, `initWhatsApp()` abre el socket Baileys (`syncFullHistory`, `markOnlineOnConnect: false`). La reconexión usa un
   único temporizador (`scheduleReconnect`) con espera creciente 3 s -> 60 s; una sesión cerrada desde el celular (`loggedOut`) borra
   las credenciales y genera un QR nuevo.

## Estados de conexión

`connectionStatus` en el gateway: `disconnected`, `connecting`, `qr_ready`, `connected`. Cada cambio se envía a Python en
`POST /api/whatsapp/connection` con `{status, phone, push_name, has_qr, timestamp, ...}`; los extras posibles son `qr_data_url`
(estado `qr_ready`), `retry_in`, `pairing_cut` y `error`. Además existe el estado informativo `logged_out`.
La app también puede consultar `GET /status` y `GET /qr` (polling).

## Eventos gateway -> Python (webhook, puerto 5015)

| Endpoint | Cuándo | Efecto en la app |
|---|---|---|
| `POST /api/whatsapp/incoming` | Mensaje nuevo, propio sincronizado, edición (`edited`), borrado (`revoked`), llamada perdida (`is_call`), mensaje programado ya enviado (`upsert_type: append`) | `save_message` + señal `message_received` (con `is_new`) |
| `POST /api/whatsapp/connection` | Cambio de estado de conexión | señal `connection_changed` |
| `POST /api/whatsapp/sync_batch` | Historial, lista de chats/grupos, contactos renombrados (`rename_chats`) | `save_sync_batch` + `history_synced` |
| `POST /api/whatsapp/status` | Acuse de un mensaje propio: `sent` / `delivered` / `read` | `update_message_status` + `message_status` |
| `POST /api/whatsapp/read_elsewhere` | Leído (o marcado no leído) en el celular | `mark_*_elsewhere` + `read_elsewhere` |
| `POST /api/whatsapp/reaction` | Reacción con emoji (texto vacío = retirada) | `set_reaction` + `reaction_changed` |
| `POST /api/whatsapp/avatar` | Foto de perfil descargada | `update_chat_avatar` + `avatar_updated` |
| `POST /api/whatsapp/presence` | "en línea" / "escribiendo..." (solo chats suscritos) | `presence_changed` |
| `POST /api/whatsapp/call` | Llamada entrante (`offer`, `accept`, `reject`, `timeout`, `terminate`) | `call_event` |
| `POST /api/whatsapp/contact_status` | Estado nuevo (o borrado, con `removed`) de un contacto | `contact_status` |
| `POST /api/whatsapp/scheduled` | Un mensaje programado terminó: `sent` / `failed` / `expired` | `scheduled_changed` |
| `POST /api/app/activate` | Otra instancia o notificación pide mostrar la ventana | `app_activate` |

Respuestas del webhook: `200 {"status":"ok"}` si se procesó, `500` si falló (el gateway reintenta), `401` token inválido, `404` ruta
desconocida.

### Entrega confiable (bandeja de salida)

`incoming`, `sync_batch`, `read_elsewhere` y `scheduled` van por `deliverReliably()`: 3 intentos (30 s de tope cada uno) y, si la app no
responde, el aviso se guarda en `~/.magsapp/outbox.json` (máx. 5000) y se reenvía **en orden** cada 10 s y al conectar. Mientras haya
pendientes, lo nuevo se encola detrás para no desordenar la conversación. Un aviso que la app rechaza con 400/404/413/422, o con errores
repetidos durante unos 5 minutos, se descarta para no bloquear la cola. Los eventos de presencia, llamadas, estados de contactos, avatares,
acuses (`status`) y reacciones se mandan una sola vez, sin bandeja (`postEvent` / `fetch` con `.catch`).

## Endpoints de control (Python -> gateway, puerto 5016)

Todos exigen el token (en Android, también `/status`). Cuerpos y respuestas son JSON; los errores devuelven `{"error": "..."}`.

**Conexión y cuenta**

| Método y ruta | Descripción |
|---|---|
| `GET /status` | `{build, pid, status, phone, push_name, has_qr}`. Sin token en escritorio (lo usa el vigilante) |
| `GET /qr` | `{status, qr_data_url, message}` |
| `POST /pairing-code` | `{phone}` -> código de 8 dígitos para vincular sin QR (reinicia credenciales y socket) |
| `POST /disconnect` | Cierra sesión, borra credenciales y genera QR nuevo |
| `POST /shutdown` | Apaga el proceso (guarda la bandeja de salida y los estados) |
| `POST /presence` | `{available}`: figurar "en línea" solo con la ventana al frente |
| `POST /chat-presence` | `{to, state: composing|recording|paused, subscribe}` |

**Mensajes**

| Método y ruta | Cuerpo principal |
|---|---|
| `POST /send` | `{to, text, quoted?, image_path\|document_path\|video_path\|audio_path\|sticker_path\|image_url, image_thumb, document_name, mimetype, audio_seconds, audio_waveform}`. Prioridad de adjuntos: documento, imagen, video, audio, sticker, url. Devuelve `{success, message_id}`; 404 si falta el archivo, 503 si no hay conexión |
| `POST /react` | `{to, message_id, from_me, participant, emoji}` |
| `POST /read` | `{to, messages:[ids]}`: tildes azules (en grupos solo ids de los que se conoce el remitente) |
| `POST /delete` | `{to, message_id}`: eliminar para todos |
| `POST /pin` | `{to, message_id, from_me, participant, pinned}` (7 días) |
| `POST /media/download` | `{message_id, media_type, media_raw}` -> `{media_path}` en `~/.magsapp/media/`. Si WhatsApp ya borró el archivo, pide al celular que lo vuelva a subir (30 s) |

**Contactos y avatares**: `GET /contacts`, `POST /contact/update {jid, name, phone}`, `POST /fetch-avatar {jid}`.

**Programados (la cola vive en el gateway y sigue funcionando con la ventana en la bandeja)**

| Ruta | Descripción |
|---|---|
| `GET/POST /status-schedule`, `POST .../update`, `.../cancel`, `.../remove` | Estados programados (`text`/`image`/`video`, `at` epoch s) -> `~/.magsapp/estados_programados.json`. Revisión cada 20 s |
| `GET/POST /message-schedule`, `POST .../update`, `.../cancel`, `.../remove` | Mensajes programados: mismo cuerpo que `/send` más `at`, `chat_name`, `quoted_text`, `quoted_sender`, `consume` -> `mensajes_programados.json`. Un único temporizador apuntado al próximo envío (tope 60 s) |

Reglas comunes: estados `pending -> sending -> sent | failed | expired | canceled`; 3 intentos máximo con 120 s entre reintentos (un corte de
conexión no cuenta como intento en mensajes); se publican hasta 6 h tarde, pasado eso quedan `expired`. En mensajes cada elemento tiene un `wa_id` fijo
(`generateMessageIDV2`) para que un reintento no duplique el envío. Al terminar un mensaje, el gateway avisa por `scheduled` y por `incoming`
(el eco de lo que envía el propio gateway se descarta, así que la app se entera por ahí).

**Estados (stories)**: `GET /my-statuses`, `POST /my-statuses/sync|media|delete`; `GET /contact-statuses`, `POST /contact-statuses/media|seen`.
Se guardan 24 h en `mis_estados.json` y `estados_contactos.json`; la foto o el video se baja recién al abrirlo.

## Flujos típicos

**Mensaje entrante**: Baileys `messages.upsert` -> `extractMessageDetails` (desenvuelve efímeros/ediciones/borrados, saca el texto y el mensaje citado)
-> unifica LID/teléfono (`preferPhoneJid`) -> `serializeMediaRaw` (guarda las claves de cifrado, no el archivo) ->
`deliverReliably(incoming)` -> `save_message` -> señal `message_received` -> UI y notificaciones.

**Mensaje saliente**: UI -> `GatewayManager.send_message` -> `POST /send` -> `sendWhatsApp` -> `sock.sendMessage`. El id queda en
`sentBotMessageIds` para descartar el eco de `messages.upsert`; la UI guarda el mensaje propio al recibir la respuesta. Los acuses llegan
después por `messages.update` / `message-receipt.update` -> `POST /status`.

**Archivo recibido**: la UI (o `media_service`) llama `POST /media/download` con el `media_raw` guardado en la base; el gateway baja,
descifra y escribe el archivo; la respuesta trae `media_path`.

**Mensaje programado**: UI -> `POST /message-schedule` (copia o mueve el adjunto a `media/mensajes_programados/`) -> temporizador
-> `runScheduledMessage` -> `sendWhatsApp` -> `scheduled` + `incoming (append)` a la app.

## Mensajería extra (`gateway/extras.js`)

Módulo del gateway (viaja junto a `server.js` en PC, instalador y APK). Datos en la carpeta de datos, escritura atómica.

| Ruta | Descripción |
|---|---|
| `GET/POST /auto-reply`, `POST /auto-reply/mode` | Respuestas automáticas -> `respuestas_automaticas.json` (+ `_estado.json` con la última respuesta por contacto). Reglas `schedule` (días, franja) o `manual`; `exclude`/`only` por jid; `cooldown_hours` |
| `POST /poll`, `POST /polls/get` | Crear encuesta (`{to, name, options, multiple}`); conteo por ids. Los votos se descifran en el gateway (`encuestas.json`) y se avisan por `POST /api/whatsapp/poll` |
| `POST /send-many`, `GET /send-many`, `POST /send-many/cancel` | Un mismo `/send` a `targets:[{to, chat_name}]`, de a uno, con pausa `delay_min`..`delay_max` s. Cada envío llega a la app como `incoming (append)`; al terminar avisa `POST /api/whatsapp/bulk` |
| `POST /message-schedule` y `/update` | Aceptan `repeat: {type: daily\|weekly\|monthly\|yearly, days?, every?}`: tras salir, el item vuelve a `pending` con la próxima fecha (`anchor_at` evita que el día se corra) |

Eliminados y ediciones se conservan en la base de cada cliente: `messages.revoked_orig` (JSON del contenido) y la tabla
`message_edits` (versiones anteriores), salvo que se apague el ajuste `keep_deleted`. Destacados: `messages.starred_at`;
fijados: `pinned_at`; encuestas: `messages.poll_json` (opciones y votos tal como los manda el gateway).

## Android

El mismo `gateway/server.js` corre dentro de la app con `nodejs-mobile` (`ADVANCEDWHATSAPP_DATA_DIR` apunta a la carpeta privada). El
equivalente de `webhook_server.py` es `gateway/WebhookServer.kt` y el de `db.py` es `data/Db.kt`. Detalles en `android/README.md`.

## Emojis y stickers

- **Biblioteca** (`app/stickers.py`; Android: `data/Stickers.kt`): `~/.magsapp/media/stickers/<id>.webp` (512x512, el id es el hash del
  contenido) + `index.json` (paquetes, favoritos, recientes, emojis recientes, mensajes ya importados), escrito de forma atómica.
  Las imágenes se convierten a webp 512x512 con Pillow en PC y con `Bitmap` en Android; el gateway no convierte nada (sin sharp/jimp).
- **Emojis**: `android/app/src/main/assets/emojis.txt` es la fuente; `app/emoji_data.py` es su copia embebida para PC (si se edita uno,
  actualizar el otro). Se parsean la primera vez que se abre el selector.
- **Envío**: `POST /send` con `sticker_path` (webp) y, si es animado, `sticker_animated: true`. Se copia a `media/enviados/` antes de enviar.
- **Importación**: archivos/carpetas/.wastickers, historial de chats (baja con `/media/download` los que faltan) y, en Android, las carpetas
  de WhatsApp (`Android/media/com.whatsapp/...`) con el selector de carpetas (SAF).

## Notas de voz enviadas (causa raíz del "audio no disponible")

WhatsApp solo reproduce una nota de voz si el OGG/Opus está bien formado: páginas con BOS/EOS, *granule position*
coherente con la duración real y CRC válido. El `MediaRecorder` de Android (`OutputFormat.OGG` + `AudioEncoder.OPUS`)
deja el contenedor con granule incorrecto y sin marca de fin de stream, y el receptor mostraba error al reproducir.
`sendWhatsApp` (gateway) pasa ahora todo audio por `normalizeOggOpus`: reempaqueta las páginas sin tocar los paquetes
Opus y calcula la duración exacta (`seconds`); si el archivo no es OGG/Opus se envía tal cual. Sirve igual para PC
(FFmpeg ya genera OGG/Opus válido) y Android (mismo `server.js`, sin dependencias nativas).
## Organización y privacidad

Todo es local (nada pasa por el gateway). PC: `app/organization.py` (+ `app/chat_lock.py`); Android: `data/Organization.kt` (+ `data/ChatLock.kt`).

- **Carpetas inteligentes** (`smart_folders`, reglas en JSON): un chat entra si cumple *todas* las reglas puestas (tags en PC / pestañas en Android,
  tipo individual/grupo, no leídos, silenciados) o si es uno de los contactos elegidos. Sin reglas no muestra nada. Aparecen como píldoras bajo los filtros
  de la lista; `get_chats(extra_where=...)` / `Db.getChats(ChatFilter.Folder)` arman la consulta.
- **Archivados** (`chats.is_archived`): los filtros de la lista no los muestran (la búsqueda sí); píldora «Archivados». Archivado automático:
  `auto_archive_days` (config.ini / Prefs, 0 = apagado) archiva los chats sin actividad que no estén fijados; `save_message` / `Db.saveMessage` los saca de
  Archivados cuando llega o se manda un mensaje reciente, salvo que estén silenciados.
- **Chats ocultos** (`chat_hidden` en PC, `lock_hidden` en Android): solo un chat bloqueado, y con código secreto creado. Su hash (PBKDF2, claves `hide_*`
  de `lock_meta` / `lock_secret_*`) es independiente de la contraseña. `get_chats` los excluye salvo `show_secret=True`; escribir el código en el buscador
  (`chat_lock.try_secret`) los muestra hasta que la app se re-bloquea (`lock_all`). No suman en contadores y sus notificaciones se descartan.
- **Lectura invisible por chat** (`chat_silent_read`): `GatewayManager.send_read_receipts` (PC) y `Actions.markRead` (Android) no mandan tildes azules.
- **Bloqueo de la app entera**: pref `app_lock_enabled` + minutos (`app_lock_minutes`); reutiliza la contraseña de `chat_lock`. PC: capa `AppLockOverlay`
  al abrir y al restaurar de la bandeja/minimizada tras X minutos. Android: capa sobre la navegación (contraseña o huella) al abrir y al volver de segundo plano.
- **Galería**: `gallery_page(chat, kind, limit, offset)` (PC) / `Organization.gallery` (Android): fotos y videos, documentos, links y audios, paginados.
  Miniaturas con caché (LRU en PC, Coil en Android) y descarga de las fotos a la vista bajo demanda (`media_service.request_media` / `Actions.downloadMedia`).
