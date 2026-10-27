param(
    [string]$Mode = 'both',
    [string]$RuntimeConfig = '',
    [int]$Cycles = 3,
    [switch]$Visible,
    [string]$OutputDirectory = ''
)
$ErrorActionPreference = 'Stop'
$TaskRoot = Split-Path -Parent $PSScriptRoot
$Game = Join-Path $TaskRoot 'out\runtime\Windows\HdUEBackend'
$Bin = Join-Path $Game 'Binaries\Win64'
$BackendDll=Join-Path $Bin 'HdUEBackend.dll'
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
$RunOutput = if ($OutputDirectory) { [IO.Path]::GetFullPath($OutputDirectory) } else { Join-Path $TaskRoot ('out\backend-smoke\' + (Get-Date -Format 'yyyyMMdd-HHmmss-fff')) }
if (Test-Path -LiteralPath $RunOutput) { throw 'Test output directory already exists; fresh evidence is required.' }
$Arguments = @('--backend', $BackendDll, '--project', $RuntimeProject,
    '--output', $RunOutput, '--mode', $Mode, '--cycles', $Cycles)
if ($Visible) { $Arguments += '--visible' }
& (Join-Path $TaskRoot 'build\Release\hdue_backend_smoke.exe') @Arguments
if ($LASTEXITCODE) { throw 'Real UE embedding test failed. Hydra integration is not yet validated.' }
# Some UE startup failures terminate the process with exit code zero. Require
# fresh evidence from this run, never the native process exit code alone.
$MetricsPath = Join-Path $RunOutput 'metrics.json'
if (-not (Test-Path -LiteralPath $MetricsPath)) { throw 'UE exited before producing test evidence.' }
$Metrics = Get-Content -Raw -LiteralPath $MetricsPath | ConvertFrom-Json
$PerCycle = if ($Mode -eq 'both') { 3 } elseif ($Mode -eq 'pathtraced') { 2 } else { 1 }
if ($Metrics.passed -ne $true -or -not $Metrics.native_picking -or $Metrics.frames.Count -ne ($PerCycle * $Cycles)) {
    throw 'Incomplete or failed real UE rendering evidence.'
}
Write-Output "Verified real UE output: $RunOutput"

