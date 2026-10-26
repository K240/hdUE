param(
    [ValidateSet('Release')][string]$Configuration = 'Release',
    [string]$OpenUsdRoot = ''
)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'Local-Paths.ps1')
$OpenUsdRoot=Get-HdUELocalPath -Key openusd_root -Value $OpenUsdRoot
$TaskRoot = Split-Path -Parent $PSScriptRoot
& cmake -S $TaskRoot -B (Join-Path $TaskRoot 'build') -G 'Visual Studio 17 2022' -A x64 "-DOPENUSD_ROOT=$OpenUsdRoot" "-Dpxr_DIR=$OpenUsdRoot"
if ($LASTEXITCODE) { throw 'CMake configuration failed.' }
& cmake --build (Join-Path $TaskRoot 'build') --config $Configuration --parallel
if ($LASTEXITCODE) { throw 'Host build failed.' }
& ctest --test-dir (Join-Path $TaskRoot 'build') -C $Configuration --output-on-failure
if ($LASTEXITCODE) { throw 'Native contract tests failed.' }

