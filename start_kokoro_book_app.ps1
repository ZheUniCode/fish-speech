param(
    [string]$Distro = "Ubuntu",
    [int]$Port = 7863
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
    param([int]$PreferredPort, [int]$MaxAttempts = 30)

    for ($i = 0; $i -lt $MaxAttempts; $i++) {
        $candidate = $PreferredPort + $i
        if (-not (Test-PortInUse -PortNumber $candidate)) {
            return $candidate
        }
    }

    throw "No available port found in range $PreferredPort-$($PreferredPort + $MaxAttempts - 1)"
}

function Escape-ForBashSingleQuote {
    param([string]$Value)
    if ($null -eq $Value) { return "" }
    return $Value.Replace("'", "'\''")
}

$repoRoot = Resolve-RepoPath "."
$wslRepo = Convert-ToWslPath -WindowsPath $repoRoot
$entrypoint = Join-Path $repoRoot "tools\run_kokoro_book_webui.py"

if (-not (Test-Path $entrypoint)) {
    throw "Kokoro upload app entrypoint not found: $entrypoint"
}

if (Test-PortInUse -PortNumber $Port) {
    $resolvedPort = Resolve-AvailablePort -PreferredPort $Port
    Write-Host "[kokoro-book] Port $Port is already in use, switching to $resolvedPort"
    $Port = $resolvedPort
}

Write-Host "[kokoro-book] Starting Kokoro upload app..."
Write-Host "[kokoro-book] Open: http://127.0.0.1:$Port"
Write-Host "[kokoro-book] First run installs Kokoro in ~/.venvs/fish-kokoro (one-time)."
Write-Host "[kokoro-book] Press Ctrl+C to stop."

Start-Process "http://127.0.0.1:$Port" | Out-Null

$safeRepo = Escape-ForBashSingleQuote $wslRepo

$cmd = "cd '$safeRepo' || exit 1; " +
             "set -e; " +
             "kokoro_venv=\"$HOME/.venvs/fish-kokoro\"; " +
             "mkdir -p \"$HOME/.venvs\"; " +
             "if [ ! -f \"$kokoro_venv/bin/activate\" ]; then rm -rf \"$kokoro_venv\"; python3 -m venv \"$kokoro_venv\"; fi; " +
             "source \"$kokoro_venv/bin/activate\"; " +
             "python -m pip install -q --upgrade pip || exit 1; " +
             "python -m pip install -q 'kokoro>=0.9.4' soundfile gradio numpy || exit 1; " +
             "if ! command -v espeak-ng >/dev/null 2>&1; then echo '[kokoro-book] Warning: espeak-ng not found. Install for best language fallback.'; fi; " +
             "exec python tools/run_kokoro_book_webui.py --listen 0.0.0.0:$Port"

& wsl -d $Distro bash -lc $cmd

if ($LASTEXITCODE -ne 0) {
    throw "Kokoro upload app exited with code $LASTEXITCODE on port $Port"
}
