param([string]$EngineRoot = '', [string]$OutputDirectory = '')
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'Local-Paths.ps1')
$EngineRoot=Get-HdUELocalPath -Key engine_root -Value $EngineRoot
$TaskRoot = Split-Path -Parent $PSScriptRoot
$Project = Join-Path $TaskRoot 'unreal\HdUEBackend\HdUEBackend.uproject'
$Stage = if ($OutputDirectory) {[IO.Path]::GetFullPath($OutputDirectory)} else {Join-Path $TaskRoot 'out\runtime'}
& (Join-Path $EngineRoot 'Engine\Build\BatchFiles\RunUAT.bat') BuildCookRun "-project=$Project" -noP4 -platform=Win64 -clientconfig=Development -target=HdUEBackend -skipbuild -skipcook -stage -skippackage "-stagingdirectory=$Stage" -unattended -utf8output
if ($LASTEXITCODE) { throw 'Runtime staging failed.' }
$Runtime = Join-Path $Stage 'Windows'
$Game = Join-Path $Runtime 'HdUEBackend'
$Bin = Join-Path $Game 'Binaries\Win64'
New-Item -ItemType Directory -Force -Path $Bin | Out-Null
Copy-Item -LiteralPath (Join-Path $TaskRoot 'build\Release\hdue_backend_smoke.exe') -Destination $Bin
Copy-Item -LiteralPath $Project -Destination (Join-Path $Game 'HdUEBackend.uproject')
Copy-Item -LiteralPath (Join-Path $TaskRoot 'unreal\HdUEBackend\Shaders') -Destination $Game -Recurse -Force
Write-Output "Runtime ready: $Runtime"

