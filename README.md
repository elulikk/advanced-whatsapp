# 💬 MAGSAPP - WhatsApp Desktop para Windows

Aplicación de escritorio nativa para Windows tipo **WhatsApp Desktop**, construida con **Python 3 (PySide6 / Qt)** y un Gateway ligero de **Node.js** con **@whiskeysockets/baileys**.

---

## 🚀 Características Principales

1. **Interfaz Idéntica a WhatsApp Desktop**:
   - Tema oscuro oficial (colores `#111b21`, `#202c33`, `#005c4b`, `#00a884`).
   - Panel lateral de chats con avatares dinámicos, previsualización de mensajes, timestamp relativo ("11:30", "Ayer", etc.) y contador de mensajes no leídos.
   - Filtros rápidos ("Todos" / "No leídos") y buscador en tiempo real.
   - Visor de mensajes con diseño de burbujas redondeadas (entrantes y salientes), separadores de fecha y tildes de lectura (`✓`, `✓✓`).
   - Compositor de mensajes multi-línea (Envío rápido con `Enter`, salto de línea con `Shift+Enter`).
   - Envío de archivos adjuntos (imágenes y documentos): botón, arrastrar o pegar con `Ctrl+V` (capturas, imágenes copiadas o archivos copiados en el Explorador).
   - Notas de voz: con el campo vacío el botón verde es un micrófono. Graba, pausa, descarta (`Esc`) o envía (`Enter`);
     llegan como nota de voz de WhatsApp con su duración y su onda. Requiere **FFmpeg** (`winget install Gyan.FFmpeg`)
     para convertir a OGG/Opus; las notas enviadas quedan en `~/.magsapp/media/enviados/`.

   - **Modo resumen**: el botón ✨ junto a la hora de cada mensaje (o «Resumir con IA» en su menú) resume,
     simplifica e interpreta el mensaje con IA: *en pocas palabras*, *qué quiere decir* y *qué espera*. Usa los
     mensajes anteriores como contexto y el proveedor/modelo elegidos en el redactor. En las notas de voz resume la
     transcripción. El resumen queda guardado (se vuelve a calcular solo si el mensaje se edita o pulsás «Rehacer»).

   - **Estados programados** (pestaña Estados, `Ctrl+2`): textos con color de fondo, fotos o videos con día y hora.
     Varias fotos/videos de una vez se reparten con un intervalo fijo (ideal para dejar un fin de semana cargado).
     Se pueden publicar ya, reprogramar, editar el texto o cancelar. Los publica el gateway, así que **la PC tiene que
     estar encendida y con internet**; al cerrar la ventana con estados pendientes, MAGSAPP ofrece quedar en la bandeja
     y mientras haya pendientes le pide a Windows que no se suspenda (no evita la suspensión al cerrar la tapa).
     Se envían a los contactos de tu agenda; si la PC estuvo apagada, un estado se publica tarde solo hasta 6 h después
     de su hora (pasado eso queda como "Vencido"). Cola en `~/.magsapp/estados_programados.json`, copias de los archivos
     en `~/.magsapp/media/estados_programados/`.

   - **Mis estados activos**: en la misma pestaña se ven los estados publicados en las últimas 24 h (desde MAGSAPP o
     desde el celular), cuántos y quiénes los vieron, se puede abrir la foto/video y eliminarlos para todos.
     Solo se registran los estados y vistas que llegan mientras MAGSAPP está conectado (`~/.magsapp/mis_estados.json`).

   - **Estados de tus contactos**: en la pestaña Estados aparecen los contactos que publicaron en las últimas 24 h
     (anillo verde = sin ver). El visor es como el del celular: barras de progreso, avanza solo, `←`/`→` o clic a los
     costados para pasar, `Espacio` o mantener apretado para pausar, fotos, videos, audios y textos con su fondo.
     Al verlo, el contacto recibe el "visto" (se puede desactivar en Configuración). Se registran los estados que
     llegan mientras MAGSAPP está abierto (`~/.magsapp/estados_contactos.json`). Punto verde en el menú si hay sin ver.

   - **Notificaciones de Windows**: toasts nativos con el nombre y la foto del contacto, el texto (o la miniatura de
     la foto) y botones *Responder* / *Marcar como leído*. Una notificación por chat ("3 mensajes nuevos") que se
     borra sola al abrir el chat. No avisa si estás mirando ese chat (con la ventana al frente en otro chat suena un
     aviso suave), ni de chats silenciados, ni de la tanda de mensajes viejos al abrir la app. Llamadas entrantes con
     aviso propio (se atienden en el celular) y las perdidas quedan en el chat. Respeta "No molestar" de Windows.
     Configurable en *Configuración › Notificaciones y Windows*.

   - **Integración con Windows**: icono en la bandeja con el número de chats sin leer y contador sobre el icono de
     la barra de tareas; la X deja MAGSAPP en segundo plano (salir: bandeja › Salir o `Ctrl+Q`); opción de abrir al
     iniciar Windows (minimizado); acceso "MAGSAPP" en el menú Inicio; protocolo `magsapp://` para que las
     notificaciones abran el chat; una sola instancia (abrirla de nuevo trae la ventana al frente); recuerda tamaño y
     posición. Se apaga y cierra sesión de Windows sin trabarse.

   - **Chats**: fijar chats y silenciar notificaciones (8 h, 1 semana, siempre) con clic derecho; "en línea" /
     "escribiendo…" del contacto en la cabecera y el tuyo hacia él; los mensajes editados se actualizan con la marca
     *Editado* y los borrados para todos reemplazan al original (antes aparecían como mensajes nuevos). Fotos y audios
     viejos que WhatsApp ya borró del servidor se le piden de nuevo al celular automáticamente.
     Lo que leés en el celular queda leído también en MAGSAPP (en vivo y al reconectar tras un rato sin conexión),
     y si un chat se marca "no leído" en el celular también aparece así. Botón **Marcar leídos** junto a los filtros
     (aparece solo si hay chats sin leer) como red de seguridad.

2. **Vinculación por Código QR**:
   - Diálogo modal con código QR en alta definición generado al vuelo.
   - Instrucciones paso a paso para escanear desde la app oficial de WhatsApp en el celular (Dispositivos vinculados).
   - Persistencia de sesión multi-archivo en disco (`auth_info_baileys`), lo que permite que al reabrir la app se reconecte automáticamente sin volver a pedir QR.
   - Si desvinculás la PC desde el celular, MAGSAPP genera un QR nuevo y te avisa (antes quedaba desconectado).

3. **Robustez**:
   - El gateway se vigila cada 10 s: si se cae o deja de responder, se relanza solo. Si queda abierto un gateway de
     otra versión, se reemplaza; y si la app se cierra de golpe, el gateway se apaga solo (sin procesos huérfanos).
   - Bandeja de salida en disco (`~/.magsapp/outbox.json`): si la ventana no responde, los mensajes se guardan y se
     entregan en orden después. Antes, tras unos reintentos, se perdían.
   - Reconexión con espera creciente (3 s → 60 s) sin internet; errores sueltos de Baileys ya no tumban el gateway.
   - MAGSAPP figura "en línea" solo con la ventana al frente: así el celular sigue recibiendo sus notificaciones.
   - Errores de Python a `~/.magsapp/app.log` (con `pythonw` no hay consola); arranque de Node en `gateway_stderr.log`.

4. **Arquitectura Robusta y Desacoplada**:
   - **Frontend**: PySide6 (Qt para Python) de alto rendimiento, nativo para Windows.
   - **Base de Datos Local**: SQLite en modo WAL (`magsapp.db`) en el perfil del usuario para máxima velocidad y evitar bloqueos de Google Drive.
   - **Gateway Baileys**: Microservicio en Node.js en segundo plano (puerto `5016`) que se comunica con los servidores de WhatsApp mediante WebSockets seguros.
   - **Webhook en Tiempo Real**: Servidor local HTTP (puerto `5015`) que despacha los mensajes y cambios de estado instantáneamente a la UI mediante Qt Signals.

---

## 📁 Estructura del Proyecto

```
MAGSAPP/
├── app/
│   ├── __init__.py
│   ├── config.py             # Configuración de rutas y puertos
│   ├── db.py                 # Capa de base de datos SQLite (chats, mensajes, contactos)
│   ├── gateway_manager.py     # Administrador del proceso Node.js Baileys (instala dependencias solo)
│   ├── ai/ai_service.py       # IA: Gemini, Groq y OpenAI con fallback automático
│   ├── ai/message_summary.py  # Modo resumen: resume e interpreta un mensaje (botón ✨)
│   ├── ai/psych_analysis.py   # Análisis psicológico: Normal / Comerciante / Interno (yo), notas por chat
│   ├── webhook_server.py      # Servidor HTTP local de eventos y señales Qt
│   ├── notifications.py       # Cuándo y cómo avisar (toasts, sonido, llamadas, parpadeo)
│   ├── windows_integration.py # Toasts nativos, magsapp://, menú Inicio, inicio con Windows, contador de barra
│   └── ui/
│       ├── __init__.py
│       ├── theme.py          # Paleta (C), medidas (alto de cabecera, radios) y hoja de estilos QSS global
│       ├── icons.py          # Iconos vectoriales de línea (SVG en memoria, nítidos a cualquier escala) y tool_button()
│       ├── effects.py        # Hover con fundido y menús/tooltips redondeados por Windows 11 (DWM)
│       ├── chat_list.py      # Panel lateral de lista de chats y avatares
│       ├── chat_view.py      # Visor de conversación, burbujas y compositor
│       ├── qr_dialog.py      # Diálogo modal de escaneo de QR y estado
│       ├── contact_dialog.py # Agenda y nuevo chat
│       ├── status_view.py    # Estados: de contactos, activos (vistas, borrar), programar e historial
│       ├── status_viewer.py  # Visor de estados de contactos (estilo celular)
│       ├── settings_view.py  # Configuración (WhatsApp, IA)
│       ├── nav_rail.py / splash_screen.py
│       └── main_window.py    # Ventana principal y splitter
├── gateway/
│   ├── package.json          # Dependencias (@whiskeysockets/baileys, express, qrcode)
│   └── server.js             # Gateway WebSocket y API REST local
├── tests/test_db.py          # Pruebas de la capa SQLite (pytest)
├── main.py                   # Punto de entrada de la aplicación
├── requirements.txt          # Dependencias de Python (PySide6, requests)
├── INSTALAR_DEPENDENCIAS.bat # Instalador: dependencias + accesos directos con icono
├── INICIAR_MAGSAPP.bat       # Lanzador directo de Windows
├── assets/                   # Icono de la app (magsapp.ico/.png) y SVG del tema
├── tools/instalar.py         # Paso final del instalador (gateway, registro, accesos)
├── tools/generar_iconos.py   # Regenera los archivos de assets/
├── tools/build_portable.py   # Arma la versión portable (zip) y, opcional, el instalador
├── tools/instalador.iss      # Script de Inno Setup del instalador
└── README.md                 # Documentación
```

---

## 🛠️ Instalación (después de `git clone`)

```powershell
git clone <url-del-repo> MAGSAPP
```

1. Doble clic en **`INSTALAR_DEPENDENCIAS.bat`** (no hace falta ser administrador). Instala lo que falte:
   - **Python 3.13**, **Node.js LTS** y **FFmpeg** (con `winget`, si no están).
   - Las librerías de Python (`requirements.txt`) y las del gateway de WhatsApp (`npm install`).
   - Registra MAGSAPP en Windows (nombre e icono en las notificaciones, protocolo `magsapp://`).
   - Crea el acceso directo **MAGSAPP** con su icono en el **escritorio** y en el **menú Inicio**
     (con la API de Windows: `IShellLink` + AppUserModelID, para que la barra de tareas y las notificaciones
     muestren MAGSAPP y no Python).
2. Abrí MAGSAPP desde el icono del escritorio (o buscando "MAGSAPP" en el menú Inicio).

Se puede volver a ejecutar las veces que haga falta (por ejemplo, si moviste la carpeta: los accesos se actualizan).
El icono está en `assets/magsapp.ico`; para cambiar el diseño, editá y ejecutá `python tools/generar_iconos.py`.

## 📦 Versión para compartir (portable)

Para pasarle MAGSAPP a alguien que no tiene Python ni nada instalado:

```powershell
python tools/build_portable.py
```

Genera `dist/MAGSAPP-<versión>.zip` (y la carpeta `dist/MAGSAPP/`). Adentro va todo: `MAGSAPP.exe` con Python y
las librerías, Node.js con el gateway ya instalado y FFmpeg. Quien lo recibe descomprime la carpeta donde quiera y
abre `MAGSAPP.exe`; la primera vez la app crea su icono en el escritorio y en el menú Inicio. El zip incluye un
`LEEME.txt` con esos pasos.

- **Versión**: sale de `APP_VERSION` en `app/config.py`; subila antes de armar una versión nueva.
- **Actualizar**: el amigo cierra MAGSAPP (bandeja › Salir) y reemplaza la carpeta. Los chats y la sesión de
  WhatsApp viven en `~/.magsapp`, así que no se pierden.
- **Aviso de Windows**: como el `.exe` no está firmado, la primera vez aparece "Windows protegió su PC"
  → *Más información* → *Ejecutar de todas formas*.
- El build usa un entorno aparte (`build/venv`) con solo `requirements.txt`, para no arrastrar otras librerías de
  la PC. Requiere Python, Node.js/npm y FFmpeg en la PC que arma la versión.
- **Tiempos**: la primera vez ~10 min; después ~2 min, porque reutiliza lo guardado en `build/` (el venv se rehace
  solo si cambia `requirements.txt` y `npm ci` solo si cambia `gateway/package.json`). Para empezar de cero, borrá
  `build/`.
- **Probar sin cerrar tu MAGSAPP**: con `MAGSAPP_WEBHOOK_PORT=5115` y `MAGSAPP_GATEWAY_PORT=5116` corre una copia de
  prueba que no choca con la abierta ni toca accesos directos ni registro (usá también otro `USERPROFILE` para que
  tenga datos propios).
- **Instalador (opcional)**: `python tools/build_portable.py --instalador` arma además
  `dist/MAGSAPP-Setup-<versión>.exe` con Inno Setup (`winget install JRSoftware.InnoSetup`): instala sin pedir
  administrador, crea los accesos, aparece en "Aplicaciones" para desinstalar y, corrido encima de una versión
  anterior, la actualiza. Script: `tools/instalador.iss`.

## 🏃 Cómo Iniciar la Aplicación

Desde el acceso directo del escritorio, o con doble clic en `INICIAR_MAGSAPP.bat`
(si faltan dependencias, abre el instalador). O desde la consola:
```powershell
python main.py
```

### Vincular tu cuenta:
1. Al iniciar la aplicación por primera vez, se abrirá la ventana con el código QR.
2. Abre WhatsApp en tu celular > **Ajustes** > **Dispositivos vinculados** > **Vincular un dispositivo**.
3. Apunta la cámara al código QR de la pantalla.
4. ¡Listo! Verás el indicador verde de **Conectado** y tus conversaciones comenzarán a sincronizarse.

---

## 🔒 Seguridad y datos

- El gateway (`5016`) y el webhook (`5015`) escuchan solo en `127.0.0.1` y exigen el header `X-Magsapp-Token`
  (excepto `GET /status`). El token se genera una vez en `~/.magsapp/gateway.token`.
- Los datos viven en `~/.magsapp/` (base de datos, sesión, media, logs). Las API keys de IA se guardan en
  texto plano en `~/.magsapp/config.ini`; si faltan, se heredan de `MAGNETO/config.ini`.
- Los análisis psicológicos viven en `~/.magsapp/analisis/<chat>/` (`notas.txt` acumulativo y editable +
  `ultimo_<enfoque>.txt`); el análisis interno (botón 🧠 del rail) en `~/.magsapp/analisis/_yo/`.
- En el primer arranque se ejecuta `npm install` automáticamente (requiere Node.js y npm en el PATH).
  El log queda en `~/.magsapp/npm_install.log`.

## 🧪 Pruebas

```powershell
pip install pytest
python -m pytest tests -q
```
