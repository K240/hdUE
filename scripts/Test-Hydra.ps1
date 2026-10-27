param(
    [string]$OpenUsdRoot = '',
    [string]$RuntimeConfig = '',
    [string]$InputUsd = '',
    [string]$Mode = 'both',
    [int]$Cycles = 2,
    [switch]$SelfTest,
    [switch]$LightingTest,
    [string]$OutputDirectory = '',
    [string]$CameraPath = '/Camera',
    [double]$Time = 0,
    [uint32]$Width = 640,
    [uint32]$Height = 360,
    [string[]]$NativeMesh = @(),
    [string[]]$NativeMaterial = @()
)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'Local-Paths.ps1')
$OpenUsdRoot=Get-HdUELocalPath -Key openusd_root -Value $OpenUsdRoot
$TaskRoot = Split-Path -Parent $PSScriptRoot
$Game = Join-Path $TaskRoot 'out\runtime\Windows\HdUEBackend'
$BackendDll=Join-Path $Game 'Binaries\Win64\HdUEBackend.dll'
$RuntimeProject=Join-Path $Game 'HdUEBackend.uproject'
if ($RuntimeConfig) {
    $ConfigPath=[IO.Path]::GetFullPath($RuntimeConfig)
    $Profile=Get-Content -LiteralPath $ConfigPath -Raw | ConvertFrom-Json
    if ($Profile.version -ne 1) {throw 'Unsupported runtime configuration.'}
    foreach ($Key in @('backend_dll','runtime_project')) {
        if (-not [IO.Path]::IsPathRooted($Profile.$Key)) {$Profile.$Key=[IO.Path]::GetFullPath((Join-Path (Split-Path -Parent $ConfigPath) $Profile.$Key))}
    }
    $BackendDll=$Profile.backend_dll; $RuntimeProject=$Profile.runtime_project
}
if (-not $InputUsd) { $InputUsd = Join-Path $TaskRoot 'samples\smoke.usda' }
$RunOutput = if ($OutputDirectory) { [IO.Path]::GetFullPath($OutputDirectory) } else { Join-Path $TaskRoot ('out\hydra\' + (Get-Date -Format 'yyyyMMdd-HHmmss-fff')) }
if (Test-Path -LiteralPath $RunOutput) { throw 'Test output directory already exists; fresh evidence is required.' }
$PythonExe = & uv python find 3.11
if ($LASTEXITCODE) { throw 'The OpenUSD SDK requires its uv-managed Python 3.11 runtime.' }
$PreviousPath = $env:PATH
$PreviousPlugins = $env:PXR_PLUGINPATH_NAME
try {
    $env:PATH = "$OpenUsdRoot\lib;$OpenUsdRoot\bin;$(Split-Path -Parent $PythonExe);$PreviousPath"
    $env:PXR_PLUGINPATH_NAME = "$OpenUsdRoot\plugin\usd;$TaskRoot\build\resources"
    $Arguments = @('--backend', $BackendDll,
        '--project', $RuntimeProject, '--input', $InputUsd,
        '--plugins', (Join-Path $TaskRoot 'build\resources'), '--output', $RunOutput, '--mode', $Mode, '--cycles', $Cycles,
        '--camera', $CameraPath, '--time', $Time.ToString([Globalization.CultureInfo]::InvariantCulture), '--width', $Width, '--height', $Height)
    foreach ($Mapping in $NativeMesh) { $Arguments += @('--native-mesh', $Mapping) }
    foreach ($Mapping in $NativeMaterial) { $Arguments += @('--native-material', $Mapping) }
    if ($SelfTest) { $Arguments += '--self-test' }
    if ($LightingTest) { $Arguments += '--lighting-test' }
    & (Join-Path $TaskRoot 'build\Release\hdue_usd_render.exe') @Arguments
    if ($LASTEXITCODE) { throw 'Hydra/UE rendering failed.' }
    $MetricsPath = Join-Path $RunOutput 'metrics.json'
    if (-not (Test-Path -LiteralPath $MetricsPath)) { throw 'UE exited before completing the Hydra test.' }
    $Metrics = Get-Content -Raw -LiteralPath $MetricsPath | ConvertFrom-Json
    $PerCycle = if ($Mode -eq 'both') { 2 } else { 1 }
    if ($SelfTest) { $PerCycle += 15 }
    if ($LightingTest) { $PerCycle += 8 }
    if ($Metrics.passed -ne $true -or $Metrics.frames.Count -ne ($PerCycle * $Cycles)) { throw 'Incomplete Hydra rendering evidence.' }
    Write-Output "Verified Hydra/UE output: $RunOutput"
} finally {
    $env:PATH = $PreviousPath
    $env:PXR_PLUGINPATH_NAME = $PreviousPlugins
}
