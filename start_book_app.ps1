param(
    [string]$Distro = "Ubuntu",
    [int]$Port = 7861,
    [string]$ApiUrl = "http://127.0.0.1:8888"
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

function Test-PortInUse {
    param([int]$PortNumber)

    $connection = Get-NetTCPConnection -State Listen -LocalPort $PortNumber -ErrorAction SilentlyContinue | Select-Object -First 1
    return $null -ne $connection
}

function Resolve-AvailablePort {
    param(
        [int]$PreferredPort,
        [int]$MaxAttempts = 30
    )

    for ($i = 0; $i -lt $MaxAttempts; $i++) {
        $candidate = $PreferredPort + $i
        if (-not (Test-PortInUse -PortNumber $candidate)) {
            return $candidate
        }
    }

    throw "No available port found in range $PreferredPort-$($PreferredPort + $MaxAttempts - 1)"
}

$repoRoot = Resolve-RepoPath "."
$wslRepo = Convert-ToWslPath -WindowsPath $repoRoot

$entrypoint = Join-Path $repoRoot "tools\run_book_webui.py"
if (-not (Test-Path $entrypoint)) {
    throw "Book upload app entrypoint not found: $entrypoint"
}

Write-Host "[fish-book] Starting upload app..."
if (Test-PortInUse -PortNumber $Port) {
    $resolvedPort = Resolve-AvailablePort -PreferredPort $Port
    Write-Host "[fish-book] Port $Port is already in use, switching to $resolvedPort"
    $Port = $resolvedPort
}

Write-Host "[fish-book] Open: http://127.0.0.1:$Port"
Write-Host "[fish-book] Press Ctrl+C to stop."

Start-Process "http://127.0.0.1:$Port" | Out-Null

$cmd = "cd '$wslRepo' || exit 1; exec .venv/bin/python tools/run_book_webui.py --listen 0.0.0.0:$Port --api-url '$ApiUrl'"
& wsl -d $Distro bash -lc $cmd

if ($LASTEXITCODE -ne 0) {
    throw "Book upload app exited with code $LASTEXITCODE on port $Port"
}
