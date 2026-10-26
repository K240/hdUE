#include "Scene.h"
#include "Materials/MaterialInstanceDynamic.h"
#include "Engine/Texture.h"
#include "MaterialShared.h"
#include "RHI.h"
#include "RenderUtils.h"

HdueResult FHdueScene::SetMaterialParameters(const HdueMaterialUpdate& Update, FString& Error) {
    if (!Update.material_id || Update.parameter_count > 256 || (Update.parameter_count && !Update.parameters)) return HDUE_INVALID_ARGUMENT;
    const auto* Entry = Materials.Find(UTF8_TO_TCHAR(Update.material_id));
    auto* Material = Entry ? Cast<UMaterialInstanceDynamic>(Entry->Get()) : nullptr;
    if (!Material) { Error = TEXT("Material instance was not found in the scene."); return HDUE_INVALID_ARGUMENT; }
    TArray<FMaterialParameterInfo> Scalars, Vectors, Textures; TArray<FGuid> Guids;
    Material->GetAllScalarParameterInfo(Scalars, Guids); Guids.Empty();
    Material->GetAllVectorParameterInfo(Vectors, Guids); Guids.Empty();
    Material->GetAllTextureParameterInfo(Textures, Guids);
    const auto Has = [](const TArray<FMaterialParameterInfo>& Info, FName Name) {
        return Info.ContainsByPredicate([&](const auto& P) { return P.Name == Name && P.Association == EMaterialParameterAssociation::GlobalParameter; });
    };
    TSet<FName> Names;
    TArray<TStrongObjectPtr<UTexture>> LoadedTextures;
    for (uint32 I = 0; I < Update.parameter_count; ++I) {
        const auto& P = Update.parameters[I];
        if (!P.name || !*P.name || P.type > HDUE_PARAMETER_TEXTURE) return HDUE_INVALID_ARGUMENT;
        const FName Name(UTF8_TO_TCHAR(P.name));
        if (Names.Contains(Name)) { Error = TEXT("Duplicate material parameter."); return HDUE_INVALID_ARGUMENT; }
        Names.Add(Name);
        if (!Has(P.type == HDUE_PARAMETER_SCALAR ? Scalars : P.type == HDUE_PARAMETER_VECTOR ? Vectors : Textures, Name)) {
            Error = FString::Printf(TEXT("Unknown material parameter or wrong type: %s"), UTF8_TO_TCHAR(P.name)); return HDUE_INVALID_ARGUMENT;
        }
        for (uint32 C = 0; C < (P.type == HDUE_PARAMETER_SCALAR ? 1u : P.type == HDUE_PARAMETER_VECTOR ? 4u : 0u); ++C)
            if (!FMath::IsFinite(P.value[C])) return HDUE_INVALID_ARGUMENT;
        if (P.type == HDUE_PARAMETER_TEXTURE) {
            if (!P.texture_asset || !*P.texture_asset) return HDUE_INVALID_ARGUMENT;
            auto* Texture = LoadObject<UTexture>(nullptr, UTF8_TO_TCHAR(P.texture_asset));
            if (!Texture) { Error = TEXT("Cooked texture parameter asset was not found."); return HDUE_INVALID_ARGUMENT; }
            UTexture* Default = nullptr;
            Material->GetTextureParameterValue(FMaterialParameterInfo(Name), Default);
            if (!Default || Texture->VirtualTextureStreaming != Default->VirtualTextureStreaming ||
                (Texture->CompressionSettings == TC_Normalmap) != (Default->CompressionSettings == TC_Normalmap) || Texture->SRGB != Default->SRGB) {
                Error = TEXT("Texture override must match the slot's virtual, normal-map, and sRGB sampler kind."); return HDUE_INVALID_ARGUMENT;
            }
            LoadedTextures.Add(TStrongObjectPtr<UTexture>(Texture));
        }
    }
    int32 TextureIndex = 0;
    for (uint32 I = 0; I < Update.parameter_count; ++I) {
        const auto& P = Update.parameters[I]; const FName Name(UTF8_TO_TCHAR(P.name));
        if (P.type == HDUE_PARAMETER_SCALAR) Material->SetScalarParameterValue(Name, P.value[0]);
        else if (P.type == HDUE_PARAMETER_VECTOR) Material->SetVectorParameterValue(Name, FLinearColor(P.value[0], P.value[1], P.value[2], P.value[3]));
        else Material->SetTextureParameterValue(Name, LoadedTextures[TextureIndex++].Get());
    }
    return HDUE_OK;
}
HdueResult FHdueScene::GetMaterialStats(const char* Id, HdueMaterialStats& Stats, FString& Error) {
    if (!Id) return HDUE_INVALID_ARGUMENT;
    const auto* Entry = Materials.Find(UTF8_TO_TCHAR(Id));
    const UMaterialInterface* Material = Entry ? Entry->Get() : nullptr;
    const FMaterialResource* Resource = Material ? Material->GetMaterialResource(EShaderPlatform(GMaxRHIShaderPlatform)) : nullptr;
    if (!Resource || !Resource->GetGameThreadShaderMap()) { Error = TEXT("Material shader resource is unavailable."); return HDUE_NOT_SUPPORTED; }
    Stats.substrate_enabled = Substrate::IsSubstrateEnabled();
    Stats.closure_count = Resource->MaterialGetSubstrateClosureCount_GameThread();
    Stats.bytes_per_pixel = Resource->MaterialGetSubstrateUintPerPixel_GameThread() * 4;
    Stats.blend_mode = uint32(Material->GetBlendMode());
    TArray<FMaterialParameterInfo> Info; TArray<FGuid> Guids;
    Material->GetAllScalarParameterInfo(Info, Guids); Stats.scalar_parameters = Info.Num(); Info.Empty(); Guids.Empty();
    Material->GetAllVectorParameterInfo(Info, Guids); Stats.vector_parameters = Info.Num(); Info.Empty(); Guids.Empty();
    Material->GetAllTextureParameterInfo(Info, Guids); Stats.texture_parameters = Info.Num();
    return HDUE_OK;
}
