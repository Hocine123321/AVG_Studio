<#
    AVG Studio - build tool. PowerShell port of the original
    tools/build_flatpak.sh (bash) - see git history for that version if
    something here needs cross-checking against it.

    Ported so the app's build tooling has one language across both
    platforms, matching tools/make_release.ps1's own conventions (this
    file follows that script's house style: Step/Note helpers,
    $ErrorActionPreference = 'Stop', $PSScriptRoot-based self-location,
    "& cmd; if ($LASTEXITCODE -ne 0) { throw ... }" after every external
    call) rather than reinventing one. Runs the same on Windows or Linux
    wherever `pwsh` and the underlying tools (flatpak/flatpak-builder/npm)
    are present - flatpak itself is Linux-only, so in practice this only
    does anything on Linux, but the script itself has no OS-specific
    syntax of its own.

    Two things this does, either picked from a menu or by flag:

      1) Launch from source - runs the API + a browser tab against the
         current source tree, no packaging. Fast; use this to iterate.
      2) Build flatpak package - a clean flatpak-builder build, installed
         locally and exported as a single shareable .flatpak bundle.

    Usage:
        pwsh tools/build_flatpak.ps1                    interactive menu
        pwsh tools/build_flatpak.ps1 -Launch             skip the menu
        pwsh tools/build_flatpak.ps1 -BuildPackage       skip the menu
        Either of the above also takes -SkipWebBuild, to reuse an
        already-built web/dist instead of rebuilding it.

        -BuildPackage runs tools/verify_release.py first and stops if it
        fails, so a broken tree never becomes a bundle. -SkipVerification
        bypasses that: for iterating on packaging itself, never for a
        release you publish.
#>
[CmdletBinding()]
param(
    # Skip the interactive menu and go straight to "Launch from source".
    [switch]$Launch,

    # Skip the interactive menu and go straight to "Build flatpak package".
    [switch]$BuildPackage,

    # Reuse an existing web/dist instead of running npm install/build -
    # for the fast dev loop, once the frontend has already been built and
    # nothing under web/ has changed since.
    [switch]$SkipWebBuild,

    # Package without running the release gate (tools/verify_release.py).
    # Prints a warning. Never for a build you intend to publish.
    [switch]$SkipVerification
)

$ErrorActionPreference = 'Stop'

if ($Launch -and $BuildPackage) {
    throw "-Launch and -BuildPackage are mutually exclusive - pick one."
}

$Repo = Split-Path -Parent $PSScriptRoot
Set-Location $Repo

$AppId = 'io.github.Hocine123321.AVGStudio'
$Manifest = Join-Path $Repo "packaging/flatpak/$AppId.yml"
# The runtime/SDK version this manifest targets - kept as one variable
# so a future bump only needs changing here, not at every call site
# that names it.
$RuntimeVersion = '25.08'
$Branch = 'stable'
$BuildDir = Join-Path $Repo 'build-dir'
$DistDir = Join-Path $Repo 'flatpak-dist'
$RepoDir = Join-Path $DistDir 'repo'
$BundlePath = Join-Path $DistDir 'AVGStudio.flatpak'
# .venv-linux, not .venv or .venv-build: a separate dev venv so this
# tool's own dependency footprint (this script installs only '.[dev]' -
# never media/gui extras) never fights with whatever else those other
# two venvs are used for.
$DevVenv = Join-Path $Repo '.venv-linux'
# The release gate's own venv - '.[dev,media]', which the dev venv above
# deliberately never is (see Install-DevVenv). Separate on purpose: putting
# media in the dev venv would stop it catching the bug class it exists for.
$VerifyVenv = Join-Path $Repo '.venv-verify'
$DevPort = 8756
$DevToken = 'devtoken'

function Step([string]$Text) { Write-Host "`n=== $Text ===" -ForegroundColor Cyan }
function Note([string]$Text) { Write-Host "    $Text" -ForegroundColor DarkGray }

function Test-CommandExists([string]$Name) {
    return [bool](Get-Command $Name -ErrorAction SilentlyContinue)
}

function Invoke-External {
    <#
        Runs an external command and throws with its own exit code on
        failure. make_release.ps1's own two external calls each check
        $LASTEXITCODE inline; this script has closer to a dozen, so the
        check lives here once instead of being repeated at every call
        site (verified separately that $LASTEXITCODE from a native
        command run inside the scriptblock does propagate out through
        this wrapper correctly, rather than assumed).
    #>
    param([string]$Description, [scriptblock]$Command)
    & $Command
    if ($LASTEXITCODE -ne 0) { throw "$Description failed (exit $LASTEXITCODE)" }
}

function Build-Web {
    Step 'Building the frontend'
    $indexHtml = Join-Path $Repo 'web/dist/index.html'
    if ($SkipWebBuild) {
        if (-not (Test-Path $indexHtml)) {
            throw "-SkipWebBuild was passed but web/dist/index.html does not exist - nothing to reuse."
        }
        Note '-SkipWebBuild: reusing the existing web/dist'
        return
    }
    if (-not (Test-CommandExists 'npm')) {
        throw "npm is not installed. On CachyOS/Arch: sudo pacman -S npm - or pass -SkipWebBuild if web/dist is already built."
    }
    Push-Location (Join-Path $Repo 'web')
    try {
        if (-not (Test-Path 'node_modules')) {
            Note 'web/node_modules missing, running npm install'
            Invoke-External 'npm install' { npm install }
        }
        Note 'npm run build'
        Invoke-External 'npm run build' { npm run build }
    } finally {
        Pop-Location
    }
    if (-not (Test-Path $indexHtml)) {
        throw "npm run build finished but web/dist/index.html is still missing - the build did not actually produce output."
    }
    Note 'web/dist built'
}

# ── action 1: launch from source ────────────────────────────────────

function Install-DevVenv {
    Step 'Setting up the local dev environment'
    if (-not (Test-CommandExists 'python3')) {
        throw "python3 is not installed."
    }
    if (-not (Test-Path $DevVenv)) {
        Note "creating $DevVenv"
        Invoke-External 'python3 -m venv' { python3 -m venv $DevVenv }
        Invoke-External 'pip upgrade' { & "$DevVenv/bin/pip" install -q --upgrade pip }
    }
    # '.[dev]' only, deliberately never '.[dev,media]' - this is the
    # fast dev loop's whole point (see tools/dev_serve_linux.py's own
    # docstring, and the real bug this distinction already caught:
    # avg/pipeline/phases/visuals.py once had a module-level Pillow
    # import that broke GET /api/settings under exactly this venv).
    Note "pip install -e '.[dev]' (fast once already satisfied)"
    Invoke-External "pip install -e '.[dev]'" { & "$DevVenv/bin/pip" install -q -e '.[dev]' }
    Note "dev venv ready at $DevVenv"
}

function Start-FromSource {
    Install-DevVenv
    Build-Web
    Step 'Launching from source'
    $url = "http://127.0.0.1:$DevPort/?token=$DevToken"
    if (Test-CommandExists 'xdg-open') {
        <#
            Backgrounded and given a moment to let the dev server below
            actually start accepting connections first - a browser tab
            opened before the server is listening just shows a
            connection error instead of the app. Never allowed to fail
            the whole script: no browser (a headless box, a browser
            that's not configured) is not a reason to refuse to start
            the server.

            Spawned via `bash -c` + Start-Process, NOT Start-Job:
            verified directly (not assumed) that a Start-Job is tied to
            this script's own process and is killed the instant it
            exits, unlike bash's `&`-backgrounded subshell, which
            survives an `exec` into a long-running replacement process -
            Start-Process is what actually reproduces that "survives the
            parent" behaviour here. The single-string form of
            -ArgumentList is deliberate too, not a style choice: also
            verified directly that passing an array of separate argument
            strings to Start-Process on this platform silently fails to
            reach the child process's own argv (bash ran but never saw
            its -c script at all) - only the single pre-joined command-
            line string form actually works.
        #>
        $inner = "sleep 1 && xdg-open '$url' >/dev/null 2>&1"
        Start-Process -FilePath 'bash' -ArgumentList "-c `"$inner`"" | Out-Null
    }
    Write-Host "`n$url" -ForegroundColor White
    Write-Host "Ctrl+C to stop.`n" -ForegroundColor DarkGray
    & "$DevVenv/bin/python" (Join-Path $Repo 'tools/dev_serve_linux.py')
    if ($LASTEXITCODE -ne 0) { throw "dev_serve_linux.py exited with code $LASTEXITCODE" }
}

# ── action 2: build flatpak package ─────────────────────────────────

function Test-FlatpakPrerequisites {
    Step 'Checking flatpak prerequisites'
    foreach ($cmd in @('flatpak', 'flatpak-builder', 'npm')) {
        if (-not (Test-CommandExists $cmd)) {
            throw "$cmd is not installed. On CachyOS/Arch: sudo pacman -S $cmd"
        }
    }
    $flatpakVersion = ((& flatpak --version) -replace '^\S+\s+', '').Trim()
    Note "flatpak $flatpakVersion, flatpak-builder, npm present"

    $remotes = & flatpak remote-list
    if (-not ($remotes -match '^flathub')) {
        Note 'adding the flathub remote'
        Invoke-External 'flatpak remote-add' {
            flatpak remote-add --if-not-exists flathub https://flathub.org/repo/flathub.flatpakrepo
        }
    }
    Note 'flathub remote configured'

    foreach ($ref in @("org.freedesktop.Platform//$RuntimeVersion", "org.freedesktop.Sdk//$RuntimeVersion")) {
        & flatpak info $ref *> $null
        if ($LASTEXITCODE -ne 0) {
            Note "installing $ref"
            Invoke-External 'flatpak install' {
                flatpak install --noninteractive --no-related flathub $ref
            }
        }
    }
    Note "org.freedesktop.Platform/Sdk//$RuntimeVersion installed"
}

function Invoke-Verification {
    if ($SkipVerification) {
        Write-Warning '-SkipVerification: building WITHOUT the release gate. Do not publish this.'
        return
    }
    Step 'Verifying the tree (tools/verify_release.py)'
    if (-not (Test-CommandExists 'python3')) {
        throw "python3 is not installed."
    }
    if (-not (Test-Path $VerifyVenv)) {
        Note "creating $VerifyVenv"
        Invoke-External 'python3 -m venv' { python3 -m venv $VerifyVenv }
        Invoke-External 'pip upgrade' { & "$VerifyVenv/bin/pip" install -q --upgrade pip }
    }
    # The tests and the Deep Check need the media extra (numpy/Pillow/pydub -
    # Voice has no path without it). Fast once already satisfied.
    Note "pip install -e '.[dev,media]' (fast once already satisfied)"
    Invoke-External "pip install -e '.[dev,media]'" { & "$VerifyVenv/bin/pip" install -q -e '.[dev,media]' }
    & "$VerifyVenv/bin/python" (Join-Path $Repo 'tools/verify_release.py')
    if ($LASTEXITCODE -ne 0) {
        throw "verification failed - not building. Fix the failure above, or pass -SkipVerification to package anyway."
    }
}

function Build-FlatpakPackage {
    Test-FlatpakPrerequisites
    Invoke-Verification
    Build-Web

    Step 'Building (clean build - installs locally and exports a bundle)'
    New-Item -ItemType Directory -Force -Path $DistDir | Out-Null
    Invoke-External 'flatpak-builder' {
        flatpak-builder --user --install "--repo=$RepoDir" --force-clean $BuildDir $Manifest
    }

    Step 'Bundling into a single .flatpak file'
    Invoke-External 'flatpak build-bundle' {
        flatpak build-bundle $RepoDir $BundlePath $AppId $Branch
    }

    $size = ((& du -h $BundlePath) -split '\s+')[0]
    Write-Host ''
    Write-Host "  Installed locally and built $BundlePath ($size)" -ForegroundColor Green
    Write-Host "`nRun it now:" -ForegroundColor DarkGray
    Write-Host "    flatpak run $AppId"
    Write-Host "`nOr hand $BundlePath to someone else - they install it with:" -ForegroundColor DarkGray
    Write-Host "    flatpak install --user $BundlePath`n"
}

# ── entry point ──────────────────────────────────────────────────────

Write-Host ''
Write-Host '╔══════════════════════════════════════════════════╗' -ForegroundColor Cyan
Write-Host '║              AVG Studio — Build Tool              ║' -ForegroundColor Cyan
Write-Host '╚══════════════════════════════════════════════════╝' -ForegroundColor Cyan
Write-Host ''

$action = $null
if ($Launch) { $action = 'launch' }
elseif ($BuildPackage) { $action = 'build-package' }

if (-not $action) {
    Write-Host '  1) Launch from source     - browser tab, no packaging (fast - use this to iterate)'
    Write-Host '  2) Build flatpak package  - clean build, installs locally + a shareable .flatpak file'
    Write-Host '  3) Quit'
    Write-Host ''
    while (-not $action) {
        $choice = Read-Host 'Choose an option [1-3]'
        switch ($choice) {
            '1' { $action = 'launch' }
            '2' { $action = 'build-package' }
            '3' { Write-Host 'Bye.'; exit 0 }
            default { Write-Host 'Not a valid choice - enter 1, 2, or 3.' }
        }
    }
}

switch ($action) {
    'launch' { Start-FromSource }
    'build-package' { Build-FlatpakPackage }
}
