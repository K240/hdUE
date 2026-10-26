param(
    [string]$InputUsd = '',
    [string]$OpenUsdRoot = '',
    [ValidateSet('realtime', 'pathtraced')][string]$Mode = 'realtime',
    [ValidateSet('auto', 'off')][string]$ViewerLight = 'auto',
    [string]$CameraPath = '',
    [string]$TestOutput = '',
    [string]$MaterialXLibrary = '',
    [string]$GlassProfile = '',
    [string]$RuntimeConfig = '',
    [Parameter(ValueFromRemainingArguments = $true)][string[]]$UsdviewArguments = @()
)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'Local-Paths.ps1')
$OpenUsdRoot=Get-HdUELocalPath -Key openusd_root -Value $OpenUsdRoot
$TaskRoot = Split-Path -Parent $PSScriptRoot
if (-not $InputUsd) { $InputUsd = Join-Path $TaskRoot 'samples\smoke.usda' }
$InputUsd = [IO.Path]::GetFullPath($InputUsd)
if (-not (Test-Path -LiteralPath $InputUsd -PathType Leaf)) { throw "USD input not found: $InputUsd" }
$Names = @('PATH', 'PYTHONPATH', 'PXR_PLUGINPATH_NAME', 'HDUE_OPENUSD_ROOT', 'HDUE_BACKEND_DLL')
$Previous = @{}
foreach ($Name in $Names) { $Previous[$Name] = [Environment]::GetEnvironmentVariable($Name, 'Process') }
try {
    $env:PATH = "$OpenUsdRoot\bin;$OpenUsdRoot\lib;$env:PATH"
    $env:PYTHONPATH = "$OpenUsdRoot\lib\python"
    $env:PXR_PLUGINPATH_NAME = "$OpenUsdRoot\plugin\usd;$TaskRoot\build\resources"
    $env:HDUE_OPENUSD_ROOT = $OpenUsdRoot
    $Arguments = @('--hdue-mode', $Mode, '--hdue-viewer-light', $ViewerLight)
    if ($CameraPath) { $Arguments += @('--camera', $CameraPath) }
    if ($TestOutput) { $Arguments += @('--hdue-test-output', [IO.Path]::GetFullPath($TestOutput)) }
    if ($MaterialXLibrary) { $Arguments += @('--hdue-materialx-library', [IO.Path]::GetFullPath($MaterialXLibrary)) }
    if ($GlassProfile) { $Arguments += @('--hdue-glass-profile', [IO.Path]::GetFullPath($GlassProfile)) }
    if ($RuntimeConfig) { $Arguments += @('--hdue-runtime-config', [IO.Path]::GetFullPath($RuntimeConfig)) }
    # Use stock usdview modules via the SDK, never its @python .cmd wrappers.
    & uv run --python 3.11 --script (Join-Path $PSScriptRoot 'usdview_hdue.py') $InputUsd --renderer 'Unreal Engine (hdUE)' @Arguments @UsdviewArguments
    if ($LASTEXITCODE) { throw 'usdview / hdUE failed.' }
    if ($TestOutput) {
        $ResultPath = Join-Path $TestOutput 'verified.json'
        if (-not (Test-Path -LiteralPath $ResultPath)) { throw 'usdview exited before producing fresh verification evidence.' }
        $Result = Get-Content -Raw -LiteralPath $ResultPath | ConvertFrom-Json
        if (-not $Result.passed -or -not $Result.engine_shutdown) { throw 'Incomplete usdview verification.' }
    }
} finally {
    Restore-HdUEEnvironment -Snapshot $Previous
}
