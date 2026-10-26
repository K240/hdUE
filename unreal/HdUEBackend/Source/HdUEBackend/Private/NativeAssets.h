#pragma once
#include "CoreMinimal.h"
#include "Engine/StreamableManager.h"
#include "UObject/StrongObjectPtr.h"
#include "hdue/asset_api.h"
class UWorld;
class ULevelStreamingDynamic;
class AActor;
class FHdueNativeAssets {
public:
    explicit FHdueNativeAssets(UWorld* InWorld) : World(InWorld) {}
    ~FHdueNativeAssets();
    HdueResult Upsert(const HdueAssetDesc& Desc, FString& Error);
    void Remove(const FString& Id);
    void Reset();
    void Tick();
    void GetSceneStatus(HdueAssetSceneStatus& Status) const;
    HdueResult GetStatus(const FString& Id, HdueAssetStatus& Status) const;
    TArray<AActor*> GetActors() const;
private:
    struct FEntry {
        FString Path, Error;
        uint32 Kind = HDUE_ASSET_UNKNOWN, State = HDUE_ASSET_LOADING;
        FTransform Transform;
        bool Visible = true;
        double Started = 0;
        uint64 ReadyFrame = 0;
        TSharedPtr<FStreamableHandle> Load;
        TStrongObjectPtr<UObject> Asset;
        TStrongObjectPtr<ULevelStreamingDynamic> Level;
        TWeakObjectPtr<AActor> Actor;
    };
    UWorld* World;
    FStreamableManager Streamable;
    TMap<FString, TUniquePtr<FEntry>> Entries;
    TArray<TStrongObjectPtr<ULevelStreamingDynamic>> Retiring;
    static void Actors(const FEntry& Entry, TArray<AActor*>& Out);
    static void Stats(const FEntry& Entry, HdueAssetStatus& Status);
};
