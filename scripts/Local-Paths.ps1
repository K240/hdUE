# Machine-specific SDKs, scenes and runtime profiles belong in ignored local configuration.
function Get-HdUELocalPath {
    param(
        [Parameter(Mandatory=$true)][string]$Key,
        [string]$Value='',
        [switch]$Optional
    )
    if ($Value) { return [IO.Path]::GetFullPath($Value) }
    $Variables=@{
        engine_root='HDUE_ENGINE_ROOT'; editor_root='HDUE_EDITOR_ROOT';
        openusd_root='HDUE_OPENUSD_ROOT'; houdini_root='HDUE_HOUDINI_ROOT';
        runtime_config='HDUE_RUNTIME_CONFIG'; review_scene='HDUE_REVIEW_SCENE'
        houdini_build_root='HDUE_HOUDINI_BUILD_ROOT'
    }
    if (-not $Variables.ContainsKey($Key)) { throw "Unknown local path key: $Key" }
    $Resolved=[Environment]::GetEnvironmentVariable($Variables[$Key],'Process')
    if (-not $Resolved -and $Key -eq 'houdini_root') { $Resolved=$env:HFS }
    if (-not $Resolved) {
        $LocalFile=if ($env:HDUE_LOCAL_CONFIG) {$env:HDUE_LOCAL_CONFIG} else {Join-Path (Split-Path -Parent $PSScriptRoot) '.local\paths.json'}
        if (Test-Path -LiteralPath $LocalFile -PathType Leaf) {
            $Local=Get-Content -LiteralPath $LocalFile -Raw | ConvertFrom-Json
            $Resolved=$Local.$Key
            if ($Resolved -and -not [IO.Path]::IsPathRooted($Resolved)) {
                $Resolved=Join-Path (Split-Path -Parent ([IO.Path]::GetFullPath($LocalFile))) $Resolved
            }
        }
    }
    if ($Resolved) { return [IO.Path]::GetFullPath($Resolved) }
    if ($Optional) { return '' }
    throw "Configure $Key in .local/paths.json, set $($Variables[$Key]), or pass the corresponding script parameter."
}

function Restore-HdUEEnvironment {
    param([Parameter(Mandatory=$true)][hashtable]$Snapshot)
    foreach ($Name in $Snapshot.Keys) {
        if ($null -eq $Snapshot[$Name]) {
            # Passing null through the .NET overload can create an empty value.
            # Houdini treats an empty SDK/preferences variable as a real setting.
            Remove-Item -LiteralPath "Env:$Name" -ErrorAction SilentlyContinue
        } else {
            [Environment]::SetEnvironmentVariable($Name,$Snapshot[$Name],'Process')
        }
    }
}
