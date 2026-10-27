param([string]$HoudiniRoot='',[string]$BuildDirectory='')
$ErrorActionPreference='Stop'
. (Join-Path $PSScriptRoot 'Local-Paths.ps1')
$HoudiniRoot=Get-HdUELocalPath -Key houdini_root -Value $HoudiniRoot
$BuildDirectory=Get-HdUELocalPath -Key houdini_build_root -Value $BuildDirectory -Optional
$TaskRoot=Split-Path -Parent $PSScriptRoot
$Build=if ($BuildDirectory) {[IO.Path]::GetFullPath($BuildDirectory)} else {Join-Path $TaskRoot 'build\houdini-22.0.466'}
& cmake -S $TaskRoot -B $Build -G 'Visual Studio 17 2022' -A x64 "-DHDUE_HOUDINI_ROOT=$HoudiniRoot"
if ($LASTEXITCODE) {throw 'Houdini SDK configuration failed.'}
& cmake --build $Build --config Release --parallel 8
if ($LASTEXITCODE) {throw 'Houdini delegate build failed.'}
& ctest --test-dir $Build -C Release --output-on-failure
if ($LASTEXITCODE) {throw 'Houdini native contracts failed.'}
