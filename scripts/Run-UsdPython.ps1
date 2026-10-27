param(
    [Parameter(Mandatory=$true)][string]$Script,
    [string[]]$ScriptArguments=@(),
    [string]$OpenUsdRoot=''
)
$ErrorActionPreference='Stop'
. (Join-Path $PSScriptRoot 'Local-Paths.ps1')
$OpenUsdRoot=Get-HdUELocalPath openusd_root -Value $OpenUsdRoot
$Previous=@{}
foreach ($Name in @('PATH','PYTHONPATH','PXR_PLUGINPATH_NAME')) {$Previous[$Name]=[Environment]::GetEnvironmentVariable($Name,'Process')}
try {
    $env:PATH="$OpenUsdRoot\bin;$OpenUsdRoot\lib;$env:PATH"
    $env:PYTHONPATH="$OpenUsdRoot\lib\python"
    $env:PXR_PLUGINPATH_NAME="$OpenUsdRoot\plugin\usd"
    & uv run --python 3.11 $Script @ScriptArguments
    if ($LASTEXITCODE) {throw 'Standalone USD Python operation failed.'}
} finally {Restore-HdUEEnvironment -Snapshot $Previous}
