@echo off
chcp 65001 >nul
setlocal
TITLE MAGSAPP - Instalar dependencias

REM =========================================================
REM  MAGSAPP - Instalador de dependencias
REM  Despues de "git clone", ejecutar este archivo una vez.
REM  Instala Python, Node.js y FFmpeg si faltan (con winget),
REM  las librerias de Python y del gateway, y crea los accesos
REM  directos con el icono de MAGSAPP en el escritorio y en el
REM  menu Inicio. No necesita permisos de administrador.
REM =========================================================

set "MDIR=%~dp0"
if "%MDIR:~-1%"=="\" set "MDIR=%MDIR:~0,-1%"
cd /d "%MDIR%"

echo ====================================================
echo   MAGSAPP - Instalacion de dependencias
echo ====================================================
echo.

set HAS_WINGET=0
where winget >nul 2>&1
if not errorlevel 1 set HAS_WINGET=1

REM ---------------------------------------------------------
REM [1/5] Python 3.10 o superior
REM ---------------------------------------------------------
echo [1/5] Verificando Python...
call :find_python
if defined PYTHON_EXE goto python_ok

if "%HAS_WINGET%"=="0" goto python_manual
echo [INFO] Python no encontrado. Instalando Python 3.13 con winget...
winget install -e --id Python.Python.3.13 --scope user --silent --accept-source-agreements --accept-package-agreements
call :find_python
if defined PYTHON_EXE goto python_ok

:python_manual
echo [ERROR] No se encontro Python 3.10 o superior.
echo         Instalalo desde https://www.python.org/downloads/
echo         marcando "Add python.exe to PATH", y volve a ejecutar este archivo.
start "" https://www.python.org/downloads/
goto fail

:python_ok
echo [OK] Python: %PYTHON_EXE%
echo.

REM ---------------------------------------------------------
REM [2/5] Node.js (gateway de WhatsApp)
REM ---------------------------------------------------------
echo [2/5] Verificando Node.js...
call :find_node
if defined NODE_DIR goto node_ok

if "%HAS_WINGET%"=="0" goto node_manual
echo [INFO] Node.js no encontrado. Instalando Node.js LTS con winget...
winget install -e --id OpenJS.NodeJS.LTS --silent --accept-source-agreements --accept-package-agreements
call :find_node
if defined NODE_DIR goto node_ok

:node_manual
echo [ERROR] No se encontro Node.js.
echo         Instalalo desde https://nodejs.org/ - version LTS - y volve a ejecutar este archivo.
start "" https://nodejs.org/
goto fail

:node_ok
REM Un Node recien instalado todavia no esta en el PATH de esta ventana
set "PATH=%NODE_DIR%;%APPDATA%\npm;%PATH%"
echo [OK] Node.js en: %NODE_DIR%
echo.

REM ---------------------------------------------------------
REM [3/5] FFmpeg (opcional: solo para enviar notas de voz)
REM ---------------------------------------------------------
echo [3/5] Verificando FFmpeg - necesario para las notas de voz...
call :find_ffmpeg
if "%HAS_FFMPEG%"=="1" goto ffmpeg_ok
if "%HAS_WINGET%"=="0" goto ffmpeg_skip
echo [INFO] FFmpeg no encontrado. Instalando con winget...
winget install -e --id Gyan.FFmpeg --silent --accept-source-agreements --accept-package-agreements
call :find_ffmpeg
if "%HAS_FFMPEG%"=="1" goto ffmpeg_ok

:ffmpeg_skip
echo [WARN] FFmpeg no quedo instalado. MAGSAPP funciona igual, pero sin enviar notas de voz.
echo        Se puede instalar despues con:  winget install Gyan.FFmpeg
echo.
goto pip

:ffmpeg_ok
echo [OK] FFmpeg disponible.
echo.

REM ---------------------------------------------------------
REM [4/5] Librerias de Python
REM ---------------------------------------------------------
:pip
echo [4/5] Instalando librerias de Python - puede tardar varios minutos, no cierres esta ventana...
"%PYTHON_EXE%" -m pip install --upgrade pip --disable-pip-version-check -q
"%PYTHON_EXE%" -m pip install -r "%MDIR%\requirements.txt" --disable-pip-version-check
if errorlevel 1 (
    echo [ERROR] Fallo la instalacion de las librerias de Python. Revisa los mensajes de arriba.
    goto fail
)
echo [OK] Librerias de Python instaladas.
echo.

REM ---------------------------------------------------------
REM [5/5] Gateway de WhatsApp, registro en Windows y accesos directos
REM ---------------------------------------------------------
echo [5/5] Preparando el gateway de WhatsApp y los accesos directos...
"%PYTHON_EXE%" "%MDIR%\tools\instalar.py"
if errorlevel 1 goto fail

echo.
echo ====================================================
echo   INSTALACION COMPLETA
echo   Abri MAGSAPP desde el icono del escritorio
echo   o buscando "MAGSAPP" en el menu Inicio.
echo   La primera vez vas a escanear un codigo QR con
echo   WhatsApp del celular: Dispositivos vinculados.
echo ====================================================
echo.
choice /C SN /M "Abrir MAGSAPP ahora"
if errorlevel 2 goto end
start "" "%PYTHONW_EXE%" "%MDIR%\main.py"
goto end

:fail
echo.
echo ====================================================
echo   LA INSTALACION NO SE COMPLETO
echo   Corregi el error indicado arriba y volve a
echo   ejecutar INSTALAR_DEPENDENCIAS.bat
echo ====================================================
echo.
pause
exit /b 1

:end
endlocal
exit /b 0


REM =========================================================
REM  Subrutinas
REM =========================================================

:find_python
REM Deja en PYTHON_EXE un python.exe 3.10+ que funcione, y en PYTHONW_EXE su pythonw.exe
set "PYTHON_EXE="
set "PYTHONW_EXE="
for %%P in (
    "%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
    "C:\Python313\python.exe"
    "%ProgramFiles%\Python313\python.exe"
    "%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
    "C:\Python312\python.exe"
    "%ProgramFiles%\Python312\python.exe"
    "%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
    "C:\Python311\python.exe"
    "%ProgramFiles%\Python311\python.exe"
) do (
    if not defined PYTHON_EXE if exist %%P call :try_python %%P
)
if not defined PYTHON_EXE (
    for /f "delims=" %%i in ('py -3 -c "import sys;print(sys.executable)" 2^>nul') do call :try_python "%%i"
)
if not defined PYTHON_EXE (
    for /f "delims=" %%i in ('python -c "import sys;print(sys.executable)" 2^>nul') do call :try_python "%%i"
)
if not defined PYTHON_EXE exit /b 0
set "PYTHONW_EXE=%PYTHON_EXE:python.exe=pythonw.exe%"
if not exist "%PYTHONW_EXE%" set "PYTHONW_EXE=%PYTHON_EXE%"
exit /b 0

:try_python
if defined PYTHON_EXE exit /b 0
"%~1" -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
if not errorlevel 1 set "PYTHON_EXE=%~1"
exit /b 0

:find_node
REM Deja en NODE_DIR la carpeta que contiene node.exe y npm.cmd
set "NODE_DIR="
for /f "delims=" %%i in ('where node 2^>nul') do if not defined NODE_DIR set "NODE_DIR=%%~dpi"
if not defined NODE_DIR if exist "%ProgramFiles%\nodejs\node.exe" set "NODE_DIR=%ProgramFiles%\nodejs\"
if not defined NODE_DIR if exist "%ProgramFiles(x86)%\nodejs\node.exe" set "NODE_DIR=%ProgramFiles(x86)%\nodejs\"
if not defined NODE_DIR if exist "%LOCALAPPDATA%\Programs\nodejs\node.exe" set "NODE_DIR=%LOCALAPPDATA%\Programs\nodejs\"
if defined NODE_DIR set "NODE_DIR=%NODE_DIR:~0,-1%"
exit /b 0

:find_ffmpeg
set HAS_FFMPEG=0
where ffmpeg >nul 2>&1
if not errorlevel 1 set HAS_FFMPEG=1
if exist "%LOCALAPPDATA%\Microsoft\WinGet\Links\ffmpeg.exe" set HAS_FFMPEG=1
if exist "C:\ffmpeg\bin\ffmpeg.exe" set HAS_FFMPEG=1
if exist "%ProgramFiles%\ffmpeg\bin\ffmpeg.exe" set HAS_FFMPEG=1
exit /b 0
