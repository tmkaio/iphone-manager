<#
.SYNOPSIS
  Builds the self-contained app folder and the Windows installer for iPhone Manager.

.DESCRIPTION
  1. Downloads the official CPython embeddable package matching the build Python (3.13, x64).
  2. pip-installs requirements.txt (locked by installer/constraints.txt) into its Lib\site-packages.
  3. Adds tkinter (+ Tcl/Tk) from the build Python (the embeddable package doesn't ship it).
  4. Copies the app, run.py, LICENSE and generates THIRD-PARTY-NOTICES.txt.
  5. Smoke-tests the built folder (GET / and /api/devices on a spare port).
  6. Compiles installer/iphone-manager.iss with Inno Setup (ISCC.exe).

  Output: build\app (app folder), dist\iPhoneManager-Setup-<version>.exe

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File installer\build.ps1
  powershell -ExecutionPolicy Bypass -File installer\build.ps1 -Python "C:\Python313\python.exe" -SkipInstaller
#>
[CmdletBinding()]
param(
    # Python 3.x x64 used to run pip; its major.minor.patch selects the embeddable package.
    [string]$Python = "",
    [string]$Iscc = "",
    [int]$SmokePort = 8799,
    [switch]$SkipSmokeTest,
    [switch]$SkipInstaller
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"   # Invoke-WebRequest is very slow with the progress bar

$Root = Split-Path -Parent $PSScriptRoot
$BuildDir = Join-Path $Root "build"
$AppDir = Join-Path $BuildDir "app"
$PyDir = Join-Path $AppDir "python"
$SitePackages = Join-Path $PyDir "Lib\site-packages"
$CacheDir = Join-Path $BuildDir "cache"
$DistDir = Join-Path $Root "dist"

function Invoke-Checked {
    param([string]$Exe, [string[]]$Arguments)
    & $Exe @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$Exe $($Arguments -join ' ') failed with exit code $LASTEXITCODE" }
}

# ---------- version ----------
$initPy = Get-Content (Join-Path $Root "app\__init__.py") -Raw
if ($initPy -notmatch '__version__\s*=\s*"([^"]+)"') { throw "__version__ not found in app\__init__.py" }
$Version = $Matches[1]
Write-Host "iPhone Manager $Version" -ForegroundColor Cyan

# ---------- build Python ----------
if (-not $Python) {
    $py = Get-Command py -ErrorAction SilentlyContinue
    if ($py) {
        $Python = (& py -3.13 -c "import sys; print(sys.executable)" 2>$null)
    }
    if (-not $Python) { $Python = (Get-Command python -ErrorAction Stop).Source }
}
$info = (& $Python -c "import sys, platform, struct; print(platform.python_version(), struct.calcsize('P')*8, sys.base_prefix, sep='|')").Trim()
$PyVersion, $Bits, $BasePrefix = $info.Split("|")
if ($Bits -ne "64") { throw "A 64-bit Python is required (got $Bits-bit $Python)" }
$Tag = ($PyVersion.Split(".")[0..1] -join "")    # e.g. 313
Write-Host "Build Python: $Python ($PyVersion, base $BasePrefix)"

# ---------- clean ----------
if (Test-Path $AppDir) { Remove-Item -Recurse -Force $AppDir }
New-Item -ItemType Directory -Force $PyDir, $CacheDir, $DistDir | Out-Null

# ---------- embeddable Python ----------
$embedZip = Join-Path $CacheDir "python-$PyVersion-embed-amd64.zip"
if (-not (Test-Path $embedZip)) {
    $url = "https://www.python.org/ftp/python/$PyVersion/python-$PyVersion-embed-amd64.zip"
    Write-Host "Downloading $url"
    Invoke-WebRequest -Uri $url -OutFile "$embedZip.part" -UseBasicParsing
    Move-Item -Force "$embedZip.part" $embedZip
}
Expand-Archive -Path $embedZip -DestinationPath $PyDir -Force

# python3XX._pth: when present it fully defines sys.path. ".." is the app root, so run.py and
# "python -m app.xxx" (used by the app for its helper processes) import the app package.
$pth = Join-Path $PyDir "python$Tag._pth"
@(
    "python$Tag.zip",
    ".",
    "Lib",
    "Lib\site-packages",
    "..",
    "import site"
) | Set-Content -Encoding ascii $pth

# ---------- dependencies ----------
Write-Host "Installing dependencies into $SitePackages"
Invoke-Checked $Python @("-m", "pip", "install", "--disable-pip-version-check", "--no-input",
    "--only-binary", ":all:", "--no-compile",
    "--target", $SitePackages,
    "-r", (Join-Path $Root "requirements.txt"),
    "-c", (Join-Path $Root "installer\constraints.txt"))
# console-script launchers in bin/Scripts point at the build machine's Python: drop them
foreach ($d in @("bin", "Scripts")) {
    $p = Join-Path $SitePackages $d
    if (Test-Path $p) { Remove-Item -Recurse -Force $p }
}
# jedi (pulled in by IPython for pymobiledevice3's interactive shells) ships ~25 MB of type stubs with
# very deep paths; they are only used for code completion and push installs past MAX_PATH
$stubs = Join-Path $SitePackages "jedi\third_party"
if (Test-Path $stubs) { Remove-Item -Recurse -Force $stubs }

# ---------- tkinter (folder/file pickers) ----------
$tkSrc = Join-Path $BasePrefix "Lib\tkinter"
if (-not (Test-Path $tkSrc)) { throw "tkinter not found in $BasePrefix (install Python with Tcl/Tk)" }
New-Item -ItemType Directory -Force (Join-Path $PyDir "Lib") | Out-Null
Copy-Item -Recurse $tkSrc (Join-Path $PyDir "Lib\tkinter")
foreach ($f in @("_tkinter.pyd", "tcl86t.dll", "tk86t.dll", "zlib1.dll")) {
    $src = Join-Path $BasePrefix "DLLs\$f"
    if (Test-Path $src) { Copy-Item $src $PyDir -Force }
    elseif ($f -ne "zlib1.dll") { throw "$src not found" }
}
New-Item -ItemType Directory -Force (Join-Path $PyDir "tcl") | Out-Null
Get-ChildItem (Join-Path $BasePrefix "tcl") -Directory | Where-Object { $_.Name -ne "nmake" } |
    ForEach-Object { Copy-Item -Recurse $_.FullName (Join-Path $PyDir "tcl\$($_.Name)") }
Get-ChildItem $PyDir -Recurse -Directory -Filter "test*" |
    Where-Object { $_.FullName -like "*\Lib\tkinter\test*" } | Remove-Item -Recurse -Force

# ---------- app files ----------
Copy-Item -Recurse (Join-Path $Root "app") (Join-Path $AppDir "app")
Get-ChildItem (Join-Path $AppDir "app") -Recurse -Directory -Filter "__pycache__" | Remove-Item -Recurse -Force
foreach ($f in @("run.py", "LICENSE", "README.md")) {
    Copy-Item (Join-Path $Root $f) $AppDir
}
Copy-Item (Join-Path $Root "installer\app.ico") $AppDir

$embedPy = Join-Path $PyDir "python.exe"
Invoke-Checked $embedPy @((Join-Path $Root "installer\gen_notices.py"), $SitePackages,
    (Join-Path $AppDir "THIRD-PARTY-NOTICES.txt"))

# byte-compile with the bundled interpreter (faster first start; ignore the odd py2-only test file)
& $embedPy -m compileall -q -j 0 $SitePackages (Join-Path $PyDir "Lib\tkinter") (Join-Path $AppDir "app") | Out-Null

# import check of every module the app loads lazily, so a missing package fails the build
Invoke-Checked $embedPy @("-c", @"
import importlib, sys
mods = ['app.server', 'app.mirror', 'app.mount_ddi', 'app.restore_runner', 'app.irecv_md', 'app.ptp',
        'pymobiledevice3.__main__', 'pymobiledevice3.cli.restore', 'pymobiledevice3.restore.recovery',
        'pyicloud', 'pillow_heif', 'av', 'libusb', 'usb.core', 'win32com.shell.shell', 'pythoncom', 'tkinter',
        'uvicorn', 'httptools', 'websockets']
for m in mods:
    importlib.import_module(m)
import tkinter
tkinter.Tcl().eval('info patchlevel')
print('imports ok:', len(mods), 'modules; python', sys.version.split()[0])
"@)

# ---------- smoke test ----------
if (-not $SkipSmokeTest) {
    Write-Host "Smoke test on port $SmokePort"
    $log = Join-Path $BuildDir "smoke.log"
    $proc = Start-Process -FilePath $embedPy -ArgumentList @("run.py", "--no-window", "--port", "$SmokePort") `
        -WorkingDirectory $AppDir -PassThru -NoNewWindow -RedirectStandardError $log -RedirectStandardOutput "$log.out"
    try {
        $ok = $false
        for ($i = 0; $i -lt 60 -and -not $ok; $i++) {
            Start-Sleep -Seconds 1
            if ($proc.HasExited) { break }
            try {
                $r = Invoke-WebRequest -Uri "http://127.0.0.1:$SmokePort/" -UseBasicParsing -TimeoutSec 5
                $ok = ($r.StatusCode -eq 200)
            } catch { }
        }
        if (-not $ok) { Get-Content $log -ErrorAction SilentlyContinue; throw "server didn't serve / on port $SmokePort" }
        # 200 = device list; 503 + JSON detail = the handler ran but Apple Mobile Device Support isn't
        # installed (e.g. on a CI runner). Anything else (500, no answer) fails the build.
        try {
            $d = Invoke-WebRequest -Uri "http://127.0.0.1:$SmokePort/api/devices" -UseBasicParsing -TimeoutSec 60
            Write-Host "GET / -> 200, GET /api/devices -> 200 ($($d.Content.Length) bytes)" -ForegroundColor Green
        } catch {
            $resp = $_.Exception.Response
            if ($resp -and [int]$resp.StatusCode -eq 503) {
                Write-Host "GET / -> 200, GET /api/devices -> 503 (no Apple Mobile Device service on this machine)" -ForegroundColor Yellow
            } else {
                Get-Content $log -ErrorAction SilentlyContinue | Select-Object -Last 40
                throw "/api/devices failed: $($_.Exception.Message)"
            }
        }
    } finally {
        if (-not $proc.HasExited) { Stop-Process -Id $proc.Id -Force }
    }
}

$files = Get-ChildItem $AppDir -Recurse -File
$size = ($files | Measure-Object Length -Sum).Sum
# longest path inside the app folder; the installer refuses install dirs that would exceed MAX_PATH
$MaxRelPath = ($files | ForEach-Object { $_.FullName.Length - $AppDir.Length - 1 } | Measure-Object -Maximum).Maximum
Write-Host ("App folder: {0} ({1:N0} MB, {2} files, longest relative path {3})" -f $AppDir, ($size / 1MB), $files.Count, $MaxRelPath)
if ($MaxRelPath -gt 170) { throw "A file path in the app folder is $MaxRelPath chars long; trim it to stay under MAX_PATH" }

# ---------- installer ----------
if ($SkipInstaller) { return }
if (-not $Iscc) {
    $candidates = @(
        "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
        "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
        "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe"
    )
    $Iscc = $candidates | Where-Object { Test-Path $_ } | Select-Object -First 1
    if (-not $Iscc) {
        $cmd = Get-Command ISCC.exe -ErrorAction SilentlyContinue
        if ($cmd) { $Iscc = $cmd.Source }
    }
}
if (-not $Iscc) { throw "Inno Setup 6 (ISCC.exe) not found. Install it (winget install JRSoftware.InnoSetup) or pass -Iscc." }
Invoke-Checked $Iscc @("/Qp", "/DAppVersion=$Version", "/DSourceDir=$AppDir", "/DMaxRelPath=$MaxRelPath", "/O$DistDir",
    (Join-Path $PSScriptRoot "iphone-manager.iss"))
$setup = Join-Path $DistDir "iPhoneManager-Setup-$Version.exe"
Write-Host ("Installer: {0} ({1:N1} MB)" -f $setup, ((Get-Item $setup).Length / 1MB)) -ForegroundColor Green
