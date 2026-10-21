# Install a per-user Startup shortcut so the launcher is available after login.
# This only writes one .lnk shortcut into the current user's Startup folder.
# No admin rights, scheduled task, Windows service or registry change is used.
# The .lnk stores Unicode paths, so non-ASCII repository paths keep working.

$ErrorActionPreference = 'Stop'

$repo = Split-Path -Parent $PSScriptRoot
$startScript = Join-Path $repo 'scripts\start_services.ps1'
if (-not (Test-Path $startScript)) {
    throw "Missing $startScript"
}

$startup = [Environment]::GetFolderPath('Startup')
if (-not $startup -or -not (Test-Path $startup)) {
    throw 'Cannot locate the current user Startup folder.'
}

$shortcutPath = Join-Path $startup 'AIinteraction-Services.lnk'
$legacyPath = Join-Path $startup 'AIinteraction-Services.cmd'
if (Test-Path $legacyPath) {
    Remove-Item -LiteralPath $legacyPath -Force
}

$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = 'powershell.exe'
$shortcut.Arguments = "-NoProfile -ExecutionPolicy RemoteSigned -WindowStyle Hidden -File `"$startScript`""
$shortcut.WorkingDirectory = $repo
$shortcut.WindowStyle = 7
$shortcut.Description = 'AIinteraction local service launcher'
$shortcut.Save()

Write-Host "Installed startup shortcut: $shortcutPath"
Write-Host 'Starting the launcher now...'
& $startScript
