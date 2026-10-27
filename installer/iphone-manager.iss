; Inno Setup script for iPhone Manager (per-user install, no admin rights).
; Build with installer\build.ps1, which prepares the self-contained app folder and calls:
;   ISCC /DAppVersion=1.0.0 /DSourceDir=<repo>\build\app /O<repo>\dist installer\iphone-manager.iss

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef SourceDir
  #define SourceDir "..\build\app"
#endif
; longest file path inside the app folder (computed by build.ps1), used to reject too-deep install dirs
#ifndef MaxRelPath
  #define MaxRelPath "150"
#endif

#define AppName "iPhone Manager"
#define AppPublisher "iPhone Manager contributors"
#define AppURL "https://github.com/tmkaio/iphone-manager"

[Setup]
AppId={{456CC167-3E53-477B-BC50-54F55AB9AFC3}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}/issues
AppUpdatesURL={#AppURL}/releases
VersionInfoVersion={#AppVersion}
VersionInfoProductName={#AppName}
VersionInfoDescription={#AppName} Setup
; per-user: {autopf} = %LOCALAPPDATA%\Programs when not elevated
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
DisableDirPage=auto
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputBaseFilename=iPhoneManager-Setup-{#AppVersion}
SetupIconFile={#SourceDir}\app.ico
UninstallDisplayIcon={app}\app.ico
UninstallDisplayName={#AppName}
Compression=lzma2/ultra64
SolidCompression=yes
LZMAUseSeparateProcess=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no
ChangesAssociations=no
ChangesEnvironment=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"; InfoBeforeFile: "info-en.txt"
Name: "brazilianportuguese"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"; InfoBeforeFile: "info-pt.txt"

[CustomMessages]
english.PathTooLong=The installation folder path is too long for Windows (MAX_PATH). Please choose a shorter folder, for example C:\iPhone Manager.
brazilianportuguese.PathTooLong=O caminho da pasta de instalação é longo demais para o Windows (MAX_PATH). Escolha uma pasta mais curta, por exemplo C:\iPhone Manager.
english.RemoveUserData=Also delete iPhone Manager's data in%n%1%n(downloaded iOS firmware, photo cache, iCloud session and logs)?
brazilianportuguese.RemoveUserData=Apagar também os dados do iPhone Manager em%n%1%n(firmwares do iOS baixados, cache de fotos, sessão do iCloud e logs)?

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "stop-app.ps1"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
; pythonw.exe has no console window; run.py starts the server and opens the Edge app window
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\python\pythonw.exe"; Parameters: """{app}\run.py"""; WorkingDir: "{app}"; IconFilename: "{app}\app.ico"; Comment: "{#AppName}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\python\pythonw.exe"; Parameters: """{app}\run.py"""; WorkingDir: "{app}"; IconFilename: "{app}\app.ico"; Comment: "{#AppName}"; Tasks: desktopicon

[Run]
Filename: "{app}\python\pythonw.exe"; Parameters: """{app}\run.py"""; WorkingDir: "{app}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{sys}\WindowsPowerShell\v1.0\powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File ""{app}\stop-app.ps1"""; Flags: runhidden waituntilterminated; RunOnceId: "StopApp"

[UninstallDelete]
; byte-code caches written at run time
Type: filesandordirs; Name: "{app}\python"
Type: filesandordirs; Name: "{app}\app"
Type: dirifempty; Name: "{app}"

[Code]
procedure StopRunningApp();
var
  Script: String;
  ResultCode: Integer;
begin
  Script := ExpandConstant('{app}\stop-app.ps1');
  if FileExists(Script) then
    Exec(ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'),
      '-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "' + Script + '"',
      '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  Result := '';
  if Length(ExpandConstant('{app}')) + 1 + {#MaxRelPath} > 259 then
  begin
    Result := CustomMessage('PathTooLong');
    exit;
  end;
  // upgrading over a running copy: stop it so its files can be replaced
  StopRunningApp();
end;

function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := True;
  if (CurPageID = wpSelectDir) and (Length(WizardDirValue()) + 1 + {#MaxRelPath} > 259) then
  begin
    MsgBox(CustomMessage('PathTooLong'), mbError, MB_OK);
    Result := False;
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  DataDir: String;
begin
  if CurUninstallStep = usPostUninstall then
  begin
    DataDir := ExpandConstant('{localappdata}\iPhoneManager');
    if DirExists(DataDir) and not UninstallSilent() then
      if MsgBox(FmtMessage(CustomMessage('RemoveUserData'), [DataDir]), mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
        DelTree(DataDir, True, True, True);
  end;
end;
