# One-click, repeatable Windows installer for Whisper Face.
# Windows deliberately retains the faster-whisper Tiny -> Turbo cascade;
# FluidAudio/Parakeet is an Apple Core ML optimization and is Mac-only.
# Safe to rerun: the login task is replaced and private user files survive.
$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$Repo = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Repo
$Mode = "full"
$VerifyOnly = $false

foreach ($Argument in $Args) {
    switch ($Argument) {
        { $_ -in @("--server-only", "-ServerOnly") } { $Mode = "server-only" }
        { $_ -in @("--verify", "-Verify") } { $VerifyOnly = $true }
        { $_ -in @("-h", "--help", "-Help") } {
            Write-Host "Usage: .\setup.ps1 [--server-only] [--verify]"
            Write-Host "  --server-only  install the headless endpoint without UI/mic"
            Write-Host "  --verify       check an existing installation without changing it"
            exit 0
        }
        default { throw "Unknown option: $Argument" }
    }
}

function Write-Step([string]$Message) {
    Write-Host ""
    Write-Host "== $Message"
}

function Refresh-ProcessPath {
    $Machine = [Environment]::GetEnvironmentVariable("Path", "Machine")
    $User = [Environment]::GetEnvironmentVariable("Path", "User")
    $env:Path = "$Machine;$User;$env:USERPROFILE\.local\bin"
}

function Find-Executable([string]$Name, [string[]]$Fallbacks) {
    $Command = Get-Command $Name -ErrorAction SilentlyContinue
    if ($null -ne $Command) { return $Command.Source }
    foreach ($Candidate in $Fallbacks) {
        if ($Candidate -and (Test-Path $Candidate)) { return $Candidate }
    }
    return $null
}

function Require-Winget {
    if ($null -eq (Get-Command winget -ErrorAction SilentlyContinue)) {
        throw "Windows Package Manager (winget) is required. Install App Installer, then rerun Install.cmd."
    }
}

function Install-WingetPackage([string]$Id) {
    Require-Winget
    & winget install --id $Id --exact --source winget --silent `
        --accept-package-agreements --accept-source-agreements
    if ($LASTEXITCODE -ne 0) { throw "winget could not install $Id" }
    Refresh-ProcessPath
}

function Test-Endpoint([string]$Uri) {
    try {
        Invoke-WebRequest -UseBasicParsing -Uri $Uri -TimeoutSec 2 | Out-Null
        return $true
    } catch {
        return $false
    }
}

function Test-PrivateFile([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return $false }
    try {
        $Item = Get-Item -Force -LiteralPath $Path
        if (($Item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
            return $false
        }
        $CurrentSid = [Security.Principal.WindowsIdentity]::GetCurrent().User
        $Acl = Get-Acl -LiteralPath $Path
        $Owner = ([Security.Principal.NTAccount]$Acl.Owner).Translate(
            [Security.Principal.SecurityIdentifier])
        $Rules = @($Acl.Access)
        if (-not $Acl.AreAccessRulesProtected -or
                $Owner.Value -ne $CurrentSid.Value -or $Rules.Count -ne 1) {
            return $false
        }
        $Rule = $Rules[0]
        $Identity = $Rule.IdentityReference.Translate(
            [Security.Principal.SecurityIdentifier])
        return $Rule.AccessControlType -eq [System.Security.AccessControl.AccessControlType]::Allow -and
            $Rule.FileSystemRights -eq [System.Security.AccessControl.FileSystemRights]::FullControl -and
            -not $Rule.IsInherited -and $Identity.Value -eq $CurrentSid.Value
    } catch {
        return $false
    }
}

function Set-PrivateFile([string]$Path) {
    $Parent = Split-Path -Parent $Path
    $Leaf = Split-Path -Leaf $Path
    $Item = Get-ChildItem -Force -LiteralPath $Parent -ErrorAction Stop |
        Where-Object { $_.Name -ieq $Leaf } | Select-Object -First 1
    if ($null -ne $Item) {
        if (-not $Item.PSIsContainer -and
                (($Item.Attributes -band [IO.FileAttributes]::ReparsePoint) -eq 0)) {
            # Preserve existing content; only repair its ownership and ACL.
        } else {
            throw "private runtime log is not a regular file: $Path"
        }
    } else {
        New-Item -ItemType File -Path $Path -Force | Out-Null
    }
    $CurrentSid = [Security.Principal.WindowsIdentity]::GetCurrent().User
    $Acl = Get-Acl -LiteralPath $Path
    $Acl.SetAccessRuleProtection($true, $false)
    foreach ($Rule in @($Acl.Access)) {
        [void]$Acl.RemoveAccessRuleAll($Rule)
    }
    $Acl.SetOwner($CurrentSid)
    $Acl.AddAccessRule((New-Object System.Security.AccessControl.FileSystemAccessRule(
        $CurrentSid, [System.Security.AccessControl.FileSystemRights]::FullControl,
        [System.Security.AccessControl.AccessControlType]::Allow)))
    Set-Acl -LiteralPath $Path -AclObject $Acl
    if (-not (Test-PrivateFile $Path)) {
        throw "could not apply a private runtime log ACL: $Path"
    }
}

$Required = @(
    "dictate.py", "dictate.py.lock", "parrot_core.py", "voice_compiler.py",
    "insertion_integrity.py", "personal_regression.py",
    "cleanup_circuit_breaker.py",
    "model_wallet.py", "model_wallet_shadow.py", "model_readiness_evidence.py",
    "acoustic_keyword_memory.py", "acoustic_time_machine.py",
    "voice_objects.py", "voice_object_command_parser.py", "voice_inbox.py",
    "voice_object_inbox_bridge.py", "macos_email_compose.py",
    "macos_voice_draft_clipboard.py",
    "demonstration_drafts.py",
    "risky_action_confirmation.py",
    "point_and_speak_resolver.py", "point_and_speak_transaction.py",
    "macos_point_and_speak_snapshot.py",
    "macos_drop_to_target_snapshot.py", "drop_to_target.py",
    "whisper_face_gui.py",
    "snippets.template.json", "tones.template.json",
    "preferences.template.json", "acoustic_keyword_memory.template.json",
    "dictionary.template.txt",
    "icons\faces\parrot-idle.svg", "icons\faces\parrot-talk.svg",
    "icons\faces\fox-idle.svg", "icons\faces\fox-talk.svg",
    "icons\faces\owl-idle.svg", "icons\faces\owl-talk.svg",
    "icons\faces\cat-idle.svg", "icons\faces\cat-talk.svg",
    "icons\faces\bear-idle.svg", "icons\faces\bear-talk.svg"
)
foreach ($Relative in $Required) {
    if (-not (Test-Path (Join-Path $Repo $Relative))) {
        throw "Repository is incomplete: missing $Relative"
    }
}

if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) {
    throw "setup.ps1 must be run on Windows"
}
$Architecture = if ($env:PROCESSOR_ARCHITEW6432) {
    $env:PROCESSOR_ARCHITEW6432
} else {
    $env:PROCESSOR_ARCHITECTURE
}
if ($Architecture -notin @("AMD64", "x86_64")) {
    throw "Windows x64 is currently required by the faster-Whisper runtime (detected $Architecture)."
}

$TaskName = "Whisper Face"
$LegacyTaskName = "Whispering Parrot"
$LauncherDir = Join-Path $Repo ".windows"
$Launcher = Join-Path $LauncherDir "launch.ps1"
$LauncherReceipt = Join-Path $LauncherDir "launch.sha256"
$Log = Join-Path $Repo "dictate.log"
$OllamaLog = Join-Path $Repo "ollama.log"
$OllamaErrorLog = Join-Path $Repo "ollama-error.log"

function Get-InstalledTools {
    Refresh-ProcessPath
    $script:Uv = Find-Executable "uv.exe" @(
        (Join-Path $env:USERPROFILE ".local\bin\uv.exe")
    )
    $script:Ffmpeg = Find-Executable "ffmpeg.exe" @()
    $script:Ollama = Find-Executable "ollama.exe" @(
        (Join-Path $env:LOCALAPPDATA "Programs\Ollama\ollama.exe")
    )
}

function Get-LauncherDigest([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "Windows login launcher is missing"
    }
    $Digest = (Get-FileHash -Algorithm SHA256 -LiteralPath $Path).Hash
    if ($Digest -notmatch '^[0-9A-F]{64}$') {
        throw "Windows login launcher digest is invalid"
    }
    return $Digest.ToLowerInvariant()
}

function Confirm-TaskLauncherBinding {
    if (-not (Test-Path -LiteralPath $LauncherReceipt -PathType Leaf)) {
        throw "Windows login launcher receipt is missing; rerun Install.cmd"
    }
    $RecordedDigest = [IO.File]::ReadAllText($LauncherReceipt)
    if ($RecordedDigest -notmatch '^[0-9a-f]{64}$') {
        throw "Windows login launcher receipt is invalid; rerun Install.cmd"
    }
    if ((Get-LauncherDigest $Launcher) -cne $RecordedDigest) {
        throw "Windows login launcher has changed; rerun Install.cmd"
    }

    $LauncherFull = [IO.Path]::GetFullPath($Launcher)
    $RepoFull = [IO.Path]::GetFullPath($Repo)
    $RepoPrefix = $RepoFull.TrimEnd([IO.Path]::DirectorySeparatorChar) +
        [IO.Path]::DirectorySeparatorChar
    if (-not $LauncherFull.StartsWith($RepoPrefix,
            [StringComparison]::OrdinalIgnoreCase)) {
        throw "Windows login launcher is outside this checkout; rerun Install.cmd"
    }

    $LauncherBody = [IO.File]::ReadAllText($Launcher)
    $EscapedRepo = $Repo.Replace("'", "''")
    $EscapedScript = (Join-Path $Repo "dictate.py").Replace("'", "''")
    $EscapedLog = $Log.Replace("'", "''")
    foreach ($RequiredLauncherText in @(
            "Set-Location '$EscapedRepo'",
            "run --locked --script '$EscapedScript'",
            "*>> '$EscapedLog'")) {
        if (-not $LauncherBody.Contains($RequiredLauncherText)) {
            throw "Windows login launcher does not target this checkout; rerun Install.cmd"
        }
    }

    $Task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($null -eq $Task) {
        throw "Windows login task is missing"
    }
    $CurrentIdentity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $CurrentPrincipalIds = @(
        $env:USERNAME,
        $CurrentIdentity.Name,
        $CurrentIdentity.User.Value
    ) | Where-Object { -not [string]::IsNullOrWhiteSpace($_) }
    $TaskPrincipalMatches = $false
    if ($null -ne $Task.Principal) {
        foreach ($CurrentPrincipalId in $CurrentPrincipalIds) {
            if ($Task.Principal.UserId -ieq $CurrentPrincipalId) {
                $TaskPrincipalMatches = $true
                break
            }
        }
    }
    if (-not $TaskPrincipalMatches) {
        throw "Windows login task is not bound to the current user; rerun Install.cmd"
    }
    $Actions = @($Task.Actions)
    if ($Actions.Count -ne 1) {
        throw "Windows login task has an unexpected action count; rerun Install.cmd"
    }
    $ExpectedArguments = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$Launcher`""
    if ($Actions[0].Execute -ine "powershell.exe" -or
            $Actions[0].Arguments -cne $ExpectedArguments) {
        throw "Windows login task does not launch this checkout; rerun Install.cmd"
    }
}

function Confirm-Installation {
    Write-Step "verifying Windows installation"
    Get-InstalledTools
    if (-not $Uv) { throw "uv is not installed" }
    if (-not $Ffmpeg) { throw "ffmpeg is not installed" }
    if (-not $Ollama) { throw "Ollama is not installed" }
    foreach ($PrivateLog in @($Log, $OllamaLog, $OllamaErrorLog)) {
        if (-not (Test-PrivateFile $PrivateLog)) {
            throw "private runtime log is missing or has an unsafe ACL"
        }
    }
    Confirm-TaskLauncherBinding
    $Task = Get-ScheduledTask -TaskName $TaskName -ErrorAction Stop
    if ($Task.State -ne "Running") {
        throw "Windows login task is not running (state: $($Task.State))"
    }
    & $Uv lock --check --script (Join-Path $Repo "dictate.py") | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "dictate.py.lock does not match dictate.py" }
    & $Uv sync --locked --script (Join-Path $Repo "dictate.py") --check | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "locked Python environment is incomplete" }
    & $Uv run --locked --script (Join-Path $Repo "dictate.py") `
        --verify-ollama-model | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Qwen model manifest verification failed" }
    & $Ollama show "qwen3.5:4b" *> $null
    if ($LASTEXITCODE -ne 0) { throw "qwen3.5:4b is not installed" }
    $ModelFiles = @(Get-ChildItem (Join-Path $Repo ".models") `
        -Filter "model.bin" -File -Recurse -ErrorAction SilentlyContinue)
    if ($ModelFiles.Count -lt 2) {
        throw "both faster-Whisper models are not cached"
    }
    if (-not (Test-Endpoint "http://127.0.0.1:8787/health")) {
        throw "dictation process is not ready; inspect $Log"
    }
    Write-Host "== verified: locked environment, models, login task, and health"
}

if ($VerifyOnly) {
    Confirm-Installation
    exit 0
}

Write-Step "Whisper Face setup in $Repo (Windows, mode: $Mode)"
$Drive = [IO.DriveInfo]::new((Get-Item $Repo).PSDrive.Root)
if ($Drive.AvailableFreeSpace -lt (8 * 1024 * 1024 * 1024)) {
    throw "At least 8 GB of free disk space is required"
}

Get-InstalledTools
if (-not $Uv) {
    Write-Step "installing uv"
    if ($null -ne (Get-Command winget -ErrorAction SilentlyContinue)) {
        Install-WingetPackage "astral-sh.uv"
    } else {
        Invoke-RestMethod "https://astral.sh/uv/install.ps1" | Invoke-Expression
        Refresh-ProcessPath
    }
}
Get-InstalledTools
if (-not $Ffmpeg) {
    Write-Step "installing ffmpeg"
    Install-WingetPackage "Gyan.FFmpeg"
}
if (-not $Ollama) {
    Write-Step "installing Ollama"
    Install-WingetPackage "Ollama.Ollama"
}
Get-InstalledTools
if (-not $Uv -or -not $Ffmpeg -or -not $Ollama) {
    throw "A dependency installed but was not found on PATH; restart Windows and rerun Install.cmd"
}

Write-Step "starting the local Ollama service"
foreach ($PrivateLog in @($Log, $OllamaLog, $OllamaErrorLog)) {
    Set-PrivateFile $PrivateLog
}
if (-not (Test-Endpoint "http://127.0.0.1:11434/api/tags")) {
    Start-Process -FilePath $Ollama -ArgumentList "serve" `
        -WindowStyle Hidden `
        -RedirectStandardOutput $OllamaLog `
        -RedirectStandardError $OllamaErrorLog
}
$OllamaReady = $false
for ($Attempt = 0; $Attempt -lt 60; $Attempt++) {
    if (Test-Endpoint "http://127.0.0.1:11434/api/tags") {
        $OllamaReady = $true
        break
    }
    Start-Sleep -Seconds 1
}
if (-not $OllamaReady) { throw "Ollama did not become ready; inspect ollama-error.log" }

& $Ollama show "qwen3.5:4b" *> $null
if ($LASTEXITCODE -ne 0) {
    Write-Step "downloading qwen3.5:4b (~3.4 GB)"
    & $Ollama pull "qwen3.5:4b"
    if ($LASTEXITCODE -ne 0) { throw "Ollama could not download qwen3.5:4b" }
} else {
    Write-Host "== qwen3.5:4b already present"
}

Write-Step "installing the locked Windows Python environment"
& $Uv sync --locked --script (Join-Path $Repo "dictate.py")
if ($LASTEXITCODE -ne 0) { throw "uv could not install the locked environment" }
& $Uv run --locked --script (Join-Path $Repo "dictate.py") `
    --verify-ollama-model
if ($LASTEXITCODE -ne 0) { throw "Qwen model manifest verification failed" }
Write-Step "downloading Whisper Tiny and large-v3-turbo"
& $Uv run --locked --script (Join-Path $Repo "dictate.py") --preload-models
if ($LASTEXITCODE -ne 0) { throw "Whisper model preload failed" }

Write-Step "creating private per-machine files (existing files are preserved)"
$PrivateTemplates = @{
    "snippets.json" = "snippets.template.json"
    "tones.json" = "tones.template.json"
    "preferences.json" = "preferences.template.json"
    "acoustic_keyword_memory.json" = "acoustic_keyword_memory.template.json"
    "dictionary.txt" = "dictionary.template.txt"
}
foreach ($DestinationName in $PrivateTemplates.Keys) {
    $Destination = Join-Path $Repo $DestinationName
    if (-not (Test-Path $Destination)) {
        Copy-Item (Join-Path $Repo $PrivateTemplates[$DestinationName]) $Destination
    }
    & icacls $Destination /inheritance:r /grant:r "${env:USERNAME}:(F)" /Q | Out-Null
}
foreach ($PrivateStateName in @("voice_inbox.json", "demonstrations.json")) {
    $PrivateState = Join-Path $Repo $PrivateStateName
    if (Test-Path $PrivateState) {
        & icacls $PrivateState /inheritance:r /grant:r "${env:USERNAME}:(F)" /Q | Out-Null
    }
}
Write-Step "installing the Windows login task"
New-Item -ItemType Directory -Force -Path $LauncherDir | Out-Null
$ExtraArgument = if ($Mode -eq "server-only") { " --server-only" } else { "" }
$EscapedUv = $Uv.Replace("'", "''")
$EscapedScript = (Join-Path $Repo "dictate.py").Replace("'", "''")
$EscapedLog = $Log.Replace("'", "''")
$LauncherBody = @"
`$env:PYTHONUNBUFFERED = "1"
Set-Location '$($Repo.Replace("'", "''"))'
& '$EscapedUv' run --locked --script '$EscapedScript'$ExtraArgument *>> '$EscapedLog'
"@
Set-Content -Path $Launcher -Value $LauncherBody -Encoding UTF8
$LauncherDigest = Get-LauncherDigest $Launcher
Set-Content -Path $LauncherReceipt -Value $LauncherDigest -Encoding ascii -NoNewline
& icacls $LauncherReceipt /inheritance:r /grant:r "${env:USERNAME}:(F)" /Q | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw "could not apply the private Windows launcher receipt ACL"
}

foreach ($ExistingName in @($TaskName, $LegacyTaskName)) {
    $ExistingTask = Get-ScheduledTask -TaskName $ExistingName -ErrorAction SilentlyContinue
    if ($null -ne $ExistingTask) {
        Stop-ScheduledTask -TaskName $ExistingName -ErrorAction SilentlyContinue
        Unregister-ScheduledTask -TaskName $ExistingName -Confirm:$false
    }
}
$Action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument `
    "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$Launcher`""
$Trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$Settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger `
    -Settings $Settings -Description "Local Whisper Face dictation" `
    -User $env:USERNAME -RunLevel Limited -Force | Out-Null
Start-ScheduledTask -TaskName $TaskName

Write-Host -NoNewline "== waiting for dictation service"
$Ready = $false
for ($Attempt = 0; $Attempt -lt 180; $Attempt++) {
    if (Test-Endpoint "http://127.0.0.1:8787/health") {
        $Ready = $true
        break
    }
    Write-Host -NoNewline "."
    Start-Sleep -Seconds 1
}
Write-Host ""
if (-not $Ready) { throw "dictation service did not become ready; inspect $Log" }

Confirm-Installation
Write-Host ""
Write-Host "== installation complete"
if ($Mode -eq "full") {
    Write-Host "== Hold Right Alt, speak, and release to paste."
    Write-Host "== The Whisper Face tray menu controls character, pause, Flight Recorder, logs, and quit."
} else {
    Write-Host "== server-only installation is ready."
}
Write-Host "== Logs: $Log"
