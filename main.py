import sys
import os
import time

# Asegurar codificación UTF-8 en Windows
if sys.stdout is not None and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# Agregar ruta base al sys.path
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

ARGS = sys.argv[1:]
LAUNCH_URI = next((a for a in ARGS if a.lower().startswith("advancedwhatsapp:")), None)
BACKGROUND = "--background" in ARGS


def _setup_logging():
    """Con pythonw no hay consola: los print() y los errores no capturados van a ~/.advancedwhatsapp/app.log."""
    from app.config import DATA_DIR
    log_path = os.path.join(DATA_DIR, "app.log")
    try:
        if os.path.getsize(log_path) > 2 * 1024 * 1024:
            os.replace(log_path, log_path + ".old")
    except OSError:
        pass
    try:
        log = open(log_path, "a", encoding="utf-8", errors="replace", buffering=1)
    except OSError:
        return
    if sys.stdout is None or not sys.stdout.isatty():
        sys.stdout = log
    if sys.stderr is None or not sys.stderr.isatty():
        sys.stderr = log
    print(f"\n===== Advanced WhatsApp iniciado {time.strftime('%Y-%m-%d %H:%M:%S')} (pid {os.getpid()}) =====")

    import traceback
    import threading

    def _hook(exc_type, exc, tb):
        print("[Advanced WhatsApp] Error no capturado:\n" + "".join(traceback.format_exception(exc_type, exc, tb)))
    sys.excepthook = _hook
    threading.excepthook = lambda a: _hook(a.exc_type, a.exc_value, a.exc_traceback)
    try:
        import faulthandler
        faulthandler.enable(log)   # cuelgues duros de Qt/C también dejan rastro
    except Exception:
        pass


# Una sola Advanced WhatsApp: si ya hay una abierta, se le pasa el pedido (mostrar / abrir un chat) y esta se cierra.
# Se hace antes de cargar Qt para que el clic en una notificación responda rápido.
from app.windows_integration import acquire_single_instance, forward_to_running_instance, APP_ID
from app.config import DATA_DIR

PID_FILE = os.path.join(DATA_DIR, "app.pid")


def _log_relaunch(text: str):
    """La segunda instancia no abre el log completo: deja una línea para saber qué pasó al reabrir."""
    try:
        with open(os.path.join(DATA_DIR, "app.log"), "a", encoding="utf-8", errors="replace") as f:
            f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] [reabrir pid {os.getpid()}] {text}\n")
    except OSError:
        pass


if not acquire_single_instance():
    if not (BACKGROUND and not LAUNCH_URI):   # el inicio automático no debe traer al frente la ventana
        t0 = time.monotonic()
        ok = forward_to_running_instance(LAUNCH_URI or "advancedwhatsapp://show")
        _log_relaunch(f"{'ok' if ok else 'SIN RESPUESTA'} en {time.monotonic() - t0:.1f}s ({LAUNCH_URI or 'show'})")
        if not ok and not LAUNCH_URI:
            try:
                with open(PID_FILE, encoding="utf-8") as f:
                    running = f.read().strip()
            except OSError:
                running = "?"
            try:
                import ctypes
                # MB_ICONWARNING | MB_SETFOREGROUND | MB_TOPMOST: con pythonw el aviso quedaba escondido detrás
                ctypes.windll.user32.MessageBoxW(
                    None, "Advanced WhatsApp ya está abierto pero no responde.\n\nCerralo desde el Administrador de tareas "
                    f"(pestaña Detalles, proceso pythonw.exe con PID {running}; ojo que otras apps de Python "
                    "también se llaman pythonw.exe) y volvé a abrirlo.", "Advanced WhatsApp", 0x30 | 0x10000 | 0x40000)
            except Exception:
                pass
    sys.exit(0)

try:
    with open(PID_FILE, "w", encoding="utf-8") as f:
        f.write(str(os.getpid()))
except OSError:
    pass

_setup_logging()

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QIcon

from app.windows_integration import ICON_ICO as ICON_PATH


def _set_windows_app_id():
    """Identidad propia en Windows: icono propio en barra de tareas y administrador de tareas."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
    except Exception:
        pass


from app.db import init_db
from app.ui.splash_screen import SplashScreen
from app.ui.main_window import MainWindow


class _NoSplash:
    """En el inicio con Windows no se muestra la pantalla de carga."""
    def set_status(self, *_):
        pass

    def show(self):
        pass

    def close(self):
        pass


def _wait_for_whatsapp(app, splash, window, timeout: float = 60.0, quiet: float = 3.0, max_sync: float = 20.0) -> bool:
    """
    Espera en el splash a que el gateway conecte y a que lleguen los mensajes pendientes.
    Devuelve True solo si hace falta escanear un QR.
    """
    from app.webhook_server import signals

    last_activity = [time.monotonic()]

    def _touch(*_):
        last_activity[0] = time.monotonic()

    signals.message_received.connect(_touch)
    signals.history_synced.connect(_touch)

    start = time.monotonic()
    connected_at = None
    needs_link = False
    try:
        while True:
            app.processEvents()
            now = time.monotonic()
            st = window.gateway.get_status()
            status = st.get("status")

            if status == "connected":
                if connected_at is None:
                    connected_at = now
                    _touch()
                # Esperar a que se calme la llegada de mensajes pendientes
                if now - last_activity[0] >= quiet and now - connected_at >= quiet:
                    break
                if now - connected_at >= max_sync:
                    break
                splash.set_status("Sincronizando mensajes nuevos...", 90)
            elif status == "qr_ready" or st.get("has_qr"):
                needs_link = True
                break
            elif now - start >= timeout:
                break
            elif window.gateway.last_error and now - start > 3:
                splash.set_status(f"Problema con el servicio de WhatsApp: {window.gateway.last_error[:60]}", 70)
            else:
                splash.set_status("Conectando con WhatsApp...", 70)

            time.sleep(0.25)
    finally:
        signals.message_received.disconnect(_touch)
        signals.history_synced.disconnect(_touch)

    # Refrescar la lista con lo que llegó durante la espera
    window.chat_list_panel.reload_chats()
    return needs_link


def main():
    # Soporte High-DPI en pantallas modernas
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )

    _set_windows_app_id()
    app = QApplication(sys.argv)
    app.setApplicationName("Advanced WhatsApp")
    app.setOrganizationName("MagTecnologia")
    app.setQuitOnLastWindowClosed(False)   # la ventana puede quedar oculta en la bandeja
    if os.path.exists(ICON_PATH):
        app.setWindowIcon(QIcon(ICON_PATH))

    # Registro en Windows (identidad, advancedwhatsapp://, menú Inicio) sin demorar el arranque
    import threading
    from app import windows_integration
    threading.Thread(target=lambda: (windows_integration.integrate_with_windows(),
                                     windows_integration.refresh_autostart_command()),
                     daemon=True, name="advancedwhatsapp-win-integration").start()

    # 1. Mostrar SplashScreen inmediatamente (salvo en el inicio con Windows)
    splash = _NoSplash() if BACKGROUND else SplashScreen()
    splash.show()
    app.processEvents()

    # 2. Inicializar SQLite
    splash.set_status("Iniciando base de datos...", 30)
    app.processEvents()
    init_db()

    # 3. Cargar interfaz principal y chats
    splash.set_status("Cargando tus mensajes...", 50)
    app.processEvents()
    window = MainWindow(start_hidden=BACKGROUND)
    app.commitDataRequest.connect(window.on_session_ending)
    app.aboutToQuit.connect(window.shutdown)

    # 4. Conectar con WhatsApp dentro de la pantalla de carga (sin diálogos intermedios)
    needs_link = _wait_for_whatsapp(app, splash, window)

    # 5. Transición a MainWindow
    splash.set_status("¡Listo!", 100)
    app.processEvents()
    if not BACKGROUND:
        time.sleep(0.3)
        window.restore_geometry_and_show()
    splash.close()
    window.after_startup()
    if needs_link:
        if BACKGROUND:
            window.notifications.notify_simple(
                "Advanced WhatsApp necesita que vuelvas a vincular WhatsApp",
                "Tocá acá y escaneá el código QR con tu celular.", uri="advancedwhatsapp://link", tag="link", sound=True)
        else:
            # Solo si WhatsApp pide vincular de verdad (sesión cerrada o primera vez)
            QTimer.singleShot(200, window._open_qr_dialog)
    if LAUNCH_URI:
        QTimer.singleShot(300, lambda: window.handle_uri(LAUNCH_URI))

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
