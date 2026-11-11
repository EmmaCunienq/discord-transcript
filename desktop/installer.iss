; Installeur Windows (Inno Setup 6) de l'application construite dans dist\DiscordTranscript\.
; Compilé par desktop\build.ps1, qui passe la version (app/__init__.py) : ISCC /DAppVersion=x.y.z
; Installation sans droits administrateur, dans %LOCALAPPDATA%\Programs\Discord Transcript.
; Les données (clés, profils, réunions, Ollama) restent dans %LOCALAPPDATA%\DiscordTranscript :
; ni une mise à jour ni la désinstallation n'y touchent.

#ifndef AppVersion
  #error Version manquante : compiler avec ISCC /DAppVersion=x.y.z (voir desktop\build.ps1)
#endif

[Setup]
; Identifiant fixe : une nouvelle version remplace l'ancienne au lieu de s'installer à côté
AppId={{373F0BF6-11F0-48F8-9A95-5A447E6202B8}
AppName=Discord Transcript
AppVersion={#AppVersion}
AppVerName=Discord Transcript {#AppVersion}
AppPublisher=Discord Transcript
DefaultDirName={localappdata}\Programs\Discord Transcript
DefaultGroupName=Discord Transcript
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
SetupIconFile=icon.ico
UninstallDisplayIcon={app}\DiscordTranscript.exe
OutputDir=..\dist
OutputBaseFilename=DiscordTranscript-Setup-{#AppVersion}
Compression=lzma2/max
SolidCompression=yes
LZMAUseSeparateProcess=yes
WizardStyle=modern
; Ferme l'app (et son bot) si elle est ouverte pendant une mise à jour
CloseApplications=force

[Languages]
Name: "french"; MessagesFile: "compiler:Languages\French.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[InstallDelete]
; Les fichiers d'une version précédente qui n'existent plus ne doivent pas rester
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "..\dist\DiscordTranscript\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Discord Transcript"; Filename: "{app}\DiscordTranscript.exe"
Name: "{autodesktop}\Discord Transcript"; Filename: "{app}\DiscordTranscript.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\DiscordTranscript.exe"; Description: "{cm:LaunchProgram,Discord Transcript}"; Flags: nowait postinstall skipifsilent
