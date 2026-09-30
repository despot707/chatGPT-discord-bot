[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$Host.UI.RawUI.WindowTitle = 'Discord AI Bot'
Set-Location -LiteralPath $PSScriptRoot
$powershell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
$statusScript = Join-Path $PSScriptRoot 'local-status.ps1'
$startScript = Join-Path $PSScriptRoot 'start-local.ps1'

try {
    $statusOutput = @(& $powershell `
        -NoProfile -ExecutionPolicy Bypass -File $statusScript 2>&1)
    $statusExitCode = $LASTEXITCODE
    $statusText = ($statusOutput | ForEach-Object { [string]$_ }) -join [Environment]::NewLine

    if ($statusExitCode -eq 0 -and $statusText -match '^Local bot is running\b') {
        Write-Host 'Bot is already running.'
    } else {
        # The guarded launcher handles stale records after reboot and refuses
        # ambiguous or duplicate processes. Keep those checks in one place.
        & $powershell -NoProfile -ExecutionPolicy Bypass -File $startScript
        if ($LASTEXITCODE -ne 0) { throw "Bot launcher exited with code $LASTEXITCODE." }
        & $powershell -NoProfile -ExecutionPolicy Bypass -File $statusScript
        if ($LASTEXITCODE -ne 0) { throw "Bot status check exited with code $LASTEXITCODE." }
    }

    Write-Host ''
    Write-Host 'The bot runs in the background, so you can close this window.'
    Write-Host 'In Discord, mention the bot account to chat.'
    Write-Host 'To stop it, run .\stop-local.ps1 in this folder.'
} catch {
    Write-Host "Desktop bot launcher failed: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host 'This window stays open so you can read the error above.'
    $global:LASTEXITCODE = 1
}
