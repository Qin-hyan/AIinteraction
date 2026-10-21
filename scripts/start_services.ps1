# Start the local service launcher in the background.
# The launcher binds 127.0.0.1 only and idempotently starts:
#   - voice service  (default 8080, see service/launcher.config.json)
#   - vision service (default 8090, see service/launcher.config.json)
# Safe to run repeatedly: it exits early when the launcher is already up.

$ErrorActionPreference = 'Stop'

$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo

$port = 8079
$configPath = Join-Path $repo 'service\launcher.config.json'
if (Test-Path $configPath) {
    try {
        $config = Get-Content $configPath -Raw | ConvertFrom-Json
        if ($config.port) { $port = [int]$config.port }
    } catch {
        Write-Warning "Cannot parse $configPath; using default port 8079."
    }
}

function Test-LocalPort([int]$Port) {
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $async = $client.BeginConnect('127.0.0.1', $Port, $null, $null)
        if (-not $async.AsyncWaitHandle.WaitOne(400)) { return $false }
        $client.EndConnect($async)
        return $true
    } catch {
        return $false
    } finally {
        $client.Close()
    }
}

if (Test-LocalPort $port) {
    try {
        $health = Invoke-RestMethod "http://127.0.0.1:$port/api/launcher/health" -TimeoutSec 2
        if ($health.service -eq 'service_launcher') {
            Write-Host "Launcher already running: http://127.0.0.1:$port/"
            exit 0
        }
    } catch {
        # Port is open but not our launcher; report instead of silently succeeding.
    }
    throw "Port $port is already in use by another process; refusing to start the launcher."
}

$python = $env:AI_LAUNCHER_PYTHON
if (-not $python) {
    $command = Get-Command python -ErrorAction SilentlyContinue
    if ($command) { $python = $command.Source }
}
if (-not $python) {
    throw 'Python not found. Set AI_LAUNCHER_PYTHON to a Python 3.11 executable.'
}

$logFile = Join-Path $repo '.workbuddy\logs\launcher.log'
New-Item -ItemType Directory -Force -Path (Split-Path $logFile) | Out-Null
$quotedLogFile = '"' + $logFile + '"'

Start-Process -FilePath $python `
    -ArgumentList @('-m', 'service.service_launcher', '--port', "$port", '--log-file', $quotedLogFile) `
    -WorkingDirectory $repo `
    -WindowStyle Hidden

for ($i = 0; $i -lt 40; $i++) {
    Start-Sleep -Milliseconds 300
    if (Test-LocalPort $port) {
        Write-Host "Launcher started: http://127.0.0.1:$port/"
        exit 0
    }
}

throw "Launcher did not open port $port. Check .workbuddy\logs\launcher log or run 'python -m service.service_launcher' manually."
