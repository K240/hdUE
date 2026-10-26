#include "HdUEPrepareCommandlet.h"
#include "Engine/World.h"
#include "Engine/StaticMesh.h"
#include "Engine/StaticMeshActor.h"
#include "Engine/DirectionalLight.h"
#include "Components/StaticMeshComponent.h"
#include "Components/DirectionalLightComponent.h"
#include "FileHelpers.h"
#include "AssetRegistry/AssetRegistryModule.h"
#include "Materials/Material.h"
#include "Materials/MaterialExpressionVectorParameter.h"
#include "Materials/MaterialExpressionScalarParameter.h"
#include "Materials/MaterialExpressionTextureSampleParameter2D.h"
#include "Materials/MaterialExpressionMultiply.h"
#include "Materials/MaterialExpressionAdd.h"
#include "Materials/MaterialExpressionSubtract.h"
#include "Materials/MaterialExpressionDivide.h"
#include "Materials/MaterialExpressionConstant3Vector.h"
#include "Materials/MaterialExpressionPathTracingQualitySwitch.h"
#include "Materials/MaterialExpressionSubstrate.h"
#include "Misc/PackageName.h"
#include "UObject/SavePackage.h"
#include "StaticMeshCompiler.h"
#include "Misc/Paths.h"
#include "EngineUtils.h"

namespace {
bool SaveAsset(UObject* Asset) {
    UPackage* Package = Asset->GetOutermost();
    FSavePackageArgs Args;
    Args.TopLevelFlags = RF_Public | RF_Standalone;
    const FString File = FPackageName::LongPackageNameToFilename(Package->GetName(), FPackageName::GetAssetPackageExtension());
    FAssetRegistryModule::AssetCreated(Asset);
    return UPackage::SavePackage(Package, Asset, *File, Args);
}
UMaterial* CreatePBR(const TCHAR* Name, FLinearColor Color, float Metal, float Roughness, bool Translucent = false) {
    const FString Path = FString(TEXT("/Game/HdUE/")) + Name;
    UPackage* Package = CreatePackage(*Path);
    auto* Material = LoadObject<UMaterial>(nullptr, *(Path + TEXT(".") + Name), nullptr, LOAD_NoWarn);
    if (Material && Material->HasSubstrateFrontMaterialConnected() &&
        (!Translucent || Material->GetBlendMode() == BLEND_TranslucentGreyTransmittance)) return Material;
    if (!Material) Material = NewObject<UMaterial>(Package, Name, RF_Public | RF_Standalone);
    Material->PreEditChange(nullptr);
    Material->GetExpressionCollection().Empty();
    auto* Base = NewObject<UMaterialExpressionVectorParameter>(Material);
    Base->ParameterName = TEXT("BaseColor"); Base->DefaultValue = Color;
    auto* Metallic = NewObject<UMaterialExpressionScalarParameter>(Material);
    Metallic->ParameterName = TEXT("Metallic"); Metallic->DefaultValue = Metal;
    auto* Rough = NewObject<UMaterialExpressionScalarParameter>(Material);
    Rough->ParameterName = TEXT("Roughness"); Rough->DefaultValue = Roughness;
    auto* Texture = NewObject<UMaterialExpressionTextureSampleParameter2D>(Material);
    Texture->ParameterName = TEXT("BaseColorTexture");
    Texture->Texture = LoadObject<UTexture>(nullptr, TEXT("/Engine/EngineResources/WhiteSquareTexture.WhiteSquareTexture"));
    auto* Multiply = NewObject<UMaterialExpressionMultiply>(Material);
    Multiply->A.Connect(0, Base); Multiply->B.Connect(0, Texture);
    auto* Conversion = NewObject<UMaterialExpressionSubstrateMetalnessToDiffuseAlbedoF0>(Material);
    Conversion->BaseColor.Connect(0, Multiply);
    Conversion->Metallic.Connect(0, Metallic);
    auto* Slab = NewObject<UMaterialExpressionSubstrateSlabBSDF>(Material);
    Slab->DiffuseAlbedo.Connect(0, Conversion);
    Slab->F0.Connect(1, Conversion);
    Slab->Roughness.Connect(0, Rough);
    const TArray<UMaterialExpression*> Expressions = { Base, Metallic, Rough, Texture, Multiply, Conversion, Slab };
    for (UMaterialExpression* Expression : Expressions)
        Material->GetExpressionCollection().AddExpression(Expression);
    auto* Data = Material->GetEditorOnlyData();
    Data->BaseColor.Expression = nullptr;
    Data->Metallic.Expression = nullptr;
    Data->Roughness.Expression = nullptr;
    Data->FrontMaterial.Connect(0, Slab);
    if (Translucent) {
        // Opacity is coverage, with no fabricated glass IOR or absorption.
        auto* Opacity = NewObject<UMaterialExpressionScalarParameter>(Material);
        Opacity->ParameterName = TEXT("Opacity"); Opacity->DefaultValue = 1.f;
        auto* Weight = NewObject<UMaterialExpressionSubstrateWeight>(Material);
        Weight->A.Connect(0, Slab); Weight->Weight.Connect(0, Opacity);
        Material->GetExpressionCollection().AddExpression(Opacity);
        Material->GetExpressionCollection().AddExpression(Weight);
        Data->FrontMaterial.Connect(0, Weight);
        Material->BlendMode = BLEND_TranslucentGreyTransmittance;
        Material->TranslucencyLightingMode = TLM_SurfacePerPixelLighting;
    }
    Material->PostEditChange();
    return SaveAsset(Material) ? Material : nullptr;
}
UMaterial* CreateGlass() {
    const FString Path = TEXT("/Game/HdUE/M_Glass");
    UPackage* Package = CreatePackage(*Path);
    auto* Material = LoadObject<UMaterial>(nullptr, TEXT("/Game/HdUE/M_Glass.M_Glass"), nullptr, LOAD_NoWarn);
    if (Material && Material->HasSubstrateFrontMaterialConnected()) {
        for (const auto& Expression : Material->GetExpressionCollection().Expressions)
            if (Cast<UMaterialExpressionPathTracingQualitySwitch>(Expression)) return Material;
    }
    if (!Material) Material = NewObject<UMaterial>(Package, TEXT("M_Glass"), RF_Public | RF_Standalone);
    Material->PreEditChange(nullptr);
    Material->GetExpressionCollection().Empty();
    const auto Scalar = [&](const TCHAR* Name, float Value) {
        auto* Expression = NewObject<UMaterialExpressionScalarParameter>(Material);
        Expression->ParameterName = Name; Expression->DefaultValue = Value;
        Material->GetExpressionCollection().AddExpression(Expression);
        return Expression;
    };
    auto* IOR = Scalar(TEXT("IOR"), 1.5f);
    auto* Roughness = Scalar(TEXT("Roughness"), .04f);
    auto* Distance = Scalar(TEXT("AttenuationDistanceCm"), 1.f);
    auto* Thickness = Scalar(TEXT("SurfaceThicknessCm"), .2f);
    auto* Coverage = Scalar(TEXT("Coverage"), 1.f);
    auto* Color = NewObject<UMaterialExpressionVectorParameter>(Material);
    Color->ParameterName = TEXT("AttenuationColor"); Color->DefaultValue = FLinearColor(.95f, .98f, .95f);
    auto* MFP = NewObject<UMaterialExpressionSubstrateTransmittanceToMFP>(Material);
    MFP->TransmittanceColor.Connect(0, Color); MFP->Thickness.Connect(0, Distance);
    // UE 5.8.4's thickness stack reads the root's constant, not its expression.
    // Keep that slab at 0.01 cm, scaling MFP so the realtime optical depth is
    // SurfaceThicknessCm / physical MFP. PT must retain unscaled world-space MFP.
    constexpr float RealtimeSlabThicknessCm = .01f;
    auto* ThicknessScale = NewObject<UMaterialExpressionDivide>(Material);
    ThicknessScale->ConstA = RealtimeSlabThicknessCm; ThicknessScale->B.Connect(0, Thickness);
    auto* RealtimeMFP = NewObject<UMaterialExpressionMultiply>(Material);
    RealtimeMFP->A.Connect(0, MFP); RealtimeMFP->B.Connect(0, ThicknessScale);
    auto* SelectedMFP = NewObject<UMaterialExpressionPathTracingQualitySwitch>(Material);
    SelectedMFP->Normal.Connect(0, RealtimeMFP); SelectedMFP->PathTraced.Connect(0, MFP);
    // Fresnel F0 = ((n-1)/(n+1))^2; the same IOR drives ray bending.
    auto* Numerator = NewObject<UMaterialExpressionSubtract>(Material);
    Numerator->A.Connect(0, IOR); Numerator->ConstB = 1;
    auto* Denominator = NewObject<UMaterialExpressionAdd>(Material);
    Denominator->A.Connect(0, IOR); Denominator->ConstB = 1;
    auto* Ratio = NewObject<UMaterialExpressionDivide>(Material);
    Ratio->A.Connect(0, Numerator); Ratio->B.Connect(0, Denominator);
    auto* F0 = NewObject<UMaterialExpressionMultiply>(Material);
    F0->A.Connect(0, Ratio); F0->B.Connect(0, Ratio);
    auto* Black = NewObject<UMaterialExpressionConstant3Vector>(Material);
    Black->Constant = FLinearColor::Black;
    auto* Slab = NewObject<UMaterialExpressionSubstrateSlabBSDF>(Material);
    Slab->DiffuseAlbedo.Connect(0, Black);
    Slab->SubSurfaceType = MSS_SimpleVolume;
    Slab->F0.Connect(0, F0); Slab->Roughness.Connect(0, Roughness);
    Slab->SSSMFP.Connect(0, SelectedMFP);
    auto* Weight = NewObject<UMaterialExpressionSubstrateWeight>(Material);
    Weight->A.Connect(0, Slab); Weight->Weight.Connect(0, Coverage);
    for (auto* Expression : TArray<UMaterialExpression*>{Color, MFP, ThicknessScale, RealtimeMFP, SelectedMFP, Numerator, Denominator, Ratio, F0, Black, Slab, Weight})
        Material->GetExpressionCollection().AddExpression(Expression);
    auto* Data = Material->GetEditorOnlyData();
    Data->FrontMaterial.Connect(0, Weight);
    Data->Refraction.Connect(0, IOR);
    Data->SurfaceThickness.Expression = nullptr;
    Data->SurfaceThickness.UseConstant = true;
    Data->SurfaceThickness.Constant = RealtimeSlabThicknessCm;
    Material->BlendMode = BLEND_TranslucentColoredTransmittance;
    Material->TranslucencyLightingMode = TLM_SurfacePerPixelLighting;
    Material->RefractionMethod = RM_IndexOfRefraction;
    Material->bIsThinSurface = false;
    Material->TwoSided = true;
    Material->PostEditChange();
    return SaveAsset(Material) ? Material : nullptr;
}
}

UHdUEPrepareCommandlet::UHdUEPrepareCommandlet()
{
    IsClient = false; IsServer = false; IsEditor = true; LogToConsole = true;
}
int32 UHdUEPrepareCommandlet::Main(const FString& Params)
{
    UMaterial* Preview = CreatePBR(TEXT("M_PreviewSurface"), FLinearColor(0.7f, 0.7f, 0.7f), 0, 0.5f);
    UMaterial* Translucent = CreatePBR(TEXT("M_PreviewSurfaceTranslucent"), FLinearColor(0.7f, 0.7f, 0.7f), 0, 0.5f, true);
    UMaterial* Glass = CreateGlass();
    UMaterial* Native = CreatePBR(TEXT("M_Native"), FLinearColor(0.05f, 0.15f, 0.8f), 0.6f, 0.15f);
    UMaterial* Red = CreatePBR(TEXT("M_ReferenceRed"), FLinearColor(0.7f, 0.05f, 0.02f), 0.05f, 0.4f);
    UMaterial* Grey = CreatePBR(TEXT("M_ReferenceGrey"), FLinearColor(0.4f, 0.4f, 0.4f), 0.f, 0.7f);
    if (!Preview || !Translucent || !Glass || !Native || !Red || !Grey) return 3;
    for (auto* Material : {Preview, Native, Red, Grey}) {
        if (!Material->bUsedWithNanite) {
            Material->PreEditChange(nullptr);
            Material->bUsedWithNanite = true;
            Material->PostEditChange();
            if (!SaveAsset(Material)) return 3;
        }
    }
    UPackage* MeshPackage = CreatePackage(TEXT("/Game/HdUE/SM_Nanite"));
    auto* NativeMesh = LoadObject<UStaticMesh>(nullptr, TEXT("/Game/HdUE/SM_Nanite.SM_Nanite"), nullptr, LOAD_NoWarn);
    if (!NativeMesh) NativeMesh = DuplicateObject<UStaticMesh>(LoadObject<UStaticMesh>(nullptr, TEXT("/Engine/BasicShapes/Cube.Cube")), MeshPackage, TEXT("SM_Nanite"));
    NativeMesh->SetFlags(RF_Public | RF_Standalone);
    NativeMesh->GetNaniteSettings().bEnabled = true;
    NativeMesh->bAllowCPUAccess = true;
    NativeMesh->NeverStream = true;
    NativeMesh->SetMaterial(0, Native);
    NativeMesh->Build(false);
    FStaticMeshCompilingManager::Get().FinishCompilation({ NativeMesh });
    if (!SaveAsset(NativeMesh)) return 4;
    const FString MapFile = FPackageName::LongPackageNameToFilename(TEXT("/Game/HdUETest"), FPackageName::GetMapPackageExtension());
    UWorld* World = FPaths::FileExists(MapFile) ? UEditorLoadingAndSavingUtils::LoadMap(MapFile) : UEditorLoadingAndSavingUtils::NewBlankMap(false);
    if (!World) return 1;
    TArray<AActor*> OldActors;
    for (TActorIterator<AActor> It(World); It; ++It) if (It->IsA<AStaticMeshActor>() || It->IsA<ADirectionalLight>()) OldActors.Add(*It);
    for (auto* Actor : OldActors) World->DestroyActor(Actor);
    auto* Cube = World->SpawnActor<AStaticMeshActor>(FVector(0, 0, 50), FRotator::ZeroRotator);
    Cube->GetStaticMeshComponent()->SetStaticMesh(LoadObject<UStaticMesh>(nullptr, TEXT("/Engine/BasicShapes/Cube.Cube")));
    Cube->GetStaticMeshComponent()->SetMaterial(0, Red);
    auto* Ground = World->SpawnActor<AStaticMeshActor>(FVector(0, 0, -5), FRotator::ZeroRotator);
    Ground->GetStaticMeshComponent()->SetStaticMesh(LoadObject<UStaticMesh>(nullptr, TEXT("/Engine/BasicShapes/Cube.Cube")));
    Ground->GetStaticMeshComponent()->SetMaterial(0, Grey);
    Ground->SetActorScale3D(FVector(12, 12, 0.1));
    auto* Light = World->SpawnActor<ADirectionalLight>(FVector::ZeroVector, FRotator(-45, 60, 0));
    // ADirectionalLight's CDO has a -46 degree root rotation. Set the final
    // transform explicitly instead of composing a spawn rotation with it.
    Light->SetActorRotation(FRotator(-45, 60, 0));
    Light->GetLightComponent()->SetMobility(EComponentMobility::Movable);
    Light->GetLightComponent()->SetIntensity(5.0f);
    if (!UEditorLoadingAndSavingUtils::SaveMap(World, TEXT("/Game/HdUETest"))) return 2;
    UE_LOG(LogTemp, Display, TEXT("hdUE test map created at /Game/HdUETest"));
    return 0;
}

