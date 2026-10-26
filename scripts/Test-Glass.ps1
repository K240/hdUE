param([string]$InputUsd='',[Parameter(Mandatory=$true)][string]$Profile,[string]$OutputDirectory='')
$ErrorActionPreference='Stop'
. (Join-Path $PSScriptRoot 'Local-Paths.ps1')
$InputUsd=Get-HdUELocalPath -Key review_scene -Value $InputUsd
$TaskRoot=Split-Path -Parent $PSScriptRoot
$Run=if ($OutputDirectory) {[IO.Path]::GetFullPath($OutputDirectory)} else {Join-Path $TaskRoot ('out\glass-check\'+(Get-Date -Format 'yyyyMMdd-HHmmss-fff'))}
if (Test-Path -LiteralPath $Run) {throw 'Choose fresh glass evidence.'}
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $Run) | Out-Null
& (Join-Path $PSScriptRoot 'Launch-Usdview.ps1') -InputUsd $InputUsd -GlassProfile $Profile -TestOutput $Run -UsdviewArguments @('--defaultsettings','--hdue-glass-check') *> ($Run+'.log')
$Result=Get-Content -Raw (Join-Path $Run 'verified.json') | ConvertFrom-Json
if (-not $Result.passed -or -not $Result.glass_verified -or $Result.targets.Count -ne 5 -or $Result.frames.Count -ne 64 -or $Result.comparisons.Count -ne 10 -or -not $Result.invalid_profile_rejected -or -not $Result.usd_unchanged -or $Result.ue_initializations -ne 1 -or $Result.runtime_state -ne 4) {throw "Incomplete glass verification: $Run"}
$Log=[IO.File]::ReadAllText($Run+'.log').Replace([string][char]0,'')
if ($Log -match '(?m)^hdUE:|Traceback|Fatal error|Coding Error:|Runtime Error:') {throw "Glass verification reported errors: $Run.log"}
Copy-Item -LiteralPath $Profile -Destination (Join-Path $Run 'glass-profile.json')
& uv run --python 3.11 (Join-Path $PSScriptRoot 'create_glass_gallery.py') $Run
if ($LASTEXITCODE) {throw 'Glass gallery generation failed.'}
Write-Output "Verified Review scene glass: $Run"
