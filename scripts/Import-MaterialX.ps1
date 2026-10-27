param(
    [Parameter(Mandatory = $true)][string[]]$Source,
    [string]$Destination = '/Game/HdUE/MaterialX',
    [string]$OutputDirectory = '',
    [string]$EditorRoot = '',
    [switch]$SkipBuild,
    [switch]$WetProfile,
    [switch]$SkipCook
)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'Local-Paths.ps1')
$EditorRoot=Get-HdUELocalPath -Key editor_root -Value $EditorRoot
$TaskRoot = Split-Path -Parent $PSScriptRoot
$Project = Join-Path $TaskRoot 'unreal\HdUEBackend\HdUEBackend.uproject'
$Version = Get-Content -Raw -LiteralPath (Join-Path $EditorRoot 'Engine\Build\Build.version') | ConvertFrom-Json
if ($Version.MajorVersion -ne 5 -or $Version.MinorVersion -ne 8 -or $Version.PatchVersion -ne 4) { throw 'MaterialX authoring requires UE 5.8.4.' }
if (-not $Destination.StartsWith('/Game/HdUE/')) { throw 'Choose /Game/HdUE/... so prepared materials are included in the cook.' }
$RunOutput = if ($OutputDirectory) { [IO.Path]::GetFullPath($OutputDirectory) } else { Join-Path $TaskRoot ('out\materialx-import\' + (Get-Date -Format 'yyyyMMdd-HHmmss-fff')) }
if (Test-Path -LiteralPath $RunOutput) { throw 'Choose a fresh import output directory.' }
New-Item -ItemType Directory -Path $RunOutput -Force | Out-Null
if (-not $SkipBuild) {
    & (Join-Path $EditorRoot 'Engine\Build\BatchFiles\Build.bat') HdUEBackendEditor Win64 Development "-Project=$Project" -gather -WaitMutex -NoHotReload *> (Join-Path $RunOutput 'build.log')
    if ($LASTEXITCODE) { throw 'MaterialX commandlet build failed.' }
}
$Editor = Join-Path $EditorRoot 'Engine\Binaries\Win64\UnrealEditor-Cmd.exe'
$Reports = @()
$Folders = @{}
foreach ($File in $Source) {
    $ResolvedSource = (Resolve-Path -LiteralPath $File).Path
    if ([IO.Path]::GetExtension($ResolvedSource) -ne '.mtlx') { throw "Expected .mtlx: $ResolvedSource" }
    $Name = [IO.Path]::GetFileNameWithoutExtension($ResolvedSource)
    $Folder = switch ($Name) { 'standard_surface' { 'Standard' } 'open_pbr_surface' { 'OpenPBR' } default { $Name -replace '[^A-Za-z0-9_]', '_' } }
    if ($Folders.ContainsKey($Folder)) { throw 'Source names produce duplicate destination folders; import them separately with distinct destinations.' }
    $Folders[$Folder] = $true
    $Report = Join-Path $RunOutput ($Folder + '.json')
    $Log = Join-Path $RunOutput ($Folder + '.log')
    $ProfileArgs = @()
    if ($WetProfile) { $ProfileArgs += '-WetProfile' }
    & $Editor $Project -run=HdUEImportMaterialX "-Source=$ResolvedSource" "-Destination=$($Destination.TrimEnd('/'))/$Folder" "-Report=$Report" @ProfileArgs -unattended -nop4 -nosplash -nullrhi -stdout -UTF8Output *> $Log
    if ($LASTEXITCODE -or -not (Test-Path -LiteralPath $Report)) { throw "MaterialX import failed: $Log" }
    $LogText = [IO.File]::ReadAllText($Log)
    if ($LogText -match 'Log\w+: Error:|Fatal error') { throw "MaterialX import reported errors: $Log" }
    $Result = Get-Content -Raw -LiteralPath $Report | ConvertFrom-Json
    if ($Result.materials -lt 1 -or $Result.substrate -ne 1 -or $Result.gbuffer_format -ne 1) { throw "Incomplete MaterialX receipt: $Report" }
    $Reports += $Report
}
$Library = Join-Path $RunOutput 'library.json'
& uv run --quiet (Join-Path $PSScriptRoot 'build_materialx_library.py') @Reports --output $Library
if ($LASTEXITCODE) { throw 'MaterialX registry generation failed.' }
if (-not $SkipCook) {
    & $Editor $Project -run=Cook -TargetPlatform=Windows -Map=/Game/HdUETest -unattended -nop4 -nosplash -stdout -UTF8Output *> (Join-Path $RunOutput 'cook.log')
    if ($LASTEXITCODE) { throw 'MaterialX cook failed.' }
}
Write-Output "MaterialX library: $Library"
