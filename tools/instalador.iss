; MAGSAPP - Instalador (Inno Setup 6). No se compila a mano: lo arma
;   python tools/build_portable.py --instalador
; que le pasa AppVersion, SourceDir (dist\MAGSAPP) y OutputDir.
;
; Instala por usuario (sin pedir administrador) en AppData\Local\Programs\MAGSAPP. Correr una versión
; nueva encima actualiza: cierra MAGSAPP, reemplaza los archivos y conserva los datos (~\.magsapp).
; Para actualizaciones silenciosas: MAGSAPP-Setup-x.y.z.exe /SILENT /SUPPRESSMSGBOXES

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef SourceDir
  #define SourceDir "..\dist\MAGSAPP"
#endif
#ifndef OutputDir
  #define OutputDir "..\dist"
#endif
#define AppUserModelID "MagTecnologia.MAGSAPP.1"

[Setup]
; AppId fijo: cada versión nueva reemplaza a la anterior (no cambiarlo nunca)
AppId={{8F4C2B7E-5A1D-4E3B-9C6F-2D7A1B3E4F50}
AppName=MAGSAPP
AppVersion={#AppVersion}
AppVerName=MAGSAPP {#AppVersion}
AppPublisher=MagTecnologia
DefaultDirName={localappdata}\Programs\MAGSAPP
DisableProgramGroupPage=yes
DisableDirPage=auto
PrivilegesRequired=lowest
OutputDir={#OutputDir}
OutputBaseFilename=MAGSAPP-Setup-{#AppVersion}
SetupIconFile=..\assets\magsapp.ico
UninstallDisplayIcon={app}\MAGSAPP.exe
UninstallDisplayName=MAGSAPP
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
CloseApplications=force
RestartApplications=no
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "es"; MessagesFile: "compiler:Languages\Spanish.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[InstallDelete]
; Al actualizar no quedan archivos viejos de la versión anterior
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{userprograms}\MAGSAPP"; Filename: "{app}\MAGSAPP.exe"; AppUserModelID: "{#AppUserModelID}"
Name: "{userdesktop}\MAGSAPP"; Filename: "{app}\MAGSAPP.exe"; AppUserModelID: "{#AppUserModelID}"; Tasks: desktopicon

[Registry]
; Lo que registra la app al abrirse (identidad, magsapp://, inicio con Windows) se borra al desinstalar
Root: HKCU; Subkey: "Software\Classes\magsapp"; Flags: uninsdeletekey dontcreatekey
Root: HKCU; Subkey: "Software\Classes\AppUserModelId\{#AppUserModelID}"; Flags: uninsdeletekey dontcreatekey
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: none; ValueName: "MAGSAPP"; Flags: uninsdeletevalue dontcreatekey

[Run]
Filename: "{app}\MAGSAPP.exe"; Description: "{cm:LaunchProgram,MAGSAPP}"; Flags: nowait postinstall skipifsilent

[UninstallRun]
; Cierra MAGSAPP y su gateway (node.exe) para poder borrar los archivos
Filename: "{sys}\taskkill.exe"; Parameters: "/F /T /IM MAGSAPP.exe"; Flags: runhidden; RunOnceId: "CerrarMagsapp"

[Code]
procedure CurStepChanged(CurStep: TSetupStep);
var
  DataDir: String;
begin
  if CurStep = ssPostInstall then
  begin
    { El instalador ya decidió el icono del escritorio: que la app no lo cree por su cuenta }
    DataDir := ExpandConstant('{%USERPROFILE}\.magsapp');
    ForceDirectories(DataDir);
    SaveStringToFile(DataDir + '\acceso_escritorio_creado', '', False);
  end;
end;
