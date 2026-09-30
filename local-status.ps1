[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'local-process.ps1')
$paths = Get-LocalBotPaths -Root $PSScriptRoot
$metadata = Read-LocalBotMetadata -Paths $paths
if (-not $metadata) {
    $matches = @(Get-LocalBotCommandLineProcesses -Paths $paths)
    if ($matches.Count -gt 0) {
        Write-Host "A Python process with the local bot command line is running without launcher metadata (PID $($matches[0].ProcessId)); stop-local will refuse to stop it."
        exit 2
    }
    Write-Host 'Local bot is not running.'
    exit 0
}

$rootPid = [int]$metadata.processId
$process = Get-CimInstance Win32_Process -Filter "ProcessId = $rootPid" -ErrorAction SilentlyContinue
if (-not $process) {
    Write-Host "Stale metadata: recorded PID $rootPid is not running. Inspect logs\local-bot.json before removing it."
    exit 2
}
if (-not (Test-LocalBotProcessIdentity -Process $process -Paths $paths) -or
    -not (Test-LocalBotMetadataMatch -Metadata $metadata -Process $process)) {
    Write-Host "PID $rootPid does not match the recorded bot identity. No action taken."
    exit 2
}

$workerRecords = @()
if ($metadata.PSObject.Properties.Name -contains 'workerProcesses' -and $null -ne $metadata.workerProcesses) {
    $workerRecords = @($metadata.workerProcesses)
}
$workerIds = @()
foreach ($workerRecord in $workerRecords) {
    $worker = Get-CimInstance Win32_Process -Filter "ProcessId = $([int]$workerRecord.processId)" -ErrorAction SilentlyContinue
    if (-not $worker -or -not (Test-LocalBotWorkerMetadataMatch -Metadata $workerRecord -Process $worker -Paths $paths)) {
        Write-Host "Recorded Python worker PID $($workerRecord.processId) is absent or does not match its recorded identity."
        exit 2
    }
    $workerIds += [int]$worker.ProcessId
}

$allMatches = @(Get-LocalBotCommandLineProcesses -Paths $paths)
$expectedIds = @($rootPid) + $workerIds
$unexpected = @($allMatches | Where-Object { [int]$_.ProcessId -notin $expectedIds })
if ($unexpected.Count -gt 0) {
    Write-Host "An unrecorded process with the exact bot command line exists (PID $($unexpected[0].ProcessId))."
    exit 2
}
if ($allMatches.Count -ne $expectedIds.Count) {
    Write-Host "Expected $($expectedIds.Count) recorded Python processes, found $($allMatches.Count)."
    exit 2
}
Write-Host "Local bot is running (root PID $rootPid; verified Python workers $($workerIds.Count)); stdout: $($paths.Stdout); stderr: $($paths.Stderr)"
