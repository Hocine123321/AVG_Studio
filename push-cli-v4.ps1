#Requires -Version 7.0
<#
.SYNOPSIS
    push-cli v4 - interactive Git/GitHub workflow manager (Update / Undo / Redo).

.DESCRIPTION
    Cross-platform (Windows/Linux/macOS, PowerShell 7+) CLI that commits/pushes a local
    folder to GitHub, with a non-destructive Undo/Redo state engine and resilient
    gh-CLI -> REST API -> manual fallback for remote operations.

.PARAMETER Path
    Local folder to operate on. Prompted for interactively if omitted (non-interactive
    mode defaults to the current directory).

.PARAMETER Action
    Update | Pull | Undo | Redo. Omit to get an interactive menu.
    Pull overwrites the local FOLDER with the GitHub repo's contents - destructive,
    always backed up first, always requires explicit confirmation.

.PARAMETER RepoName
    GitHub repo name. Defaults to the folder name.

.PARAMETER Private
    Create as a private repo. Default: $true.

.PARAMETER Token
    GitHub PAT (scopes: repo, workflow). Falls back to $env:GH_TOKEN. Avoids the
    interactive paste prompt entirely if supplied.

.PARAMETER ForceReauth
    Wipe current gh auth session and re-authenticate.

.PARAMETER Force
    Skip all y/n confirmations.

.PARAMETER DryRun
    Simulate every mutating action (init/add/commit/push/reset/revert/gitignore edits/
    repo creation) without touching local or remote state. Read-only checks still run
    for accurate simulation.

.PARAMETER NonInteractive
    Never block on a prompt. Anything that would normally ask a question aborts with
    a clear error instead. Intended for CI/automation.

.PARAMETER MaxRetries
    Retry count for network-sensitive operations (fetch/push). Default: 3.

.PARAMETER SetDefaultPath
    Save this folder as the default and enable auto-use (no more path prompt on future
    runs). Also used for this run.

.PARAMETER ClearDefaultPath
    Disable auto-use and forget the saved default folder.

.EXAMPLE
    .\push-cli-v4.ps1 -Path "D:\AVG_Studio" -Action Update

.EXAMPLE
    .\push-cli-v4.ps1 -SetDefaultPath "D:\AVG_Studio" -Action Update

.EXAMPLE
    .\push-cli-v4.ps1 -Action Undo   # uses saved default folder automatically

.EXAMPLE
    .\push-cli-v4.ps1 -Path "D:\AVG_Studio" -Action Update -DryRun
#>

param(
    [string]$Path,
    [ValidateSet("Update", "Pull", "Undo", "Redo")]
    [string]$Action,
    [string]$RepoName,
    [bool]$Private = $true,
    [string]$Token,
    [switch]$ForceReauth,
    [switch]$Force,
    [switch]$DryRun,
    [switch]$NonInteractive,
    [int]$MaxRetries = 3,
    [string]$SetDefaultPath,
    [switch]$ClearDefaultPath
)

# Root-cause fix carried over from v3: native git/gh stderr must not be treated as a
# terminating error under $ErrorActionPreference = "Stop" (PS7+ behavior). Harmless on
# older hosts where the variable has no special meaning.
$PSNativeCommandUseErrorActionPreference = $false
$ErrorActionPreference = "Stop"
try { Set-PSReadLineOption -PredictionSource None -ErrorAction SilentlyContinue } catch {}

$script:HasGh = $false
$script:LogFile = $null

#region Helpers -------------------------------------------------------------

function Log($msg, $color = "Gray") {
    $ts = Get-Date -Format "HH:mm:ss"
    Write-Host "[$ts] $msg" -ForegroundColor $color
    if ($script:LogFile) { "$ts $msg" | Out-File -Append -FilePath $script:LogFile }
}

# When double-clicked / run via "Run with PowerShell", the host window closes the
# instant the script exits - any early exit (bad path, missing tool, etc.) looks like
# an instant crash with no visible message. This keeps the window open so it's readable.
function Exit-Safely([int]$Code = 0) {
    if (-not $NonInteractive) {
        Write-Host ""
        Read-Host "Press Enter to exit"
    }
    exit $Code
}

function Read-Safe($prompt) {
    if ($NonInteractive) {
        Log "Non-interactive mode - cannot prompt ('$prompt'). Aborting." Red
        throw "NonInteractivePromptBlocked"
    }
    try { return Read-Host $prompt }
    catch {
        Log "Read-Host failed, falling back to raw console read..." Yellow
        Write-Host "$prompt`: " -NoNewline
        return [Console]::ReadLine()
    }
}

function Show-FailureMenu($context) {
    if ($NonInteractive) { Log "Non-interactive mode - aborting after $context failure." Red; return "abort" }
    Write-Host ""
    Write-Host "Action failed ($context). Choose:" -ForegroundColor Yellow
    Write-Host "  1) Retry"
    Write-Host "  2) Change remote settings"
    Write-Host "  3) Abort safely"
    $sel = Read-Safe "Choice (1-3)"
    switch ($sel) {
        "1" { return "retry" }
        "2" { return "remote" }
        default { return "abort" }
    }
}

# Generic retry wrapper. $Action must return @{ ExitCode = <int>; Output = <string> }.
function Invoke-WithRetry {
    param([scriptblock]$Action, [string]$Description, [int]$Retries = $MaxRetries)
    $result = @{ ExitCode = -1; Output = "" }
    :attempts for ($i = 1; $i -le $Retries; $i++) {
        $result = & $Action
        if ($result.ExitCode -eq 0) { return $result }

        $out = [string]$result.Output
        if ($out -match "Authentication failed|401|403|Bad credentials|could not read Username") {
            Log "$Description failed: authentication problem." Red
            $choice = Show-FailureMenu "auth"
        } elseif ($out -match "Could not resolve host|timed out|Connection refused|Network is unreachable|Recv failure|SSL_ERROR") {
            Log "$Description failed: network problem (attempt $i/$Retries)." Yellow
            if ($i -lt $Retries) { Start-Sleep -Seconds (2 * $i); continue attempts }
            $choice = Show-FailureMenu "network"
        } else {
            $lastLine = ($out.Trim() -split "`n" | Select-Object -Last 1)
            Log "$Description failed: $lastLine" Red
            $choice = Show-FailureMenu "generic"
        }

        switch ($choice) {
            "retry"  { continue attempts }
            "remote" { return @{ ExitCode = -2; Output = "remote-change-requested" } }
            default  { return @{ ExitCode = -1; Output = "aborted-by-user" } }
        }
    }
    return $result
}

function Invoke-GitCapture([string[]]$GitArgs) {
    $out = & git @GitArgs 2>&1 | Out-String
    return @{ ExitCode = $LASTEXITCODE; Output = $out }
}

#endregion

#region State Engine (Undo/Redo) --------------------------------------------

function Get-StatePath {
    $gitDir = (git rev-parse --git-dir 2>$null)
    if (-not $gitDir) { return $null }
    $gitDir = $gitDir.Trim()
    if (-not [System.IO.Path]::IsPathRooted($gitDir)) { $gitDir = Join-Path (Get-Location).Path $gitDir }
    return Join-Path $gitDir "push-cli-state.json"
}

function Load-State {
    $path = Get-StatePath
    if ($path -and (Test-Path $path)) {
        try { return (Get-Content $path -Raw | ConvertFrom-Json -AsHashtable) } catch {}
    }
    return @{ Undone = @() }
}

function Save-State($state) {
    if ($DryRun) { return }
    $path = Get-StatePath
    if ($path) { $state | ConvertTo-Json -Depth 6 | Out-File -Encoding utf8 $path }
}

#endregion

#region Auth & Environment ---------------------------------------------------

function Test-GitHubReachable {
    try {
        $r = Invoke-WebRequest -Uri "https://github.com" -UseBasicParsing -TimeoutSec 6
        return $r.StatusCode -eq 200
    } catch { return $false }
}

function Ensure-GhAuth {
    if (-not $script:HasGh) { return }
    if ($ForceReauth) { gh auth logout -h github.com 2>$null }

    gh auth status 2>$null
    if ($LASTEXITCODE -eq 0 -and -not $ForceReauth) { return }

    if (-not $Token) { $Token = $env:GH_TOKEN }
    if ($Token) {
        Log "Using supplied token (param/env var)..." Gray
        ($Token.Trim()) | gh auth login --hostname github.com --git-protocol https --with-token 2>$null
        if ($LASTEXITCODE -ne 0) { Log "Token rejected. Check scopes (repo, workflow)." Red; Exit-Safely 1 }
        return
    }

    Log "Trying browser login (HTTPS)..." Yellow
    gh auth login --hostname github.com --git-protocol https --web 2>$null
    gh auth status 2>$null
    if ($LASTEXITCODE -ne 0) {
        Log "Browser auth failed. Create a token: https://github.com/settings/tokens/new (scopes: repo, workflow)" Yellow
        $pasted = Read-Safe "Paste token (blank to abort)"
        if (-not $pasted) { Log "Aborted - no auth." Red; Exit-Safely 1 }
        ($pasted.Trim()) | gh auth login --hostname github.com --git-protocol https --with-token 2>$null
        if ($LASTEXITCODE -ne 0) { Log "Token auth failed." Red; Exit-Safely 1 }
    }
}

function Test-Environment {
    if (-not (Get-Command git -ErrorAction SilentlyContinue)) { Log "git not found in PATH." Red; Exit-Safely 1 }
    $script:HasGh = [bool](Get-Command gh -ErrorAction SilentlyContinue)
    if (-not $script:HasGh) { Log "gh CLI not found - repo creation will use REST API / manual fallback." Yellow }

    if (-not (git config --global user.name 2>$null)) {
        Log "git user.name not set." Yellow
        $n = Read-Safe "Name for commits"
        if ($n) { git config --global user.name $n }
    }
    if (-not (git config --global user.email 2>$null)) {
        Log "git user.email not set." Yellow
        $e = Read-Safe "Email for commits"
        if ($e) { git config --global user.email $e }
    }

    if ($script:HasGh) {
        gh config set -h github.com git_protocol https 2>$null
        Ensure-GhAuth
    }
}

#endregion

#region Remote resilience ----------------------------------------------------

# gh -> GitHub REST API (needs a token) -> manual paste, in that order.
function New-GitHubRepo {
    param([string]$Owner, [string]$Name, [bool]$Private)

    if ($DryRun) { Log "[DRYRUN] would create GitHub repo $Owner/$Name" DarkYellow; return $true }

    if ($script:HasGh) {
        $vis = if ($Private) { "--private" } else { "--public" }
        gh repo create $Name $vis --source=. --remote=origin 2>$null
        if ($LASTEXITCODE -eq 0) { Log "Created repo via gh CLI" Green; return $true }

        gh repo view "$Owner/$Name" 2>$null
        if ($LASTEXITCODE -eq 0) { Log "Repo already existed, reusing it" Yellow; return $true }
        Log "gh repo create failed - falling back to REST API..." Yellow
    }

    $restToken = if ($Token) { $Token } else { $env:GH_TOKEN }
    if (-not $restToken) {
        Log "No gh CLI success and no token for REST fallback." Red
        Log "Create the repo manually: https://github.com/new" Yellow
        $manualUrl = Read-Safe "Paste the new repo's HTTPS clone URL (blank to abort)"
        if (-not $manualUrl) { return $false }
        git remote add origin $manualUrl 2>$null
        return $true
    }

    try {
        $body = @{ name = $Name; private = $Private } | ConvertTo-Json
        $headers = @{ Authorization = "token $($restToken.Trim())"; "User-Agent" = "push-cli-v4" }
        Invoke-RestMethod -Uri "https://api.github.com/user/repos" -Method Post -Headers $headers -Body $body | Out-Null
        Log "Created repo via REST API fallback" Green
        return $true
    } catch {
        if ($_.Exception.Message -match "already exists") { Log "Repo already existed (REST), reusing it" Yellow; return $true }
        Log "REST API repo creation failed: $($_.Exception.Message)" Red
        return $false
    }
}

function Push-WithResilience {
    while ($true) {
        $result = Invoke-WithRetry -Description "git push" -Action {
            if ($DryRun) { Log "[DRYRUN] would run: git push -u origin main" DarkYellow; return @{ ExitCode = 0; Output = "" } }
            return Invoke-GitCapture @("push", "-u", "origin", "main")
        }
        if ($result.ExitCode -eq 0) { return $true }
        if ($result.ExitCode -eq -2) {
            $newUrl = Read-Safe "New remote URL (blank to cancel)"
            if ($newUrl) { git remote set-url origin $newUrl 2>$null; continue }
            return $false
        }
        return $false
    }
}

#endregion

#region Core Actions ----------------------------------------------------------

function Invoke-Update {
    Log "=== UPDATE: $Path -> $RepoName ($(if($Private){'private'}else{'public'})) ===" Cyan

    if (-not (Test-GitHubReachable)) { Log "Cannot reach github.com. Check network/VPN first." Red; return }

    if (-not (Test-Path ".git")) {
        if ($DryRun) { Log "[DRYRUN] would run: git init" DarkYellow }
        else { git init 2>$null | Out-Null; git branch -M main 2>$null }
        Log "Initialized new git repo" Yellow
    } else {
        Log "Existing git repo detected" Gray
    }

    if (-not (Test-Path ".gitignore")) {
        $lines = @("# General", "*.log", "*.tmp", ".env", "Thumbs.db", ".DS_Store", "")
        if (Get-ChildItem -Filter "*.py" -Recurse -File -ErrorAction SilentlyContinue) { $lines += @("# Python", "__pycache__/", "*.pyc", "venv/", ".venv/", "") }
        if (Test-Path "package.json") { $lines += @("# Node", "node_modules/", "dist/", "") }
        if (Get-ChildItem -Filter "*.ps1" -Recurse -File -ErrorAction SilentlyContinue) { $lines += @("# PowerShell", "*.ps1xml", "") }
        if ($DryRun) { Log "[DRYRUN] would generate .gitignore" DarkYellow }
        else { $lines -join "`n" | Out-File -Encoding utf8 ".gitignore"; Log "Generated .gitignore" Yellow }
    }

    if ($DryRun) { Log "[DRYRUN] would run: git add -A" DarkYellow } else { git add -A 2>$null }
    $stagedFiles = @(git diff --cached --name-only 2>$null)

    # Secret scan (staged files only)
    $secretPatterns = @('AKIA[0-9A-Z]{16}', 'sk-[a-zA-Z0-9]{20,}', 'ghp_[a-zA-Z0-9]{36}', 'AIza[0-9A-Za-z\-_]{35}')
    $flagged = @()
    foreach ($f in $stagedFiles) {
        if (Test-Path $f -PathType Leaf) {
            $content = Get-Content $f -Raw -ErrorAction SilentlyContinue
            if ($content) { foreach ($p in $secretPatterns) { if ($content -match $p) { $flagged += $f; break } } }
        }
    }
    if ($flagged.Count -gt 0) {
        Log "Possible secrets/API keys detected:" Red
        $flagged | ForEach-Object { Log "  - $_" Red }
        if (-not $Force -and -not $DryRun) {
            $ans = Read-Safe "Commit anyway? (y/N)"
            if ($ans -ne "y") { Log "Aborted by user." Red; return }
        }
    }

    # Oversized files - auto-excluded, since a >100MB file guarantees a rejected push
    $bigFiles = @()
    foreach ($f in $stagedFiles) {
        if (Test-Path $f -PathType Leaf) {
            $len = (Get-Item $f).Length
            if ($len -gt 95MB) { $bigFiles += [PSCustomObject]@{ Path = $f; MB = [math]::Round($len / 1MB, 1) } }
        }
    }
    if ($bigFiles.Count -gt 0) {
        Log "Oversized files (>95MB) - auto-excluding:" Yellow
        foreach ($bf in $bigFiles) {
            Log ("  - {0} ({1} MB)" -f $bf.Path, $bf.MB) Yellow
            if ($DryRun) { Log "[DRYRUN] would add to .gitignore and unstage" DarkYellow }
            else { Add-Content ".gitignore" "`n$($bf.Path)"; git reset -- "$($bf.Path)" 2>$null | Out-Null }
        }
    }

    $staged = git diff --cached --stat 2>$null
    if (-not $staged) {
        Log "Nothing new to commit." Gray
    } elseif ($DryRun) {
        Log "[DRYRUN] would commit staged changes" DarkYellow
    } else {
        git commit -m "Sync $(Get-Date -Format 'yyyy-MM-dd HH:mm')" 2>$null | Out-Null
        $newSha = (git rev-parse HEAD 2>$null).Trim()
        $state = Load-State
        $state.Undone = @()   # a fresh commit invalidates any pending redo chain
        Save-State $state
        Log "Committed changes ($($newSha.Substring(0, 7)))" Green
    }

    # Remote resolution
    $ghUser = $null
    if ($script:HasGh) { $ghUser = (gh api user -q ".login" 2>$null) }
    if ($ghUser) { $ghUser = $ghUser.Trim() }
    if (-not $ghUser) { $ghUser = Read-Safe "GitHub username (needed to build the remote URL)" }
    $httpsUrl = "https://github.com/$ghUser/$RepoName.git"
    $existingRemote = git remote get-url origin 2>$null

    if ($existingRemote -match '^git@github\.com:') {
        Log "Origin is SSH - converting to HTTPS" Yellow
        if (-not $DryRun) { git remote set-url origin $httpsUrl 2>$null }
        $existingRemote = $httpsUrl
    }
    if ($existingRemote -and ($existingRemote -notmatch [regex]::Escape($RepoName))) {
        Log "Origin points elsewhere: $existingRemote" Red
        if (-not $Force) {
            $ans = Read-Safe "Replace with $httpsUrl ? (y/N)"
            if ($ans -ne "y") { Log "Aborted - remote mismatch." Red; return }
        }
        if (-not $DryRun) { git remote remove origin 2>$null }
        $existingRemote = $null
    }

    if (-not $existingRemote) {
        $exists = $false
        if ($script:HasGh) { gh repo view "$ghUser/$RepoName" 2>$null; $exists = ($LASTEXITCODE -eq 0) }
        if ($exists) {
            Log "Repo already exists on GitHub." Gray
            if (-not $DryRun) { git remote add origin $httpsUrl 2>$null }
        } else {
            if (-not $Private -and -not $Force) {
                $ans = Read-Safe "Create as PUBLIC repo? (y/N)"
                if ($ans -ne "y") { Log "Aborted - visibility not confirmed." Red; return }
            }
            if (-not (New-GitHubRepo -Owner $ghUser -Name $RepoName -Private:$Private)) {
                Log "Could not create/attach a remote repo." Red; return
            }
        }
    }

    $currentBranch = (git rev-parse --abbrev-ref HEAD 2>$null)
    if ($currentBranch) { $currentBranch = $currentBranch.Trim() }
    if ($currentBranch -and $currentBranch -ne "main") {
        Log "Renaming local branch '$currentBranch' -> 'main'" Yellow
        if (-not $DryRun) { git branch -M main 2>$null }
    }

    if (Push-WithResilience) { Log "Done: https://github.com/$ghUser/$RepoName" Green }
    else { Log "Push did not complete." Red }
}

# Full-fidelity safety net before any destructive folder overwrite: zips everything
# outside .git (untracked/uncommitted files included - a git branch alone wouldn't
# save those) to a sibling folder. Falls back to a plain folder copy if zipping fails.
function Backup-Folder([string]$Reason) {
    $ts = Get-Date -Format "yyyyMMdd-HHmmss"
    $leaf = Split-Path $Path -Leaf
    $parent = Split-Path $Path -Parent
    $zipPath = Join-Path $parent "$($leaf)_backup_$ts.zip"

    if ($DryRun) { Log "[DRYRUN] would create backup: $zipPath" DarkYellow; return $zipPath }

    Log "Creating safety backup before $Reason..." Yellow
    try {
        $items = Get-ChildItem -Path $Path -Force | Where-Object { $_.Name -ne ".git" }
        if (-not $items) { Log "Folder is empty, nothing to back up." Gray; return $null }
        Compress-Archive -Path $items.FullName -DestinationPath $zipPath -Force
        Log "Backup saved: $zipPath" Green
        return $zipPath
    } catch {
        Log "Zip backup failed ($($_.Exception.Message)) - falling back to a folder copy." Yellow
        try {
            $copyDest = Join-Path $parent "$($leaf)_backup_$ts"
            Copy-Item -Path $Path -Destination $copyDest -Recurse -Force
            Remove-Item (Join-Path $copyDest ".git") -Recurse -Force -ErrorAction SilentlyContinue
            Log "Backup saved: $copyDest" Green
            return $copyDest
        } catch {
            Log "Backup failed entirely: $($_.Exception.Message)" Red
            return $null
        }
    }
}

# Overwrites the local FOLDER with whatever is on the GitHub repo (the reverse of
# Update). Destructive - always backed up first, always requires typed confirmation.
function Invoke-Pull {
    Log "=== PULL: overwrite $Path with $RepoName from GitHub ===" Cyan
    Write-Host ""
    Write-Host "  This OVERWRITES local files with what's on GitHub." -ForegroundColor Red
    Write-Host "  A backup is made first, but local-only work not yet pushed can still be lost from the live folder." -ForegroundColor Red
    Write-Host ""

    if (-not $Force) {
        $confirm = Read-Safe "Type OVERWRITE to continue (anything else cancels)"
        if ($confirm -ne "OVERWRITE") { Log "Cancelled - nothing changed." Yellow; return }
    }

    if (-not (Test-GitHubReachable)) { Log "Cannot reach github.com. Check network/VPN first." Red; return }

    $ghUser = $null
    if ($script:HasGh) { $ghUser = (gh api user -q ".login" 2>$null) }
    if ($ghUser) { $ghUser = $ghUser.Trim() }
    if (-not $ghUser) { $ghUser = Read-Safe "GitHub username" }
    $httpsUrl = "https://github.com/$ghUser/$RepoName.git"

    $backupPath = Backup-Folder "pull"
    if (-not $backupPath -and -not $DryRun -and -not $Force) {
        $ans = Read-Safe "Backup failed. Continue anyway WITHOUT a backup? (y/N)"
        if ($ans -ne "y") { Log "Aborted - no backup available." Red; return }
    }

    if (-not (Test-Path ".git")) {
        if ($DryRun) { Log "[DRYRUN] would run: git init" DarkYellow }
        else { git init 2>$null | Out-Null; git branch -M main 2>$null }
    } else {
        $head = (git rev-parse HEAD 2>$null)
        if ($head) {
            $ts = Get-Date -Format "yyyyMMdd-HHmmss"
            $backupBranch = "backup/pre-pull-$ts"
            if ($DryRun) { Log "[DRYRUN] would create safety branch $backupBranch" DarkYellow }
            else { git branch $backupBranch $head.Trim() 2>$null | Out-Null; Log "Safety branch: $backupBranch" Gray }
        }
    }

    $existingRemote = git remote get-url origin 2>$null
    if (-not $existingRemote) {
        if ($DryRun) { Log "[DRYRUN] would add remote origin -> $httpsUrl" DarkYellow }
        else { git remote add origin $httpsUrl 2>$null }
    } elseif ($existingRemote.Trim() -match '^git@github\.com:') {
        if (-not $DryRun) { git remote set-url origin $httpsUrl 2>$null }
    }

    $fetchResult = Invoke-WithRetry -Description "git fetch" -Action { Invoke-GitCapture @("fetch", "origin") }
    if ($fetchResult.ExitCode -ne 0) { Log "Fetch failed - aborting, nothing changed locally." Red; return }

    $remoteBranch = "origin/main"
    git rev-parse --verify $remoteBranch 2>$null | Out-Null
    if ($LASTEXITCODE -ne 0) {
        git rev-parse --verify "origin/master" 2>$null | Out-Null
        if ($LASTEXITCODE -eq 0) { $remoteBranch = "origin/master" }
        else { Log "Could not find a main or master branch on the remote." Red; return }
    }

    if ($DryRun) {
        Log "[DRYRUN] would run: git reset --hard $remoteBranch" DarkYellow
    } else {
        $resetResult = Invoke-GitCapture @("reset", "--hard", $remoteBranch)
        if ($resetResult.ExitCode -ne 0) { Log "Reset failed: $($resetResult.Output.Trim())" Red; return }
    }

    Log "Folder now matches $httpsUrl ($remoteBranch)." Green
    if ($backupPath) { Log "Backup kept at: $backupPath - delete manually once you've verified everything." Gray }

    if (-not $Force -and -not $DryRun) {
        $cleanAns = Read-Safe "Also remove local files that aren't in the repo? (y/N)"
        if ($cleanAns -eq "y") { git clean -fd 2>$null | Out-Null; Log "Untracked files removed." Yellow }
    }
}

function Invoke-Undo {
    Log "=== UNDO ===" Cyan
    $head = (git rev-parse HEAD 2>$null)
    if (-not $head) { Log "No commits yet - nothing to undo." Yellow; return }
    $head = $head.Trim()

    $fetchResult = Invoke-WithRetry -Description "git fetch" -Action { Invoke-GitCapture @("fetch", "origin") }
    $alreadyPushed = $true   # safe default if we can't verify (revert is always non-destructive)
    if ($fetchResult.ExitCode -eq 0) {
        $remoteHead = (git rev-parse origin/main 2>$null)
        if ($remoteHead) { $alreadyPushed = ($remoteHead.Trim() -eq $head) }
    } else {
        Log "Could not verify remote state - defaulting to the safe (revert) path." Yellow
    }

    $ts = Get-Date -Format "yyyyMMdd-HHmmss"
    $dirty = git status --porcelain 2>$null
    $stashLabel = $null
    if ($dirty) {
        $stashLabel = "autosave-before-undo-$ts"
        if ($DryRun) { Log "[DRYRUN] would stash dirty working tree as '$stashLabel'" DarkYellow }
        else { git stash push -u -m $stashLabel 2>$null | Out-Null; Log "Dirty changes stashed as: $stashLabel" Yellow }
    }

    $backupBranch = "backup/pre-undo-$ts"
    if ($DryRun) { Log "[DRYRUN] would create safety branch $backupBranch" DarkYellow }
    else { git branch $backupBranch $head 2>$null | Out-Null; Log "Safety branch created: $backupBranch" Gray }

    if ($alreadyPushed) {
        Log "Commit is on the remote - undoing via revert (safe for shared history)" Yellow
        if ($DryRun) { Log "[DRYRUN] would run: git revert --no-edit HEAD" DarkYellow; $newSha = "dryrun" }
        else {
            git revert --no-edit HEAD 2>$null | Out-Null
            if ($LASTEXITCODE -ne 0) { Log "Revert failed (conflicts) - resolve manually, then commit." Red; return }
            $newSha = (git rev-parse HEAD 2>$null).Trim()
        }
        $record = @{ type = "revert"; beforeSha = $head; afterSha = $newSha; stashLabel = $stashLabel; backupBranch = $backupBranch; timestamp = $ts }
    } else {
        Log "Commit not yet pushed - undoing via soft reset (nothing shared is touched)" Yellow
        if ($DryRun) { Log "[DRYRUN] would run: git reset --soft HEAD~1" DarkYellow; $afterSha = "dryrun" }
        else {
            git reset --soft HEAD~1 2>$null | Out-Null
            if ($LASTEXITCODE -ne 0) { Log "Reset failed - likely only one commit exists." Red; return }
            $afterSha = (git rev-parse HEAD 2>$null).Trim()
        }
        $record = @{ type = "reset"; beforeSha = $head; afterSha = $afterSha; stashLabel = $stashLabel; backupBranch = $backupBranch; timestamp = $ts }
    }

    if (-not $DryRun) {
        $state = Load-State
        $state.Undone = @($state.Undone) + $record
        Save-State $state
    }
    $stashNote = if ($stashLabel) { " | stash: $stashLabel" } else { "" }
    Log "Undo complete. Backup branch: $backupBranch$stashNote" Green
}

function Invoke-Redo {
    Log "=== REDO ===" Cyan
    $state = Load-State
    $undone = @($state.Undone)
    if ($undone.Count -eq 0) { Log "Nothing to redo." Yellow; return }
    $record = $undone[-1]

    if ($record.type -eq "reset") {
        Log "Restoring commit $($record.beforeSha.Substring(0, 7))" Yellow
        if ($DryRun) { Log "[DRYRUN] would run: git reset --soft $($record.beforeSha)" DarkYellow }
        else {
            git reset --soft $record.beforeSha 2>$null | Out-Null
            if ($LASTEXITCODE -ne 0) { Log "Redo failed." Red; return }
        }
    } else {
        Log "Re-reverting the revert commit $($record.afterSha.Substring(0, 7))" Yellow
        if ($DryRun) { Log "[DRYRUN] would run: git revert --no-edit $($record.afterSha)" DarkYellow }
        else {
            git revert --no-edit $record.afterSha 2>$null | Out-Null
            if ($LASTEXITCODE -ne 0) { Log "Redo revert failed (conflicts) - resolve manually." Red; return }
        }
    }

    if (-not $DryRun) {
        $state.Undone = if ($undone.Count -le 1) { @() } else { $undone[0..($undone.Count - 2)] }
        Save-State $state
    }
    Log "Redo complete." Green
    if ($record.stashLabel) { Log "Note: stash '$($record.stashLabel)' from that undo is still saved - 'git stash list' to review." Gray }
}

#endregion

#region Settings (default folder) ---------------------------------------------

function Get-ConfigPath { return Join-Path $HOME ".push-cli" "config.json" }

function Load-PushCliConfig {
    $p = Get-ConfigPath
    if (Test-Path $p) {
        try { return (Get-Content $p -Raw | ConvertFrom-Json -AsHashtable) } catch {}
    }
    return @{ DefaultPath = $null; AutoUse = $false }
}

function Save-PushCliConfig($cfg) {
    $p = Get-ConfigPath
    $dir = Split-Path $p -Parent
    if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
    $cfg | ConvertTo-Json | Out-File -Encoding utf8 $p
}

# Explorer's "Copy as path" wraps the value in quotes - a raw paste into Read-Host
# keeps them literally, which made Test-Path silently fail and the script exit
# instantly. Strip them everywhere a path can come from user input.
function Strip-Quotes([string]$s) { if ($s) { return $s.Trim('"', "'") } else { return $s } }

#endregion

#region UI ---------------------------------------------------------------------

function Show-Banner {
    $branch = (git rev-parse --abbrev-ref HEAD 2>$null)
    $branch = if ($branch) { $branch.Trim() } else { "(no commits yet)" }
    $dirtyCount = @(git status --porcelain 2>$null).Count

    $ahead = 0; $behind = 0
    $trackLine = (git status -sb 2>$null | Select-Object -First 1)
    if ($trackLine -match '\[ahead (\d+)') { $ahead = [int]$Matches[1] }
    if ($trackLine -match 'behind (\d+)')  { $behind = [int]$Matches[1] }

    $rule = "=" * 55
    Write-Host ""
    Write-Host $rule -ForegroundColor DarkCyan
    Write-Host " push-cli v4  |  $RepoName" -ForegroundColor Cyan
    Write-Host $rule -ForegroundColor DarkCyan
    Write-Host " Folder : $Path"
    Write-Host " Branch : $branch"
    $statusColor = if ($dirtyCount -eq 0) { "Green" } else { "Yellow" }
    $statusText  = if ($dirtyCount -eq 0) { "clean" } else { "$dirtyCount uncommitted change(s)" }
    Write-Host " Status : $statusText" -ForegroundColor $statusColor
    if ($ahead -gt 0 -or $behind -gt 0) {
        Write-Host " Sync   : $ahead ahead / $behind behind origin (local info, may be stale)" -ForegroundColor Yellow
    }
    Write-Host $rule -ForegroundColor DarkCyan
}

function Show-Menu {
    Show-Banner
    Write-Host " 1) Update   - commit & push local changes" -ForegroundColor White
    Write-Host " 2) Pull     - overwrite FOLDER with GitHub repo  " -NoNewline -ForegroundColor White
    Write-Host "[DANGEROUS]" -ForegroundColor Red
    Write-Host " 3) Undo     - undo last change" -ForegroundColor White
    Write-Host " 4) Redo     - redo last undo" -ForegroundColor White
    Write-Host " 5) Exit" -ForegroundColor White
    Write-Host ""
    $sel = Read-Safe "Choice"
    $choice = switch ($sel) { "1" { "Update" } "2" { "Pull" } "3" { "Undo" } "4" { "Redo" } default { $null } }
    return $choice
}

#endregion

#region Main ------------------------------------------------------------------

if ($Path) { $Path = Strip-Quotes $Path }

$pushCliConfig = Load-PushCliConfig

if ($ClearDefaultPath) {
    Save-PushCliConfig @{ DefaultPath = $null; AutoUse = $false }
    Write-Host "Default folder cleared." -ForegroundColor Yellow
    if (-not $Path -and -not $SetDefaultPath) { Exit-Safely 0 }
}

if ($SetDefaultPath) {
    $SetDefaultPath = Strip-Quotes $SetDefaultPath
    if (-not (Test-Path $SetDefaultPath)) { Write-Host "Path not found: $SetDefaultPath" -ForegroundColor Red; Exit-Safely 1 }
    $resolvedDefault = (Resolve-Path $SetDefaultPath).Path
    Save-PushCliConfig @{ DefaultPath = $resolvedDefault; AutoUse = $true }
    Write-Host "Saved default folder: $resolvedDefault (future runs use it automatically)" -ForegroundColor Green
    if (-not $Path) { $Path = $resolvedDefault }
}

if (-not $Path) {
    if ($pushCliConfig.AutoUse -and $pushCliConfig.DefaultPath -and (Test-Path $pushCliConfig.DefaultPath)) {
        $Path = $pushCliConfig.DefaultPath
        Write-Host "Using saved default folder: $Path" -ForegroundColor Gray
    } elseif ($NonInteractive) {
        $Path = (Get-Location).Path
    } else {
        $p = Read-Safe "Project folder path (blank = current directory)"
        $Path = if ($p) { Strip-Quotes $p } else { (Get-Location).Path }

        # Only offer to remember it the first time (no config file exists yet) - don't nag every run.
        if (-not (Test-Path (Get-ConfigPath)) -and (Test-Path $Path)) {
            $ans = Read-Safe "Always use this folder automatically from now on? (y/N)"
            if ($ans -eq "y") {
                Save-PushCliConfig @{ DefaultPath = (Resolve-Path $Path).Path; AutoUse = $true }
                Write-Host "Saved. Use -ClearDefaultPath anytime to reset." -ForegroundColor Gray
            } else {
                Save-PushCliConfig @{ DefaultPath = $null; AutoUse = $false }
            }
        }
    }
}

if (-not (Test-Path $Path)) { Write-Host "Path not found: $Path" -ForegroundColor Red; Exit-Safely 1 }
$Path = (Resolve-Path $Path).Path
if (-not $RepoName) { $RepoName = Split-Path $Path -Leaf }
$script:LogFile = Join-Path $Path "push-cli.log"

try {
    Set-Location $Path
    Test-Environment

    if (-not $Action) {
        if ($NonInteractive) { Log "No -Action specified in non-interactive mode." Red; Exit-Safely 1 }
        $Action = Show-Menu
        if (-not $Action) { Exit-Safely 0 }
    }

    switch ($Action) {
        "Update" { Invoke-Update }
        "Pull"   { Invoke-Pull }
        "Undo"   { Invoke-Undo }
        "Redo"   { Invoke-Redo }
    }
} catch {
    Log "Unhandled error: $($_.Exception.Message)" Red
    Log $_.ScriptStackTrace Gray
} finally {
    Exit-Safely 0
}

#endregion
