param(
    [Parameter(Mandatory = $true)][string]$InputUsd,
    [string]$OpenUsdRoot = '',
    [string]$OutputDirectory = ''
)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'Local-Paths.ps1')
$OpenUsdRoot=Get-HdUELocalPath -Key openusd_root -Value $OpenUsdRoot
$TaskRoot = Split-Path -Parent $PSScriptRoot
$RunOutput = if ($OutputDirectory) { [IO.Path]::GetFullPath($OutputDirectory) } else { Join-Path $TaskRoot ('out\scene-check\' + (Get-Date -Format 'yyyyMMdd-HHmmss-fff')) }
if (Test-Path -LiteralPath $RunOutput) { throw 'Test output directory already exists; fresh evidence is required.' }
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $RunOutput) | Out-Null
& (Join-Path $PSScriptRoot 'Launch-Usdview.ps1') -InputUsd $InputUsd -OpenUsdRoot $OpenUsdRoot -TestOutput $RunOutput -UsdviewArguments @('--defaultsettings', '--hdue-scene-check') *> ($RunOutput + '.log')
$Result = Get-Content -Raw -LiteralPath (Join-Path $RunOutput 'verified.json') | ConvertFrom-Json
if (-not $Result.passed -or $Result.frames.Count -ne 18 -or -not $Result.usd_unchanged -or -not $Result.same_process -or $Result.runtime_state -ne 4 -or $Result.ue_initializations -ne 1) {
    throw "Incomplete scene verification: $RunOutput"
}
Write-Output "Verified viewer lighting: $RunOutput"
