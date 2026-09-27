[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'local-process.ps1')
$paths = Get-LocalBotPaths -Root $PSScriptRoot
New-Item -ItemType Directory -Path $paths.Logs -Force | Out-Null

$lockStream = $null
try {
    try {
        $lockStream = [IO.File]::Open($paths.Lock, [IO.FileMode]::OpenOrCreate, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None)
    } catch [IO.IOException] {
        throw 'Another local bot start/stop operation is in progress. Retry after it finishes.'
    }

    $metadata = Read-LocalBotMetadata -Paths $paths
    if (-not $metadata) {
        $matches = @(Get-LocalBotCommandLineProcesses -Paths $paths)
        if ($matches.Count -gt 0) {
            throw "A matching bot process exists (PID $($matches[0].ProcessId)) without launcher metadata. Refusing to stop an unverified process."
        }
        Write-Host 'Local bot is not running.'
        exit 0
    }

    $rootPid = [int]$metadata.processId
    $process = Get-CimInstance Win32_Process -Filter "ProcessId = $rootPid" -ErrorAction SilentlyContinue
    if ($process -and (-not (Test-LocalBotProcessIdentity -Process $process -Paths $paths) -or
        -not (Test-LocalBotMetadataMatch -Metadata $metadata -Process $process))) {
        throw "PID $rootPid no longer matches the recorded bot executable, command line, and start time. Refusing to stop it."
    }

    $workerRecords = @()
    if ($metadata.PSObject.Properties.Name -contains 'workerProcesses' -and $null -ne $metadata.workerProcesses) {
        $workerRecords = @($metadata.workerProcesses)
    }
    $workers = [System.Collections.Generic.List[object]]::new()
    foreach ($workerRecord in $workerRecords) {
        $worker = Get-CimInstance Win32_Process -Filter "ProcessId = $([int]$workerRecord.processId)" -ErrorAction SilentlyContinue
        if (-not $worker) { continue }
        if (-not (Test-LocalBotWorkerMetadataMatch -Metadata $workerRecord -Process $worker -Paths $paths)) {
            throw "Recorded worker PID $($workerRecord.processId) no longer matches its executable, parent, command line, and start time. Refusing to stop it."
        }
        $workers.Add($worker)
    }

    if ($process) {
        foreach ($worker in @(Get-LocalBotWorkers -Paths $paths -ParentProcessId $rootPid)) {
            if (-not ($workers | Where-Object { [int]$_.ProcessId -eq [int]$worker.ProcessId })) {
                throw "An unrecorded matching Python worker exists (PID $($worker.ProcessId)). Refusing to stop an unverified process."
            }
        }
    } elseif ($workerRecords.Count -eq 0) {
        $orphanWorkers = @(Get-LocalBotWorkers -Paths $paths -ParentProcessId $rootPid)
        $otherMatches = @(Get-LocalBotCommandLineProcesses -Paths $paths)
        if ($orphanWorkers.Count -gt 0 -or $otherMatches.Count -gt 0) {
            throw "Recorded PID $rootPid exited, but matching process(es) remain without verifiable worker metadata. Refusing to stop them."
        }
    }

    $allMatches = @(Get-LocalBotCommandLineProcesses -Paths $paths)
    $allowedIds = @($rootPid) + @($workers | ForEach-Object { [int]$_.ProcessId })
    $unexpected = @($allMatches | Where-Object { [int]$_.ProcessId -notin $allowedIds })
    if ($unexpected.Count -gt 0) {
        throw "An unrecorded process with the exact bot command line exists (PID $($unexpected[0].ProcessId)). Refusing to stop it."
    }

    foreach ($worker in $workers) {
        Stop-Process -Id ([int]$worker.ProcessId) -Force -ErrorAction Stop
    }
    $deadline = [datetime]::UtcNow.AddSeconds(10)
    do {
        $remainingWorkers = @($workers | Where-Object {
            Get-Process -Id ([int]$_.ProcessId) -ErrorAction SilentlyContinue
        })
        if ($remainingWorkers.Count -eq 0) { break }
        Start-Sleep -Milliseconds 100
    } while ([datetime]::UtcNow -lt $deadline)
    if ($remainingWorkers.Count -gt 0) {
        throw "Worker PID $($remainingWorkers[0].ProcessId) is still running. Metadata was kept for a safe retry."
    }

    $process = Get-CimInstance Win32_Process -Filter "ProcessId = $rootPid" -ErrorAction SilentlyContinue
    if ($process) {
        if (-not (Test-LocalBotProcessIdentity -Process $process -Paths $paths) -or
            -not (Test-LocalBotMetadataMatch -Metadata $metadata -Process $process)) {
            throw "PID $rootPid changed identity during shutdown. Refusing to stop it."
        }
        Stop-Process -Id $rootPid -Force -ErrorAction Stop
    }

    $deadline = [datetime]::UtcNow.AddSeconds(10)
    do {
        $remaining = @(Get-LocalBotCommandLineProcesses -Paths $paths)
        if ($remaining.Count -eq 0) { break }
        Start-Sleep -Milliseconds 100
    } while ([datetime]::UtcNow -lt $deadline)
    if ($remaining.Count -gt 0) {
        throw "Matching bot process PID $($remaining[0].ProcessId) is still running. Metadata was kept for a safe retry."
    }
    Remove-Item -LiteralPath $paths.Metadata -Force
    Write-Host "Local bot stopped (root PID $rootPid; verified workers $($workers.Count))."
} finally {
    if ($lockStream) { $lockStream.Dispose() }
}
