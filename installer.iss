#define AppName "CD Kill Announcer"
#define AppVersion "0.2.0"
[Setup]
AppId={{1F13FCA4-EB9B-412F-9D32-D5986F9636B1}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=sg0hann
DefaultDirName={localappdata}\Programs\CD Kill Announcer
DefaultGroupName=CD Kill Announcer
OutputDir=.
OutputBaseFilename=CD-Kill-Announcer-Setup
Compression=lzma2
SolidCompression=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
WizardStyle=modern
CloseApplications=yes
RestartApplications=no
UninstallDisplayIcon={app}\CD-Kill-Announcer.exe
[Files]
Source: "dist\CD-Kill-Announcer\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion
[Icons]
Name: "{group}\CD Kill Announcer"; Filename: "{app}\CD-Kill-Announcer.exe"
Name: "{autodesktop}\CD Kill Announcer"; Filename: "{app}\CD-Kill-Announcer.exe"; Tasks: desktopicon
[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; Flags: unchecked
[Run]
Filename: "{app}\CD-Kill-Announcer.exe"; Description: "Launch CD Kill Announcer"; Flags: nowait postinstall skipifsilent