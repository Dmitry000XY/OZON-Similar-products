param(
    [string]$RunnerRoot = "D:\ozon-local-runner\actions-runner",
    [string]$RunnerName = "ozon-local-heavy-runner",
    [string]$RuntimeRoot = "D:\ozon-local-runner",
    [string]$GitHubActionsUrl = "https://github.com/Dmitry000XY/OZON-Similar-products/actions"
)

$ErrorActionPreference = "Stop"

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$logsRoot = Join-Path $RuntimeRoot "logs"
New-Item -ItemType Directory -Path $logsRoot -Force | Out-Null

function Get-RunnerState
{
    $service = Get-Service -ErrorAction SilentlyContinue |
            Where-Object { $_.Name -like "*$RunnerName*" -or $_.DisplayName -like "*$RunnerName*" } |
            Select-Object -First 1

    $listener = Get-Process -ErrorAction SilentlyContinue |
            Where-Object { $_.Path -like "$RunnerRoot*" -and $_.ProcessName -eq "Runner.Listener" } |
            Select-Object -First 1

    if ($service -and $service.Status -eq "Running")
    {
        return "running"
    }
    if ($listener)
    {
        return "running"
    }
    return "stopped"
}

function Update-IconText
{
    $state = Get-RunnerState
    $notifyIcon.Text = "Ozon runner: $state"
}

function Start-RunnerScript
{
    param([string]$ScriptName)

    $scriptPath = Join-Path $scriptRoot $ScriptName
    Start-Process `
    -FilePath "powershell.exe" `
    -ArgumentList @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $scriptPath) `
    -WindowStyle Hidden
}

function Open-CmdWrapper
{
    param([string]$CmdName)

    $cmdPath = Join-Path $scriptRoot $CmdName
    Start-Process -FilePath "cmd.exe" -ArgumentList @("/c", "`"$cmdPath`"")
}

$notifyIcon = New-Object System.Windows.Forms.NotifyIcon
$notifyIcon.Icon = [System.Drawing.SystemIcons]::Application
$notifyIcon.Visible = $true

$menu = New-Object System.Windows.Forms.ContextMenuStrip

$startItem = $menu.Items.Add("Start runner")
$startItem.add_Click({
    Start-RunnerScript -ScriptName "start-ozon-runner.ps1"
    Start-Sleep -Milliseconds 800
    Update-IconText
})

$stopItem = $menu.Items.Add("Stop runner")
$stopItem.add_Click({
    Start-RunnerScript -ScriptName "stop-ozon-runner.ps1"
    Start-Sleep -Milliseconds 800
    Update-IconText
})

$statusItem = $menu.Items.Add("Status")
$statusItem.add_Click({ Open-CmdWrapper -CmdName "status-ozon-runner.cmd" })

$logsItem = $menu.Items.Add("Open logs folder")
$logsItem.add_Click({ Start-Process -FilePath "explorer.exe" -ArgumentList @($logsRoot) })

$actionsItem = $menu.Items.Add("Open GitHub Actions")
$actionsItem.add_Click({ Start-Process $GitHubActionsUrl })

[void]$menu.Items.Add("-")

$exitItem = $menu.Items.Add("Exit tray monitor")
$exitItem.add_Click({
    $notifyIcon.Visible = $false
    $notifyIcon.Dispose()
    [System.Windows.Forms.Application]::Exit()
})

$notifyIcon.ContextMenuStrip = $menu

$timer = New-Object System.Windows.Forms.Timer
$timer.Interval = 5000
$timer.add_Tick({ Update-IconText })
$timer.Start()

Update-IconText
[System.Windows.Forms.Application]::Run()
