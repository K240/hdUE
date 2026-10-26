param([string]$EngineRoot = '')
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'Local-Paths.ps1')
$EngineRoot=Get-HdUELocalPath -Key engine_root -Value $EngineRoot
$Tag = '5.8.4-release'
$Commit = 'f45729c0b6f097038e72b52d366a62baeca3275b'
if (-not (Test-Path -LiteralPath $EngineRoot)) {
    & git clone --depth 1 --branch $Tag https://github.com/EpicGames/UnrealEngine.git $EngineRoot
    if ($LASTEXITCODE) { throw 'Cannot obtain Epic source. Check the linked GitHub/Epic account.' }
}
$Actual = & git -C $EngineRoot rev-parse HEAD
if ($LASTEXITCODE -or $Actual.Trim() -ne $Commit) {
    throw "Expected the pinned $Tag source commit $Commit; existing checkout was not changed."
}
$GitDeps = Join-Path $EngineRoot 'Engine\Binaries\DotNET\GitDependencies\win-x64\GitDependencies.exe'
# Invoke GitDependencies directly: Setup.bat also installs/registers system tools.
# IOS also contains resources embedded by the Windows AutomationTool build.
& $GitDeps "--root=$EngineRoot" --threads=16 --force --exclude=Mac --exclude=Linux --exclude=LinuxArm64 --exclude=Android --exclude=TVOS --exclude=Win32 --exclude=HoloLens --exclude=Documentation --exclude=Samples --exclude=Templates
if ($LASTEXITCODE) { throw 'Engine dependency synchronization failed.' }

