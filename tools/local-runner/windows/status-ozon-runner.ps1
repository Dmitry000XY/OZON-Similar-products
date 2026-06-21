param(
    [string]$RunnerRoot = "D:\ozon-local-runner\actions-runner",
    [string]$RunnerName = "ozon-local-heavy-runner",
    [string]$RuntimeRoot = "D:\ozon-local-runner"
)

$ErrorActionPreference = "Continue"

$logsRoot = Join-Path $RuntimeRoot "logs"
$foregroundLog = Join-Path $logsRoot "runner-foreground.log"
$foregroundErrLog = Join-Path $logsRoot "runner-foreground.err.log"
$legacyForegroundLog = Join-Path $RunnerRoot "runner-foreground.log"
$pidPath = Join-Path $RunnerRoot ".runner-pid"

function Test-ProcessAlive
{
    param([int]$ProcessId)

    $process = Get-Process -Id $ProcessId -ErrorAction SilentlyContinue
    if ($null -eq $process)
    {
        return $false
    }
    return $true
}

Write-Host "RunnerName=$RunnerName"
Write-Host "RunnerRoot=$RunnerRoot"
Write-Host "RuntimeRoot=$RuntimeRoot"
Write-Host "LogsRoot=$logsRoot"

Write-Host "`nService:"
$services = @(
Get-Service |
        Where-Object {
            $_.Name -like "*$RunnerName*" -or $_.DisplayName -like "*$RunnerName*"
        }
)
if ($services.Count -eq 0)
{
    Write-Host "No service found for $RunnerName."
}
else
{
    $services | Format-Table Name, DisplayName, Status -AutoSize
}
$serviceRunning = @($services | Where-Object { $_.Status -eq "Running" }).Count -gt 0

Write-Host "`nForeground runner processes:"
$runnerProcesses = @(
Get-Process -ErrorAction SilentlyContinue |
        Where-Object { $_.Path -like "$RunnerRoot*" }
)
$listenerProcesses = @($runnerProcesses | Where-Object { $_.ProcessName -eq "Runner.Listener" })
if ($runnerProcesses.Count -eq 0)
{
    Write-Host "No foreground runner processes found under $RunnerRoot."
}
else
{
    $runnerProcesses | Select-Object Id, ProcessName, Path, StartTime | Format-Table -AutoSize
}

Write-Host "`nForeground PID file:"
if (Test-Path -LiteralPath $pidPath)
{
    $pidValue = (Get-Content -LiteralPath $pidPath -ErrorAction SilentlyContinue | Select-Object -First 1)
    $pidAlive = $false
    if ($pidValue -match "^\d+$")
    {
        $pidAlive = Test-ProcessAlive -ProcessId ([int]$pidValue)
    }
    [pscustomobject]@{
        PidPath = $pidPath
        Pid = $pidValue
        Alive = $pidAlive
    } | Format-List
}
else
{
    Write-Host "No PID file found at $pidPath."
}

$recentLogs = @($foregroundLog, $legacyForegroundLog) |
        Where-Object { Test-Path -LiteralPath $_ } |
        Select-Object -Unique
$listeningInLog = $false
foreach ($log in $recentLogs)
{
    $tailText = (Get-Content -LiteralPath $log -Tail 80 -ErrorAction SilentlyContinue) -join "`n"
    if ($tailText -match "Listening for Jobs")
    {
        $listeningInLog = $true
    }
}

Write-Host "`nLocal runner state:"
[pscustomobject]@{
    LocallyOnline = ($serviceRunning -or $listenerProcesses.Count -gt 0)
    ServiceRunning = $serviceRunning
    ForegroundProcessRunning = ($listenerProcesses.Count -gt 0)
    ListeningForJobsInRecentLog = $listeningInLog
} | Format-List

Write-Host "`nLogs:"
@(
    $foregroundLog,
    $foregroundErrLog,
    $legacyForegroundLog,
    (Join-Path $RunnerRoot "runner-foreground.err.log")
) | Select-Object -Unique | ForEach-Object {
    [pscustomobject]@{
        Path = $_
        Exists = Test-Path -LiteralPath $_
    }
} | Format-Table -AutoSize

foreach ($log in $recentLogs)
{
    Write-Host ("`nLast lines from {0}:" -f $log)
    Get-Content -LiteralPath $log -Tail 30 -ErrorAction SilentlyContinue
}

Write-Host "`nDocker:"
try
{
    docker --version
    docker compose version
    docker info --format "Docker server={{.ServerVersion}} os={{.OSType}} arch={{.Architecture}} cpus={{.NCPU}} memory={{.MemTotal}}"
}
catch
{
    Write-Host "Docker is not available: $( $_.Exception.Message )"
}

Write-Host "`nPaths:"
@(
    $RuntimeRoot,
    $logsRoot,
    (Join-Path $RuntimeRoot "data\raw"),
    (Join-Path $RuntimeRoot "data\raw\user_actions"),
    (Join-Path $RuntimeRoot "data\raw\product_information"),
    (Join-Path $RuntimeRoot "data\processed"),
    (Join-Path $RuntimeRoot "outputs"),
    (Join-Path $RuntimeRoot "artifacts"),
    $RunnerRoot
) | ForEach-Object {
    [pscustomobject]@{
        Path = $_
        Exists = Test-Path -LiteralPath $_
    }
} | Format-Table -AutoSize

Write-Host "`nFree disk:"
Get-PSDrive -PSProvider FileSystem | Format-Table Name, Root, Free, Used -AutoSize
