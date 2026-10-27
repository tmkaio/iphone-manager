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

; ffmpeg for video previews: an official Windows build (gyan.dev, mirrored on GitHub with fixed
; versions), downloaded and checked against its SHA-256 at install time rather than redistributed
#define FfmpegVersion "9.0.2"
#define FfmpegBuild "ffmpeg-" + FfmpegVersion + "-essentials_build"
#define FfmpegUrl "https://github.com/GyanD/codexffmpeg/releases/download/" + FfmpegVersion + "/" + FfmpegBuild + ".zip"
#define FfmpegSha256 "60f467265b1e312373dbcd92200c2618a74850f98d3d078e94296bb3fa2047ba"
; Apple Devices (Microsoft Store), which brings the Apple Mobile Device driver and service
#define AppleDevicesStoreId "9NP83LWLPZ9K"

#if Ver < EncodeVer(6, 5, 0)
  #error Inno Setup 6.5 or newer is required ([Files] download and extractarchive flags)
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
; .zip support for the ffmpeg download
ArchiveExtraction=full

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"; InfoBeforeFile: "info-en.txt"
Name: "brazilianportuguese"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"; InfoBeforeFile: "info-pt.txt"

[CustomMessages]
english.PathTooLong=The installation folder path is too long for Windows (MAX_PATH). Please choose a shorter folder, for example C:\iPhone Manager.
brazilianportuguese.PathTooLong=O caminho da pasta de instalação é longo demais para o Windows (MAX_PATH). Escolha uma pasta mais curta, por exemplo C:\iPhone Manager.
english.RemoveUserData=Also delete iPhone Manager's data in%n%1%n(downloaded iOS firmware, photo cache, iCloud session and logs)?
brazilianportuguese.RemoveUserData=Apagar também os dados do iPhone Manager em%n%1%n(firmwares do iOS baixados, cache de fotos, sessão do iCloud e logs)?

english.TypeFull=Full installation (recommended)
brazilianportuguese.TypeFull=Instalação completa (recomendada)
english.TypeCustom=Custom installation
brazilianportuguese.TypeCustom=Instalação personalizada
english.CompMain=iPhone Manager
brazilianportuguese.CompMain=iPhone Manager
english.CompFfmpeg=ffmpeg {#FfmpegVersion} - plays iPhone videos in the app (downloads about 115 MB)
brazilianportuguese.CompFfmpeg=ffmpeg {#FfmpegVersion} - reproduz os vídeos do iPhone no app (baixa cerca de 115 MB)
english.CompApple=Apple Devices (Microsoft Store) - Apple's driver for connecting the iPhone
brazilianportuguese.CompApple=Apple Devices (Microsoft Store) - driver da Apple para conectar o iPhone
english.InstallingApple=Installing Apple Devices from the Microsoft Store (this can take a few minutes)...
brazilianportuguese.InstallingApple=Instalando o Apple Devices pela Microsoft Store (pode levar alguns minutos)...
english.AppleFailed=Apple Devices couldn't be installed automatically (winget exit code %1).%n%nOpen its Microsoft Store page to install it now? iPhone Manager needs it, or iTunes or iMazing, to see the iPhone.
brazilianportuguese.AppleFailed=Não foi possível instalar o Apple Devices automaticamente (código do winget %1).%n%nAbrir a página dele na Microsoft Store para instalar agora? O iPhone Manager precisa dele, ou do iTunes ou do iMazing, para ver o iPhone.

[Types]
Name: "full"; Description: "{cm:TypeFull}"
Name: "custom"; Description: "{cm:TypeCustom}"; Flags: iscustom

[Components]
Name: "main"; Description: "{cm:CompMain}"; Types: full custom; Flags: fixed
Name: "ffmpeg"; Description: "{cm:CompFfmpeg}"; Types: full custom; ExtraDiskSpaceRequired: 200_000_000
; only offered when no Apple Mobile Device service/driver is installed yet
Name: "apple"; Description: "{cm:CompApple}"; Types: full custom; Check: not AppleDriverInstalled

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[InstallDelete]
; an older ffmpeg build, replaced by this version's
Type: filesandordirs; Name: "{app}\ffmpeg"; Components: ffmpeg; Check: not FfmpegInstalled

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Components: main; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "stop-app.ps1"; DestDir: "{app}"; Components: main; Flags: ignoreversion
; downloaded (and hash-checked) at the start of "Preparing to Install", extracted to {app}\ffmpeg\<build>\bin;
; skipped when this version is already there (upgrades)
Source: "{#FfmpegUrl}"; DestName: "{#FfmpegBuild}.zip"; DestDir: "{app}\ffmpeg"; Components: ffmpeg; \
  Hash: "{#FfmpegSha256}"; ExternalSize: 115_000_000; Check: not FfmpegInstalled; \
  Flags: external download extractarchive recursesubdirs createallsubdirs ignoreversion

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
Type: filesandordirs; Name: "{app}\ffmpeg"
Type: dirifempty; Name: "{app}"

[Code]
function FfmpegInstalled(): Boolean;
begin
  Result := FileExists(ExpandConstant('{app}\ffmpeg\{#FfmpegBuild}\bin\ffmpeg.exe'));
end;

// the same places app\mobiledevice.py looks, plus the Windows service of iTunes / Apple Devices
function AppleDriverInstalled(): Boolean;
begin
  Result := RegKeyExists(HKLM, 'SYSTEM\CurrentControlSet\Services\Apple Mobile Device Service')
    or FileExists(ExpandConstant('{commonappdata}\DigiDNA\iMazing\MobileDevice\Current\AppleMobileDeviceProcess.exe'))
    or FileExists(ExpandConstant('{commoncf64}\Apple\Mobile Device Support\AppleMobileDeviceProcess.exe'));
end;

procedure InstallAppleDevices();
var
  Winget: String;
  ResultCode, ErrorCode: Integer;
begin
  WizardForm.StatusLabel.Caption := CustomMessage('InstallingApple');
  WizardForm.FilenameLabel.Caption := '';
  ResultCode := -1;
  Winget := ExpandConstant('{localappdata}\Microsoft\WindowsApps\winget.exe');
  if FileExists(Winget) then
    Exec(Winget, 'install --id {#AppleDevicesStoreId} --source msstore --exact --silent '
      + '--accept-package-agreements --accept-source-agreements --disable-interactivity',
      '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  if (ResultCode <> 0) and not AppleDriverInstalled() and not WizardSilent() then
    if MsgBox(FmtMessage(CustomMessage('AppleFailed'), [IntToStr(ResultCode)]), mbError, MB_YESNO) = IDYES then
      ShellExec('open', 'ms-windows-store://pdp/?ProductId={#AppleDevicesStoreId}', '', '', SW_SHOWNORMAL,
        ewNoWait, ErrorCode);
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
  begin
    // ffplay isn't used by the app (about 90 MB)
    DeleteFile(ExpandConstant('{app}\ffmpeg\{#FfmpegBuild}\bin\ffplay.exe'));
    if WizardIsComponentSelected('apple') and not AppleDriverInstalled() then
      InstallAppleDevices();
  end;
end;

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
    SuppressibleMsgBox(CustomMessage('PathTooLong'), mbError, MB_OK, IDOK);
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
