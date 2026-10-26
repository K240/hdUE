#include "VirtualTextureResidency.h"
#if !WITH_EDITOR
#include "Engine/Texture2D.h"
#include "TextureResource.h"
#include "Misc/ScopeLock.h"
#include "VT/VirtualTextureSystem.h"
#include "VT/VirtualTextureProducer.h"
#include "VT/VirtualTexturePhysicalSpace.h"
#include "VT/AllocatedVirtualTexture.h"
#include "VT/VirtualTextureSpace.h"
#include "VT/TexturePageMap.h"
#include "VT/TexturePagePool.h"
#include "Async/Mutex.h"
#include "VirtualTextureDefinitions.h"

FHdueVirtualTextureResidency::FHdueVirtualTextureResidency(TArray<TStrongObjectPtr<UTexture2D>>&& InTextures,
    uint64 Generation, const HdueResidencyOptions& InOptions)
    : Textures(MoveTemp(InTextures)), Options(InOptions), Started(FPlatformTime::Seconds()) {
    Progress.struct_size = sizeof(Progress);
    Progress.generation = Generation;
    Progress.textures = Textures.Num();
    Progress.phase = Textures.IsEmpty() ? HDUE_RESIDENCY_READY : HDUE_RESIDENCY_PREPARING;
    for (const auto& Texture : Textures) {
        auto* Resource = Texture->GetResource();
        auto* VT = Resource ? Resource->GetVirtualTexture2DResource() : nullptr;
        if (!VT) { Fail(TEXT("Virtual texture resource is not ready; no Path Tracer samples were started.")); break; }
        Handles.AddUnique(VT->GetProducerHandle());
    }
}
FHdueVirtualTextureResidency::~FHdueVirtualTextureResidency() { check(Locks.IsEmpty()); }
HdueResidencyStatus FHdueVirtualTextureResidency::Status() const { FScopeLock Lock(&Mutex); return Progress; }
void FHdueVirtualTextureResidency::Fail(const FString& Message) {
    Progress.phase = HDUE_RESIDENCY_FAILED;
    FTCHARToUTF8 Utf8(*Message);
    const int32 Length = FMath::Min(Utf8.Length(), int32(sizeof(Progress.message) - 1));
    FMemory::Memcpy(Progress.message, Utf8.Get(), Length);
    Progress.message[Length] = 0;
}
void FHdueVirtualTextureResidency::Release() {
    check(IsInRenderingThread());
    auto& System = FVirtualTextureSystem::Get();
    UE::TScopeLock SystemLock(System.Mutex);
    check(!System.bUpdating);
    for (const auto& Tile : Locks) if (auto* Producer = System.FindProducer(Tile.GetProducerHandle())) System.UnlockTile(Tile, Producer);
    Locks.Empty();
    FScopeLock Lock(&Mutex);
    Progress.locked_tiles = 0;
}
void FHdueVirtualTextureResidency::Update() {
    check(IsInRenderingThread());
    FScopeLock Lock(&Mutex);
    if (Progress.phase != HDUE_RESIDENCY_PREPARING) return;
    Progress.preparation_ms = (FPlatformTime::Seconds() - Started) * 1000;
    ++Progress.preparation_frames;
    if (Progress.preparation_ms > Options.timeout_seconds * 1000) {
        Fail(FString::Printf(TEXT("VT residency timed out: %llu/%llu pages, %llu/%llu mappings; Path Tracer has not started."),
            Progress.resident_pages, Progress.required_pages, Progress.ready_mappings, Progress.required_mappings));
        return;
    }
    auto& System = FVirtualTextureSystem::Get();
    UE::TScopeLock SystemLock(System.Mutex);
    check(!System.bUpdating);
    // Bound planning work for sparse or extremely large UDIMs; never wait for I/O here.
    uint32 Budget = 8192;
    while (!Planned && ProducerIndex < Handles.Num() && Budget) {
        auto* Producer = System.FindProducer(Handles[ProducerIndex]);
        if (!Producer) { Fail(TEXT("Virtual texture producer disappeared during preparation.")); return; }
        const uint32 LastLevel = Producer->GetMaxLevel() - FMath::Min(Producer->GetMaxLevel(), FMath::FloorLog2(Producer->GetDescription().TileSize));
        const uint32 Width = FMath::DivideAndRoundUp(Producer->GetWidthInTiles(), 1u << Level);
        const uint32 Height = FMath::DivideAndRoundUp(Producer->GetHeightInTiles(), 1u << Level);
        uint32 Address = FMath::MortonCode2(X) | (FMath::MortonCode2(Y) << 1);
        const uint32 Bias = Producer->GetVirtualTexture()->GetLocalMipBias(Level, Address);
        Address >>= 2 * Bias;
        Tiles.Add(FVirtualTextureLocalTile(Handles[ProducerIndex], Address, Level + Bias));
        --Budget;
        if (++X == Width) { X = 0; if (++Y == Height) { Y = 0; if (++Level > LastLevel) { Level = 0; ++ProducerIndex; } } }
    }
    if (!Planned && ProducerIndex == Handles.Num()) {
        // Count physical groups separately. Other users' locks also consume pool capacity.
        TMap<FVirtualTexturePhysicalSpace*, uint64> Required;
        for (const auto& Tile : Tiles) {
            auto* Producer = System.FindProducer(Tile.GetProducerHandle());
            if (!Producer) { Fail(TEXT("Virtual texture producer disappeared during planning.")); return; }
            for (uint32 Group = 0; Group < Producer->GetNumPhysicalGroups(); ++Group) ++Required.FindOrAdd(Producer->GetPhysicalSpaceForPhysicalGroup(Group));
        }
        Progress.required_pages = 0;
        for (const auto& Entry : Required) Progress.required_pages += Entry.Value;
        if (Options.maximum_pages && Progress.required_pages > Options.maximum_pages) {
            Fail(FString::Printf(TEXT("VT residency budget exceeded: %llu required pages, budget %u. Path Tracer has not started."), Progress.required_pages, Options.maximum_pages)); return;
        }
        for (const auto& Entry : Required) {
            auto& Pool = Entry.Key->GetPagePool();
            TSet<FVirtualTextureLocalTile> AlreadyLocked;
            Pool.GetAllLockedPages(&System, AlreadyLocked);
            uint64 Overlap = 0;
            for (const auto& Tile : AlreadyLocked) if (Tiles.Contains(Tile)) ++Overlap;
            const uint64 Unrelated = Pool.GetNumLockedPages() - FMath::Min<uint64>(Overlap, Pool.GetNumLockedPages());
            if (Entry.Value + Unrelated > Pool.GetNumPages()) {
                Fail(FString::Printf(TEXT("VT physical pool %s is too small: %llu required + %llu other locked pages, capacity %u. Increase the VT pool; Path Tracer has not started."),
                    *Entry.Key->GetFormatString(), Entry.Value, Unrelated, Pool.GetNumPages())); return;
            }
        }
        for (const auto& Tile : Tiles) { System.LockTile(Tile); Locks.Add(Tile); }
        ResidentMasks.SetNumZeroed(Locks.Num());
        Progress.locked_tiles = Locks.Num();
        Planned = true;
        return; // Let UE process the logical locks before recording physical residency.
    }
    if (!Planned) return;
    // Once loaded, our locks prevent eviction. Remember confirmed groups instead
    // of rescanning an unbounded tile set every preview frame.
    Budget = FMath::Min(8192, Locks.Num());
    while (Budget--) {
        const auto& Tile = Locks[ResidencyIndex];
        auto& Mask = ResidentMasks[ResidencyIndex];
        auto* Producer = System.FindProducer(Tile.GetProducerHandle());
        if (!Producer) { Fail(TEXT("Virtual texture producer disappeared while waiting for residency.")); return; }
        for (uint32 Group = 0; Group < Producer->GetNumPhysicalGroups(); ++Group) {
            if (Mask & (1u << Group)) continue;
            const auto& Pool = Producer->GetPhysicalSpaceForPhysicalGroup(Group)->GetPagePool();
            if (Pool.FindPageAddress(Tile.GetProducerHandle(), Group, Tile.Local_vAddress, Tile.Local_vLevel) != ~0u) { Mask |= 1u << Group; ++Progress.resident_pages; }
        }
        if (++ResidencyIndex == Locks.Num()) ResidencyIndex = 0;
    }
    if (Progress.resident_pages != Progress.required_pages) return;
    // Physical residency alone is insufficient: material stacks must map the exact
    // finest available source page too. Let UE create mappings through its normal
    // request path, including mixed-resolution blocks, sparse holes and mip bias.
    TArray<FMappingTarget> Targets;
    for (const auto& Entry : System.AllocatedVTs) {
        auto* VT = Entry.Value;
        for (uint32 Layer = 0; Layer < VT->GetNumPageTableLayers(); ++Layer) {
            const uint32 Index = VT->GetProducerIndexForPageTableLayer(Layer);
            const auto Handle = VT->GetUniqueProducerHandle(Index);
            if (!Handles.Contains(Handle)) continue;
            if (VT->GetAllocatedAdaptiveBias()) { Fail(TEXT("Adaptive VT residency is unsupported; only streaming UDIM textures can start Path Tracer.")); return; }
            if (VT->GetSpaceID() >= VIRTUALTEXTURE_MAX_FEEDBACK_SPACES) { Fail(TEXT("VT page table does not support explicit streaming requests.")); return; }
            Targets.Add({VT, Layer, VT->GetFrameAllocated()});
        }
    }
    if (Targets != MappingTargets) {
        MappingTargets = MoveTemp(Targets);
        MappingIndex = 0; MappingMip = MappingX = MappingY = 0;
        ScanReady = StableFrames = 0;
        Progress.required_mappings = Progress.ready_mappings = 0;
        for (const auto& Target : MappingTargets) for (uint32 Mip = 0; Mip <= Target.VT->GetMaxLevel(); ++Mip)
            Progress.required_mappings += uint64(FMath::Max(1u, Target.VT->GetWidthInTiles() >> Mip)) * FMath::Max(1u, Target.VT->GetHeightInTiles() >> Mip);
    }
    // Sparse UDIM virtual extents can be much larger than the resident tile set.
    // Bound both lookup and request work; do not enqueue the entire rectangle.
    Budget = 8192;
    while (MappingIndex < MappingTargets.Num() && Budget--) {
        const auto& Target = MappingTargets[MappingIndex];
        auto* VT = Target.VT;
        const uint32 Layer = Target.Layer, Index = VT->GetProducerIndexForPageTableLayer(Layer);
        const auto Handle = VT->GetUniqueProducerHandle(Index);
        auto* Producer = System.FindProducer(Handle);
        auto* Physical = VT->GetPhysicalSpaceForPageTableLayer(Layer);
        const uint32 MipBias = VT->GetUniqueProducerMipBias(Index);
        const uint32 MappingLevel = FMath::Max(MappingMip, MipBias);
        uint32 LocalLevel = MappingLevel - MipBias;
        uint32 Address = FMath::MortonCode2((MappingX << MappingMip) >> MappingLevel) | (FMath::MortonCode2((MappingY << MappingMip) >> MappingLevel) << 1);
        const uint32 Bias = Producer->GetVirtualTexture()->GetLocalMipBias(LocalLevel, Address);
        LocalLevel += Bias; Address >>= 2 * Bias;
        const uint32 PhysicalAddress = Physical->GetPagePool().FindPageAddress(Handle, VT->GetProducerPhysicalGroupIndexForPageTableLayer(Layer), Address, LocalLevel);
        const uint32 GlobalX = VT->GetVirtualPageX() + (MappingX << MappingMip), GlobalY = VT->GetVirtualPageY() + (MappingY << MappingMip);
        const uint32 Global = FMath::MortonCode2(GlobalX) | (FMath::MortonCode2(GlobalY) << 1);
        const auto Mapped = System.GetSpace(VT->GetSpaceID())->GetPageMapForPageTableLayer(Layer).FindPagePhysicalSpaceIDAndAddress(MappingMip, Global);
        if (PhysicalAddress != ~0u && Mapped == FPhysicalSpaceIDAndAddress(Physical->GetID(), PhysicalAddress)) ++ScanReady;
        else {
            // Mirrors the pinned UE 5.8.4 EncodePage in VirtualTextureSystem.cpp.
            // Coordinates are mip-relative here, not the full-resolution Morton address.
            const uint32 Packed = (GlobalX >> MappingMip) | ((GlobalY >> MappingMip) << 12) |
                ((MappingMip + UE::HLSL::VirtualTextureFeedbackBias) << 24) | (VT->GetSpaceID() << 28);
            System.RequestedPackedTiles.Add(Packed);
        }
        const uint32 W = FMath::Max(1u, VT->GetWidthInTiles() >> MappingMip), H = FMath::Max(1u, VT->GetHeightInTiles() >> MappingMip);
        if (++MappingX == W) { MappingX = 0; if (++MappingY == H) { MappingY = 0; if (++MappingMip > VT->GetMaxLevel()) { MappingMip = 0; ++MappingIndex; } } }
    }
    Progress.ready_mappings = ScanReady;
    if (!MappingTargets.IsEmpty() && MappingIndex == MappingTargets.Num()) {
        // Two complete scans plus the preview/readback fence cover queued GPU
        // uploads and page-table updates before the Game Thread enables PT.
        if (ScanReady == Progress.required_mappings) {
            if (++StableFrames >= 2) Progress.phase = HDUE_RESIDENCY_READY;
        } else StableFrames = 0;
        ScanReady = 0; MappingIndex = 0;
    }
}
#endif
