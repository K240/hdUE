param(
    [string]$InputUsd = '',
    [string]$Library = '',
    [string]$OutputDirectory = '',
    [string]$OpenUsdRoot = ''
)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'Local-Paths.ps1')
$OpenUsdRoot=Get-HdUELocalPath -Key openusd_root -Value $OpenUsdRoot
$TaskRoot = Split-Path -Parent $PSScriptRoot
if (-not $InputUsd) { $InputUsd = Join-Path $TaskRoot 'samples\materialx\showcase.usda' }
if (-not $Library) { $Library = Join-Path $TaskRoot 'out\materialx\library.json' }
$RunOutput = if ($OutputDirectory) { [IO.Path]::GetFullPath($OutputDirectory) } else { Join-Path $TaskRoot ('out\materialx-check\' + (Get-Date -Format 'yyyyMMdd-HHmmss-fff')) }
if (Test-Path -LiteralPath $RunOutput) { throw 'Fresh MaterialX evidence is required.' }
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $RunOutput) | Out-Null
& (Join-Path $PSScriptRoot 'Launch-Usdview.ps1') -InputUsd $InputUsd -OpenUsdRoot $OpenUsdRoot -MaterialXLibrary $Library -CameraPath '/Camera' -TestOutput $RunOutput -UsdviewArguments @('--defaultsettings','--hdue-materialx-check') *> ($RunOutput + '.log')
$Result = Get-Content -Raw -LiteralPath (Join-Path $RunOutput 'verified.json') | ConvertFrom-Json
$ExpectedFrames = if ($Result.inline_materialx_verified) {54} else {50}
if (-not $Result.passed -or $Result.frames.Count -ne $ExpectedFrames -or $Result.materialx_bindings.Count -ne 10 -or $Result.effect_comparisons.Count -ne 20 -or -not $Result.pathtracer_reset -or -not $Result.source_unchanged -or -not $Result.atomic_parameter_updates -or $Result.runtime_state -ne 4 -or $Result.ue_initializations -ne 1) {
    throw "Incomplete MaterialX verification: $RunOutput"
}
$Log = [IO.File]::ReadAllText($RunOutput + '.log').Replace([string][char]0,'')
if ($Log -match '(?m)^hdUE:|Traceback|Runtime Error:|Coding Error:|Fatal error') {
    throw "MaterialX rendering or teardown reported an error: $RunOutput.log"
}
Write-Output "Verified MaterialX/Substrate: $RunOutput"
