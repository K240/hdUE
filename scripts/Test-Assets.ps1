param(
    [Parameter(Mandatory=$true)][string]$RuntimeConfig,
    [Parameter(Mandatory=$true)][string]$InputUsd,
    [string]$OutputDirectory=''
)
$ErrorActionPreference='Stop'
$TaskRoot=Split-Path -Parent $PSScriptRoot
$Run=if ($OutputDirectory) {[IO.Path]::GetFullPath($OutputDirectory)} else {Join-Path $TaskRoot ('out\asset-check\'+(Get-Date -Format 'yyyyMMdd-HHmmss-fff'))}
if (Test-Path -LiteralPath $Run) {throw 'Choose a fresh native asset evidence directory.'}
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $Run) | Out-Null
& "$PSScriptRoot\Launch-Usdview.ps1" -InputUsd $InputUsd -RuntimeConfig $RuntimeConfig -TestOutput $Run -CameraPath /Cameras/Level -UsdviewArguments @('--defaultsettings','--hdue-asset-check') *> ($Run+'.log')
$R=Get-Content -Raw -LiteralPath (Join-Path $Run 'verified.json') | ConvertFrom-Json
if (-not $R.passed -or -not $R.source_unchanged -or $R.frames.Count -lt 12 -or $R.instances.Count -lt 8 -or $R.ue_initializations -ne 1 -or $R.runtime_state -ne 4) {throw "Incomplete native asset check: $Run"}
& uv run --python 3.11 "$PSScriptRoot\create_asset_gallery.py" $Run
if ($LASTEXITCODE) {throw 'Cannot create native asset gallery.'}
Write-Output "Native asset review: $Run"
