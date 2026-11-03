@echo off
cd /d "%~dp0"

REM Abre Advanced WhatsApp sin consola. Si todavia no se instalaron las dependencias, corre el instalador.
set "PYW="
for /f "delims=" %%i in ('python -c "import PySide6, sys, os; print(os.path.join(os.path.dirname(sys.executable), 'pythonw.exe'))" 2^>nul') do set "PYW=%%i"
if not defined PYW (
    echo Faltan dependencias de Advanced WhatsApp. Abriendo el instalador...
    call "%~dp0INSTALAR_DEPENDENCIAS.bat"
    exit /b
)
if not exist "%PYW%" set "PYW=pythonw"
start "" "%PYW%" "%~dp0main.py"
