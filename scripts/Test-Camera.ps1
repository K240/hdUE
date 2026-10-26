param(
    [string]$OpenUsdRoot = '',
    [string]$OutputDirectory = ''
)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'Local-Paths.ps1')
$OpenUsdRoot=Get-HdUELocalPath -Key openusd_root -Value $OpenUsdRoot
$TaskRoot = Split-Path -Parent $PSScriptRoot
$RunOutput = if ($OutputDirectory) { [IO.Path]::GetFullPath($OutputDirectory) } else { Join-Path $TaskRoot ('out\camera\' + (Get-Date -Format 'yyyyMMdd-HHmmss-fff')) }
if (Test-Path -LiteralPath $RunOutput) { throw 'Camera test requires a fresh output directory.' }
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $RunOutput) | Out-Null
& (Join-Path $PSScriptRoot 'Launch-Usdview.ps1') -OpenUsdRoot $OpenUsdRoot -TestOutput $RunOutput -UsdviewArguments @('--defaultsettings', '--camera', '/Camera', '--hdue-camera-benchmark') *> ($RunOutput + '.log')
$Result = Get-Content -Raw -LiteralPath (Join-Path $RunOutput 'verified.json') | ConvertFrom-Json
if (-not $Result.camera_benchmark -or $Result.runtime_state -ne 4) { throw 'Incomplete camera benchmark.' }
$Result.camera_benchmark | ConvertTo-Json
Write-Output "Camera benchmark: $RunOutput"
