$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
. (Join-Path $root 'local-process.ps1')

function Get-FakeBotPythonProcesses {
    param([Parameter(Mandatory)]$Paths)
    @(Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" -ErrorAction Stop |
        Where-Object { Test-LocalBotCommandLine -Process $_ -Paths $Paths })
}

foreach ($script in @('start-local.ps1', 'stop-local.ps1', 'local-status.ps1')) {
    $tokens = $null
    $errors = $null
    [System.Management.Automation.Language.Parser]::ParseFile((Join-Path $root $script), [ref]$tokens, [ref]$errors) | Out-Null
    if ($errors.Count) { throw "$script has PowerShell parse errors: $($errors -join '; ')" }
}

$paths = Get-LocalBotPaths -Root (Join-Path $env:TEMP 'bot checkout with spaces')
$valid = [pscustomobject]@{
    ExecutablePath = $paths.Python
    CommandLine = '"' + $paths.Python + '" "' + $paths.Main + '" --env-file "' + $paths.EnvFile + '"'
    ProcessId = 4321
    CreationDate = '20260927120000.000000+000'
}
if (-not (Test-LocalBotProcessIdentity -Process $valid -Paths $paths)) { throw 'Valid local bot process was rejected.' }
$foreground = [pscustomobject]@{
    ExecutablePath = $paths.Python
    CommandLine = $paths.Python + ' ' + $paths.Main + ' --env-file ' + $paths.EnvFile
}
if (-not (Test-LocalBotProcessIdentity -Process $foreground -Paths $paths)) { throw 'Unquoted foreground run.ps1 command line was rejected.' }

$wrongPython = [pscustomobject]@{ ExecutablePath = 'C:\Python313\python.exe'; CommandLine = $valid.CommandLine }
if (Test-LocalBotProcessIdentity -Process $wrongPython -Paths $paths) { throw 'A different Python executable was accepted.' }

$wrongCheckout = [pscustomobject]@{ ExecutablePath = $paths.Python; CommandLine = '"' + $paths.Python + '" "C:\other\main.py" --env-file "' + $paths.EnvFile + '"' }
if (Test-LocalBotProcessIdentity -Process $wrongCheckout -Paths $paths) { throw 'A different checkout was accepted.' }

$wrongEnv = [pscustomobject]@{ ExecutablePath = $paths.Python; CommandLine = '"' + $paths.Python + '" "' + $paths.Main + '" --env-file "C:\other\.env"' }
if (Test-LocalBotProcessIdentity -Process $wrongEnv -Paths $paths) { throw 'A different env file was accepted.' }

$metadata = [pscustomobject]@{ processId = 4321; python = $paths.Python; processStartedAt = '2026-09-27T12:00:00.0000000Z' }
if (-not (Test-LocalBotMetadataMatch -Metadata $metadata -Process $valid)) { throw 'Matching metadata was rejected.' }
$reusedPid = [pscustomobject]@{ processId = 4321; python = $paths.Python; processStartedAt = '2026-09-27T12:05:00.0000000Z' }
if (Test-LocalBotMetadataMatch -Metadata $reusedPid -Process $valid) { throw 'Reused PID with a different start time was accepted.' }
$realDateTime = [pscustomobject]@{ ProcessId = 4321; ExecutablePath = $paths.Python; CreationDate = [datetime]::Parse('2026-09-27T12:00:00Z') }
if (-not (Test-LocalBotMetadataMatch -Metadata $metadata -Process $realDateTime)) { throw 'DateTime process creation value was rejected.' }

# Exercise the full launcher against a temporary fake bot. The copied interpreter
# runs a script that sleeps; no Discord client or credentials are involved.
$repo = $root
$tempRoot = Join-Path $env:TEMP ('local-bot-launcher-test-' + [guid]::NewGuid().ToString('N'))
$ownedPid = $null
try {
    New-Item -ItemType Directory -Path $tempRoot -Force | Out-Null
    foreach ($file in @('start-local.ps1', 'stop-local.ps1', 'local-status.ps1', 'local-process.ps1')) {
        Copy-Item -LiteralPath (Join-Path $root $file) -Destination $tempRoot
    }
    $testPython = Join-Path $repo '.venv\Scripts\python.exe'
    & $testPython -m venv --without-pip (Join-Path $tempRoot '.venv')
    if ($LASTEXITCODE -ne 0) { throw 'Could not create the isolated fake-bot Python environment.' }
    @'
import sys
import time
if "--check-config" in sys.argv:
    raise SystemExit(0)
while True:
    time.sleep(1)
'@ | Set-Content -LiteralPath (Join-Path $tempRoot 'main.py') -Encoding UTF8
    'test-only' | Set-Content -LiteralPath (Join-Path $tempRoot '.env') -Encoding UTF8

    & pwsh -NoProfile -File (Join-Path $tempRoot 'start-local.ps1')
    if ($LASTEXITCODE -ne 0) { throw 'Fake bot failed to start through the local launcher.' }
    $fakePaths = Get-LocalBotPaths -Root $tempRoot
    $fakeMetadata = Read-LocalBotMetadata -Paths $fakePaths
    $ownedPid = [int]$fakeMetadata.processId
    $realProcess = Get-CimInstance Win32_Process -Filter "ProcessId = $ownedPid" -ErrorAction Stop
    if (-not (Test-LocalBotMetadataMatch -Metadata $fakeMetadata -Process $realProcess)) {
        $storedDate = [datetime]::Parse([string]$fakeMetadata.processStartedAt).ToUniversalTime()
        $cimDate = ConvertTo-LocalBotUtcDateTime -Value $realProcess.CreationDate
        throw "Metadata created from a real Win32_Process record did not verify. PID=$($realProcess.ProcessId)/$($fakeMetadata.processId), exe=$($realProcess.ExecutablePath)/$($fakeMetadata.python), type=$($realProcess.CreationDate.GetType().FullName), kind=$($realProcess.CreationDate.Kind), stored=$($storedDate.ToString('o')), CIM=$($cimDate.ToString('o')), diff=$([Math]::Abs(($storedDate - $cimDate).TotalSeconds))"
    }
    $runningMatches = @(Get-LocalBotProcesses -Paths $fakePaths)
    if ($runningMatches.Count -ne 1 -or [int]$runningMatches[0].ProcessId -ne $ownedPid) {
        throw "Expected one verified fake-bot Python process, found $($runningMatches.Count)."
    }
    $allFakePython = @(Get-FakeBotPythonProcesses -Paths $fakePaths)
    $verifiedWorkers = @(Get-LocalBotWorkers -Paths $fakePaths -ParentProcessId $ownedPid)
    if ($allFakePython.Count -ne 2 -or $verifiedWorkers.Count -ne 1 -or
        [int]$verifiedWorkers[0].ProcessId -ne [int]$fakeMetadata.workerProcesses[0].processId -or
        -not (Test-LocalBotWorkerMetadataMatch -Metadata $fakeMetadata.workerProcesses[0] -Process $verifiedWorkers[0] -Paths $fakePaths)) {
        $processList = ($allFakePython | ForEach-Object { "$($_.ProcessId) parent=$($_.ParentProcessId) exe=$($_.ExecutablePath) cmd=$($_.CommandLine)" }) -join "`n"
        throw "Expected a venv Python launcher and one metadata-verified base interpreter worker; found $($allFakePython.Count): $processList"
    }
    $status = & pwsh -NoProfile -File (Join-Path $tempRoot 'local-status.ps1') 2>&1
    if ($LASTEXITCODE -ne 0 -or ($status -join "`n") -notmatch 'is running') { throw "Fake bot status check failed: $status" }

    $naturalExitWorker = [int]$fakeMetadata.workerProcesses[0].processId
    Stop-Process -Id $naturalExitWorker -Force -ErrorAction Stop
    $deadline = [datetime]::UtcNow.AddSeconds(10)
    while ([datetime]::UtcNow -lt $deadline) {
        Start-Sleep -Milliseconds 100
        if (@(Get-FakeBotPythonProcesses -Paths $fakePaths).Count -eq 0) { break }
    }
    if (@(Get-FakeBotPythonProcesses -Paths $fakePaths).Count -ne 0) {
        throw 'The venv launcher did not exit after its verified base-interpreter worker exited.'
    }
    $ownedPid = $null
    & pwsh -NoProfile -File (Join-Path $tempRoot 'start-local.ps1')
    if ($LASTEXITCODE -ne 0) { throw 'Launcher did not recover from an exited process with stale metadata.' }
    $fakeMetadata = Read-LocalBotMetadata -Paths $fakePaths
    $ownedPid = [int]$fakeMetadata.processId
    & pwsh -NoProfile -File (Join-Path $tempRoot 'stop-local.ps1')
    if ($LASTEXITCODE -ne 0) { throw 'Launcher failed to stop the verified fake bot.' }
    if (Get-CimInstance Win32_Process -Filter "ProcessId = $ownedPid" -ErrorAction SilentlyContinue) {
        throw 'Verified fake bot process remained after stop.'
    }
    if (@(Get-LocalBotProcesses -Paths $fakePaths).Count -ne 0) {
        throw 'A matching fake-bot Python process remained after stop.'
    }
    if (@(Get-FakeBotPythonProcesses -Paths $fakePaths).Count -ne 0) {
        throw 'A Python process with the fake main.py command line remained after stop.'
    }
    $ownedPid = $null
    if (Test-Path -LiteralPath $fakePaths.Metadata) { throw 'Stop left metadata after the verified fake bot exited.' }

    $fakeMetadata | ConvertTo-Json | Set-Content -LiteralPath $fakePaths.Metadata -Encoding UTF8
    if (Get-Process -Id ([int]$fakeMetadata.processId) -ErrorAction SilentlyContinue) {
        throw 'Fake process remained alive after stop.'
    }
    $alreadyStopped = & pwsh -NoProfile -File (Join-Path $tempRoot 'stop-local.ps1')
    if ($LASTEXITCODE -ne 0 -or (Test-Path -LiteralPath $fakePaths.Metadata)) {
        throw "Stop did not clean metadata for an already-exited process: $alreadyStopped"
    }

    & pwsh -NoProfile -File (Join-Path $tempRoot 'start-local.ps1')
    if ($LASTEXITCODE -ne 0) { throw 'Fake bot failed to start for launcher-root termination check.' }
    $fakeMetadata = Read-LocalBotMetadata -Paths $fakePaths
    $ownedPid = [int]$fakeMetadata.processId
    Stop-Process -Id $ownedPid -Force -ErrorAction Stop
    $deadline = [datetime]::UtcNow.AddSeconds(10)
    do {
        Start-Sleep -Milliseconds 100
        $rootAfterKill = Get-CimInstance Win32_Process -Filter "ProcessId = $ownedPid" -ErrorAction SilentlyContinue
        if (-not $rootAfterKill) { break }
    } while ([datetime]::UtcNow -lt $deadline)
    if ($rootAfterKill) { throw 'Launcher root process remained after simulated root termination.' }
    $orphanWorkers = @(Get-FakeBotPythonProcesses -Paths $fakePaths)
    if ($orphanWorkers.Count -gt 0) {
        if ($orphanWorkers.Count -ne 1 -or
            -not (Test-LocalBotWorkerMetadataMatch -Metadata $fakeMetadata.workerProcesses[0] -Process $orphanWorkers[0] -Paths $fakePaths)) {
            throw 'A process remained after root termination without matching the recorded worker identity.'
        }
        & pwsh -NoProfile -File (Join-Path $tempRoot 'start-local.ps1') 2>&1 | Out-Null
        if ($LASTEXITCODE -eq 0) { throw 'Start accepted a duplicate while the recorded worker remained.' }
        & pwsh -NoProfile -File (Join-Path $tempRoot 'stop-local.ps1')
        if ($LASTEXITCODE -ne 0) { throw 'Stop failed to clean the recorded worker after root termination.' }
    } else {
        & pwsh -NoProfile -File (Join-Path $tempRoot 'start-local.ps1')
        if ($LASTEXITCODE -ne 0) { throw 'Start failed to recover after root termination left no worker.' }
        $fakeMetadata = Read-LocalBotMetadata -Paths $fakePaths
        $ownedPid = [int]$fakeMetadata.processId
        & pwsh -NoProfile -File (Join-Path $tempRoot 'stop-local.ps1')
        if ($LASTEXITCODE -ne 0) { throw 'Stop failed after stale root metadata recovery.' }
    }
    $ownedPid = $null
    if (@(Get-FakeBotPythonProcesses -Paths $fakePaths).Count -ne 0 -or
        (Test-Path -LiteralPath $fakePaths.Metadata)) {
        throw 'Fake-bot launcher root/worker processes or metadata remained after root-termination cleanup.'
    }
} finally {
    if ($tempRoot -and (Test-Path -LiteralPath $tempRoot)) {
        $fakePathsForCleanup = Get-LocalBotPaths -Root $tempRoot
        foreach ($leftover in @(Get-FakeBotPythonProcesses -Paths $fakePathsForCleanup)) {
            Stop-Process -Id ([int]$leftover.ProcessId) -Force -ErrorAction SilentlyContinue
        }
    }
    $tempFull = [IO.Path]::GetFullPath($tempRoot)
    $tempBase = [IO.Path]::GetFullPath($env:TEMP).TrimEnd('\') + '\'
    if ($tempFull.StartsWith($tempBase, [StringComparison]::OrdinalIgnoreCase) -and
        (Split-Path -Leaf $tempFull).StartsWith('local-bot-launcher-test-', [StringComparison]::Ordinal)) {
        Remove-Item -LiteralPath $tempFull -Recurse -Force -ErrorAction SilentlyContinue
    }
}

Write-Host 'Local launcher identity and lifecycle checks passed.'
