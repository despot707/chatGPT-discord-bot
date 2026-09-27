[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'local-process.ps1')
$paths = Get-LocalBotPaths -Root $PSScriptRoot

foreach ($required in @($paths.Python, $paths.Main, $paths.EnvFile)) {
    if (-not (Test-Path -LiteralPath $required -PathType Leaf)) {
        throw "Required file is missing: $required"
    }
}
New-Item -ItemType Directory -Path $paths.Logs -Force | Out-Null

$lockStream = $null
$process = $null
try {
    try {
        $lockStream = [IO.File]::Open($paths.Lock, [IO.FileMode]::OpenOrCreate, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None)
    } catch [IO.IOException] {
        throw 'Another local bot start/stop operation is in progress. Retry after it finishes.'
    }

    $metadata = Read-LocalBotMetadata -Paths $paths
    if ($metadata) {
        $recorded = Get-CimInstance Win32_Process -Filter "ProcessId = $([int]$metadata.processId)" -ErrorAction SilentlyContinue
        if ($recorded) {
            if (Test-LocalBotProcessIdentity -Process $recorded -Paths $paths) {
                throw "The local bot is already running (PID $($recorded.ProcessId)). Use .\stop-local.ps1 first."
            }
            throw "PID $($metadata.processId) is now a different process. Refusing to replace stale local bot metadata; inspect logs\local-bot.json manually."
        }
        $matches = @(Get-LocalBotCommandLineProcesses -Paths $paths)
        if ($matches.Count -gt 0) {
            $liveIds = @($matches.ProcessId) -join ', '
            throw "Recorded PID $($metadata.processId) exited, but matching bot process(es) remain (PID(s) $liveIds). Refusing to clear metadata or create a duplicate."
        }
        Remove-Item -LiteralPath $paths.Metadata -Force
        Write-Host "Removed stale metadata for exited PID $($metadata.processId)."
    }

    $matches = @(Get-LocalBotCommandLineProcesses -Paths $paths)
    if ($matches.Count -gt 0) {
        throw "A matching bot process already exists (PID $($matches[0].ProcessId)) without launcher metadata. Refusing to create a duplicate."
    }

    & $paths.Python $paths.Main --env-file $paths.EnvFile --check-config
    if ($LASTEXITCODE -ne 0) { throw "Configuration check failed with exit code $LASTEXITCODE; see the message above." }

    $argumentLine = '"{0}" --env-file "{1}"' -f $paths.Main, $paths.EnvFile
    $process = Start-Process -FilePath $paths.Python -ArgumentList $argumentLine -WorkingDirectory $paths.Root `
        -WindowStyle Hidden -RedirectStandardOutput $paths.Stdout -RedirectStandardError $paths.Stderr -PassThru

    $deadline = [datetime]::UtcNow.AddSeconds(10)
    $verified = $null
    $workers = @()
    do {
        Start-Sleep -Milliseconds 200
        $candidate = Get-CimInstance Win32_Process -Filter "ProcessId = $($process.Id)" -ErrorAction SilentlyContinue
        if ($candidate -and (Test-LocalBotProcessIdentity -Process $candidate -Paths $paths)) {
            $verified = $candidate
            $workers = @(Get-LocalBotWorkers -Paths $paths -ParentProcessId ([int]$verified.ProcessId))
            if ($workers.Count -gt 0) { break }
        }
        if (-not (Get-Process -Id $process.Id -ErrorAction SilentlyContinue)) { break }
    } while ([datetime]::UtcNow -lt $deadline)

    if (-not $verified -or $workers.Count -ne 1) {
        throw "Started PID $($process.Id), but could not verify its executable and command line. It was left untouched for safety; inspect it and logs\local-bot.stderr.log."
    }

    $record = [ordered]@{
        processId = [int]$verified.ProcessId
        python = [string]$verified.ExecutablePath
        processStartedAt = (ConvertTo-LocalBotUtcDateTime -Value $verified.CreationDate).ToString('o')
        root = $paths.Root
        main = $paths.Main
        envFile = $paths.EnvFile
        startedAt = [datetime]::UtcNow.ToString('o')
        workerProcesses = @($workers | ForEach-Object {
            [ordered]@{
                processId = [int]$_.ProcessId
                parentProcessId = [int]$_.ParentProcessId
                python = [string]$_.ExecutablePath
                processStartedAt = (ConvertTo-LocalBotUtcDateTime -Value $_.CreationDate).ToString('o')
            }
        })
    }
    $rootCheck = Get-CimInstance Win32_Process -Filter "ProcessId = $($record.processId)" -ErrorAction SilentlyContinue
    if (-not $rootCheck -or -not (Test-LocalBotProcessIdentity -Process $rootCheck -Paths $paths) -or
        -not (Test-LocalBotMetadataMatch -Metadata $record -Process $rootCheck)) {
        throw 'The local bot process exited or changed identity before startup could be recorded.'
    }
    foreach ($workerRecord in $record.workerProcesses) {
        $workerCheck = Get-CimInstance Win32_Process -Filter "ProcessId = $($workerRecord.processId)" -ErrorAction SilentlyContinue
        if (-not $workerCheck -or -not (Test-LocalBotWorkerMetadataMatch -Metadata $workerRecord -Process $workerCheck -Paths $paths)) {
            throw "Python worker PID $($workerRecord.processId) exited or changed identity before startup could be recorded."
        }
    }
    $exactProcesses = @(Get-LocalBotCommandLineProcesses -Paths $paths)
    $expectedProcessIds = @([int]$record.processId) + @($record.workerProcesses | ForEach-Object { [int]$_.processId })
    if ($exactProcesses.Count -ne $expectedProcessIds.Count -or
        @($exactProcesses | Where-Object { [int]$_.ProcessId -notin $expectedProcessIds }).Count -gt 0) {
        throw 'The exact bot command line is running under an unexpected set of Python processes; refusing to claim a successful start.'
    }
    $metadataTemp = $paths.Metadata + '.tmp'
    $record | ConvertTo-Json | Set-Content -LiteralPath $metadataTemp -Encoding UTF8
    Move-Item -LiteralPath $metadataTemp -Destination $paths.Metadata -Force
    Write-Host "Local bot started (PID $($verified.ProcessId)). Logs: $($paths.Logs)"
} finally {
    if ($lockStream) { $lockStream.Dispose() }
}
