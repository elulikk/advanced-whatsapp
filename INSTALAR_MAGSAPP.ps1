# =========================================================
#  MAGSAPP - Instalador completo (un solo archivo)
#
#  Copiar este archivo a la PC nueva, clic derecho > "Ejecutar con PowerShell".
#  Hace todo: Git, descarga de MAGSAPP, Python, Node.js, FFmpeg,
#  librerias, gateway de WhatsApp y accesos directos.
#
#  No usa winget ni instaladores (.exe/.msi): lo que falta lo baja en .zip
#  y lo descomprime en %LOCALAPPDATA%\MAGSAPP-Tools. No necesita admin.
#  Se puede volver a ejecutar: si MAGSAPP ya esta, lo actualiza.
#
#  Opciones:  -Destino "D:\MAGSAPP"   carpeta donde queda MAGSAPP
#             -SinPreguntas           no pregunta nada al final
# =========================================================
param(
    [string]$Destino = 'C:\MAGSAPP',
    [string]$Repo = 'https://github.com/elulikk/magsapp.git',
    [switch]$SinPreguntas
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$Tools = Join-Path $env:LOCALAPPDATA 'MAGSAPP-Tools'

function Info($m)  { Write-Host "[INFO] $m" }
function Ok($m)    { Write-Host "[OK] $m" -ForegroundColor Green }
function Warn($m)  { Write-Host "[WARN] $m" -ForegroundColor Yellow }
function Paso($m)  { Write-Host ""; Write-Host "=== $m" -ForegroundColor Cyan }

# Baja un .zip y lo deja descomprimido en $Dest. Si el zip trae una sola
# carpeta adentro (o se indica $Inner), usa el contenido de esa carpeta.
function Install-Zip([string]$Url, [string]$Dest, [string]$Inner = '') {
    $zip = Join-Path $env:TEMP ('magsapp-' + [guid]::NewGuid().ToString('N') + '.zip')
    $tmp = "$zip-x"
    try {
        Invoke-WebRequest $Url -UseBasicParsing -OutFile $zip
        Expand-Archive $zip -DestinationPath $tmp -Force
        $src = $tmp
        if ($Inner) {
            $src = Join-Path $tmp $Inner
        } else {
            $items = @(Get-ChildItem $tmp)
            if ($items.Count -eq 1 -and $items[0].PSIsContainer) { $src = $items[0].FullName }
        }
        if (Test-Path $Dest) { Remove-Item $Dest -Recurse -Force }
        New-Item -ItemType Directory -Force (Split-Path $Dest) | Out-Null
        Move-Item $src $Dest
    } finally {
        Remove-Item $zip -Force -ErrorAction SilentlyContinue
        Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
    }
}

# Agrega una carpeta al PATH de esta ventana y al del usuario (para las proximas).
function Add-ToPath([string]$Dir) {
    if (-not (($env:PATH -split ';') -contains $Dir)) { $env:PATH = "$Dir;$env:PATH" }
    $user = [Environment]::GetEnvironmentVariable('PATH', 'User')
    $parts = @($user -split ';' | Where-Object { $_ })
    if (-not ($parts -contains $Dir)) {
        [Environment]::SetEnvironmentVariable('PATH', (@($Dir) + $parts) -join ';', 'User')
    }
}

function First-Existing([string[]]$Paths) {
    foreach ($p in $Paths) { if ($p -and (Test-Path $p)) { return $p } }
    return $null
}

# ---------------------------------------------------------
function Get-Git {
    Paso '[1/7] Git'
    $cmd = Get-Command git -ErrorAction SilentlyContinue
    $git = if ($cmd) { $cmd.Source } else { First-Existing @("$Tools\git\cmd\git.exe") }
    if (-not $git) {
        Info 'Git no encontrado. Descargando MinGit (sin instalador)...'
        $rel = Invoke-RestMethod 'https://api.github.com/repos/git-for-windows/git/releases/latest'
        $asset = $rel.assets | Where-Object { $_.name -match '^MinGit-[\d.]+-64-bit\.zip$' } | Select-Object -First 1
        if (-not $asset) { throw 'No se encontro la descarga de MinGit.' }
        Install-Zip $asset.browser_download_url "$Tools\git"
        $git = "$Tools\git\cmd\git.exe"
        Add-ToPath "$Tools\git\cmd"
    }
    Ok "Git: $git"
    return $git
}

function Get-Code([string]$Git) {
    Paso '[2/7] Codigo de MAGSAPP'
    if (Test-Path (Join-Path $Destino '.git')) {
        Info "MAGSAPP ya esta en $Destino. Actualizando..."
        & $Git -C $Destino pull --ff-only
        if ($LASTEXITCODE -ne 0) { Warn 'No se pudo actualizar (hay cambios locales?). Sigo con la version que ya esta.' }
    } else {
        if ((Test-Path $Destino) -and @(Get-ChildItem $Destino -Force).Count -gt 0) {
            throw "La carpeta $Destino ya existe y no esta vacia. Borrala o elegi otra con -Destino."
        }
        Info "Descargando MAGSAPP en $Destino..."
        Info 'Si se abre una ventana de GitHub, inicia sesion con la cuenta que tiene acceso al repositorio.'
        & $Git clone $Repo $Destino
        if ($LASTEXITCODE -ne 0) { throw 'No se pudo descargar MAGSAPP de GitHub (revisa internet y el inicio de sesion).' }
    }
    Ok "MAGSAPP en $Destino"
}

function Test-Python([string]$Exe) {
    if (-not $Exe -or -not (Test-Path $Exe)) { return $false }
    try {
        & $Exe -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' *> $null
        return ($LASTEXITCODE -eq 0)
    } catch { return $false }
}

function Get-Python {
    Paso '[3/7] Python'
    $cands = @("$Tools\python\python.exe")
    foreach ($c in @('py', 'python')) {
        if (Get-Command $c -ErrorAction SilentlyContinue) {
            try {
                $args_ = if ($c -eq 'py') { @('-3', '-c') } else { @('-c') }
                $exe = & $c @args_ 'import sys; print(sys.executable)' 2>$null
                if ($LASTEXITCODE -eq 0 -and $exe) { $cands += "$exe".Trim() }
            } catch {}
        }
    }
    foreach ($v in '313', '312', '311', '310') {
        $cands += "$env:LOCALAPPDATA\Programs\Python\Python$v\python.exe", "C:\Python$v\python.exe", "$env:ProgramFiles\Python$v\python.exe"
    }
    $py = $cands | Where-Object { Test-Python $_ } | Select-Object -First 1
    if (-not $py) {
        Info 'Python 3.10+ no encontrado. Descargando Python (sin instalador)...'
        $all = (Invoke-RestMethod 'https://api.nuget.org/v3-flatcontainer/python/index.json').versions
        $v = $all | Where-Object { $_ -match '^3\.13\.\d+$' } | Sort-Object { [version]$_ } -Descending | Select-Object -First 1
        if (-not $v) { throw 'No se encontro una version de Python para descargar.' }
        Install-Zip "https://api.nuget.org/v3-flatcontainer/python/$v/python.$v.nupkg" "$Tools\python" 'tools'
        $py = "$Tools\python\python.exe"
        & $py -m ensurepip --upgrade --default-pip *> $null
        if (-not (Test-Python $py)) { throw 'Python se descargo pero no arranca en esta PC.' }
        Add-ToPath "$Tools\python"
        Add-ToPath "$Tools\python\Scripts"
    }
    Ok "Python: $py"
    return $py
}

function Get-Node {
    Paso '[4/7] Node.js'
    $cmd = Get-Command node -ErrorAction SilentlyContinue
    $node = if ($cmd) { $cmd.Source } else {
        First-Existing @("$env:ProgramFiles\nodejs\node.exe", "${env:ProgramFiles(x86)}\nodejs\node.exe",
                         "$env:LOCALAPPDATA\Programs\nodejs\node.exe", "$Tools\node\node.exe")
    }
    if (-not $node) {
        Info 'Node.js no encontrado. Descargando Node.js LTS (sin instalador)...'
        $rel = (Invoke-RestMethod 'https://nodejs.org/dist/index.json') |
            Where-Object { $_.lts -and ($_.files -contains 'win-x64-zip') } | Select-Object -First 1
        if (-not $rel) { throw 'No se encontro una version de Node.js para descargar.' }
        Install-Zip "https://nodejs.org/dist/$($rel.version)/node-$($rel.version)-win-x64.zip" "$Tools\node"
        $node = "$Tools\node\node.exe"
    }
    # npm y la app tienen que encontrar node en el PATH
    Add-ToPath (Split-Path $node)
    Ok "Node.js: $node"
}

function Get-FFmpeg {
    Paso '[5/7] FFmpeg (para enviar notas de voz)'
    $cmd = Get-Command ffmpeg -ErrorAction SilentlyContinue
    $ff = if ($cmd) { $cmd.Source } else {
        First-Existing @("$env:LOCALAPPDATA\Microsoft\WinGet\Links\ffmpeg.exe", 'C:\ffmpeg\bin\ffmpeg.exe',
                         "$env:ProgramFiles\ffmpeg\bin\ffmpeg.exe", "$Tools\ffmpeg\bin\ffmpeg.exe")
    }
    if (-not $ff) {
        try {
            Info 'FFmpeg no encontrado. Descargando (unos 100 MB)...'
            Install-Zip 'https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip' "$Tools\ffmpeg"
            $ff = "$Tools\ffmpeg\bin\ffmpeg.exe"
            Add-ToPath "$Tools\ffmpeg\bin"
        } catch {
            Warn "No se pudo bajar FFmpeg: $($_.Exception.Message)"
            Warn 'MAGSAPP funciona igual, pero sin enviar notas de voz.'
            return
        }
    }
    Ok "FFmpeg: $ff"
}

function Install-Libraries([string]$Py) {
    Paso '[6/7] Librerias de Python (puede tardar varios minutos, no cierres esta ventana)'
    & $Py -m pip install --upgrade pip --disable-pip-version-check -q
    & $Py -m pip install -r (Join-Path $Destino 'requirements.txt') --disable-pip-version-check
    if ($LASTEXITCODE -ne 0) { throw 'Fallo la instalacion de las librerias de Python. Revisa los mensajes de arriba.' }
    Ok 'Librerias de Python instaladas.'
}

function Finish-Setup([string]$Py) {
    Paso '[7/7] Gateway de WhatsApp y accesos directos'
    & $Py (Join-Path $Destino 'tools\instalar.py')
    if ($LASTEXITCODE -ne 0) { throw 'No se pudo completar el ultimo paso. Revisa los mensajes de arriba.' }
}

function Main {
    Write-Host '===================================================='
    Write-Host '  MAGSAPP - Instalacion completa'
    Write-Host '===================================================='
    try {
        $git = Get-Git
        Get-Code $git
        $py = Get-Python
        Get-Node
        Get-FFmpeg
        Install-Libraries $py
        Finish-Setup $py
    } catch {
        Write-Host ''
        Write-Host "[ERROR] $($_.Exception.Message)" -ForegroundColor Red
        Write-Host ''
        Write-Host 'LA INSTALACION NO SE COMPLETO. Corregi el error y volve a ejecutar este archivo.'
        if (-not $SinPreguntas) { Read-Host 'Presiona Enter para cerrar' | Out-Null }
        exit 1
    }

    Write-Host ''
    Write-Host '====================================================' -ForegroundColor Green
    Write-Host '  INSTALACION COMPLETA' -ForegroundColor Green
    Write-Host '  Abri MAGSAPP desde el icono del escritorio o'
    Write-Host '  buscando "MAGSAPP" en el menu Inicio. La primera'
    Write-Host '  vez vas a escanear un codigo QR con WhatsApp del'
    Write-Host '  celular: Dispositivos vinculados.'
    Write-Host '====================================================' -ForegroundColor Green
    if (-not $SinPreguntas) {
        $r = Read-Host 'Abrir MAGSAPP ahora? (S/N)'
        if ($r -match '^[sS]') {
            $pyw = Join-Path (Split-Path $py) 'pythonw.exe'
            if (-not (Test-Path $pyw)) { $pyw = $py }
            Start-Process $pyw -ArgumentList "`"$(Join-Path $Destino 'main.py')`"" -WorkingDirectory $Destino
        }
    }
    exit 0
}

# Al cargarlo con ". .\INSTALAR_MAGSAPP.ps1" solo define las funciones (para probar pasos sueltos)
if ($MyInvocation.InvocationName -ne '.') { Main }
