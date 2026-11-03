# Plan B cuando winget no puede instalar Python.
# No usa ningun instalador (.exe/.msi): baja el paquete oficial de Python de NuGet,
# que es un .zip, y lo descomprime en %LOCALAPPDATA%\AdvancedWhatsapp-Tools\python. Sin admin.
$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$ProgressPreference = 'SilentlyContinue'

$dest = Join-Path $env:LOCALAPPDATA 'AdvancedWhatsapp-Tools\python'
$zip  = Join-Path $env:TEMP 'advancedwhatsapp-python.zip'
$tmp  = Join-Path $env:TEMP 'advancedwhatsapp-python-extract'

try {
    $all = (Invoke-RestMethod 'https://api.nuget.org/v3-flatcontainer/python/index.json').versions
} catch {
    Write-Host "[ERROR] No se pudo conectar para bajar Python: $($_.Exception.Message)"
    exit 1
}
# La 3.13 mas nueva (o la 3.12 si no hubiera 3.13); sin versiones preliminares
$v = $all | Where-Object { $_ -match '^3\.1[23]\.\d+$' } |
    Sort-Object { [version]$_ } -Descending | Select-Object -First 1
if (-not $v) {
    Write-Host '[ERROR] No se encontro una version de Python para bajar.'
    exit 1
}

try {
    Write-Host "[INFO] Descargando Python $v (sin instalador)..."
    Invoke-WebRequest "https://api.nuget.org/v3-flatcontainer/python/$v/python.$v.nupkg" -UseBasicParsing -OutFile $zip

    Write-Host '[INFO] Descomprimiendo...'
    if (Test-Path $tmp)  { Remove-Item $tmp  -Recurse -Force }
    if (Test-Path $dest) { Remove-Item $dest -Recurse -Force }
    Expand-Archive $zip -DestinationPath $tmp -Force
    # Dentro del paquete, Python completo esta en la carpeta "tools"
    New-Item -ItemType Directory -Force (Split-Path $dest) | Out-Null
    Move-Item (Join-Path $tmp 'tools') $dest
} catch {
    Write-Host "[ERROR] No se pudo bajar o descomprimir Python: $($_.Exception.Message)"
    exit 1
} finally {
    Remove-Item $zip -ErrorAction SilentlyContinue
    Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
}

$py = Join-Path $dest 'python.exe'
& $py -m ensurepip --upgrade --default-pip *> $null
& $py -m pip --version *> $null
if ($LASTEXITCODE -ne 0) {
    Write-Host '[ERROR] Python quedo descomprimido pero pip no funciona.'
    exit 1
}
Write-Host "[OK] Python $v listo en $dest"
exit 0
