param(
    [string]$Distro = 'Ubuntu',
    [int]$Port = 8888,
    [ValidateSet('cuda', 'cpu', 'xpu', 'mps')]
    [string]$Device = 'cuda',
    [string]$CheckpointPath = 'checkpoints/s2-pro',
    [string]$DecoderCheckpointPath = ''
)

& "$PSScriptRoot\start_fish.ps1" -Distro $Distro -Port $Port -Device $Device -CheckpointPath $CheckpointPath -DecoderCheckpointPath $DecoderCheckpointPath -LowImpact -Half
