using UnrealBuildTool;
public class HdUEBackendTarget : TargetRules
{
    public HdUEBackendTarget(TargetInfo Target) : base(Target)
    {
        Type = TargetType.Game;
        DefaultBuildSettings = BuildSettingsVersion.Latest;
        IncludeOrderVersion = EngineIncludeOrderVersion.Latest;
        LinkType = TargetLinkType.Monolithic;
        BuildEnvironment = TargetBuildEnvironment.Unique;
        bShouldCompileAsDLL = true;
        GlobalDefinitions.Add("UE_LIBRARY_ENABLED=1");
        ExtraModuleNames.AddRange(new[] { "HdUEBackend", "UELibrary" });
        bCompileICU = true;
    }
}

