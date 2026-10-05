; Inno Setup script: iscc /DAppVersion=0.3.0 packaging\omni.iss  ->  dist\Omni-Setup-0.3.0.exe
#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{6F0B2A3E-7C41-4D8B-9A55-0B1E5C2D7A91}
AppName=Omni
AppVersion={#AppVersion}
AppPublisher=Wissem's Industries
AppPublisherURL=https://github.com/Wissem-Industries/omni
DefaultDirName={localappdata}\Programs\Omni
DefaultGroupName=Omni
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\dist
OutputBaseFilename=Omni-Setup-{#AppVersion}
SetupIconFile=..\omni\assets\omni.ico
UninstallDisplayIcon={app}\Omni.exe
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
DisableProgramGroupPage=yes

[Languages]
Name: "french"; MessagesFile: "compiler:Languages\French.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "..\dist\Omni\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{autoprograms}\Omni"; Filename: "{app}\Omni.exe"
Name: "{autodesktop}\Omni"; Filename: "{app}\Omni.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\Omni.exe"; Description: "{cm:LaunchProgram,Omni}"; Flags: nowait postinstall skipifsilent

; The data folder (%LOCALAPPDATA%\omni: extracted resources, exports, settings) is left in place on uninstall.
