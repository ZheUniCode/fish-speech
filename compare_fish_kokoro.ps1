param(
    [string]$Distro = "Ubuntu",
    [string]$FishUrl = "http://127.0.0.1:8888",
    [string]$KokoroVoice = "af_heart",
    [string]$Text = "This is a side by side quality check between Fish and Kokoro.",
    [double]$KokoroSpeed = 1.0
)

$ErrorActionPreference = "Stop"

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

function Escape-ForBashSingleQuote {
    param([string]$Value)
    if ($null -eq $Value) { return "" }
    return $Value.Replace("'", "'\\''")
}

$repoRoot = [System.IO.Path]::GetFullPath($PSScriptRoot)
$wslRepo = Convert-ToWslPath -WindowsPath $repoRoot

Write-Host "[compare] Running Fish vs Kokoro comparison..."

$safeRepo = Escape-ForBashSingleQuote $wslRepo
$safeText = Escape-ForBashSingleQuote $Text
$safeFishUrl = Escape-ForBashSingleQuote $FishUrl
$safeKokoroVoice = Escape-ForBashSingleQuote $KokoroVoice

$cmd = "cd '$safeRepo' || exit 1; " +
    "set -e; " +
    "kokoro_venv=`"/home/wsl_ubuntu_terminal/.venvs/fish-kokoro`"; " +
    "mkdir -p `"/home/wsl_ubuntu_terminal/.venvs`"; " +
    "if [ ! -f `"$kokoro_venv/bin/activate`" ]; then rm -rf `"$kokoro_venv`"; python3 -m venv `"$kokoro_venv`"; fi; " +
    "source `"$kokoro_venv/bin/activate`"; " +
             "python -m pip install -q --upgrade pip || exit 1; " +
             "python -m pip install -q 'kokoro>=0.9.4' soundfile numpy || exit 1; " +
             "python tools/compare_fish_kokoro.py --text '$safeText' --fish-url '$safeFishUrl' --kokoro-voice '$safeKokoroVoice' --kokoro-speed $KokoroSpeed"

& wsl -d $Distro bash -lc $cmd

if ($LASTEXITCODE -ne 0) {
    throw "Comparison failed with exit code $LASTEXITCODE"
}
