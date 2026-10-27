#pragma once
#include "CoreMinimal.h"
#include "hdue/bridge.h"
#include "hdue/material_api.h"
#include "hdue/surface_api.h"
#include "RenderCommandFence.h"
#include "UObject/StrongObjectPtr.h"
#include "NativeAssets.h"
class UWorld;
class AActor;
class UMaterialInterface;
class UTexture2D;
class UMaterialInstanceDynamic;
struct FNativePickGeometry;
class FHdueLighting;

class FHdueScene {
public:
    explicit FHdueScene(UWorld* InWorld);
    ~FHdueScene();
    bool Apply(const HdueSceneBatch& Batch, FString& Error);
    void UpdateCameraLights(const FVector& Eye, const FVector& Target, const FVector& Up);
    HdueResult SetMaterialParameters(const HdueMaterialUpdate& Update, FString& Error);
    HdueResult GetMaterialStats(const char* Id, HdueMaterialStats& Stats, FString& Error);
    HdueResult ApplySurface(const HdueSurfaceBatch& Batch, FString& Error);
    HdueSurfaceStatus SurfaceStatus() const;
    bool SurfaceReady() const;
    TArray<TStrongObjectPtr<UTexture2D>> GatherVirtualTextures() const;
    bool HasRefractiveMaterials() const;
    HdueResult PickNative(const HdueNativeRay& Ray, HdueNativeHit& Hit, FString& ObjectId, FString& Error);
    FHdueNativeAssets Assets;
    TUniquePtr<FHdueLighting> Lighting;
private:
    UWorld* World;
    TMap<FString, AActor*> Actors;
    TMap<FString, FVector> CameraLightDirections;
    TMap<FString, FString> MeshMaterials;
    TMap<FString, TStrongObjectPtr<UMaterialInterface>> Materials;
    struct FWet {
        FString Asset;
        UMaterialInterface* Base = nullptr;
        TStrongObjectPtr<UMaterialInstanceDynamic> Material;
        TArray<float> Mask;
        uint64 Revision = 0;
    };
    TMap<FString, TSharedPtr<FWet>> WetBindings;
    TMap<FString, uint64> GeometryRevisions;
    FRenderCommandFence SurfaceFence;
    uint64 MaskUploads = 0, MaskBytes = 0, GeometryUploads = 0, SurfaceUpdates = 0;
    TMap<FString, TSharedPtr<FNativePickGeometry>> NativePickGeometry;
    void Remove(const FString& Id);
    void BindMaterials();
};
