; Advanced WhatsApp - Instalador (Inno Setup 6). No se compila a mano: lo arma
;   python tools/build_portable.py --instalador
; que le pasa AppVersion, SourceDir (dist\Advanced WhatsApp) y OutputDir.
;
; Instala por usuario (sin pedir administrador) en AppData\Local\Programs\Advanced WhatsApp. Correr una versión
; nueva encima actualiza: cierra Advanced WhatsApp, reemplaza los archivos y conserva los datos (~\.magsapp).
; Para actualizaciones silenciosas: AdvancedWhatsapp-Setup-x.y.z.exe /VERYSILENT /SUPPRESSMSGBOXES /NORESTART (lo hace la app al actualizarse sola)

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef SourceDir
  #define SourceDir "..\dist\Advanced WhatsApp"
#endif
#ifndef OutputDir
  #define OutputDir "..\dist"
#endif
#define AppUserModelID "MagTecnologia.AdvancedWhatsapp.1"

[Setup]
; AppId fijo: cada versión nueva reemplaza a la anterior (no cambiarlo nunca)
AppId={{8F4C2B7E-5A1D-4E3B-9C6F-2D7A1B3E4F50}
AppName=Advanced WhatsApp
AppVersion={#AppVersion}
AppVerName=Advanced WhatsApp {#AppVersion}
AppPublisher=MagTecnologia
DefaultDirName={localappdata}\Programs\Advanced WhatsApp
DisableProgramGroupPage=yes
DisableDirPage=auto
PrivilegesRequired=lowest
OutputDir={#OutputDir}
OutputBaseFilename=AdvancedWhatsapp-Setup-{#AppVersion}
SetupIconFile=..\assets\advancedwhatsapp.ico
UninstallDisplayIcon={app}\AdvancedWhatsapp.exe
UninstallDisplayName=Advanced WhatsApp
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
Name: "{userprograms}\Advanced WhatsApp"; Filename: "{app}\AdvancedWhatsapp.exe"; AppUserModelID: "{#AppUserModelID}"
Name: "{userdesktop}\Advanced WhatsApp"; Filename: "{app}\AdvancedWhatsapp.exe"; AppUserModelID: "{#AppUserModelID}"; Tasks: desktopicon

[Registry]
; Lo que registra la app al abrirse (identidad, advancedwhatsapp://, inicio con Windows) se borra al desinstalar
Root: HKCU; Subkey: "Software\Classes\advancedwhatsapp"; Flags: uninsdeletekey dontcreatekey
Root: HKCU; Subkey: "Software\Classes\AppUserModelId\{#AppUserModelID}"; Flags: uninsdeletekey dontcreatekey
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: none; ValueName: "Advanced WhatsApp"; Flags: uninsdeletevalue dontcreatekey

[Run]
Filename: "{app}\AdvancedWhatsapp.exe"; Description: "{cm:LaunchProgram,Advanced WhatsApp}"; Flags: nowait postinstall skipifsilent
; Actualización automática (instalador silencioso): reabre la app al terminar
Filename: "{app}\AdvancedWhatsapp.exe"; Flags: nowait skipifnotsilent

[UninstallRun]
; Cierra Advanced WhatsApp y su gateway (node.exe) para poder borrar los archivos
Filename: "{sys}\taskkill.exe"; Parameters: "/F /T /IM AdvancedWhatsapp.exe"; Flags: runhidden; RunOnceId: "CerrarAdvancedWhatsapp"

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
