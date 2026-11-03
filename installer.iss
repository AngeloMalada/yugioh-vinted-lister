; Inno Setup script for Yu-Gi-Oh! Vinted Lister. Built by build.ps1.
#define AppName "Yu-Gi-Oh! Vinted Lister"
#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#define AppExe "YugiohVintedLister.exe"

[Setup]
AppId={{6C1E3B8E-6A3F-4D7B-9B57-0F1D7E2A9C41}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=angelo
DefaultDirName={localappdata}\Programs\YugiohVintedLister
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=installer
OutputBaseFilename=YugiohVintedLister-Setup
SetupIconFile=assets\icon.ico
UninstallDisplayIcon={app}\{#AppExe}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
CloseApplications=force
RestartApplications=no
ArchitecturesInstallIn64BitMode=x64compatible

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional shortcuts:"

[Files]
Source: "dist\YugiohVintedLister\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "Launch {#AppName}"; Flags: nowait postinstall skipifsilent
