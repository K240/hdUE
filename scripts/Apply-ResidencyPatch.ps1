param([string]$EngineRoot='')
$ErrorActionPreference='Stop'
. (Join-Path $PSScriptRoot 'Local-Paths.ps1')
$EngineRoot=Get-HdUELocalPath -Key engine_root -Value $EngineRoot
$Version=Get-Content -Raw -LiteralPath (Join-Path $EngineRoot 'Engine\Build\Build.version') | ConvertFrom-Json
if ($Version.MajorVersion -ne 5 -or $Version.MinorVersion -ne 8 -or $Version.PatchVersion -ne 4) {throw 'Residency adapter requires UE 5.8.4.'}
$Header=Join-Path $EngineRoot 'Engine\Source\Runtime\Renderer\Private\VT\VirtualTextureSystem.h'
$Source=[IO.File]::ReadAllText($Header)
$Friend="`tfriend class FHdueVirtualTextureResidency;"
if (-not $Source.Contains($Friend)) {
    $Anchor="`tfriend class FRendererModule;"
    if (($Source.Split($Anchor).Length-1) -ne 1) {throw 'Unexpected VirtualTextureSystem.h; inspect the pinned engine before applying the residency patch.'}
    $Newline=if ($Source.Contains("`r`n")) {"`r`n"} else {"`n"}
    [IO.File]::WriteAllText($Header,$Source.Replace($Anchor,$Anchor+$Newline+$Friend),[Text.UTF8Encoding]::new($false))
    Write-Host 'Applied hdUE VT residency friend adapter to UE 5.8.4.'
}
