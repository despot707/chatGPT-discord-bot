Set-StrictMode -Version Latest

function Get-LocalBotPaths {
    param([string]$Root)
    $rootPath = [IO.Path]::GetFullPath($Root).TrimEnd('\')
    [pscustomobject]@{
        Root = $rootPath
        Python = Join-Path $rootPath '.venv\Scripts\python.exe'
        Main = Join-Path $rootPath 'main.py'
        EnvFile = Join-Path $rootPath '.env'
        Logs = Join-Path $rootPath 'logs'
        Metadata = Join-Path $rootPath 'logs\local-bot.json'
        Lock = Join-Path $rootPath 'logs\local-bot-start.lock'
        Stdout = Join-Path $rootPath 'logs\local-bot.stdout.log'
        Stderr = Join-Path $rootPath 'logs\local-bot.stderr.log'
    }
}

function Test-LocalBotCommandLine {
    param([Parameter(Mandatory)]$Process, [Parameter(Mandatory)]$Paths)
    if (-not $Process.CommandLine) { return $false }
    try {
        $actualMain = [IO.Path]::GetFullPath([string]$Paths.Main)
        $actualEnv = [IO.Path]::GetFullPath([string]$Paths.EnvFile)
    } catch { return $false }
    $line = [string]$Process.CommandLine
    $mainPattern = '(?i)(?:^|\s)(?:"' + [regex]::Escape($actualMain) + '"|' + [regex]::Escape($actualMain) + ')(?=\s|$)'
    $envPattern = '(?i)(?:^|\s)--env-file\s+(?:"' + [regex]::Escape($actualEnv) + '"|' + [regex]::Escape($actualEnv) + ')(?=\s|$)'
    return [regex]::IsMatch($line, $mainPattern) -and [regex]::IsMatch($line, $envPattern)
}

function Test-LocalBotProcessIdentity {
    param([Parameter(Mandatory)]$Process, [Parameter(Mandatory)]$Paths)
    if (-not $Process.ExecutablePath) { return $false }
    try {
        $actualExe = [IO.Path]::GetFullPath([string]$Process.ExecutablePath)
        $expectedExe = [IO.Path]::GetFullPath([string]$Paths.Python)
    } catch { return $false }
    return [string]::Equals($actualExe, $expectedExe, [StringComparison]::OrdinalIgnoreCase) -and
        (Test-LocalBotCommandLine -Process $Process -Paths $Paths)
}

function Get-LocalBotProcesses {
    param([Parameter(Mandatory)]$Paths)
    @(Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" -ErrorAction Stop |
        Where-Object { Test-LocalBotProcessIdentity -Process $_ -Paths $Paths })
}

function Get-LocalBotCommandLineProcesses {
    param([Parameter(Mandatory)]$Paths)
    @(Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" -ErrorAction Stop |
        Where-Object { Test-LocalBotCommandLine -Process $_ -Paths $Paths })
}

function Get-LocalBotWorkers {
    param([Parameter(Mandatory)]$Paths, [Parameter(Mandatory)][int]$ParentProcessId)
    @(Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" -ErrorAction Stop |
        Where-Object {
            [int]$_.ParentProcessId -eq $ParentProcessId -and
            (Test-LocalBotCommandLine -Process $_ -Paths $Paths)
        })
}

function Read-LocalBotMetadata {
    param([Parameter(Mandatory)]$Paths)
    if (-not (Test-Path -LiteralPath $Paths.Metadata -PathType Leaf)) { return $null }
    try {
        return Get-Content -LiteralPath $Paths.Metadata -Raw -ErrorAction Stop | ConvertFrom-Json -ErrorAction Stop
    } catch {
        throw "Local bot metadata is unreadable at '$($Paths.Metadata)'. Inspect it before starting or stopping the bot. $($_.Exception.Message)"
    }
}

function Test-LocalBotMetadataMatch {
    param([Parameter(Mandatory)]$Metadata, [Parameter(Mandatory)]$Process)
    if ([int]$Metadata.processId -ne [int]$Process.ProcessId) { return $false }
    if (-not [string]::Equals([string]$Metadata.python, [string]$Process.ExecutablePath, [StringComparison]::OrdinalIgnoreCase)) { return $false }
    try {
        $expectedStart = ConvertTo-LocalBotUtcDateTime -Value $Metadata.processStartedAt
        $actualStart = ConvertTo-LocalBotUtcDateTime -Value $Process.CreationDate
        return [Math]::Abs(($expectedStart - $actualStart).TotalSeconds) -le 2
    } catch { return $false }
}

function Test-LocalBotWorkerMetadataMatch {
    param([Parameter(Mandatory)]$Metadata, [Parameter(Mandatory)]$Process, [Parameter(Mandatory)]$Paths)
    if ([int]$Metadata.processId -ne [int]$Process.ProcessId -or
        [int]$Metadata.parentProcessId -ne [int]$Process.ParentProcessId) { return $false }
    if (-not [string]::Equals([string]$Metadata.python, [string]$Process.ExecutablePath, [StringComparison]::OrdinalIgnoreCase)) { return $false }
    if (-not (Test-LocalBotCommandLine -Process $Process -Paths $Paths)) { return $false }
    try {
        $expectedStart = ConvertTo-LocalBotUtcDateTime -Value $Metadata.processStartedAt
        $actualStart = ConvertTo-LocalBotUtcDateTime -Value $Process.CreationDate
        return [Math]::Abs(($expectedStart - $actualStart).TotalSeconds) -le 2
    } catch { return $false }
}

function ConvertTo-LocalBotUtcDateTime {
    param([Parameter(Mandatory)]$Value)
    if ($Value -is [datetime]) {
        if ($Value.Kind -eq [DateTimeKind]::Unspecified) {
            return [datetime]::SpecifyKind($Value, [DateTimeKind]::Local).ToUniversalTime()
        }
        return $Value.ToUniversalTime()
    }
    if ($Value -is [datetimeoffset]) { return $Value.UtcDateTime }
    $text = [string]$Value
    try {
        return ([Management.ManagementDateTimeConverter]::ToDateTime($text)).ToUniversalTime()
    } catch {
        return ([datetime]::Parse($text)).ToUniversalTime()
    }
}
