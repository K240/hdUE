param(
    [Parameter(Mandatory=$true)][string]$InputUsd,
    [string]$OutputDirectory='',
    [string]$Destination='/Game/HdUE/UsdMaterialX',
    [string]$OpenUsdRoot='',
    [string]$EditorRoot='',
    [string]$HoudiniRoot='',
    [Nullable[double]]$Time=$null,
    [switch]$SkipBuild,
    [switch]$SkipCook
)
$ErrorActionPreference='Stop'
. (Join-Path $PSScriptRoot 'Local-Paths.ps1')
$EditorRoot=Get-HdUELocalPath -Key editor_root -Value $EditorRoot
$OpenUsdRoot=Get-HdUELocalPath -Key openusd_root -Value $OpenUsdRoot
$TaskRoot=Split-Path -Parent $PSScriptRoot
$RunOutput=if ($OutputDirectory) {[IO.Path]::GetFullPath($OutputDirectory)} else {Join-Path $TaskRoot ('out\usd-materialx-import\'+(Get-Date -Format 'yyyyMMdd-HHmmss-fff'))}
if (Test-Path -LiteralPath $RunOutput) {throw 'Choose a fresh USD MaterialX output directory.'}
$InputUsd=(Resolve-Path -LiteralPath $InputUsd).Path
New-Item -ItemType Directory -Path $RunOutput -Force | Out-Null
$PreviousPath=$env:PATH
$PreviousPythonPath=$env:PYTHONPATH
try {
    $Export=Join-Path $RunOutput 'export'
    $Arguments=@($InputUsd,'--output',$Export)
    $HoudiniRoot=Get-HdUELocalPath -Key houdini_root -Value $HoudiniRoot -Optional
    if ($HoudiniRoot) {
        $env:PATH="$HoudiniRoot\bin;$PreviousPath"
        $env:PYTHONPATH=''
        $Embedded=Join-Path $RunOutput 'embedded'
        & uv run -- (Join-Path $HoudiniRoot 'bin\hython.exe') (Join-Path $PSScriptRoot 'resolve_houdini_textures.py') $InputUsd --output $Embedded
        if ($LASTEXITCODE) {throw 'Houdini embedded texture preparation failed.'}
        $Arguments+=@('--texture-manifest',(Join-Path $Embedded 'textures.json'))
    }
    $env:PATH="$OpenUsdRoot\bin;$OpenUsdRoot\lib;$PreviousPath"
    $env:PYTHONPATH="$OpenUsdRoot\lib\python"
    if ($null -ne $Time) {$Arguments+=@('--time',$Time.ToString([Globalization.CultureInfo]::InvariantCulture))}
    & uv run --python 3.11 --script (Join-Path $PSScriptRoot 'export_usd_materialx.py') @Arguments
    if ($LASTEXITCODE) {throw 'USD MaterialX graph export failed.'}
    $Manifest=Get-Content -Raw -LiteralPath (Join-Path $Export 'export.json') | ConvertFrom-Json
    $Imported=Join-Path $RunOutput 'ue-import'
    & (Join-Path $PSScriptRoot 'Import-MaterialX.ps1') -Source @($Manifest.source) -Destination $Destination -OutputDirectory $Imported -EditorRoot $EditorRoot -SkipBuild:$SkipBuild -SkipCook:$SkipCook
    & uv run --python 3.11 (Join-Path $PSScriptRoot 'bind_usd_materialx_library.py') (Join-Path $Export 'export.json') (Join-Path $Imported 'library.json') --output (Join-Path $RunOutput 'library.json')
    if ($LASTEXITCODE) {throw 'USD MaterialX registry binding failed.'}
} finally {
    $env:PATH=$PreviousPath
    $env:PYTHONPATH=$PreviousPythonPath
}
Write-Output "USD MaterialX library: $(Join-Path $RunOutput 'library.json')"
