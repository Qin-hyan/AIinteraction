# Remove the per-user Startup shortcut created by install_autostart.ps1.
# Running services are left untouched.

$ErrorActionPreference = 'Stop'

$startup = [Environment]::GetFolderPath('Startup')
$shortcutPath = Join-Path $startup 'AIinteraction-Services.lnk'
$legacyPath = Join-Path $startup 'AIinteraction-Services.cmd'

$removed = $false
foreach ($entry in @($shortcutPath, $legacyPath)) {
    if (Test-Path $entry) {
        Remove-Item -LiteralPath $entry -Force
        Write-Host "Removed startup entry: $entry"
        $removed = $true
    }
}
if (-not $removed) {
    Write-Host 'Startup entry was not installed.'
}
