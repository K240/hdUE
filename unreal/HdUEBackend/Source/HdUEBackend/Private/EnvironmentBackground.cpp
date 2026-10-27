#include "EnvironmentBackground.h"
#include "GlobalShader.h"
#include "RenderGraphBuilder.h"
#include "RenderGraphUtils.h"
#include "SceneView.h"
#include "ShaderParameterStruct.h"
#include "RHIStaticStates.h"

class FHdueEnvironmentCS final:public FGlobalShader {
public:
    DECLARE_GLOBAL_SHADER(FHdueEnvironmentCS);
    SHADER_USE_PARAMETER_STRUCT(FHdueEnvironmentCS,FGlobalShader);
    BEGIN_SHADER_PARAMETER_STRUCT(FParameters,)
        SHADER_PARAMETER(FMatrix44f,ClipToTranslatedWorld)
        SHADER_PARAMETER(FVector3f,TranslatedEye)
        SHADER_PARAMETER(FVector2f,InverseViewSize)
        SHADER_PARAMETER(FIntPoint,ViewMin)
        SHADER_PARAMETER(FIntPoint,ViewSize)
        SHADER_PARAMETER(float,RadianceScale)
        SHADER_PARAMETER_RDG_TEXTURE(Texture2D,Depth)
        SHADER_PARAMETER_RDG_TEXTURE_UAV(RWTexture2D<float4>,Color)
        SHADER_PARAMETER_TEXTURE(TextureCube,Environment)
        SHADER_PARAMETER_SAMPLER(SamplerState,EnvironmentSampler)
    END_SHADER_PARAMETER_STRUCT()
    static bool ShouldCompilePermutation(const FGlobalShaderPermutationParameters& Parameters) {
        return IsFeatureLevelSupported(Parameters.Platform,ERHIFeatureLevel::SM6);
    }
};
IMPLEMENT_GLOBAL_SHADER(FHdueEnvironmentCS,"/HdUE/Environment.usf","MainCS",SF_Compute);

void HdUEDrawEnvironment(FRDGBuilder& GraphBuilder,const FSceneView& View,
    FRDGTexture* SceneColor,FRDGTexture* SceneDepth,FRHITexture* Environment,float Scale) {
    if(!SceneColor || !SceneDepth || !Environment) return;
    FRDGTextureRef Destination=SceneColor;
    if(!EnumHasAnyFlags(SceneColor->Desc.Flags,TexCreate_UAV)) {
        auto Desc=SceneColor->Desc; Desc.Flags|=TexCreate_UAV;
        Destination=GraphBuilder.CreateTexture(Desc,TEXT("Hdue.EnvironmentColor"));
        AddCopyTexturePass(GraphBuilder,SceneColor,Destination);
    }
    auto* Parameters=GraphBuilder.AllocParameters<FHdueEnvironmentCS::FParameters>();
    Parameters->ClipToTranslatedWorld=FMatrix44f(View.ViewMatrices.GetInvTranslatedViewProjectionMatrix());
    Parameters->TranslatedEye=FVector3f(View.ViewMatrices.GetViewOrigin()+View.ViewMatrices.GetPreViewTranslation());
    Parameters->ViewMin=View.UnscaledViewRect.Min; Parameters->ViewSize=View.UnscaledViewRect.Size();
    Parameters->InverseViewSize=FVector2f(1.f/Parameters->ViewSize.X,1.f/Parameters->ViewSize.Y);
    Parameters->RadianceScale=Scale; Parameters->Depth=SceneDepth;
    Parameters->Color=GraphBuilder.CreateUAV(Destination);
    Parameters->Environment=Environment;
    Parameters->EnvironmentSampler=TStaticSamplerState<SF_Bilinear,AM_Clamp,AM_Clamp,AM_Clamp>::GetRHI();
    TShaderMapRef<FHdueEnvironmentCS> Shader(GetGlobalShaderMap(View.GetFeatureLevel()));
    FComputeShaderUtils::AddPass(GraphBuilder,RDG_EVENT_NAME("hdUE HDR environment background"),Shader,Parameters,
        FIntVector(FMath::DivideAndRoundUp(Parameters->ViewSize.X,8),FMath::DivideAndRoundUp(Parameters->ViewSize.Y,8),1));
    if(Destination!=SceneColor) AddCopyTexturePass(GraphBuilder,Destination,SceneColor);
}
