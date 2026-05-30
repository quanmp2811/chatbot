$ErrorActionPreference = "Stop"

$logDir = Join-Path $PSScriptRoot "logs"
$logFile = Join-Path $logDir "drive-sync.log"

[Console]::InputEncoding = [System.Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$OutputEncoding = [System.Text.UTF8Encoding]::new($false)
chcp 65001 | Out-Null

New-Item -ItemType Directory -Force -Path $logDir | Out-Null
if (-not (Test-Path $logFile)) {
    New-Item -ItemType File -Path $logFile | Out-Null
}

Write-Host "[drive-log] Watching $logFile"
Get-Content -Path $logFile -Encoding UTF8 -Wait -Tail 30
