@echo off
rem Instala Advanced WhatsApp en el celular conectado por USB (compila solo si hace falta).
setlocal
cd /d "%~dp0"
set "ADB=%LOCALAPPDATA%\Android\Sdk\platform-tools\adb.exe"
if not exist "%ADB%" set "ADB=adb"
if "%JAVA_HOME%"=="" set "JAVA_HOME=C:\Program Files\Android\Android Studio\jbr"

"%ADB%" start-server >nul 2>nul
set "ABI="
for /f "delims=" %%a in ('call "%ADB%" -d shell getprop ro.product.cpu.abi 2^>nul') do set "ABI=%%a"
if "%ABI%"=="" (
    echo.
    echo No se detecta el celular. Revisa que:
    echo   1. Este conectado por USB y desbloqueado.
    echo   2. Tenga activada la Depuracion por USB ^(Ajustes ^> Opciones de desarrollador^).
    echo   3. Hayas aceptado el aviso "Permitir depuracion por USB" en la pantalla del celular.
    pause
    exit /b 1
)
if not "%ABI%"=="arm64-v8a" (
    echo.
    echo Este celular es %ABI%: la app solo esta preparada para arm64-v8a.
    pause
    exit /b 1
)

set "PAGE="
for /f "delims=" %%a in ('call "%ADB%" -d shell getconf PAGE_SIZE 2^>nul') do set "PAGE=%%a"
if not "%PAGE%"=="4096" (
    echo.
    echo AVISO: este celular usa paginas de memoria de %PAGE% bytes y Node.js para movil esta compilado para 4096.
    echo Se instala igual, pero el servicio de WhatsApp podria no arrancar.
    echo.
)

echo Compilando la app...
call gradlew.bat assembleRelease --console=plain -q
if errorlevel 1 (
    echo La compilacion fallo.
    pause
    exit /b 1
)

echo Instalando en el celular...
"%ADB%" -d install -r "app\build\outputs\apk\release\app-arm64-v8a-release.apk"
if errorlevel 1 (
    echo La instalacion fallo. Si aparece un aviso en el celular, aceptalo y volve a probar.
    pause
    exit /b 1
)
"%ADB%" -d shell am start -n com.advancedwhatsapp.app/.ui.MainActivity >nul
echo.
echo Listo: Advanced WhatsApp se abrio en el celular.
pause
