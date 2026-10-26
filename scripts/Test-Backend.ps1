param(
    [string]$Mode = 'both',
    [int]$Cycles = 3,
    [switch]$Visible,
    [string]$OutputDirectory = ''
)
$ErrorActionPreference = 'Stop'
$TaskRoot = Split-Path -Parent $PSScriptRoot
$Game = Join-Path $TaskRoot 'out\runtime\Windows\HdUEBackend'
$Bin = Join-Path $Game 'Binaries\Win64'
$RunOutput = if ($OutputDirectory) { [IO.Path]::GetFullPath($OutputDirectory) } else { Join-Path $TaskRoot ('out\backend-smoke\' + (Get-Date -Format 'yyyyMMdd-HHmmss-fff')) }
if (Test-Path -LiteralPath $RunOutput) { throw 'Test output directory already exists; fresh evidence is required.' }
$Arguments = @('--backend', (Join-Path $Bin 'HdUEBackend.dll'), '--project', (Join-Path $Game 'HdUEBackend.uproject'),
    '--output', $RunOutput, '--mode', $Mode, '--cycles', $Cycles)
if ($Visible) { $Arguments += '--visible' }
& (Join-Path $Bin 'hdue_backend_smoke.exe') @Arguments
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

