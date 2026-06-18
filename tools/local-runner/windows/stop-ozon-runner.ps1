param(
  [string]$RunnerName = "ozon-local-heavy-runner",
  [string]$RunnerRoot = "D:\ozon-local-runner\actions-runner"
)

$ErrorActionPreference = "Stop"

$services = @(
  Get-Service |
    Where-Object {
      $_.Name -like "*$RunnerName*" -or $_.DisplayName -like "*$RunnerName*"
    }
)

if ($services.Count -eq 0) {
  Write-Host "No Windows service was found for $RunnerName."
  $runnerProcesses = @(
    Get-Process -ErrorAction SilentlyContinue |
      Where-Object { $_.Path -like "$RunnerRoot*" }
  )

  if ($runnerProcesses.Count -eq 0) {
    Write-Host "No foreground runner processes found under $RunnerRoot."
    exit 0
  }

  $runnerProcesses | Select-Object Id, ProcessName, Path, StartTime | Format-Table -AutoSize
  foreach ($process in $runnerProcesses) {
    Stop-Process -Id $process.Id -Force
  }
  Write-Host "Stopped foreground runner processes under $RunnerRoot."
  exit 0
}

if ($services.Count -gt 1) {
  $services | Format-Table Name, DisplayName, Status -AutoSize
  throw "Multiple services match $RunnerName. Refusing to stop an ambiguous service."
}

$service = $services[0]
if ($service.Status -ne "Stopped") {
  Stop-Service -Name $service.Name
}

Get-Service -Name $service.Name | Format-Table Name, DisplayName, Status -AutoSize
