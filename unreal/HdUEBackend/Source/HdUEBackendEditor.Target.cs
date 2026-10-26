using UnrealBuildTool;
public class HdUEBackendEditorTarget : TargetRules
{
    public HdUEBackendEditorTarget(TargetInfo Target) : base(Target)
    {
        Type = TargetType.Editor;
        DefaultBuildSettings = BuildSettingsVersion.Latest;
        IncludeOrderVersion = EngineIncludeOrderVersion.Latest;
        ExtraModuleNames.AddRange(new[] { "HdUEBackend", "HdUEContent" });
    }
}

