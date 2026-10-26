param(
    [string]$HoudiniRoot='',
    [string]$RuntimeConfig='',
    [string]$OutputDirectory=''
)
$ErrorActionPreference='Stop'
. (Join-Path $PSScriptRoot 'Local-Paths.ps1')
$HoudiniRoot=Get-HdUELocalPath -Key houdini_root -Value $HoudiniRoot
$TaskRoot=Split-Path -Parent $PSScriptRoot
$RunOutput=if ($OutputDirectory) {[IO.Path]::GetFullPath($OutputDirectory)} else {Join-Path $TaskRoot ('out\houdini-22.0.466\'+(Get-Date -Format 'yyyyMMdd-HHmmss-fff'))}
if (Test-Path -LiteralPath $RunOutput) {throw 'A new directory is required for Houdini verification.'}
& uv run -- (Join-Path $HoudiniRoot 'bin\hython.exe') (Join-Path $TaskRoot 'tests\houdini_cook.py')
if ($LASTEXITCODE) {throw 'Solaris LOP cook test failed.'}
& uv run -- (Join-Path $HoudiniRoot 'bin\hython.exe') (Join-Path $TaskRoot 'tests\asset_controls_test.py')
if ($LASTEXITCODE) {throw 'Native asset proxy test failed.'}
& uv run -- (Join-Path $HoudiniRoot 'bin\hython.exe') (Join-Path $TaskRoot 'tests\scene_space_test.py')
if ($LASTEXITCODE) {throw 'USD / Solaris / UE space conversion test failed.'}
& (Join-Path $PSScriptRoot 'Launch-Houdini.ps1') -HoudiniRoot $HoudiniRoot -RuntimeConfig $RuntimeConfig -InputUsd (Join-Path $TaskRoot 'samples\smoke.usda') -LogDirectory $RunOutput -Test
$Result=Get-Content -LiteralPath (Join-Path $RunOutput 'verified.json') -Raw | ConvertFrom-Json
$Runtime=Get-Content -LiteralPath (Join-Path $RunOutput 'runtime-state.json') -Raw | ConvertFrom-Json
if (-not $Result.passed -or -not $Result.native_solaris_compositor_verified -or $Result.frames.Count -ne 10 -or $Result.captures.Count -ne 6 -or $Runtime.ue_initializations -ne 1 -or $Runtime.runtime_state -ne 4 -or -not $Runtime.closed) {
    throw "Incomplete Houdini / UE evidence: $RunOutput"
}
Write-Output "Verified Solaris / same-process UE: $RunOutput"
