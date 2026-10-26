param(
    [string]$EngineRoot = '',
    [int]$MaxParallelActions = 24
)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'Local-Paths.ps1')
$EngineRoot=Get-HdUELocalPath -Key engine_root -Value $EngineRoot
$TaskRoot = Split-Path -Parent $PSScriptRoot
$Project = Join-Path $TaskRoot 'unreal\HdUEBackend\HdUEBackend.uproject'
$Version = Get-Content -Raw -LiteralPath (Join-Path $EngineRoot 'Engine\Build\Build.version') | ConvertFrom-Json
if ($Version.MajorVersion -ne 5 -or $Version.MinorVersion -ne 8 -or $Version.PatchVersion -ne 4) {
    throw 'The backend is pinned to Unreal Engine 5.8.4.'
}
if (Test-Path -LiteralPath (Join-Path $EngineRoot 'Engine\Build\InstalledBuild.txt')) {
    throw 'The monolithic DLL requires a source engine, not a Launcher installed build.'
}
& (Join-Path $PSScriptRoot 'Apply-ResidencyPatch.ps1') -EngineRoot $EngineRoot
& (Join-Path $EngineRoot 'Engine\Build\BatchFiles\Build.bat') HdUEBackend Win64 Development "-Project=$Project" -gather -WaitMutex -NoHotReload "-MaxParallelActions=$MaxParallelActions"
if ($LASTEXITCODE) { throw 'Unreal backend build failed.' }

