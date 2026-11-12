# Advanced WhatsApp

App de escritorio para Windows tipo WhatsApp Desktop: UI en **Python 3 / PySide6** + **gateway Node.js** con
`@whiskeysockets/baileys` (la conexión real con WhatsApp). Hay además una app **Android** independiente que reutiliza el
mismo `gateway/server.js`. Todo el texto de UI, comentarios y commits está en español (rioplatense).

## Arquitectura

```
main.py            Arranque: instancia única, log a ~/.magsapp/app.log, splash y MainWindow (que inicia WebhookServer y GatewayManager)
app/
  config.py        Rutas (~/.magsapp), puertos, token compartido, config.ini y preferencias (get_pref/set_pref)
  db.py            SQLite (WAL) ~/.magsapp/magsapp.db: chats, messages, contacts, reactions (+ tags, ver tags.py)
  gateway_manager.py  Lanza/vigila (cada 10 s) el proceso Node y es el cliente HTTP del gateway (puerto 5016)
  webhook_server.py   Servidor HTTP local (5015) que recibe los eventos del gateway y los pasa a la UI con señales Qt
  media_service.py    Descarga de multimedia bajo demanda y transcripción de notas de voz (hilo aparte)
  notifications.py / windows_integration.py   Toasts, bandeja, advancedwhatsapp://, instancia única, inicio con Windows
  tags.py          Tags visuales de chats (tablas tags / chat_tags)
  ai/              ai_service (Gemini/Groq/OpenAI/Claude con fallback), message_summary, psych_analysis,
                   reply_polisher, transcription (Whisper), smart_features (lo que te perdiste, respuestas rápidas,
                   búsqueda por significado, fechas detectadas, traducción, «sin responder»; widgets en ui/ai_extras.py)
  reminders.py     Recordatorios locales (recordatorios.json + aviso por notificación)
  ui/              Widgets Qt: main_window, chat_list, chat_view, status_view, scheduled_messages, settings_view, theme...
gateway/           server.js (Express + Baileys) y package.json. node_modules NO se versiona
android/           App Kotlin/Compose con Node embebido (ver android/README.md); usa gateway/server.js tal cual
tools/             instalar.py, build_portable.py (+ instalador.iss), generar_iconos.py, instalar_python.ps1
tests/             pytest (base de datos, tags, mensajes programados, webhook, IA)
assets/            Iconos de la app
```

Flujo, endpoints y eventos en detalle: [`docs/ARQUITECTURA.md`](docs/ARQUITECTURA.md).

## Cómo correr

```powershell
INSTALAR_DEPENDENCIAS.bat          # una vez: Python/Node/FFmpeg (winget), pip, npm install, accesos directos
python main.py                     # o INICIAR_ADVANCEDWHATSAPP.bat (sin consola, con pythonw)
node --check gateway/server.js     # chequeo de sintaxis del gateway
```

- En desarrollo `GatewayManager` copia `gateway/server.js` y `package.json` a `~/.magsapp/gateway/` y corre ahí
  (`npm install --omit=dev` solo si cambió `package.json`). **Editar `gateway/server.js` y reabrir la app basta**: la huella
  (`gateway_build_id`) cambia y reemplaza el gateway viejo.
- Copia de prueba junto a la real: `ADVANCEDWHATSAPP_WEBHOOK_PORT=5115` y `ADVANCEDWHATSAPP_GATEWAY_PORT=5116` (más otro `USERPROFILE`).
- Logs: `~/.magsapp/app.log` (Python), `gateway.log` (Node), `gateway_stderr.log`, `npm_install.log`.

## Tests

```powershell
pip install pytest
python -m pytest tests -q
```

No hay tests del gateway Node; se verifica con `node --check` y probando contra la app. Android: `cd android; .\gradlew.bat testDebugUnitTest`.

## Empaquetar

```powershell
python tools/build_portable.py               # dist/AdvancedWhatsapp-<APP_VERSION>.zip (PyInstaller + Node + FFmpeg incluidos)
python tools/build_portable.py --instalador  # además el Setup .exe con Inno Setup
```

La versión sale de `APP_VERSION` en `app/config.py` (subirla antes de armar). Usa `build/venv` aparte; `build/` y `dist/` están en `.gitignore`.

## Datos (`~/.magsapp`)

`magsapp.db` (SQLite), `auth_info_baileys/` (sesión Baileys), `media/` (`avatars/`, `enviados/`, `estados_programados/`,
`mensajes_programados/`, `estados_contactos/`), `config.ini` (API keys de IA, texto plano), `gateway.token`, `outbox.json`
(entregas pendientes gateway -> app), `contacts_store.json` y `lid_phone_map.json` (agenda e identidades LID), `estados_programados.json`,
`mensajes_programados.json`, `mis_estados.json`, `estados_contactos.json`, `analisis/`, `app.pid`. En Android el gateway usa
`ADVANCEDWHATSAPP_DATA_DIR` en lugar de `~/.magsapp`.

## Convenciones

- Gateway y webhook escuchan solo en `127.0.0.1` y exigen el header `X-AdvancedWhatsapp-Token` (excepto `GET /status` en escritorio).
  Para dos procesos que se hablan, cualquier endpoint nuevo hereda eso; no agregar rutas sin pasar por el middleware.
- El gateway nunca debe tumbarse por un error suelto: handlers con `try/catch`, promesas con `.catch`, y lo que no se puede entregar a la app
  va a la bandeja de salida (`deliverReliably`) en vez de perderse.
- Los archivos multimedia **no** se bajan al llegar el mensaje: se guarda `media_raw` y se baja al abrirlo (`POST /media/download`).
- Las colas de estados y mensajes programados viven en el gateway (no en la ventana) y se escriben de forma atómica (`.tmp` + `rename`).
- Los cambios de `gateway/server.js` deben mantener compatibilidad con Android (`ADVANCEDWHATSAPP_DATA_DIR`, sin sharp/jimp).
- Los `.py` y `gateway/server.js` están en CRLF: respetarlo al editar (los `.bat` lo fuerza `.gitattributes`).
- Python: UI en el hilo de Qt; todo I/O de red/disco largo en hilos (`threading.Thread(daemon=True)`) y resultados por `Signal`.
- Estilos solo con la paleta `C` de `app/ui/theme.py`; iconos con `app/ui/icons.py`.
