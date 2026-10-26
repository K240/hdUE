$ErrorActionPreference='Stop'
. (Join-Path (Split-Path -Parent $PSScriptRoot) 'scripts\Local-Paths.ps1')
$Root=[IO.Path]::GetFullPath((Join-Path (Split-Path -Parent $PSScriptRoot) '.local'))
$Fixture=Join-Path $Root ('path-test-'+[guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Force -Path $Fixture | Out-Null
$PreviousConfig=$env:HDUE_LOCAL_CONFIG
$PreviousEngine=$env:HDUE_ENGINE_ROOT
$Probe='HDUE_LOCAL_PATH_TEST_VALUE'
$PreviousProbe=[Environment]::GetEnvironmentVariable($Probe,'Process')
try {
    $Config=Join-Path $Fixture 'paths.json'
    @{engine_root='engine'} | ConvertTo-Json | Set-Content -LiteralPath $Config -Encoding utf8
    $env:HDUE_LOCAL_CONFIG=$Config
    $env:HDUE_ENGINE_ROOT=''
    if ((Get-HdUELocalPath -Key engine_root) -ne (Join-Path $Fixture 'engine')) {throw 'Relative local config resolution failed'}
    $env:HDUE_ENGINE_ROOT=Join-Path $Fixture 'environment'
    if ((Get-HdUELocalPath -Key engine_root) -ne $env:HDUE_ENGINE_ROOT) {throw 'Environment precedence failed'}
    $Explicit=Join-Path $Fixture 'explicit'
    if ((Get-HdUELocalPath -Key engine_root -Value $Explicit) -ne $Explicit) {throw 'Explicit parameter precedence failed'}
    $env:HDUE_ENGINE_ROOT=''
    $env:HDUE_LOCAL_CONFIG=Join-Path $Fixture 'missing.json'
    if ((Get-HdUELocalPath -Key engine_root -Optional) -ne '') {throw 'Optional missing value failed'}
    $Rejected=$false
    try { Get-HdUELocalPath -Key engine_root | Out-Null } catch { $Rejected=$_.Exception.Message -like 'Configure engine_root*' }
    if (-not $Rejected) {throw 'Missing required configuration was accepted'}
    [Environment]::SetEnvironmentVariable($Probe,'temporary','Process')
    Restore-HdUEEnvironment -Snapshot @{$Probe=$null}
    if ($null -ne [Environment]::GetEnvironmentVariable($Probe,'Process')) {throw 'Unset environment value was not restored'}
    Restore-HdUEEnvironment -Snapshot @{$Probe=''}
    if ([Environment]::GetEnvironmentVariable($Probe,'Process') -ne '') {throw 'Empty environment value was not preserved'}
    Restore-HdUEEnvironment -Snapshot @{$Probe='original'}
    if ([Environment]::GetEnvironmentVariable($Probe,'Process') -ne 'original') {throw 'Environment value was not restored'}
    Write-Output 'PASS local paths, precedence, missing values and exact environment restoration'
} finally {
    $env:HDUE_LOCAL_CONFIG=$PreviousConfig
    $env:HDUE_ENGINE_ROOT=$PreviousEngine
    Restore-HdUEEnvironment -Snapshot @{$Probe=$PreviousProbe}
    $Resolved=[IO.Path]::GetFullPath($Fixture)
    if (-not $Resolved.StartsWith($Root.TrimEnd('\')+'\',[StringComparison]::OrdinalIgnoreCase)) {throw 'Fixture cleanup escaped local directory'}
    Remove-Item -LiteralPath $Resolved -Recurse -Force
}
