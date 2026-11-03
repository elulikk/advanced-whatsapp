"""
Advanced WhatsApp - Paso final de la instalación (lo llama INSTALAR_DEPENDENCIAS.bat)
==========================================================================
1. Comprueba que las librerías de Python quedaron instaladas.
2. Prepara el gateway de WhatsApp (copia a ~/.magsapp/gateway y corre npm install).
3. Registra Advanced WhatsApp en Windows (nombre e icono en notificaciones, protocolo advancedwhatsapp://).
4. Crea los accesos directos con el icono de Advanced WhatsApp en el escritorio y en el menú Inicio.

Devuelve 0 si todo salió bien, 1 si falta algo imprescindible.
"""

import importlib
import os
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

REQUIRED = [("PySide6", "PySide6"), ("requests", "requests"), ("win32com.shell", "pywin32")]
OPTIONAL = [("google.generativeai", "google-generativeai (IA con Gemini)")]


def ok(msg):
    print(f"[OK] {msg}")


def warn(msg):
    print(f"[WARN] {msg}")


def error(msg):
    print(f"[ERROR] {msg}")


def check_python_packages() -> bool:
    good = True
    for module, name in REQUIRED:
        try:
            importlib.import_module(module)
        except Exception as e:
            error(f"Falta {name}: {e}")
            good = False
    for module, name in OPTIONAL:
        try:
            importlib.import_module(module)
        except Exception:
            warn(f"No se pudo cargar {name}; el resto de Advanced WhatsApp funciona igual.")
    if good:
        ok("Librerías de Python listas.")
    return good


def prepare_gateway() -> bool:
    from app.gateway_manager import GatewayManager, find_node_executable
    if not find_node_executable():
        error("No se encontró Node.js. Instalalo desde https://nodejs.org/ y volvé a correr el instalador.")
        return False
    print("Instalando dependencias del gateway de WhatsApp (npm install, puede tardar unos minutos)...")
    gm = GatewayManager.get_instance()
    gm.ensure_runtime_files()
    err = gm.ensure_dependencies(pkg_changed=True)
    if err:
        error(err)
        return False
    ok("Gateway de WhatsApp listo.")
    return True


def integrate_with_windows() -> bool:
    from app import windows_integration as wi
    if not os.path.exists(wi.ICON_ICO):
        warn(f"No está el icono {wi.ICON_ICO}; los accesos directos quedan con el icono de Python.")
    wi.register_app_identity()
    wi.register_protocol()
    good = True
    if wi.ensure_desktop_shortcut():
        ok("Acceso directo \"Advanced WhatsApp\" creado en el escritorio.")
    else:
        error("No se pudo crear el acceso directo del escritorio.")
        good = False
    if wi.ensure_start_menu_shortcut():
        ok("Advanced WhatsApp agregado al menú Inicio (buscá \"Advanced WhatsApp\").")
    else:
        error("No se pudo crear el acceso del menú Inicio.")
        good = False
    wi.refresh_autostart_command()
    return good


def main() -> int:
    if sys.platform != "win32":
        error("Este instalador es solo para Windows.")
        return 1
    results = [check_python_packages()]
    if results[0]:
        results.append(prepare_gateway())
        results.append(integrate_with_windows())
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
