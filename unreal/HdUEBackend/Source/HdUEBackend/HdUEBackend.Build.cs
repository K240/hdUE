using UnrealBuildTool;
using System.IO;
public class HdUEBackend : ModuleRules
{
    public HdUEBackend(ReadOnlyTargetRules Target) : base(Target)
    {
        PCHUsage = PCHUsageMode.UseExplicitOrSharedPCHs;
        PublicIncludePaths.Add(Path.GetFullPath(Path.Combine(ModuleDirectory, "../../../../include")));
        PublicDependencyModuleNames.AddRange(new[] { "Core", "CoreUObject", "Engine" });
        PrivateDependencyModuleNames.AddRange(new[] { "RHI", "RenderCore", "Renderer", "Projects", "GeometryFramework", "GeometryCore", "ImageWrapper", "ImageCore", "AssetRegistry" });
        if (Target.Type != TargetType.Editor)
        {
            // Pinned monolithic source target; private VT access is isolated in VirtualTextureResidency.cpp.
            PrivateIncludePaths.Add(Path.Combine(GetModuleDirectory("Renderer"), "Private"));
            PrivateIncludePaths.Add(Path.Combine(GetModuleDirectory("Renderer"), "Internal"));
            PrivateDependencyModuleNames.Add("UELibrary");
            PrivateDefinitions.Add("HDUE_BRIDGE_EXPORTS=1");
        }
    }
}

