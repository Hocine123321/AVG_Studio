<#
    Builds AVG Studio from whatever is on disk RIGHT NOW and produces two things:

      "AVG APP\"                 the app, ready to run - double-click Run AVG.bat
      "installer\AVG-Setup.exe"  the installer, to carry to another machine

    Run it any time the source changes. It always rebuilds from the live files;
    there is no cache to go stale and nothing to remember to update first.

    Two things make the default run fast:
      - The installer step uses lzma2/fast, not lzma2/max. Pass -Release for the
        build you're actually about to publish/ship - THAT one is worth the extra
        few minutes of lzma2/max compression on ~430MB. Every other run doesn't
        need to pay for it.
      - `npm run build` is skipped when nothing under web/ (src, public, config,
        lockfile) changed since the last successful build. Delete
        web\.build-hash to force a rebuild if this is ever wrong.
    PyInstaller's own build\ cache is left alone between runs on purpose - it's
    what makes a rebuild with only a few changed .py files fast. Don't add
    --clean to the pyinstaller call in tools/build.py; that throws the cache
    away and turns every run into a from-scratch freeze.

    Usage (from anywhere):
        powershell -ExecutionPolicy Bypass -File "D:\AVG_Studio\tools\make_release.ps1"
        powershell ... -File tools\make_release.ps1 -SkipInstaller   # app only, fastest
        powershell ... -File tools\make_release.ps1 -Release         # publishing: full lzma2/max
#>
[CmdletBinding()]
param(
    # Skip the Inno Setup step entirely. The app in "AVG APP\" is still
    # refreshed - useful while iterating on app code with no installer changes
    # to verify.
    [switch]$SkipInstaller,
    # Full lzma2/max compression instead of the default lzma2/fast. Slower
    # (minutes, not seconds, for ~430MB) but produces the smaller installer
    # that's actually worth shipping. Use this only for the build you publish.
    [switch]$Release
)

if ($SkipInstaller -and $Release) {
    throw "-SkipInstaller and -Release are mutually exclusive (there's no installer to compress)."
}

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
    # Hash everything that can affect the bundle: source, static assets, and
    # every config file that feeds vite/tsc. Sorted paths -> deterministic hash
    # regardless of filesystem enumeration order.
    $hashInputs = @(Get-ChildItem -Recurse -File -Path 'src', 'public' -EA SilentlyContinue) +
                  @(Get-Item -EA SilentlyContinue 'package.json', 'package-lock.json',
                    'vite.config.ts', 'tsconfig.json', 'tsconfig.app.json',
                    'tsconfig.node.json', 'index.html')
    $combined = ($hashInputs | Sort-Object FullName | Get-FileHash -Algorithm SHA256).Hash -join '|'
    $hashBytes = [Text.Encoding]::UTF8.GetBytes($combined)
    $hash = [BitConverter]::ToString([Security.Cryptography.SHA256]::Create().ComputeHash($hashBytes)) -replace '-', ''

    $hashFile = '.build-hash'
    $prevHash = if (Test-Path $hashFile) { (Get-Content $hashFile -Raw).Trim() } else { '' }

    if ($hash -eq $prevHash -and (Test-Path 'dist\index.html')) {
        Note 'web/ unchanged since the last build - skipping npm run build'
    } else {
        & npm run build
        if ($LASTEXITCODE -ne 0) { throw "npm run build failed ($LASTEXITCODE)" }
        Set-Content -Path $hashFile -Value $hash -NoNewline
    }
} finally { Pop-Location }

Step 'Freezing the app'
Push-Location $repo
try {
    if ($SkipInstaller) {
        & $py tools\build.py pyinstaller
    } elseif ($Release) {
        Note 'Full lzma2/max compression (-Release) - this is the slow part, on purpose.'
        & $py tools\build.py all --release
    } else {
        Note 'Fast lzma2/fast compression (default). Pass -Release for the build you ship.'
        & $py tools\build.py all
    }
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
