param([string]$InputUsd='',[string]$OutputDirectory='')
$ErrorActionPreference='Stop'
. (Join-Path $PSScriptRoot 'Local-Paths.ps1')
$InputUsd=Get-HdUELocalPath -Key review_scene -Value $InputUsd
$TaskRoot=Split-Path -Parent $PSScriptRoot
$Run=if ($OutputDirectory) {[IO.Path]::GetFullPath($OutputDirectory)} else {Join-Path $TaskRoot ('out\opacity-check\'+(Get-Date -Format 'yyyyMMdd-HHmmss-fff'))}
if (Test-Path -LiteralPath $Run) {throw 'Choose a fresh opacity evidence directory.'}
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $Run) | Out-Null
& (Join-Path $PSScriptRoot 'Launch-Usdview.ps1') -InputUsd $InputUsd -TestOutput $Run -UsdviewArguments @('--defaultsettings','--hdue-opacity-check') *> ($Run+'.log')
$Result=Get-Content -Raw -LiteralPath (Join-Path $Run 'verified.json') | ConvertFrom-Json
if (-not $Result.passed -or -not $Result.opacity_verified -or $Result.targets.Count -ne 13 -or $Result.comparisons.Count -ne 26 -or $Result.frames.Count -ne 84 -or -not $Result.usd_unchanged -or $Result.ue_initializations -ne 1 -or $Result.runtime_state -ne 4) {throw "Incomplete opacity verification: $Run"}
$Log=[IO.File]::ReadAllText($Run+'.log').Replace([string][char]0,'')
if ($Log -match '(?m)^hdUE:|Traceback|Fatal error|Coding Error:|Runtime Error:') {throw "Opacity verification reported errors: $Run.log"}
& uv run --python 3.11 (Join-Path $PSScriptRoot 'create_opacity_gallery.py') $Run
if ($LASTEXITCODE) {throw 'Opacity gallery generation failed.'}
Write-Output "Verified Review scene displayOpacity: $Run"
