param(
    [string]$RunnerRoot = "D:\ozon-local-runner\actions-runner",
    [string]$RunnerName = "ozon-local-heavy-runner",
    [string]$RuntimeRoot = "D:\ozon-local-runner"
)

$ErrorActionPreference = "Stop"

$services = @(
Get-Service |
        Where-Object {
            $_.Name -like "*$RunnerName*" -or $_.DisplayName -like "*$RunnerName*"
        }
)

if ($services.Count -eq 1)
{
    $service = $services[0]
    if ($service.Status -ne "Running")
    {
        Start-Service -Name $service.Name
    }
    Get-Service -Name $service.Name | Format-Table Name, DisplayName, Status -AutoSize
    exit 0
}

if ($services.Count -gt 1)
{
    $services | Format-Table Name, DisplayName, Status -AutoSize
    throw "Multiple services match $RunnerName. Refusing to start an ambiguous service."
}

Write-Host "No Windows service was found for $RunnerName."
$runCmd = Join-Path $RunnerRoot "run.cmd"
if (Test-Path -LiteralPath $runCmd)
{
    $listener = Get-Process -ErrorAction SilentlyContinue |
            Where-Object {
                $_.Path -like "$RunnerRoot*" -and $_.ProcessName -eq "Runner.Listener"
            } |
            Select-Object -First 1

    if ($listener)
    {
        Write-Host "Runner.Listener is already running."
        $listener | Select-Object Id, ProcessName, Path, StartTime | Format-Table -AutoSize
        exit 0
    }

    $logsRoot = Join-Path $RuntimeRoot "logs"
    New-Item -ItemType Directory -Path $logsRoot -Force | Out-Null
    $stdout = Join-Path $logsRoot "runner-foreground.log"
    $stderr = Join-Path $logsRoot "runner-foreground.err.log"
    $process = Start-Process `
    -FilePath $runCmd `
    -WorkingDirectory $RunnerRoot `
    -WindowStyle Hidden `
    -RedirectStandardOutput $stdout `
    -RedirectStandardError $stderr `
    -PassThru
    $process.Id | Set-Content -LiteralPath (Join-Path $RunnerRoot ".runner-pid") -Encoding ascii
    Write-Host "Started foreground runner process. PID=$( $process.Id )"
    Write-Host "stdout=$stdout"
    Write-Host "stderr=$stderr"
}
else
{
    Write-Host "Runner command not found at $runCmd. Register the runner first."
}
