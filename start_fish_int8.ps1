param(
    [string]$Distro = 'Ubuntu',
    [int]$Port = 8888,
    [string]$CheckpointPath = '',
    [ValidateSet('cuda', 'cpu', 'xpu', 'mps')]
    [string]$Device = 'cuda',
    [switch]$Half,
    [switch]$LowImpact
)

$ErrorActionPreference = 'Stop'

function Get-LatestCheckpoint {
    param([string]$Pattern)

    $root = Join-Path $PSScriptRoot 'checkpoints'
    $candidate = Get-ChildItem -Path $root -Directory -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -like $Pattern } |
        Sort-Object LastWriteTime -Descending |
        Select-Object -First 1

    if (-not $candidate) {
        throw "No checkpoint matched pattern '$Pattern'. Run .\quantize_fish.ps1 first."
    }

    return $candidate.FullName
}

$resolved = if ([string]::IsNullOrWhiteSpace($CheckpointPath)) {
    Get-LatestCheckpoint -Pattern 'fs-1.2-int8-*'
} else {
    $CheckpointPath
}

& "$PSScriptRoot\start_fish.ps1" -Distro $Distro -Port $Port -Device $Device -CheckpointPath $resolved -DecoderCheckpointPath 'checkpoints/s2-pro/codec.pth' -Half:$Half -LowImpact:$LowImpact
