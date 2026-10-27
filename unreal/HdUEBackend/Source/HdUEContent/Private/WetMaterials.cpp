#include "WetMaterials.h"
#include "Materials/Material.h"
#include "Materials/MaterialExpressionSubstrate.h"
#include "Materials/MaterialExpressionScalarParameter.h"
#include "Materials/MaterialExpressionConstant.h"
#include "Materials/MaterialExpressionConstant3Vector.h"
#include "Materials/MaterialExpressionVertexColor.h"
#include "Materials/MaterialExpressionLinearInterpolate.h"
#include "Materials/MaterialExpressionMultiply.h"
#include "Materials/MaterialExpressionSaturate.h"
#include "AssetRegistry/AssetRegistryModule.h"
#include "Dom/JsonObject.h"
#include "Misc/PackageName.h"
#include "UObject/SavePackage.h"

namespace {
template<class T> T* Add(UMaterial* M) {
    auto* E = NewObject<T>(M);
    M->GetExpressionCollection().AddExpression(E);
    return E;
}
auto Scalar(UMaterial* M, const TCHAR* Name, float Value) {
    auto* E = Add<UMaterialExpressionScalarParameter>(M);
    E->ParameterName = Name; E->DefaultValue = Value;
    return E;
}
auto Constant(UMaterial* M, float Value) {
    auto* E = Add<UMaterialExpressionConstant>(M); E->R = Value; return E;
}
UMaterialExpression* Coverage(UMaterial* M) {
    auto* Amount = Scalar(M, TEXT("HdUE_WetAmount"), 1.f);
    auto* UseMask = Scalar(M, TEXT("HdUE_WetUseMask"), 0.f);
    auto* Color = Add<UMaterialExpressionVertexColor>(M);
    auto* Mask = Add<UMaterialExpressionLinearInterpolate>(M);
    Mask->ConstA = 1.f; Mask->B.Connect(1, Color); Mask->Alpha.Connect(0, UseMask);
    auto* SaturateAmount = Add<UMaterialExpressionSaturate>(M); SaturateAmount->Input.Connect(0, Amount);
    auto* SaturateMask = Add<UMaterialExpressionSaturate>(M); SaturateMask->Input.Connect(0, Mask);
    auto* Product = Add<UMaterialExpressionMultiply>(M);
    Product->A.Connect(0, SaturateAmount); Product->B.Connect(0, SaturateMask);
    return Product;
}
bool Save(UMaterial* M) {
    M->PostEditChange();
    FAssetRegistryModule::AssetCreated(M);
    FSavePackageArgs Args; Args.TopLevelFlags = RF_Public | RF_Standalone;
    const auto File = FPackageName::LongPackageNameToFilename(M->GetOutermost()->GetName(), FPackageName::GetAssetPackageExtension());
    return UPackage::SavePackage(M->GetOutermost(), M, *File, Args);
}
UMaterial* Duplicate(UMaterial* Base, const TCHAR* Suffix) {
    const auto PackageName = Base->GetOutermost()->GetName() + Suffix;
    auto* Package = CreatePackage(*PackageName);
    Package->FullyLoad();
    const auto Name=FPackageName::GetLongPackageAssetName(PackageName);
    if (auto* Previous=FindObject<UMaterial>(Package,*Name)) {
        Previous->ClearFlags(RF_Public | RF_Standalone);
        Previous->Rename(nullptr,GetTransientPackage(),REN_DontCreateRedirectors | REN_NonTransactional);
    }
    // ImportAsMaterials replaces the base on each preparation; the derivative
    // is regenerated too, rather than retaining an obsolete imported graph.
    return DuplicateObject<UMaterial>(Base, Package, *Name);
}
}
TSharedPtr<FJsonObject> HdUEPrepareWetMaterials(UMaterial* Base) {
    auto Record = MakeShared<FJsonObject>();
    Record->SetNumberField(TEXT("version"), 1);
    Record->SetBoolField(TEXT("supported"), false);
    Record->SetStringField(TEXT("base"), Base->GetPathName());
    if (Base->GetBlendMode() != BLEND_Opaque || !Base->HasSubstrateFrontMaterialConnected()) {
        Record->SetStringField(TEXT("reason"), TEXT("Wet v1 requires an opaque Substrate front material."));
        return Record;
    }
    auto* Wet = Duplicate(Base, TEXT("_Wet_v1"));
    auto Original = Wet->GetEditorOnlyData()->FrontMaterial;
    auto* Slab = Add<UMaterialExpressionSubstrateSlabBSDF>(Wet);
    Slab->DiffuseAlbedo.Connect(0, Constant(Wet, 0.f));
    constexpr float F0 = ((1.33f - 1.f) / (1.33f + 1.f)) * ((1.33f - 1.f) / (1.33f + 1.f));
    Slab->F0.Connect(0, Constant(Wet, F0));
    Slab->Roughness.Connect(0, Scalar(Wet, TEXT("HdUE_WetRoughness"), .08f));
    Slab->SubSurfaceType = MSS_SimpleVolume;
    auto* Clear = Add<UMaterialExpressionConstant3Vector>(Wet);
    Clear->Constant = FLinearColor(1.e6f, 1.e6f, 1.e6f);
    Slab->SSSMFP.Connect(0, Clear);
    auto* Weight = Add<UMaterialExpressionSubstrateWeight>(Wet);
    Weight->A.Connect(0, Slab); Weight->Weight.Connect(0, Coverage(Wet));
    auto* Layer = Add<UMaterialExpressionSubstrateVerticalLayering>(Wet);
    Layer->Top.Connect(0, Weight); Layer->Base = Original;
    Layer->Thickness.Connect(0, Constant(Wet, .01f));
    Layer->bUseParameterBlending = false;
    Wet->GetEditorOnlyData()->FrontMaterial.Connect(0, Layer);

    auto* Preview = Duplicate(Base, TEXT("_WetMask_v1"));
    Preview->GetExpressionCollection().Empty();
    auto* Unlit = Add<UMaterialExpressionSubstrateUnlitBSDF>(Preview);
    Unlit->EmissiveColor.Connect(0, Coverage(Preview));
    Preview->GetEditorOnlyData()->FrontMaterial.Connect(0, Unlit);
    if (!Save(Wet) || !Save(Preview)) {
        Record->SetStringField(TEXT("reason"), TEXT("Could not save wet material variants."));
        return Record;
    }
    Record->SetBoolField(TEXT("supported"), true);
    Record->SetStringField(TEXT("asset"), Wet->GetPathName());
    Record->SetStringField(TEXT("preview_asset"), Preview->GetPathName());
    Record->SetStringField(TEXT("mask_transport"), TEXT("linear_vertex_color_r"));
    Record->SetStringField(TEXT("amount_parameter"), TEXT("HdUE_WetAmount"));
    Record->SetStringField(TEXT("roughness_parameter"), TEXT("HdUE_WetRoughness"));
    Record->SetStringField(TEXT("mask_parameter"), TEXT("HdUE_WetUseMask"));
    Record->SetNumberField(TEXT("ior"), 1.33);
    Record->SetNumberField(TEXT("thickness_cm"), .01);
    Record->SetBoolField(TEXT("parameter_blending"), false);
    return Record;
}
