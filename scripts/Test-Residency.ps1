param([Parameter(Mandatory=$true)][string]$InputUsd,[Parameter(Mandatory=$true)][string]$Library,[string]$OutputDirectory='')
$ErrorActionPreference='Stop'
$TaskRoot=Split-Path -Parent $PSScriptRoot
$Run=if ($OutputDirectory) {[IO.Path]::GetFullPath($OutputDirectory)} else {Join-Path $TaskRoot ('out\residency-check\'+(Get-Date -Format 'yyyyMMdd-HHmmss-fff'))}
if (Test-Path -LiteralPath $Run) {throw 'Choose fresh residency evidence.'}
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $Run) | Out-Null
& (Join-Path $PSScriptRoot 'Launch-Usdview.ps1') -InputUsd $InputUsd -MaterialXLibrary $Library -CameraPath /Camera -TestOutput $Run -UsdviewArguments @('--defaultsettings','--hdue-residency-check') *> ($Run+'.log')
$Result=Get-Content -Raw (Join-Path $Run 'verified.json') | ConvertFrom-Json
if (-not $Result.passed -or -not $Result.residency_verified -or $Result.frames.Count -ne 6 -or $Result.failures.Count -ne 3 -or $Result.runtime_state -ne 4 -or $Result.ue_initializations -ne 1) {throw "Incomplete residency verification: $Run"}
Write-Output "Verified large UDIM residency: $Run"
