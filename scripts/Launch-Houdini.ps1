param(
    [string]$HoudiniRoot='',
    [string]$RuntimeConfig='',
    [string]$InputUsd='',
    [string]$LogDirectory='',
    [ValidateSet('integration','viewport','optextures')][string]$TestKind='integration',
    [switch]$Test
)
$ErrorActionPreference='Stop'
. (Join-Path $PSScriptRoot 'Local-Paths.ps1')
$HoudiniRoot=Get-HdUELocalPath -Key houdini_root -Value $HoudiniRoot
$TaskRoot=Split-Path -Parent $PSScriptRoot
$HoudiniExe=Join-Path $HoudiniRoot 'bin\houdini.exe'
$Version=(Get-Item -LiteralPath $HoudiniExe).VersionInfo
if (@($Version.FileMajorPart,$Version.FileMinorPart,$Version.FileBuildPart,$Version.FilePrivatePart) -join '.' -ne '22.0.0.466') {throw 'This delegate requires the Houdini 22.0.466 ABI.'}
if (-not $RuntimeConfig) {
    $ActiveConfig=Join-Path $TaskRoot 'out\project-assets\active-houdini.json'
    $RuntimeConfig=Get-HdUELocalPath -Key runtime_config -Optional
    if (-not $RuntimeConfig -and (Test-Path -LiteralPath $ActiveConfig)) {$RuntimeConfig=$ActiveConfig}
    if (-not (Test-Path -LiteralPath $RuntimeConfig)) {throw 'Run Update-HoudiniRuntime.ps1 or specify an updated -RuntimeConfig with the depth extension.'}
}
if (-not (Test-Path -LiteralPath $RuntimeConfig)) {throw 'Specify a prepared -RuntimeConfig.'}
if (-not $LogDirectory) {$LogDirectory=Join-Path $TaskRoot ('out\houdini-22.0.466\session-'+(Get-Date -Format 'yyyyMMdd-HHmmss-fff'))}
New-Item -ItemType Directory -Force -Path $LogDirectory | Out-Null
$Dll=Join-Path $TaskRoot 'build\houdini-22.0.466\Release\hdUE_Houdini22.dll'
if (-not (Test-Path -LiteralPath $Dll)) {throw 'Run Build-Houdini.ps1 first.'}
$Names=@('HFS','PATH','PYTHONPATH','HOUDINI_PATH','PXR_PLUGINPATH_NAME','HOUDINI_USER_PREF_DIR','HOUDINI_NO_START_PAGE_SPLASH','HDUE_HOUDINI_ENABLE','HDUE_RUNTIME_CONFIG','HDUE_HOUDINI_INPUT','HDUE_HOUDINI_LOG_DIR','HDUE_HOUDINI_TEST')
$Previous=@{}; foreach ($Name in $Names) {$Previous[$Name]=[Environment]::GetEnvironmentVariable($Name,'Process')}
try {
    $env:HFS=$HoudiniRoot
    $env:PATH="$HoudiniRoot\bin;$HoudiniRoot\python313;$env:PATH"
    $env:PYTHONPATH='' # Houdini must use its own Python/USD, not standalone usdview.
    $env:HOUDINI_PATH="$TaskRoot\integrations\houdini;"+$(if ($Previous.HOUDINI_PATH) {$Previous.HOUDINI_PATH} else {'&'})
    $env:PXR_PLUGINPATH_NAME="$HoudiniRoot\bin\usd_plugins;$HoudiniRoot\houdini\dso\usd_plugins;$TaskRoot\build\houdini-22.0.466\resources"
    $env:HDUE_HOUDINI_ENABLE='1'
    $env:HDUE_RUNTIME_CONFIG=[IO.Path]::GetFullPath($RuntimeConfig)
    $env:HDUE_HOUDINI_INPUT=if ($InputUsd) {[IO.Path]::GetFullPath($InputUsd)} else {''}
    if ($LogDirectory) {$env:HDUE_HOUDINI_LOG_DIR=[IO.Path]::GetFullPath($LogDirectory)}
    $env:HDUE_HOUDINI_TEST=if ($Test) {$TestKind} else {''}
    if ($Test) {
        # Documented batch-startup UI flag. This does not answer consent
        # questions or change any telemetry/privacy preference.
        $env:HOUDINI_NO_START_PAGE_SPLASH='1'
        $env:HOUDINI_USER_PREF_DIR=Join-Path ([IO.Path]::GetFullPath($LogDirectory)) 'preferences'
        New-Item -ItemType Directory -Force -Path $env:HOUDINI_USER_PREF_DIR | Out-Null
        # Suppress only the first-run welcome window in this disposable test
        # profile. Do not write telemetry, privacy or user preference values.
        Set-Content -LiteralPath (Join-Path $env:HOUDINI_USER_PREF_DIR 'pluto_ui.pref') -Value 'pluto_ui.welcome_seen := 1;' -Encoding utf8
    }
    $Process=Start-Process -FilePath $HoudiniExe -ArgumentList '-foreground' -PassThru -RedirectStandardOutput (Join-Path $LogDirectory 'houdini.log') -RedirectStandardError (Join-Path $LogDirectory 'houdini-error.log')
    Write-Output "Houdini PID $($Process.Id), logs: $LogDirectory"
    if ($Test) {
        if (-not $Process.WaitForExit(420000)) {throw "Houdini verification timed out (PID $($Process.Id)); see $env:HDUE_HOUDINI_LOG_DIR."}
        if ($Process.ExitCode) {throw 'Houdini / hdUE exited with an error.'}
        if (Test-Path -LiteralPath (Join-Path $env:HDUE_HOUDINI_LOG_DIR 'test-error.txt')) {throw 'Solaris integration test failed; see test-error.txt.'}
    }
} finally {Restore-HdUEEnvironment -Snapshot $Previous}
