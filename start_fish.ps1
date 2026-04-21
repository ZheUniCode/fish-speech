param(
    [string]$Distro = "Ubuntu",
    [int]$Port = 8888,
    [int]$TimeoutSeconds = 300,
    [ValidateSet('cuda', 'cpu', 'xpu', 'mps')]
    [string]$Device = 'cuda',
    [string]$CheckpointPath = "checkpoints/s2-pro",
    [string]$DecoderCheckpointPath = "",
    [switch]$LowImpact,
    [switch]$Half,
    [int]$NiceLevel = 10
)

$ErrorActionPreference = "Stop"
$PSNativeCommandUseErrorActionPreference = $false

function Convert-ToWslPath {
    param([string]$WindowsPath)

    $full = [System.IO.Path]::GetFullPath($WindowsPath)
    $full = $full -replace '\\', '/'

    if ($full -match '^([A-Za-z]):/(.*)$') {
        $drive = $Matches[1].ToLowerInvariant()
        $rest = $Matches[2]
        return "/mnt/$drive/$rest"
    }

    throw "Cannot convert path to WSL format: $WindowsPath"
}

function Resolve-RepoPath {
    param([string]$PathValue)

    if ([System.IO.Path]::IsPathRooted($PathValue)) {
        return [System.IO.Path]::GetFullPath($PathValue)
    }

    return [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot $PathValue))
}

$RepoRoot = $PSScriptRoot
$WebUiIndex = Join-Path $RepoRoot "awesome_webui\dist\index.html"
$ResolvedCheckpointPath = Resolve-RepoPath $CheckpointPath
$ResolvedDecoderCheckpointPath = if ([string]::IsNullOrWhiteSpace($DecoderCheckpointPath)) {
    Join-Path $ResolvedCheckpointPath "codec.pth"
} else {
    Resolve-RepoPath $DecoderCheckpointPath
}

if (-not (Test-Path $WebUiIndex)) {
    Write-Host "[fish] WebUI build not found. Building awesome_webui..."
    Push-Location (Join-Path $RepoRoot "awesome_webui")
    try {
        if (Test-Path "package-lock.json") {
            npm ci
        }
        else {
            npm install
        }
        npm run build
    }
    finally {
        Pop-Location
    }
}

$wslRepo = Convert-ToWslPath -WindowsPath $RepoRoot
$wslCheckpoint = Convert-ToWslPath -WindowsPath $ResolvedCheckpointPath
$wslDecoderCheckpoint = Convert-ToWslPath -WindowsPath $ResolvedDecoderCheckpointPath

if (-not (Test-Path $ResolvedCheckpointPath)) {
    throw "Checkpoint path not found: $ResolvedCheckpointPath"
}

if (-not (Test-Path $ResolvedDecoderCheckpointPath)) {
    throw "Decoder checkpoint path not found: $ResolvedDecoderCheckpointPath"
}

Write-Host "[fish] Starting API server in WSL distro '$Distro' on port $Port..."
if ($LowImpact) {
    Write-Host "[fish] Low impact mode: enabled (nice -n $NiceLevel)."
}

if ($LowImpact -and -not $Half) {
    $Half = $true
    Write-Host "[fish] Low impact mode: enabling fp16 to reduce memory use."
}

$nicePrefix = if ($LowImpact) { "nice -n $NiceLevel" } else { "" }
$halfArg = if ($Half) { ' --half' } else { '' }
$runCmd = "cd '$wslRepo' || exit 1; exec $nicePrefix .venv/bin/python tools/api_server.py --listen 0.0.0.0:$Port --device $Device$halfArg --llama-checkpoint-path '$wslCheckpoint' --decoder-checkpoint-path '$wslDecoderCheckpoint'"

Write-Host "[fish] Checkpoint: $ResolvedCheckpointPath"
Write-Host "[fish] Decoder:    $ResolvedDecoderCheckpointPath"
Write-Host "[fish] UI:     http://127.0.0.1:$Port/ui"
Write-Host "[fish] Health: http://127.0.0.1:$Port/v1/health"
Write-Host "[fish] Press Ctrl+C to stop."

& wsl -d $Distro bash -lc $runCmd
