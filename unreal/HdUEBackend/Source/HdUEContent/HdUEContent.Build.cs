using UnrealBuildTool;
public class HdUEContent : ModuleRules
{
    public HdUEContent(ReadOnlyTargetRules Target) : base(Target)
    {
        PCHUsage = PCHUsageMode.UseExplicitOrSharedPCHs;
        PrivateDependencyModuleNames.AddRange(new[] { "Core", "CoreUObject", "Engine", "UnrealEd", "AssetRegistry",
            "InterchangeCore", "InterchangeEngine", "InterchangePipelines", "InterchangeNodes", "InterchangeFactoryNodes", "TextureUtilitiesCommon", "Json", "JsonUtilities", "RenderCore" });
    }
}

