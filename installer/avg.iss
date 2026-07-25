; Inno Setup script for AVG Studio - Task 11.
;
; Wraps the PyInstaller --onedir folder (dist\AVG\, built by
; tools\avg.spec) into a single AVG-Setup.exe. Per-user install so no UAC
; prompt is needed and the app can write beside itself (thumbnail cache
; aside, which already lives under %LOCALAPPDATA% - see
; avg/media/thumbnails.py).
;
; Build with:
;   "C:\Users\HO\AppData\Local\Programs\Inno Setup 6\ISCC.exe" installer\avg.iss
;
; Expects dist\AVG\AVG.exe to already exist (tools\build.py runs PyInstaller
; first, then this).

#define MyAppName "AVG Studio"
#define MyAppExeName "AVG.exe"
#define MyAppPublisher "AVG Studio"
#define MyDistDir "..\dist\AVG"

[Setup]
; Fixed GUID so upgrades replace the previous install instead of stacking
; a second one alongside it.
AppId={{4DBB8B70-35A7-49D1-B3CD-C7AA3073250C}
AppName={#MyAppName}
AppVersion=0.1.0
DefaultDirName={localappdata}\Programs\AVG Studio
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
; Per-user install, no admin/UAC prompt - the app only ever writes beside
; itself and under the current user's %LOCALAPPDATA%.
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=AVG-Setup
Compression=lzma2/max
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\{#MyAppExeName}
WizardStyle=modern

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
; The whole PyInstaller onedir output, recursively - AVG.exe, _internal\,
; the bundled vendor\ffmpeg and web\dist data.
Source: "{#MyDistDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#MyAppName}}"; Flags: nowait postinstall skipifsilent
