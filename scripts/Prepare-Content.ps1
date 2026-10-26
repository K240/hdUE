param([string]$EditorRoot = '')
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'Local-Paths.ps1')
$EditorRoot=Get-HdUELocalPath -Key editor_root -Value $EditorRoot
$TaskRoot = Split-Path -Parent $PSScriptRoot
$Project = Join-Path $TaskRoot 'unreal\HdUEBackend\HdUEBackend.uproject'
$Version = Get-Content -Raw -LiteralPath (Join-Path $EditorRoot 'Engine\Build\Build.version') | ConvertFrom-Json
if ($Version.MajorVersion -ne 5 -or $Version.MinorVersion -ne 8 -or $Version.PatchVersion -ne 4) {
    throw 'Content preparation requires UE 5.8.4.'
}
& (Join-Path $EditorRoot 'Engine\Build\BatchFiles\Build.bat') HdUEBackendEditor Win64 Development "-Project=$Project" -gather -WaitMutex -NoHotReload
if ($LASTEXITCODE) { throw 'Content commandlet build failed.' }
$Editor = Join-Path $EditorRoot 'Engine\Binaries\Win64\UnrealEditor-Cmd.exe'
& $Editor $Project -run=HdUEPrepare -unattended -nop4 -nosplash -nullrhi -stdout -UTF8Output
if ($LASTEXITCODE) { throw 'Test content preparation failed.' }
& $Editor $Project -run=Cook -TargetPlatform=Windows -Map=/Game/HdUETest -unattended -nop4 -nosplash -stdout -UTF8Output
if ($LASTEXITCODE) { throw 'Test content cook failed.' }

