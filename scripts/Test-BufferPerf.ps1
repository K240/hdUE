param(
    [string]$OpenUsdRoot = '',
    [string]$OutputFile = ''
)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'Local-Paths.ps1')
$OpenUsdRoot=Get-HdUELocalPath -Key openusd_root -Value $OpenUsdRoot
$TaskRoot = Split-Path -Parent $PSScriptRoot
$Destination = if ($OutputFile) { [IO.Path]::GetFullPath($OutputFile) } else { Join-Path $TaskRoot ('out\buffer\' + (Get-Date -Format 'yyyyMMdd-HHmmss-fff') + '.json') }
if (Test-Path -LiteralPath $Destination) { throw 'Buffer benchmark requires a fresh output file.' }
$PythonExe = & uv python find 3.11
if ($LASTEXITCODE) { throw 'The OpenUSD SDK requires its uv-managed Python 3.11 runtime.' }
$PreviousPath = $env:PATH
try {
    $env:PATH = "$OpenUsdRoot\lib;$OpenUsdRoot\bin;$(Split-Path -Parent $PythonExe);$PreviousPath"
    $Raw = & (Join-Path $TaskRoot 'build\Release\hdue_buffer_test.exe') --benchmark
    if ($LASTEXITCODE) { throw 'Buffer benchmark failed.' }
    $Result = $Raw | ConvertFrom-Json
    if ($Result.measurements.Count -ne 2) { throw 'Incomplete buffer benchmark.' }
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $Destination) | Out-Null
    $Result | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $Destination -Encoding utf8
    $Result.measurements | ConvertTo-Json
    Write-Output "Buffer benchmark: $Destination"
} finally {
    $env:PATH = $PreviousPath
}
