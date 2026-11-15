"""
Publica una versión en GitHub Releases con `gh release create` (la app la encuentra sola desde Ajustes).

Convención (ver docs/ARQUITECTURA.md, «Actualizaciones»):
    PC       tag pc-v<APP_VERSION>          asset AdvancedWhatsapp-Setup-<ver>.exe     (+ .sha256)
    Android  tag android-v<versionName>     asset AdvancedWhatsapp-Android-<ver>.apk   (+ .sha256)

Antes hay que armar los artefactos:
    python tools/build_portable.py --instalador          -> dist/AdvancedWhatsapp-Setup-<ver>.exe
    cd android; .\\gradlew.bat assembleRelease            -> app/build/outputs/apk/release/app-arm64-v8a-release.apk

Uso:
    python tools/publicar_release.py pc                  # muestra lo que haría (no publica)
    python tools/publicar_release.py pc --publicar       # crea el release de verdad
    python tools/publicar_release.py android --publicar
    python tools/publicar_release.py pc --notas notas.md --repo dueño/repo --apk ruta.apk

Sin --publicar es un ensayo: arma los .sha256 en dist/ y imprime el comando de `gh`. Requiere `gh auth login`.
Las notas salen de --notas o, si no hay, de la sección "Sin publicar" (o la primera) de CHANGELOG.md.
"""

import argparse
import hashlib
import os
import re
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIST = os.path.join(ROOT, "dist")
DEFAULT_REPO = "elulikk/advanced-whatsapp"
APK_DEFAULT = os.path.join(ROOT, "android", "app", "build", "outputs", "apk", "release", "app-arm64-v8a-release.apk")


def leer(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def version_pc() -> str:
    m = re.search(r'^APP_VERSION\s*=\s*"([^"]+)"', leer(os.path.join(ROOT, "app", "config.py")), re.M)
    if not m:
        sys.exit("No se encontró APP_VERSION en app/config.py")
    return m.group(1)


def version_android() -> str:
    m = re.search(r'versionName\s*=\s*"([^"]+)"', leer(os.path.join(ROOT, "android", "app", "build.gradle.kts")))
    if not m:
        sys.exit("No se encontró versionName en android/app/build.gradle.kts")
    return m.group(1)


def notas_del_changelog() -> str:
    """La sección '## Sin publicar…' (o la primera) de CHANGELOG.md."""
    try:
        texto = leer(os.path.join(ROOT, "CHANGELOG.md"))
    except OSError:
        return ""
    secciones = re.split(r"(?m)^(?=## )", texto)
    secciones = [s for s in secciones if s.startswith("## ")]
    if not secciones:
        return ""
    elegida = next((s for s in secciones if s.lower().startswith("## sin publicar")), secciones[0])
    return elegida.strip()


def sha256_de(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for bloque in iter(lambda: f.read(1 << 20), b""):
            h.update(bloque)
    return h.hexdigest()


def main():
    ap = argparse.ArgumentParser(description="Publica un release de Advanced WhatsApp en GitHub.")
    ap.add_argument("plataforma", choices=["pc", "android"])
    ap.add_argument("--publicar", action="store_true", help="crear el release de verdad (sin esto es un ensayo)")
    ap.add_argument("--repo", default=DEFAULT_REPO, help=f"dueño/repo (por defecto {DEFAULT_REPO})")
    ap.add_argument("--notas", help="archivo .md con las notas del release")
    ap.add_argument("--apk", default=APK_DEFAULT, help="APK de Android a subir (por defecto el arm64-v8a de release)")
    ap.add_argument("--borrador", action="store_true", help="crearlo como borrador (no lo ven las apps hasta publicarlo)")
    args = ap.parse_args()

    if args.plataforma == "pc":
        ver = version_pc()
        origen = os.path.join(DIST, f"AdvancedWhatsapp-Setup-{ver}.exe")
        nombre = f"AdvancedWhatsapp-Setup-{ver}.exe"
        tag = f"pc-v{ver}"
        titulo = f"Advanced WhatsApp {ver} (PC)"
    else:
        ver = version_android()
        origen = args.apk
        nombre = f"AdvancedWhatsapp-Android-{ver}.apk"
        tag = f"android-v{ver}"
        titulo = f"Advanced WhatsApp {ver} (Android)"

    if not os.path.isfile(origen):
        sys.exit(f"Falta el artefacto: {origen}\nArmalo primero (ver el encabezado de este script).")

    os.makedirs(DIST, exist_ok=True)
    archivo = os.path.join(DIST, nombre)
    if os.path.abspath(origen) != os.path.abspath(archivo):
        shutil.copyfile(origen, archivo)
    sha_path = archivo + ".sha256"
    with open(sha_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(f"{sha256_de(archivo)}  {nombre}\n")

    notas = leer(args.notas) if args.notas else notas_del_changelog()
    notas_path = os.path.join(DIST, f"notas-{tag}.md")
    with open(notas_path, "w", encoding="utf-8") as f:
        f.write(notas or f"Versión {ver}.")

    cmd = ["gh", "release", "create", tag, archivo, sha_path, "--repo", args.repo,
           "--title", titulo, "--notes-file", notas_path]
    if args.borrador:
        cmd.append("--draft")
    print("Release:", tag, "·", titulo)
    print("Archivos:", archivo, "+ .sha256")
    print("Comando:", " ".join(f'"{c}"' if " " in c else c for c in cmd))
    if not args.publicar:
        print("\nEnsayo: no se publicó nada. Agregá --publicar para crear el release.")
        return
    if shutil.which("gh") is None:
        sys.exit("No se encontró GitHub CLI (gh). Instalalo con: winget install GitHub.cli  y corré: gh auth login")
    sys.exit(subprocess.call(cmd))


if __name__ == "__main__":
    main()
