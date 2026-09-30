[CmdletBinding()]
param(
    [Parameter()]
    [string]$PythonPath
)

$ErrorActionPreference = 'Stop'
$repoRoot = $PSScriptRoot
$venvPython = Join-Path $repoRoot '.venv\Scripts\python.exe'

function Get-PythonVersion([string]$Command, [string[]]$PrefixArgs) {
    $output = & $Command @PrefixArgs --version 2>&1
    if ($LASTEXITCODE -ne 0) { return $null }
    $match = [regex]::Match(($output | Out-String), 'Python\s+(\d+)\.(\d+)\.(\d+)')
    if (-not $match.Success) { return $null }
    return [version]::new([int]$match.Groups[1].Value, [int]$match.Groups[2].Value, [int]$match.Groups[3].Value)
}

$candidates = [System.Collections.Generic.List[object]]::new()
if ($PythonPath) {
    $candidates.Add([pscustomobject]@{ Command = $PythonPath; Args = @() })
} else {
    if (Get-Command py -ErrorAction SilentlyContinue) {
        $candidates.Add([pscustomobject]@{ Command = 'py'; Args = @('-3.12') })
        $candidates.Add([pscustomobject]@{ Command = 'py'; Args = @('-3.13') })
    }
    foreach ($name in @('python3.12', 'python3.13', 'python')) {
        if (Get-Command $name -ErrorAction SilentlyContinue) {
            $candidates.Add([pscustomobject]@{ Command = $name; Args = @() })
        }
    }
}

$selected = $null
foreach ($candidate in $candidates) {
    try {
        $version = Get-PythonVersion $candidate.Command $candidate.Args
        if ($version -and $version.Major -eq 3 -and $version.Minor -in @(12, 13)) {
            $selected = $candidate
            break
        }
    } catch {
        # Keep searching; an unavailable launcher candidate is not usable.
    }
}
if (-not $selected) {
    if ($PythonPath) {
        throw "PythonPath must point to a working Python 3.12 or 3.13 executable: $PythonPath"
    }
    throw 'Python 3.12 or 3.13 was not found. Install one, or pass -PythonPath to its executable.'
}

Push-Location $repoRoot
try {
    if (-not (Test-Path -LiteralPath $venvPython)) {
        $venvArgs = @($selected.Args) + @('-m', 'venv', '.venv')
        & $selected.Command @venvArgs
        if ($LASTEXITCODE -ne 0) { throw "Creating .venv failed with exit code $LASTEXITCODE." }
    }

    $venvVersion = Get-PythonVersion $venvPython @()
    if (-not $venvVersion -or $venvVersion.Major -ne 3 -or $venvVersion.Minor -notin @(12, 13)) {
        throw 'The existing .venv does not use Python 3.12 or 3.13. Recreate .venv with a supported interpreter, then run setup again.'
    }

    & $venvPython -m pip install --upgrade pip
    if ($LASTEXITCODE -ne 0) { throw "Upgrading pip failed with exit code $LASTEXITCODE." }

    & $venvPython -m pip install -r requirements.txt
    if ($LASTEXITCODE -ne 0) { throw "Installing pinned runtime dependencies failed with exit code $LASTEXITCODE." }

    $envPath = Join-Path $repoRoot '.env'
    if (-not (Test-Path -LiteralPath $envPath)) {
        Copy-Item -LiteralPath (Join-Path $repoRoot '.env.example') -Destination $envPath
        Write-Host 'Created .env from .env.example. Add your credentials to .env.'
    } else {
        Write-Host 'Kept existing .env unchanged.'
    }
    Write-Host 'Setup complete. Configure .env, then run .\run.ps1.'
} finally {
    Pop-Location
}
