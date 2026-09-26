[CmdletBinding()]
param(
    [Parameter()]
    [switch]$CheckConfig
)

$ErrorActionPreference = 'Stop'
$python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) {
    Write-Error 'Project environment not found. Run .\setup.ps1 first.'
    exit 1
}

Push-Location $PSScriptRoot
try {
    if ($CheckConfig) {
        & $python (Join-Path $PSScriptRoot 'main.py') --check-config
    } else {
        & $python (Join-Path $PSScriptRoot 'main.py')
    }
    $botExitCode = $LASTEXITCODE
} finally {
    Pop-Location
}
exit $botExitCode
