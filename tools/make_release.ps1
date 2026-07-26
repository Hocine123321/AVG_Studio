<#
    Builds AVG Studio from whatever is on disk RIGHT NOW and produces two things:

      "AVG APP\"                 the app, ready to run - double-click Run AVG.bat
      "installer\AVG-Setup.exe"  the installer, to carry to another machine

    Run it any time the source changes. It always rebuilds from the live files;
    there is no cache to go stale and nothing to remember to update first.

    Usage (from anywhere):
        powershell -ExecutionPolicy Bypass -File "D:\AVG_Studio\tools\make_release.ps1"
        powershell ... -File tools\make_release.ps1 -SkipInstaller   # app only, faster
#>
[CmdletBinding()]
param(
    # Skip the Inno Setup step. The app in "AVG APP\" is still refreshed - useful
    # while iterating, since compressing 425 MB is the slowest part by far.
    [switch]$SkipInstaller
)

$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$appDir = Join-Path $repo 'AVG APP'
$started = Get-Date

function Step([string]$text) { Write-Host "`n=== $text ===" -ForegroundColor Cyan }
function Note([string]$text) { Write-Host "    $text" -ForegroundColor DarkGray }

# The build interpreter is NOT the system python: it carries pyinstaller and the
# media dependencies. Failing loudly here beats a confusing PyInstaller error
# three minutes later.
$py = Join-Path $repo '.venv-build\Scripts\python.exe'
if (-not (Test-Path $py)) {
    throw "Build interpreter missing: $py`nSee docs/decisions/2026-07-24-packaging.md for what it needs."
}

Step 'Building the web UI'
Push-Location (Join-Path $repo 'web')
try {
    & npm run build
    if ($LASTEXITCODE -ne 0) { throw "npm run build failed ($LASTEXITCODE)" }
} finally { Pop-Location }

Step 'Freezing the app'
Push-Location $repo
try {
    if ($SkipInstaller) { & $py tools\build.py pyinstaller } else { & $py tools\build.py all }
    if ($LASTEXITCODE -ne 0) { throw "tools/build.py failed ($LASTEXITCODE)" }
} finally { Pop-Location }

Step 'Refreshing "AVG APP"'
$frozen = Join-Path $repo 'dist\AVG'
if (-not (Test-Path (Join-Path $frozen 'AVG.exe'))) { throw "Expected a frozen build at $frozen" }

# /MIR mirrors, so a file deleted from the source disappears here too - without
# it a renamed module would linger and the folder would drift from the source.
# Exclude the launcher: it is ours, not PyInstaller's, and would be deleted.
$null = robocopy $frozen $appDir /MIR /NFL /NDL /NJH /NJS /NP /XF 'Run AVG.bat'
if ($LASTEXITCODE -ge 8) { throw "robocopy failed ($LASTEXITCODE)" }

@'
@echo off
rem Runs AVG Studio from this folder. No install needed - this IS the app.
rem Rebuild it from source with "Build Installer.bat" in the parent folder.
start "" "%~dp0AVG.exe"
'@ | Set-Content -Path (Join-Path $appDir 'Run AVG.bat') -Encoding ASCII

if (-not $SkipInstaller) {
    Step 'Collecting the installer'
    $setup = Join-Path $repo 'dist\AVG-Setup.exe'
    if (Test-Path $setup) {
        $dest = Join-Path $repo 'installer\AVG-Setup.exe'
        Move-Item $setup $dest -Force
        Note "installer -> $dest"
    } else {
        Write-Warning "No installer at $setup - was the Inno Setup step skipped?"
    }
}

Step 'Done'
$appSize = [math]::Round(((Get-ChildItem $appDir -Recurse -File | Measure-Object Length -Sum).Sum) / 1MB)
Write-Host ("    app      : {0}  ({1} MB)" -f $appDir, $appSize) -ForegroundColor Green
if (-not $SkipInstaller) {
    $inst = Join-Path $repo 'installer\AVG-Setup.exe'
    if (Test-Path $inst) {
        $instSize = [math]::Round((Get-Item $inst).Length / 1MB, 1)
        Write-Host ("    installer: {0}  ({1} MB)" -f $inst, $instSize) -ForegroundColor Green
    }
}
Write-Host ("    took {0:n1} min" -f ((Get-Date) - $started).TotalMinutes) -ForegroundColor DarkGray
