param(
    [ValidateSet('int8', 'int4')]
    [string]$Mode = 'int8',
    [string]$CheckpointPath = 'checkpoints/s2-pro',
    [int]$Groupsize = 128,
    [string]$Timestamp = '',
    [string]$Distro = 'Ubuntu'
)

$ErrorActionPreference = 'Stop'

function Resolve-RepoPath {
    param([string]$PathValue)

    if ([System.IO.Path]::IsPathRooted($PathValue)) {
        return [System.IO.Path]::GetFullPath($PathValue)
    }

    return [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot $PathValue))
}

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

$resolvedCheckpoint = Resolve-RepoPath $CheckpointPath
if (-not (Test-Path $resolvedCheckpoint)) {
    throw "Checkpoint path not found: $resolvedCheckpoint"
}

$wslRepo = Convert-ToWslPath $PSScriptRoot
$wslCheckpoint = Convert-ToWslPath $resolvedCheckpoint
$timestampArg = if ([string]::IsNullOrWhiteSpace($Timestamp)) { '' } else { " --timestamp '$Timestamp'" }

Write-Host "[fish] Quantizing $resolvedCheckpoint as $Mode..."

$quantizeCmd = switch ($Mode) {
    'int8' { "cd '$wslRepo' && .venv/bin/python tools/llama/quantize.py --checkpoint-path '$wslCheckpoint' --mode int8$timestampArg" }
    'int4' { "cd '$wslRepo' && .venv/bin/python tools/llama/quantize.py --checkpoint-path '$wslCheckpoint' --mode int4 --groupsize $Groupsize$timestampArg" }
}

& wsl -d $Distro bash -lc $quantizeCmd