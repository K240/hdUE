param(
    [Parameter(Mandatory=$true)][string]$ProjectPath,
    [Parameter(Mandatory=$true)][string]$OutputDirectory,
    [Parameter(Mandatory=$true)][string]$Map,
    [string]$InspectAsset='',
    [string]$EditorRoot='',
    [string]$EngineRoot='',
    [switch]$SkipCopy,
    [switch]$SkipBuild
)
$ErrorActionPreference='Stop'
. (Join-Path $PSScriptRoot 'Local-Paths.ps1')
$EngineRoot=Get-HdUELocalPath -Key engine_root -Value $EngineRoot
$EditorRoot=Get-HdUELocalPath -Key editor_root -Value $EditorRoot
$TaskRoot=Split-Path -Parent $PSScriptRoot
$Run=[IO.Path]::GetFullPath($OutputDirectory)
New-Item -ItemType Directory -Force -Path $Run | Out-Null
if (-not $SkipCopy) {
    & "$PSScriptRoot\Copy-ProjectContent.ps1" -ProjectPath $ProjectPath -OutputDirectory (Join-Path $Run 'content-copy')
}
$Project=Join-Path $TaskRoot 'unreal\HdUEBackend\HdUEBackend.uproject'
if (-not $SkipBuild) {
    & (Join-Path $EditorRoot 'Engine\Build\BatchFiles\Build.bat') HdUEBackendEditor Win64 Development "-Project=$Project" -gather -WaitMutex -NoHotReload
    if ($LASTEXITCODE) {throw 'Editor module build failed.'}
}
$Editor=Join-Path $EditorRoot 'Engine\Binaries\Win64\UnrealEditor-Cmd.exe'
& $Editor $Project -run=HdUEInspectAssets "-InspectMap=$Map" "-InspectAsset=$InspectAsset" "-Output=$Run\catalog.json" -unattended -nop4 -nosplash -nullrhi -stdout -UTF8Output
if ($LASTEXITCODE) {throw 'Project inspection failed; resolve required plugin dependencies before cooking.'}
& $Editor $Project -run=Cook -TargetPlatform=Windows -CookAll -unattended -nop4 -nosplash -stdout -UTF8Output
if ($LASTEXITCODE) {throw 'Project asset cook failed.'}
if (-not $SkipBuild) { & "$PSScriptRoot\Build-Backend.ps1" -EngineRoot $EngineRoot -MaxParallelActions 8 }
$Staging=Join-Path $Run 'runtime'
& "$PSScriptRoot\Stage-Runtime.ps1" -EngineRoot $EngineRoot -OutputDirectory $Staging
$SourceProject=if (Test-Path -LiteralPath $ProjectPath -PathType Container) {(Get-ChildItem -LiteralPath $ProjectPath -Filter *.uproject | Select-Object -First 1).FullName} else {(Resolve-Path -LiteralPath $ProjectPath).Path}
@{
    version=1;source_project=$SourceProject;
    runtime_project=(Join-Path $Staging 'Windows\HdUEBackend\HdUEBackend.uproject');
    backend_dll=(Join-Path $Staging 'Windows\HdUEBackend\Binaries\Win64\HdUEBackend.dll');
    asset_catalog=(Join-Path $Run 'catalog.json')
} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $Run 'runtime.json') -Encoding utf8
Write-Output "Prepared project runtime: -RuntimeConfig $Run\runtime.json"
