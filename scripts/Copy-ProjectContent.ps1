param([Parameter(Mandatory=$true)][string]$ProjectPath,[Parameter(Mandatory=$true)][string]$OutputDirectory)
$ErrorActionPreference='Stop'
$TaskRoot=Split-Path -Parent $PSScriptRoot
$SourceProject=if (Test-Path -LiteralPath $ProjectPath -PathType Container) {
    $Projects=@(Get-ChildItem -LiteralPath $ProjectPath -Filter *.uproject)
    if ($Projects.Count -ne 1) {throw 'Specify one .uproject file.'}
    $Projects[0].FullName
} else {(Resolve-Path -LiteralPath $ProjectPath).Path}
$SourceContent=[IO.Path]::GetFullPath((Join-Path (Split-Path -Parent $SourceProject) 'Content'))
$Destination=[IO.Path]::GetFullPath((Join-Path $TaskRoot 'unreal\HdUEBackend\Content'))
if ($SourceContent.TrimEnd('\') -eq $Destination.TrimEnd('\')) {throw 'Source and destination content must be different.'}
$Run=[IO.Path]::GetFullPath($OutputDirectory)
if (Test-Path -LiteralPath $Run) {throw 'Choose a fresh content receipt directory.'}
$Files=@(rg --files $SourceContent)
if ($LASTEXITCODE) {throw 'Cannot enumerate source content.'}
$Receipt=@()
# Preflight every collision before copying; never overwrite workspace assets.
foreach ($File in $Files) {
    $Relative=[IO.Path]::GetRelativePath($SourceContent,$File)
    $Target=[IO.Path]::GetFullPath((Join-Path $Destination $Relative))
    if (-not $Target.StartsWith($Destination.TrimEnd('\')+'\',[StringComparison]::OrdinalIgnoreCase)) {throw 'Content target escaped its destination.'}
    $Info=Get-Item -LiteralPath $File
    if (Test-Path -LiteralPath $Target) {
        if ((Get-Item -LiteralPath $Target).Length -ne $Info.Length -or (Get-FileHash -LiteralPath $File).Hash -ne (Get-FileHash -LiteralPath $Target).Hash) {throw "Different asset already exists: $Target"}
    }
    $Receipt+=@{relative=$Relative;bytes=$Info.Length;source_write_utc=$Info.LastWriteTimeUtc.ToString('o')}
}
New-Item -ItemType Directory -Force -Path $Run | Out-Null
foreach ($File in $Files) {
    $Target=Join-Path $Destination ([IO.Path]::GetRelativePath($SourceContent,$File))
    if (-not (Test-Path -LiteralPath $Target)) {
        New-Item -ItemType Directory -Force -Path (Split-Path -Parent $Target) | Out-Null
        Copy-Item -LiteralPath $File -Destination $Target
    }
}
@{source_project=$SourceProject;project_sha256=(Get-FileHash -LiteralPath $SourceProject).Hash.ToLowerInvariant();destination=$Destination;files=$Receipt} | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $Run 'copied-content.json') -Encoding utf8
Write-Output "Copied $($Files.Count) content files into hdUE; source project unchanged."
