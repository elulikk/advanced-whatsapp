"""
Advanced WhatsApp - Arma la versión portable para compartir
=================================================
Genera dist/AdvancedWhatsapp/ (AdvancedWhatsapp.exe + _internal) y dist/AdvancedWhatsapp-<versión>.zip. La carpeta trae todo:
Python y librerías (PyInstaller), Node.js con el gateway ya instalado y FFmpeg. Quien la recibe
la descomprime, abre AdvancedWhatsapp.exe y listo; no necesita instalar nada.

Requisitos en la PC que arma la versión (no en la de quien la usa): Python, Node.js/npm y FFmpeg
(winget install Gyan.FFmpeg). PyInstaller y las librerías se instalan solos en build/venv, un entorno
aparte, para que en la carpeta no se cuele nada más que tenga instalado esta PC.

Uso:
    python tools/build_portable.py            (la versión sale de APP_VERSION en app/config.py)
    python tools/build_portable.py --instalador   (además arma dist/AdvancedWhatsapp-Setup-<versión>.exe con Inno Setup)
"""

import glob
import os
import re
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BUILD = os.path.join(ROOT, "build")
STAGING = os.path.join(BUILD, "staging")
DIST = os.path.join(ROOT, "dist")
APP_DIR = os.path.join(DIST, "AdvancedWhatsapp")

# Módulos que nunca usa Advanced WhatsApp y que PyInstaller podría arrastrar
EXCLUDES = ["tkinter", "pytest", "_pytest", "matplotlib", "numpy.tests", "IPython",
            "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtQuick", "PySide6.QtQml",
            "PySide6.Qt3DCore", "PySide6.QtCharts", "PySide6.QtDataVisualization", "PySide6.QtPdf"]

LEEME = """Advanced WhatsApp {version}
================

1. Descomprimí esta carpeta donde quieras (por ejemplo en Documentos).
   No abras Advanced WhatsApp desde adentro del .zip: primero "Extraer todo".

2. Abrí AdvancedWhatsapp.exe. La primera vez crea el icono en el escritorio y en el menú Inicio.
   Si Windows muestra "Windows protegió su PC": tocá "Más información" y después
   "Ejecutar de todas formas" (pasa porque la app no está firmada; es solo la primera vez).

3. Vinculá tu WhatsApp: en el celular, Ajustes > Dispositivos vinculados >
   Vincular un dispositivo, y escaneá el código QR que muestra Advanced WhatsApp.

Actualizar a una versión nueva:
   cerrá Advanced WhatsApp (icono junto al reloj > Salir) y reemplazá esta carpeta por la nueva.
   Tus chats y la sesión de WhatsApp no se pierden: se guardan en tu usuario (carpeta .magsapp).
"""


def step(msg):
    print(f"\n=== {msg}")


def app_version() -> str:
    with open(os.path.join(ROOT, "app", "config.py"), encoding="utf-8") as f:
        m = re.search(r'^APP_VERSION\s*=\s*"([^"]+)"', f.read(), re.M)
    if not m:
        sys.exit("No se encontró APP_VERSION en app/config.py")
    return m.group(1)


def find_ffmpeg() -> str:
    found = shutil.which("ffmpeg")
    local = os.environ.get("LOCALAPPDATA", "")
    candidates = ([found] if found else []) + [os.path.join(local, "Microsoft", "WinGet", "Links", "ffmpeg.exe")]
    candidates += glob.glob(os.path.join(local, "Microsoft", "WinGet", "Packages", "*FFmpeg*", "*", "bin", "ffmpeg.exe"))
    candidates += [r"C:\ffmpeg\bin\ffmpeg.exe"]
    for c in candidates:
        if c and os.path.isfile(c):
            return os.path.realpath(c)
    sys.exit("No se encontró FFmpeg. Instalalo con: winget install Gyan.FFmpeg")


def run(cmd, cwd=None):
    print(">", " ".join(cmd))
    r = subprocess.run(cmd, cwd=cwd, shell=False)
    if r.returncode != 0:
        sys.exit(f"Falló: {' '.join(cmd)}")


def _file_hash(*paths) -> str:
    import hashlib
    h = hashlib.sha1()
    for p in paths:
        if os.path.exists(p):
            with open(p, "rb") as f:
                h.update(f.read())
    return h.hexdigest()


def stage_gateway():
    """server.js se copia siempre; npm ci solo corre si cambiaron las dependencias (package.json / lock)."""
    dest = os.path.join(STAGING, "gateway")
    os.makedirs(dest, exist_ok=True)
    pkg = [os.path.join(ROOT, "gateway", n) for n in ("package.json", "package-lock.json")]
    for name in ("server.js", "extras.js"):
        shutil.copy2(os.path.join(ROOT, "gateway", name), dest)
    stamp = os.path.join(dest, ".deps.sha1")
    deps_hash = _file_hash(*pkg)
    try:
        with open(stamp, encoding="utf-8") as f:
            cached = f.read().strip() == deps_hash and os.path.isdir(os.path.join(dest, "node_modules"))
    except OSError:
        cached = False
    if cached:
        step("Gateway de WhatsApp: dependencias sin cambios (se reutilizan)")
        return
    step("Gateway de WhatsApp: npm ci (solo dependencias de producción)")
    shutil.rmtree(os.path.join(dest, "node_modules"), ignore_errors=True)
    for p in pkg:
        if os.path.exists(p):
            shutil.copy2(p, dest)
    npm = shutil.which("npm") or sys.exit("No se encontró npm (instalá Node.js).")
    has_lock = os.path.exists(os.path.join(dest, "package-lock.json"))
    run([npm, "ci" if has_lock else "install", "--omit=dev", "--no-audit", "--no-fund"], cwd=dest)
    with open(stamp, "w", encoding="utf-8") as f:
        f.write(deps_hash)


def stage_runtime():
    step("Node.js y FFmpeg incluidos")
    dest = os.path.join(STAGING, "runtime")
    os.makedirs(dest, exist_ok=True)
    node = shutil.which("node") or sys.exit("No se encontró node.exe (instalá Node.js).")
    shutil.copy2(os.path.realpath(node), os.path.join(dest, "node.exe"))
    shutil.copy2(find_ffmpeg(), os.path.join(dest, "ffmpeg.exe"))
    out = subprocess.run([os.path.join(dest, "node.exe"), "--version"], capture_output=True, text=True).stdout.strip()
    print(f"Node {out}  |  FFmpeg {find_ffmpeg()}")


def build_python() -> str:
    """Entorno virtual limpio con solo requirements.txt: así no se cuela nada más que haya instalado en esta PC."""
    import hashlib
    venv = os.path.join(BUILD, "venv")
    py = os.path.join(venv, "Scripts", "python.exe")
    with open(os.path.join(ROOT, "requirements.txt"), "rb") as f:
        req_hash = hashlib.sha1(f.read()).hexdigest()
    stamp = os.path.join(venv, "requirements.sha1")
    try:
        with open(stamp, encoding="utf-8") as f:
            if f.read().strip() == req_hash and os.path.isfile(py):
                return py
    except OSError:
        pass
    step("Entorno de compilación (venv con requirements.txt + PyInstaller)")
    shutil.rmtree(venv, ignore_errors=True)
    run([sys.executable, "-m", "venv", venv])
    run([py, "-m", "pip", "install", "--disable-pip-version-check", "-q", "--upgrade", "pip"])
    run([py, "-m", "pip", "install", "--disable-pip-version-check", "-q",
         "-r", os.path.join(ROOT, "requirements.txt"), "pyinstaller"])
    with open(stamp, "w", encoding="utf-8") as f:
        f.write(req_hash)
    return py


def pyinstaller(py: str):
    step("PyInstaller: AdvancedWhatsapp.exe")
    sep = os.pathsep
    args = [py, "-m", "PyInstaller", "--noconfirm", "--windowed",
            "--name", "AdvancedWhatsapp", "--icon", os.path.join(ROOT, "assets", "advancedwhatsapp.ico"),
            "--distpath", DIST, "--workpath", os.path.join(BUILD, "work"), "--specpath", BUILD,
            "--add-data", f"{os.path.join(ROOT, 'assets')}{sep}assets",
            "--add-data", f"{os.path.join(STAGING, 'gateway')}{sep}gateway",
            "--add-data", f"{os.path.join(STAGING, 'runtime')}{sep}runtime",
            "--collect-submodules", "app"]
    for mod in EXCLUDES:
        args += ["--exclude-module", mod]
    run(args + [os.path.join(ROOT, "main.py")], cwd=ROOT)


def prune():
    """Saca lo que PyInstaller copia pero Advanced WhatsApp nunca usa."""
    # Descripciones de todas las APIs de Google (~110 MB): Gemini descarga la suya de internet al usarla
    shutil.rmtree(os.path.join(APP_DIR, "_internal", "googleapiclient", "discovery_cache", "documents"),
                  ignore_errors=True)


def package(version: str) -> str:
    step("Zip para compartir")
    with open(os.path.join(APP_DIR, "LEEME.txt"), "w", encoding="utf-8-sig", newline="\r\n") as f:
        f.write(LEEME.format(version=version))
    base = os.path.join(DIST, f"AdvancedWhatsapp-{version}")
    zip_path = shutil.make_archive(base, "zip", root_dir=DIST, base_dir="AdvancedWhatsapp")
    return zip_path


def installer(version: str) -> str:
    step("Instalador (Inno Setup)")
    iscc = shutil.which("iscc") or next(
        (p for p in (os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Inno Setup 6", "ISCC.exe"),
                     r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe", r"C:\Program Files\Inno Setup 6\ISCC.exe")
         if os.path.isfile(p)), None)
    if not iscc:
        sys.exit("No se encontró Inno Setup. Instalalo con: winget install JRSoftware.InnoSetup")
    run([iscc, "/Qp", f"/DAppVersion={version}", f"/DSourceDir={APP_DIR}", f"/DOutputDir={DIST}",
         os.path.join(ROOT, "tools", "instalador.iss")])
    return os.path.join(DIST, f"AdvancedWhatsapp-Setup-{version}.exe")


def size_mb(path: str) -> float:
    if os.path.isfile(path):
        return os.path.getsize(path) / 1e6
    return sum(os.path.getsize(os.path.join(d, f)) for d, _, fs in os.walk(path) for f in fs) / 1e6


def main():
    import time
    t0 = time.monotonic()
    version = app_version()
    print(f"Advanced WhatsApp {version}")
    # Lo armado antes en build/ se reutiliza (venv, node_modules, caché de PyInstaller).
    # Para empezar de cero: borrar la carpeta build/.
    shutil.rmtree(APP_DIR, ignore_errors=True)
    shutil.rmtree(os.path.join(STAGING, "runtime"), ignore_errors=True)
    stage_gateway()
    stage_runtime()
    pyinstaller(build_python())
    prune()
    zip_path = package(version)
    print(f"\nCarpeta: {APP_DIR}  ({size_mb(APP_DIR):.0f} MB)")
    print(f"Zip:     {zip_path}  ({size_mb(zip_path):.0f} MB)")
    if "--instalador" in sys.argv:
        setup = installer(version)
        print(f"Instalador: {setup}  ({size_mb(setup):.0f} MB)")
    print(f"Listo en {(time.monotonic() - t0) / 60:.1f} min")


if __name__ == "__main__":
    main()
