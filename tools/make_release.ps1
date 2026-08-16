<#
    Builds AVG Studio from whatever is on disk RIGHT NOW.

      "AVG APP\"                 the app, ready to run - double-click Run AVG.bat
      "installer\AVG-Setup.exe"  the installer, to carry to another machine

    Run it any time the source changes. It always rebuilds from the live files;
    there is no cache to go stale and nothing to remember to update first.

    With no switches and a real console attached, it shows a menu:

        === AVG Studio - Build ===
          [1] Refresh app only        - AVG APP\ from source, no installer
          [2] Refresh installer only  - repack the app already in dist\AVG
          [3] Refresh both            - fast installer compression (default)
          [4] Refresh both - RELEASE  - full lzma2/max compression, for shipping
          [Q] Quit

    Pass a switch (below) to skip the menu and run that choice directly -
    scripted/automated calls should always do this.

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
        powershell -ExecutionPolicy Bypass -File tools\make_release.ps1                # menu
        powershell ... -File tools\make_release.ps1 -SkipInstaller                     # app only
        powershell ... -File tools\make_release.ps1 -InstallerOnly                     # installer only
        powershell ... -File tools\make_release.ps1 -Release                           # both, ship-ready
        powershell ... -File tools\make_release.ps1 -NoMenu                            # both, fast, no prompt
#>
[CmdletBinding()]
param(
    # App only: refresh "AVG APP\" and skip the Inno Setup step entirely.
    # Useful while iterating on app code with no installer changes to verify.
    [switch]$SkipInstaller,
    # Installer only: skip the web build and the PyInstaller freeze, and just
    # repack whatever is already frozen at dist\AVG\ into AVG-Setup.exe. For
    # when only installer\avg.iss (icon, wizard text, ...) changed and the app
    # itself didn't - the app step is the slow part, so skip it. Fails loudly
    # if dist\AVG\AVG.exe doesn't exist yet (run an app refresh first).
    [switch]$InstallerOnly,
    # Full lzma2/max compression instead of the default lzma2/fast. Slower
    # (minutes, not seconds, for ~430MB) but produces the smaller installer
    # that's actually worth shipping. Use this only for the build you publish.
    [switch]$Release,
    # Skip the interactive menu and run with whatever switches were passed
    # (i.e. "both, fast" if nothing else was passed). For scripted/CI calls
    # that shouldn't ever block on a prompt.
    [switch]$NoMenu
)

function Show-BuildMenu {
    Write-Host "`n=== AVG Studio - Build ===" -ForegroundColor Cyan
    Write-Host '  [1] Refresh app only        - AVG APP\ from source, no installer' -ForegroundColor White
    Write-Host '  [2] Refresh installer only   - repack the app already in dist\AVG' -ForegroundColor White
    Write-Host '  [3] Refresh both             - fast installer compression (default)' -ForegroundColor White
    Write-Host '  [4] Refresh both - RELEASE   - full lzma2/max compression, for shipping' -ForegroundColor White
    Write-Host '  [Q] Quit' -ForegroundColor DarkGray
    while ($true) {
        $pick = (Read-Host "`nSelect").Trim().ToUpper()
        switch ($pick) {
            '1' { return @{ SkipInstaller = $true;  InstallerOnly = $false; Release = $false } }
            '2' { return @{ SkipInstaller = $false; InstallerOnly = $true;  Release = $false } }
            '3' { return @{ SkipInstaller = $false; InstallerOnly = $false; Release = $false } }
            '4' { return @{ SkipInstaller = $false; InstallerOnly = $false; Release = $true  } }
            'Q' { Write-Host 'Cancelled.' -ForegroundColor DarkGray; exit 0 }
            default { Write-Host '  Invalid option - enter 1, 2, 3, 4, or Q.' -ForegroundColor Red }
        }
    }
}

# Only show the menu when nothing was passed AND there's a real console to read
# from - a scripted/CI/piped call must never block waiting on Read-Host.
$explicitChoice = $SkipInstaller -or $InstallerOnly -or $Release -or $NoMenu
$hasConsole = [Environment]::UserInteractive -and -not [Console]::IsInputRedirected
if (-not $explicitChoice -and $hasConsole) {
    $picked = Show-BuildMenu
    $SkipInstaller = $picked.SkipInstaller
    $InstallerOnly = $picked.InstallerOnly
    $Release       = $picked.Release
}

if ($SkipInstaller -and $InstallerOnly) {
    throw "-SkipInstaller (app only) and -InstallerOnly (installer only) are mutually exclusive."
}
if ($SkipInstaller -and $Release) {
    throw "-SkipInstaller and -Release are mutually exclusive (there's no installer to compress)."
}

$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$appDir = Join-Path $repo 'AVG APP'
$started = Get-Date

# What this run actually does. InstallerOnly touches only dist\AVG-Setup.exe;
# everything else refreshes AVG APP\ (and the installer too, unless -SkipInstaller).
$doApp       = -not $InstallerOnly
$doInstaller = -not $SkipInstaller

function Step([string]$text) { Write-Host "`n=== $text ===" -ForegroundColor Cyan }
function Note([string]$text) { Write-Host "    $text" -ForegroundColor DarkGray }

# The build interpreter is NOT the system python: it carries pyinstaller and the
# media dependencies. Failing loudly here beats a confusing PyInstaller error
# three minutes later.
$py = Join-Path $repo '.venv-build\Scripts\python.exe'
if (-not (Test-Path $py)) {
    throw "Build interpreter missing: $py`nSee docs/decisions/2026-07-24-packaging.md for what it needs."
}

if ($InstallerOnly) {
    $frozenExe = Join-Path $repo 'dist\AVG\AVG.exe'
    if (-not (Test-Path $frozenExe)) {
        throw "InstallerOnly: no frozen app at $frozenExe yet. Run a '1' (app) or '3'/'4' (both) build first."
    }
}

if ($doApp) {
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
}

Step 'Freezing the app'
Push-Location $repo
try {
    if ($InstallerOnly) {
        Note 'Skipping the web build and PyInstaller freeze - repacking dist\AVG as-is.'
        if ($Release) {
            Note 'Full lzma2/max compression (-Release) - this is the slow part, on purpose.'
            & $py tools\build.py installer --release
        } else {
            Note 'Fast lzma2/fast compression (default). Pass -Release for the build you ship.'
            & $py tools\build.py installer
        }
    } elseif ($SkipInstaller) {
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

if ($doApp) {
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
}

if ($doInstaller) {
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
if ($doApp) {
    $appSize = [math]::Round(((Get-ChildItem $appDir -Recurse -File | Measure-Object Length -Sum).Sum) / 1MB)
    Write-Host ("    app      : {0}  ({1} MB)" -f $appDir, $appSize) -ForegroundColor Green
}
if ($doInstaller) {
    $inst = Join-Path $repo 'installer\AVG-Setup.exe'
    if (Test-Path $inst) {
        $instSize = [math]::Round((Get-Item $inst).Length / 1MB, 1)
        Write-Host ("    installer: {0}  ({1} MB)" -f $inst, $instSize) -ForegroundColor Green
    }
}
Write-Host ("    took {0:n1} min" -f ((Get-Date) - $started).TotalMinutes) -ForegroundColor DarkGray