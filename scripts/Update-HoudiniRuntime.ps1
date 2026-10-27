param(
    [string]$RuntimeConfig='',
    [string]$OutputConfig='',
    [switch]$SetDefault
)
$ErrorActionPreference='Stop'
. (Join-Path $PSScriptRoot 'Local-Paths.ps1')
$TaskRoot=Split-Path -Parent $PSScriptRoot
$RuntimeConfig=Get-HdUELocalPath -Key runtime_config -Value $RuntimeConfig
$ConfigPath=[IO.Path]::GetFullPath($RuntimeConfig)
$Config=Get-Content -LiteralPath $ConfigPath -Raw | ConvertFrom-Json
if ($Config.version -ne 1) {throw 'Unsupported runtime configuration.'}
foreach ($Key in @('backend_dll','runtime_project','asset_catalog','materialx_library')) {
    if ($Config.$Key -and -not [IO.Path]::IsPathRooted($Config.$Key)) {$Config.$Key=[IO.Path]::GetFullPath((Join-Path (Split-Path -Parent $ConfigPath) $Config.$Key))}
}
$Source=Join-Path $TaskRoot 'unreal\HdUEBackend\Binaries\Win64\HdUEBackend.dll'
if (-not (Test-Path -LiteralPath $Source)) {throw 'Build-Backend.ps1 must finish first.'}
$SourceShader=Join-Path $TaskRoot 'unreal\HdUEBackend\Shaders\Environment.usf'
$RuntimeShader=Join-Path (Split-Path -Parent $Config.runtime_project) 'Shaders\Environment.usf'
if (-not (Test-Path -LiteralPath $RuntimeShader) -or
    (Get-FileHash -LiteralPath $SourceShader).Hash -ne (Get-FileHash -LiteralPath $RuntimeShader).Hash) {
    throw 'This runtime needs the updated global shaders. Prepare content, build the backend, and stage a fresh runtime before updating its DLL. Preserve any caller-supplied asset catalog/material library in the new runtime configuration.'
}
$Hash=(Get-FileHash -LiteralPath $Source -Algorithm SHA256).Hash.Substring(0,16).ToLowerInvariant()
$Destination=Join-Path (Split-Path -Parent $Config.backend_dll) ("HdUEBackend-h22-$Hash.dll")
if (-not (Test-Path -LiteralPath $Destination)) {Copy-Item -LiteralPath $Source -Destination $Destination}
if ((Get-FileHash -LiteralPath $Source).Hash -ne (Get-FileHash -LiteralPath $Destination).Hash) {throw 'Runtime DLL copy verification failed.'}
$Config.backend_dll=$Destination
if (-not $OutputConfig) {$OutputConfig=Join-Path (Split-Path -Parent $ConfigPath) 'runtime-houdini.json'}
$Config | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath ([IO.Path]::GetFullPath($OutputConfig)) -Encoding utf8
Write-Output "Houdini runtime configuration: $([IO.Path]::GetFullPath($OutputConfig))"
if ($SetDefault) {
    $DefaultConfig=Join-Path $TaskRoot 'out\project-assets\active-houdini.json'
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $DefaultConfig) | Out-Null
    $Config | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath $DefaultConfig -Encoding utf8
    Write-Output "Default Houdini runtime: $DefaultConfig"
}
