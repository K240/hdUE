param(
    [string]$InputUsd='', [string]$GraphJob='',
    [string]$MaterialXLibrary='',
    [string]$OutputDirectory='',
    [string]$Destination='/Game/HdUE/Substrate',
    [string]$HoudiniRoot='', [string]$EditorRoot='',
    [switch]$SkipBuild, [switch]$SkipCook
)
$ErrorActionPreference='Stop'
if ((-not $InputUsd -and -not $GraphJob) -or ($InputUsd -and $GraphJob)) {throw 'Choose InputUsd or GraphJob.'}
. (Join-Path $PSScriptRoot 'Local-Paths.ps1')
$HoudiniRoot=Get-HdUELocalPath houdini_root -Value $HoudiniRoot -Optional:([bool]$GraphJob)
$EditorRoot=Get-HdUELocalPath editor_root -Value $EditorRoot
$TaskRoot=Split-Path -Parent $PSScriptRoot
$RunOutput=if ($OutputDirectory) {[IO.Path]::GetFullPath($OutputDirectory)} else {Join-Path $TaskRoot ('out\substrate-'+(Get-Date -Format 'yyyyMMdd-HHmmss-fff'))}
if (Test-Path -LiteralPath $RunOutput) {throw 'Choose a fresh Substrate output directory.'}
if (-not $Destination.StartsWith('/Game/HdUE/')) {throw 'Substrate assets must be prepared below /Game/HdUE/.'}
$Arguments=if ($GraphJob) {@('export-job',[IO.Path]::GetFullPath($GraphJob),'--output',$RunOutput,'--destination',$Destination)} else {@('export',[IO.Path]::GetFullPath($InputUsd),'--output',$RunOutput,'--destination',$Destination)}
if ($MaterialXLibrary) {$Arguments+=@('--library',[IO.Path]::GetFullPath($MaterialXLibrary))}
if ($GraphJob) {
    & (Join-Path $PSScriptRoot 'Run-UsdPython.ps1') -Script (Join-Path $PSScriptRoot 'prepare_substrate.py') -ScriptArguments $Arguments
} else {
    & uv run -- (Join-Path $HoudiniRoot 'bin\hython.exe') (Join-Path $PSScriptRoot 'prepare_substrate.py') @Arguments
}
if ($LASTEXITCODE) {throw 'Substrate graph export failed.'}
$Project=Join-Path $TaskRoot 'unreal\HdUEBackend\HdUEBackend.uproject'
if (-not $SkipBuild) {
    & (Join-Path $EditorRoot 'Engine\Build\BatchFiles\Build.bat') HdUEBackendEditor Win64 Development "-Project=$Project" -gather -WaitMutex -NoHotReload *> (Join-Path $RunOutput 'build.log')
    if ($LASTEXITCODE) {throw 'Substrate commandlet build failed.'}
}
$Editor=Join-Path $EditorRoot 'Engine\Binaries\Win64\UnrealEditor-Cmd.exe'
$Manifests=Get-Content -LiteralPath (Join-Path $RunOutput 'export.json') -Raw | ConvertFrom-Json
$Compiled=[Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
foreach ($Manifest in $Manifests) {
    foreach ($File in @($Manifest.file,$Manifest.preview)) {
        if (-not $Compiled.Add($File)) {continue}
        $Report=[IO.Path]::ChangeExtension($File,'.receipt.json'); $Log=[IO.Path]::ChangeExtension($File,'.log')
        & $Editor $Project -run=HdUESubstrate "-Graph=$File" "-Report=$Report" -unattended -nop4 -nosplash -nullrhi -stdout *> $Log
        if ($LASTEXITCODE -or -not (Test-Path -LiteralPath $Report)) {throw "Substrate graph compilation failed: $Log"}
        if ((Get-Content -LiteralPath $Log -Raw) -match 'Log\w+: Error:|Fatal error') {throw "Substrate graph compilation reported an error: $Log"}
    }
}
if ($GraphJob) {
    & (Join-Path $PSScriptRoot 'Run-UsdPython.ps1') -Script (Join-Path $PSScriptRoot 'prepare_substrate.py') -ScriptArguments @('registry',$RunOutput)
} else {
    & uv run -- (Join-Path $HoudiniRoot 'bin\hython.exe') (Join-Path $PSScriptRoot 'prepare_substrate.py') registry $RunOutput
}
if ($LASTEXITCODE) {throw 'Substrate registry generation failed.'}
if (-not $SkipCook) {
    & $Editor $Project -run=Cook -TargetPlatform=Windows -Map=/Game/HdUETest -unattended -nop4 -nosplash -stdout *> (Join-Path $RunOutput 'cook.log')
    if ($LASTEXITCODE) {throw 'Substrate cook failed.'}
}
Write-Output "Substrate library: $(Join-Path $RunOutput 'library.json')"
