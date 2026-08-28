#define AppName "iSponsorBlockTV"
#define AppVersion "2.6.1"
#define AppPublisher "iSponsorBlockTV"
#define AppExeName "iSponsorBlockTV.exe"

#ifndef SourceDir
  #define SourceDir "..\build\windows\app"
#endif
#ifndef OutputDir
  #define OutputDir "..\dist\windows"
#endif

[Setup]
AppId={{B6B2F8F1-8B8A-4C5A-9E2B-1C9D1D5B1D90}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={autopf}\iSponsorBlockTV
DefaultGroupName={#AppName}
OutputDir={#OutputDir}
OutputBaseFilename=iSponsorBlockTV-{#AppVersion}-setup
Compression=lzma2
SolidCompression=yes
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=admin
UninstallDisplayIcon={app}\.venv\Scripts\pythonw.exe

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{group}\iSponsorBlockTV"; Filename: "{app}\.venv\Scripts\pythonw.exe"; Parameters: """{app}\scripts\gui_launcher.py"""; WorkingDir: "{app}"
Name: "{commondesktop}\iSponsorBlockTV"; Filename: "{app}\.venv\Scripts\pythonw.exe"; Parameters: """{app}\scripts\gui_launcher.py"""; WorkingDir: "{app}"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional shortcuts:"

[Run]
Filename: "{app}\.venv\Scripts\pythonw.exe"; Parameters: """{app}\scripts\gui_launcher.py"""; WorkingDir: "{app}"; Description: "Launch iSponsorBlockTV"; Flags: nowait postinstall skipifsilent
