param(
    [string]$HoudiniRoot='',
    [string]$RuntimeConfig='',
    [string]$OutputDirectory=''
)
$ErrorActionPreference='Stop'
. (Join-Path $PSScriptRoot 'Local-Paths.ps1')
$HoudiniRoot=Get-HdUELocalPath -Key houdini_root -Value $HoudiniRoot
$TaskRoot=Split-Path -Parent $PSScriptRoot
$RunOutput=if ($OutputDirectory) {[IO.Path]::GetFullPath($OutputDirectory)} else {Join-Path $TaskRoot ('out\houdini-22.0.466\op-textures-'+(Get-Date -Format 'yyyyMMdd-HHmmss-fff'))}
if (Test-Path -LiteralPath $RunOutput) {throw 'A new directory is required for Houdini verification.'}
& uv run -- (Join-Path $HoudiniRoot 'bin\hython.exe') (Join-Path $TaskRoot 'tests\houdini_op_textures.py')
if ($LASTEXITCODE) {throw 'COP texture cache / LOP tests failed.'}
& (Join-Path $PSScriptRoot 'Launch-Houdini.ps1') -HoudiniRoot $HoudiniRoot -RuntimeConfig $RuntimeConfig -LogDirectory $RunOutput -Test -TestKind optextures
$Result=Get-Content -LiteralPath (Join-Path $RunOutput 'verified.json') -Raw | ConvertFrom-Json
if (-not $Result.passed -or $Result.frames.Count -ne 7 -or $Result.captures.Count -ne 5 -or -not $Result.runtime.closed -or $Result.runtime.runtime_state -ne 4 -or $Result.runtime.ue_initializations -ne 1) {
    throw "Incomplete live op: texture evidence: $RunOutput"
}
Write-Output "Verified live COP textures / Solaris / same-process UE: $RunOutput"
