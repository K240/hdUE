#pragma once
#include "CoreMinimal.h"
#if !WITH_EDITOR
#include "hdue/residency_api.h"
#include "UObject/StrongObjectPtr.h"
#include "VirtualTexturing.h"
class UTexture2D;
class FAllocatedVirtualTexture;

// Game Thread constructs/reads; Render Thread alone owns producer/page operations.
// The sole engine patch grants this adapter access to the VT system's lock refcounts.
class FHdueVirtualTextureResidency {
public:
    FHdueVirtualTextureResidency(TArray<TStrongObjectPtr<UTexture2D>>&& Textures, uint64 Generation, const HdueResidencyOptions& Options);
    ~FHdueVirtualTextureResidency();
    void Update(); // Render Thread, outside an active VT update.
    void Release(); // Render Thread; balances only our own locks.
    HdueResidencyStatus Status() const;
private:
    TArray<TStrongObjectPtr<UTexture2D>> Textures;
    TArray<FVirtualTextureProducerHandle> Handles;
    TSet<FVirtualTextureLocalTile> Tiles;
    TArray<FVirtualTextureLocalTile> Locks;
    TArray<uint8> ResidentMasks;
    int32 ResidencyIndex = 0;
    HdueResidencyOptions Options;
    mutable FCriticalSection Mutex;
    HdueResidencyStatus Progress{};
    double Started;
    int32 ProducerIndex = 0;
    uint32 Level = 0, X = 0, Y = 0;
    bool Planned = false;
    uint32 StableFrames = 0;
    struct FMappingTarget {
        FAllocatedVirtualTexture* VT;
        uint32 Layer, CreatedFrame;
        bool operator==(const FMappingTarget& Other) const { return VT == Other.VT && Layer == Other.Layer && CreatedFrame == Other.CreatedFrame; }
    };
    TArray<FMappingTarget> MappingTargets;
    int32 MappingIndex = 0;
    uint32 MappingMip = 0, MappingX = 0, MappingY = 0;
    uint64 ScanReady = 0;
    void Fail(const FString& Message);
};
#endif
