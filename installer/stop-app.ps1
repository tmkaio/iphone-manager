# Stops iPhone Manager processes started from this installation (used by the installer/uninstaller).
# Only Python processes whose executable lives in this folder are touched.
$root = Split-Path -Parent $PSCommandPath
Get-Process -Name python, pythonw -ErrorAction SilentlyContinue |
    Where-Object { $_.Path -and $_.Path.StartsWith($root + "\", [System.StringComparison]::OrdinalIgnoreCase) } |
    Stop-Process -Force -ErrorAction SilentlyContinue
