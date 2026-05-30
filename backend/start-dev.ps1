$ErrorActionPreference = "Stop"

$driveLogScript = Join-Path $PSScriptRoot "open-drive-log.ps1"
Start-Process powershell -ArgumentList @("-NoExit", "-ExecutionPolicy", "Bypass", "-File", $driveLogScript) | Out-Null

function Test-PortOpen {
    param(
        [string]$Host = "127.0.0.1",
        [int]$Port
    )

    try {
        $client = New-Object System.Net.Sockets.TcpClient
        $async = $client.BeginConnect($Host, $Port, $null, $null)
        $ok = $async.AsyncWaitHandle.WaitOne(500)
        if (-not $ok) {
            $client.Close()
            return $false
        }
        $client.EndConnect($async)
        $client.Close()
        return $true
    } catch {
        return $false
    }
}

Write-Host "[dev] Checking Ollama server on port 11434..."
if (-not (Test-PortOpen -Port 11434)) {
    Write-Host "[dev] Starting Ollama server..."
    Start-Process -FilePath "ollama" -ArgumentList "serve" -WindowStyle Minimized | Out-Null
    Start-Sleep -Seconds 2
} else {
    Write-Host "[dev] Ollama is already running."
}

if (-not (Test-PortOpen -Port 11434)) {
    Write-Host "[warn] Ollama did not start on port 11434. Check 'ollama' installation."
} else {
    Write-Host "[dev] Ollama is ready at http://localhost:11434"
}

Write-Host "[dev] Starting FastAPI with reload..."
uvicorn app.main:app --reload
