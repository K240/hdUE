#pragma once
#include "CoreMinimal.h"
#include "hdue/bridge.h"
#include "hdue/material_api.h"
#include "UObject/StrongObjectPtr.h"
#include "NativeAssets.h"
class UWorld;
class AActor;
class UMaterialInterface;
class UTexture2D;
struct FNativePickGeometry;

class FHdueScene {
public:
    explicit FHdueScene(UWorld* InWorld);
    ~FHdueScene();
    bool Apply(const HdueSceneBatch& Batch, FString& Error);
    void UpdateCameraLights(const FVector& Eye, const FVector& Target, const FVector& Up);
    HdueResult SetMaterialParameters(const HdueMaterialUpdate& Update, FString& Error);
    HdueResult GetMaterialStats(const char* Id, HdueMaterialStats& Stats, FString& Error);
    TArray<TStrongObjectPtr<UTexture2D>> GatherVirtualTextures() const;
    bool HasRefractiveMaterials() const;
    HdueResult PickNative(const HdueNativeRay& Ray, HdueNativeHit& Hit, FString& ObjectId, FString& Error);
    FHdueNativeAssets Assets;
private:
    UWorld* World;
    TMap<FString, AActor*> Actors;
    TMap<FString, FVector> CameraLightDirections;
    TMap<FString, FString> MeshMaterials;
    TMap<FString, TStrongObjectPtr<UMaterialInterface>> Materials;
    TMap<FString, TSharedPtr<FNativePickGeometry>> NativePickGeometry;
    void Remove(const FString& Id);
    void BindMaterials();
};
