param(
    [string]$OpenUsdRoot = '',
    [string]$OutputDirectory = ''
)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'Local-Paths.ps1')
$OpenUsdRoot=Get-HdUELocalPath -Key openusd_root -Value $OpenUsdRoot
$TaskRoot = Split-Path -Parent $PSScriptRoot
$RunOutput = if ($OutputDirectory) { [IO.Path]::GetFullPath($OutputDirectory) } else { Join-Path $TaskRoot ('out\usdview\' + (Get-Date -Format 'yyyyMMdd-HHmmss-fff')) }
if (Test-Path -LiteralPath $RunOutput) { throw 'Test output directory already exists; fresh evidence is required.' }
$RunLog = $RunOutput + '.log'
$Parent = Split-Path -Parent $RunOutput
New-Item -ItemType Directory -Force -Path $Parent | Out-Null
& (Join-Path $PSScriptRoot 'Launch-Usdview.ps1') -OpenUsdRoot $OpenUsdRoot -TestOutput $RunOutput -UsdviewArguments @('--defaultsettings', '--camera', '/Camera') *> $RunLog
$Result = Get-Content -Raw -LiteralPath (Join-Path $RunOutput 'verified.json') | ConvertFrom-Json
if ($Result.frames.Count -ne 24 -or $Result.picks.Count -ne 10 -or $Result.ue_initializations -ne 1 -or $Result.runtime_state -ne 4 -or -not $Result.same_process) {
    throw "Incomplete usdview verification: $RunOutput"
}
Write-Output "Verified standard usdview / same-process UE: $RunOutput"
