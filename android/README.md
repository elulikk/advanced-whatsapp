# Advanced WhatsApp para Android

App independiente: no necesita la PC. Lleva adentro Node.js (`nodejs-mobile`) y el mismo gateway de escritorio
(`gateway/server.js` con Baileys), y se vincula a tu cuenta como un dispositivo más, igual que WhatsApp Web.

## Instalar en el celular

1. En el celular activá **Opciones de desarrollador › Depuración por USB** y conectalo a la PC.
2. Doble clic en **`INSTALAR_EN_CELULAR.bat`** y aceptá los avisos que aparezcan en el celular.
3. En la app elegí **Con código**, escribí tu número con código de país y tocá *Obtener código*.
4. En WhatsApp: **Dispositivos vinculados › Vincular un dispositivo › Vincular con el número de teléfono**, y escribí el código.
5. Tocá el aviso de la lista de chats para permitir que la app funcione en segundo plano.

Requiere Android 10 o superior y un celular de 64 bits (`arm64-v8a`).

## Preparar una PC nueva para compilar

`app/libnode/` no está en el repositorio (pesa más de 100 MB). Descargá
[`nodejs-mobile-v18.20.4-android.zip`](https://github.com/nodejs-mobile/nodejs-mobile/releases/tag/v18.20.4) y extraé
`bin/arm64-v8a`, `bin/x86_64` e `include` dentro de `android/app/libnode/`. Hace falta Android Studio (trae Java y el SDK);
Gradle baja solo el NDK y CMake.

```powershell
.\gradlew.bat testDebugUnitTest      # pruebas de la lógica de IA
.\gradlew.bat assembleRelease        # app\build\outputs\apk\release\
```

## Qué hay adentro

| Parte | Dónde | Equivale en la PC a |
|---|---|---|
| Gateway (proceso `:node`) | `gateway/server.js` + `app/src/main/node/android_main.mjs` | el mismo `gateway/server.js` |
| Eventos del gateway | `gateway/WebhookServer.kt` | `app/webhook_server.py` |
| Base de datos | `data/Db.kt` | `app/db.py` |
| IA | `ai/` | `app/ai/` (mismos textos para la IA) |
| Pantallas | `ui/` (Jetpack Compose) | `app/ui/` |

- **`android_main.mjs`**: `nodejs-mobile` trae Node 18 sin ICU y Baileys 7 pide Node 20. Este archivo completa lo que falta
  (`crypto` global, `TextDecoder` con `fatal`, `File`, etc.) antes de cargar Baileys.
- **Procesos**: Node solo puede arrancar una vez por proceso, por eso va en `:node`. Si termina, Android lo reinicia; si deja de
  responder, `GatewayService` lo mata para que se reinicie.
- **Seguridad**: gateway y webhook escuchan solo en `127.0.0.1`, pero otras apps del celular alcanzan esos puertos: todo exige un
  token aleatorio, también `/status`. Los datos y las claves de IA quedan en la carpeta privada de la app, sin copia de seguridad.
- **Datos separados**: la app tiene su propia sesión y base; no comparte chats, notas ni análisis con la PC.

## Limitaciones de esta versión

- Sin tags. Los estados de los contactos se pueden ver, pero todavía no se publican desde el celular. Como en la PC, los grupos no
  se muestran.
- La cámara abre la app de cámara del celular, por eso la app no pide ese permiso. Solo saca fotos: los videos se eligen de la galería.
- Las llamadas solo avisan: se atienden en la app oficial.
- Firmada con la clave de depuración de la PC que compila: sirve para instalar directo, no para Play Store. Compilar desde otra PC
  exige desinstalar antes, y eso borra los datos.
- La librería de Node solo está alineada a páginas de memoria de 4 KB; en un celular de 16 KB podría no arrancar.
- Mantener la conexión siempre activa gasta más batería; se puede apagar en Ajustes, y entonces los mensajes pueden demorarse con la
  pantalla apagada.
