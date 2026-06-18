param(
  [string]$RepoUrl = "https://github.com/Dmitry000XY/OZON-Similar-products",
  [string]$RunnerRoot = "D:\ozon-local-runner\actions-runner",
  [string]$RunnerName = "ozon-local-heavy-runner",
  [string]$RunnerLabels = "ozon-local-heavy",
  [switch]$AsService,
  [switch]$Replace,
  [switch]$TokenFromClipboard
)

$ErrorActionPreference = "Stop"

function Convert-SecureStringToPlainText {
  param([Parameter(Mandatory = $true)][securestring]$SecureValue)

  $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($SecureValue)
  try {
    [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
  } finally {
    if ($bstr -ne [IntPtr]::Zero) {
      [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
    }
  }
}

function Get-RunnerAsset {
  $release = Invoke-RestMethod `
    -Uri "https://api.github.com/repos/actions/runner/releases/latest" `
    -Headers @{ "User-Agent" = "ozon-local-runner-setup" }

  $asset = $release.assets |
    Where-Object { $_.name -like "actions-runner-win-x64-*.zip" } |
    Select-Object -First 1

  if (-not $asset) {
    throw "Could not find a Windows x64 runner asset in the latest actions/runner release."
  }
  if ($asset.browser_download_url -notmatch "^https://github\.com/actions/runner/releases/download/") {
    throw "Unexpected runner download URL: $($asset.browser_download_url)"
  }

  $asset
}

function Get-RunnerService {
  Get-Service |
    Where-Object {
      $_.Name -like "*$RunnerName*" -or $_.DisplayName -like "*$RunnerName*"
    }
}

Write-Host "RepoUrl=$RepoUrl"
Write-Host "RunnerRoot=$RunnerRoot"
Write-Host "RunnerName=$RunnerName"
Write-Host "RunnerLabels=$RunnerLabels"

New-Item -ItemType Directory -Path $RunnerRoot -Force | Out-Null
Set-Location $RunnerRoot

if (-not (Test-Path -LiteralPath (Join-Path $RunnerRoot "config.cmd"))) {
  $asset = Get-RunnerAsset
  $zipPath = Join-Path $RunnerRoot $asset.name
  Write-Host "Downloading official GitHub Actions runner asset: $($asset.name)"
  Invoke-WebRequest -Uri $asset.browser_download_url -OutFile $zipPath
  Expand-Archive -LiteralPath $zipPath -DestinationPath $RunnerRoot -Force
} else {
  Write-Host "Runner binaries already exist in $RunnerRoot"
}

$existingServices = @(Get-RunnerService)
if ($existingServices.Count -gt 0 -and -not $Replace) {
  Write-Host "A service matching $RunnerName already exists:"
  $existingServices | Format-Table Name, DisplayName, Status -AutoSize
  throw "Use -Replace only after confirming this runner is safe to re-register."
}

$token = $null
if ($TokenFromClipboard) {
  $clipboardText = (Get-Clipboard -Raw).Trim()
  if ([string]::IsNullOrWhiteSpace($clipboardText)) {
    throw "Clipboard is empty. Copy a GitHub Actions runner registration token first."
  }

  $tokenMatch = [regex]::Match($clipboardText, "--token\s+([^\s]+)")
  if ($tokenMatch.Success) {
    $clipboardText = $tokenMatch.Groups[1].Value
  }

  $token = ConvertTo-SecureString -String $clipboardText -AsPlainText -Force
  $clipboardText = $null
} else {
  $token = Read-Host -Prompt "Paste a GitHub Actions runner registration token for this repo" -AsSecureString
}
$plainToken = Convert-SecureStringToPlainText -SecureValue $token
try {
  if ((Test-Path -LiteralPath (Join-Path $RunnerRoot ".runner")) -and $Replace) {
    Write-Host "Existing local runner config found. Removing local config before replacement."
    & .\config.cmd remove --local
    if ($LASTEXITCODE -ne 0) {
      throw "config.cmd remove --local failed with exit code $LASTEXITCODE"
    }
  }

  $configArgs = @(
    "--unattended",
    "--url", $RepoUrl,
    "--token", $plainToken,
    "--name", $RunnerName,
    "--labels", $RunnerLabels,
    "--work", "_work"
  )
  if ($Replace) {
    $configArgs += "--replace"
  }
  if ($AsService) {
    $configArgs += "--runasservice"
  }

  & .\config.cmd @configArgs
  if ($LASTEXITCODE -ne 0) {
    throw "config.cmd failed with exit code $LASTEXITCODE"
  }
} finally {
  $plainToken = $null
  $token.Dispose()
}

Write-Host "Runner registration complete."
Write-Host "Check status with: tools\local-runner\windows\status-ozon-runner.ps1"
