param(
    [string]$Distro = "Ubuntu",
    [int]$Port = 7861,
    [string]$ApiUrl = "http://127.0.0.1:8888"
)

$ErrorActionPreference = "Stop"

& "$PSScriptRoot\start_book_app.ps1" -Distro $Distro -Port $Port -ApiUrl $ApiUrl
