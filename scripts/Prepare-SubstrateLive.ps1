param(
    [Parameter(Mandatory=$true)][string]$GraphJob,
    [Parameter(Mandatory=$true)][string]$OutputDirectory,
    [Parameter(Mandatory=$true)][string]$Destination,
    [Parameter(Mandatory=$true)][string]$RuntimeProject
)
$ErrorActionPreference='Stop'
. (Join-Path $PSScriptRoot 'Local-Paths.ps1')
$TaskRoot=Split-Path -Parent $PSScriptRoot
$RunOutput=[IO.Path]::GetFullPath($OutputDirectory)
$ProgressFile=Join-Path (Split-Path -Parent $RunOutput) 'progress.json'
function Write-ProgressState([string]$Phase,[string]$Message) {
    $Json=@{phase=$Phase;message=$Message} | ConvertTo-Json
    $Temporary=$ProgressFile+'.pending'
    [IO.File]::WriteAllText($Temporary,$Json,[Text.UTF8Encoding]::new($false))
    Move-Item -LiteralPath $Temporary -Destination $ProgressFile -Force
}
$Hash=[Security.Cryptography.SHA256]::Create().ComputeHash([Text.Encoding]::UTF8.GetBytes($TaskRoot.ToLowerInvariant()))
$Name='Local\hdUE_Substrate_'+([BitConverter]::ToString($Hash).Replace('-','').Substring(0,24))
$Mutex=[Threading.Mutex]::new($false,$Name); $Owned=$false
try {
    Write-ProgressState 'waiting' 'Waiting for the Substrate preparation worker'
    $Deadline=[DateTime]::UtcNow.AddMinutes(15)
    while (-not $Owned) {
        try {$Owned=$Mutex.WaitOne(200)} catch [Threading.AbandonedMutexException] {$Owned=$true}
        if ([DateTime]::UtcNow -gt $Deadline) {throw 'Timed out waiting for the Substrate preparation worker.'}
    }
    Write-ProgressState 'compiling' 'Compiling the edited Substrate graph'
    & (Join-Path $PSScriptRoot 'Prepare-Substrate.ps1') -GraphJob $GraphJob -OutputDirectory $RunOutput -Destination $Destination -SkipBuild -SkipCook
    $EditorRoot=Get-HdUELocalPath editor_root
    $Editor=Join-Path $EditorRoot 'Engine\Binaries\Win64\UnrealEditor-Cmd.exe'
    $Project=Join-Path $TaskRoot 'unreal\HdUEBackend\HdUEBackend.uproject'
    $CookDirectory=Join-Path $TaskRoot ('unreal\HdUEBackend\Content\'+$Destination.Substring('/Game/'.Length))
    Write-ProgressState 'cooking' 'Cooking the edited material with inline shader code'
    $CookArguments=@($Project,'-run=Cook','-TargetPlatform=Windows',"-CookDir=$CookDirectory",'-CookSkipRequests','-CookSkipHardRefs',
        "-OutputDir=$(Join-Path $RunOutput 'cooked\[Platform]')",
        '-ini:Game:[/Script/UnrealEd.ProjectPackagingSettings]:bShareMaterialShaderCode=False',
        '-unattended','-nop4','-nosplash','-nullrhi','-stdout')
    & $Editor @CookArguments *> (Join-Path $RunOutput 'live-cook.log')
    if ($LASTEXITCODE) {throw 'Live material cook failed; see live-cook.log.'}
    $Log=Get-Content -LiteralPath (Join-Path $RunOutput 'live-cook.log') -Raw
    if ($Log -match 'Log\w+: Error:|Fatal error|Failed to compile Material') {throw 'Live material cook reported an error; see live-cook.log.'}
    Write-ProgressState 'publishing' 'Publishing the completed material to the active runtime'
    & (Join-Path $PSScriptRoot 'Run-UsdPython.ps1') -Script (Join-Path $PSScriptRoot 'prepare_substrate.py') -ScriptArguments @('publish',$RunOutput,'--runtime-project',$RuntimeProject,'--destination',$Destination)
    if ($LASTEXITCODE) {throw 'Live material publication failed.'}
    Write-ProgressState 'ready' 'The edited Substrate graph is ready'
} catch {
    Write-ProgressState 'failed' $_.Exception.Message
    throw
} finally {
    if ($Owned) {$Mutex.ReleaseMutex()}
    $Mutex.Dispose()
}
