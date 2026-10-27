#include "Scene.h"
#include "Lighting.h"
#include "Components/DynamicMeshComponent.h"
#include "Components/StaticMeshComponent.h"
#include "Components/DirectionalLightComponent.h"
#include "Components/PointLightComponent.h"
#include "Components/RectLightComponent.h"
#include "DynamicMesh/DynamicMesh3.h"
#include "DynamicMesh/DynamicMeshAttributeSet.h"
#include "Engine/StaticMesh.h"
#include "Engine/Texture2D.h"
#include "Engine/World.h"
#include "EngineUtils.h"
#include "ImageUtils.h"
#include "Materials/MaterialInstanceDynamic.h"
#include "Materials/Material.h"
#include "MaterialShared.h"
#include "RHI.h"

FHdueScene::FHdueScene(UWorld* InWorld) : Assets(InWorld), Lighting(MakeUnique<FHdueLighting>(InWorld)), World(InWorld) {}
FHdueScene::~FHdueScene() {
    for (auto& Entry : Actors) if (IsValid(Entry.Value)) World->DestroyActor(Entry.Value);
}
bool FHdueScene::HasRefractiveMaterials() const {
    for (const auto& Entry : Materials) {
        const auto* Base = Entry.Value->GetMaterial();
        if (Base && Base->BlendMode == BLEND_TranslucentColoredTransmittance &&
            (Base->RefractionMethod == RM_IndexOfRefraction || Base->RefractionMethod == RM_IndexOfRefractionFromF0)) return true;
    }
    for (auto* Actor : Assets.GetActors()) {
        TArray<UMeshComponent*> Meshes; Actor->GetComponents(Meshes);
        for (const auto* Mesh : Meshes) for (int32 I=0; I<Mesh->GetNumMaterials(); ++I) {
            const auto* Material=Mesh->GetMaterial(I); const auto* Base=Material?Material->GetMaterial():nullptr;
            if (Base && Base->BlendMode==BLEND_TranslucentColoredTransmittance &&
                (Base->RefractionMethod==RM_IndexOfRefraction || Base->RefractionMethod==RM_IndexOfRefractionFromF0)) return true;
        }
    }
    return false;
}
TArray<TStrongObjectPtr<UTexture2D>> FHdueScene::GatherVirtualTextures() const {
    TSet<UTexture2D*> Unique;
    TArray<TStrongObjectPtr<UTexture2D>> Result;
    TArray<AActor*> All = Assets.GetActors();
    for (const auto& Entry : Actors) All.Add(Entry.Value);
    for (auto* Actor : All) {
        if (!IsValid(Actor) || Actor->IsHidden()) continue;
        TArray<UMeshComponent*> Meshes; Actor->GetComponents(Meshes);
        for (const auto* Mesh : Meshes) {
        if (!Mesh->IsVisible()) continue;
        for (int32 Index = 0; Index < Mesh->GetNumMaterials(); ++Index) {
            const auto* Material = Mesh->GetMaterial(Index);
            if (!Material) continue;
            TArray<UTexture*> Textures;
            Material->GetUsedTextures(Textures);
            for (UTexture* Texture : Textures) {
                auto* Texture2D = Cast<UTexture2D>(Texture);
                if (Texture2D && Texture2D->VirtualTextureStreaming && !Unique.Contains(Texture2D)) {
                    Unique.Add(Texture2D);
                    Result.Emplace(Texture2D);
                }
            }
        }
        }
    }
    return Result;
}
void FHdueScene::Remove(const FString& Id) {
    Lighting->Remove(Id);
    CameraLightDirections.Remove(Id);
    if (AActor** Actor = Actors.Find(Id)) {
        if (IsValid(*Actor)) World->DestroyActor(*Actor);
        Actors.Remove(Id);
    }
    Materials.Remove(Id);
    MeshMaterials.Remove(Id);
}
void FHdueScene::BindMaterials() {
    for (const auto& Entry : MeshMaterials) {
        AActor** Actor = Actors.Find(Entry.Key);
        if (!Actor || !IsValid(*Actor)) continue;
        auto* Component = (*Actor)->FindComponentByClass<UMeshComponent>();
        if (const auto* Material = Materials.Find(Entry.Value)) Component->SetMaterial(0, Material->Get());
    }
}
bool FHdueScene::Apply(const HdueSceneBatch& Batch, FString& Error) {
    if ((Batch.material_count && !Batch.materials) || (Batch.mesh_count && !Batch.meshes) ||
        (Batch.light_count && !Batch.lights) || (Batch.removed_count && !Batch.removed_ids)) {
        Error = TEXT("Missing scene batch data."); return false;
    }
    // Validate all mesh array bounds before mutating the scene.
    for (uint32 Index = 0; Index < Batch.mesh_count; ++Index) {
        const auto& Mesh = Batch.meshes[Index];
        if (!Mesh.id || Mesh.index_count % 3 || Mesh.vertex_count > 10000000 ||
            (Mesh.vertex_count && !Mesh.positions) || (Mesh.index_count && !Mesh.indices)) {
            Error = TEXT("Invalid mesh descriptor."); return false;
        }
        for (uint32 I = 0; I < Mesh.index_count; ++I) {
            if (Mesh.indices[I] >= Mesh.vertex_count) { Error = TEXT("Mesh index out of range."); return false; }
        }
    }
    if (Batch.reset_scene) {
        Lighting->Clear();
        Assets.Reset();
        for (const auto& Entry : Actors) if (IsValid(Entry.Value)) World->DestroyActor(Entry.Value);
        // Preserve the native host's bootstrap map until an explicit reset.
        // Never sweep another streamed Level's actors during a scene update.
        TArray<AActor*> Fixtures;
        for (TActorIterator<AActor> It(World); It; ++It)
            if (It->GetLevel()==World->PersistentLevel && (It->FindComponentByClass<UMeshComponent>() || It->FindComponentByClass<ULightComponent>())) Fixtures.Add(*It);
        for (auto* A : Fixtures) World->DestroyActor(A);
        Actors.Empty(); CameraLightDirections.Empty(); MeshMaterials.Empty(); Materials.Empty();
    }
    for (uint32 Index = 0; Index < Batch.removed_count; ++Index) {
        if (Batch.removed_ids[Index]) Remove(UTF8_TO_TCHAR(Batch.removed_ids[Index]));
    }
    for (uint32 Index = 0; Index < Batch.material_count; ++Index) {
        const auto& Desc = Batch.materials[Index];
        if (!Desc.id) { Error = TEXT("Material has no ID."); return false; }
        UMaterialInterface* Material = nullptr;
        if (Desc.native_asset && *Desc.native_asset) {
            Material = LoadObject<UMaterialInterface>(nullptr, UTF8_TO_TCHAR(Desc.native_asset));
            if (Material) Material = UMaterialInstanceDynamic::Create(Material, World);
        } else {
            if (!FMath::IsFinite(Desc.base_color[3])) { Error = TEXT("Material opacity must be finite."); return false; }
            const float Opacity = FMath::Clamp(Desc.base_color[3], 0.f, 1.f);
            const TCHAR* ParentPath = Opacity < 1.f
                ? TEXT("/Game/HdUE/M_PreviewSurfaceTranslucent.M_PreviewSurfaceTranslucent")
                : TEXT("/Game/HdUE/M_PreviewSurface.M_PreviewSurface");
            auto* Parent = LoadObject<UMaterialInterface>(nullptr, ParentPath);
            if (Parent) {
                auto* Instance = UMaterialInstanceDynamic::Create(Parent, World);
                Instance->SetVectorParameterValue(TEXT("BaseColor"), FLinearColor(Desc.base_color[0], Desc.base_color[1], Desc.base_color[2], Desc.base_color[3]));
                Instance->SetScalarParameterValue(TEXT("Metallic"), FMath::Clamp(Desc.metallic, 0.f, 1.f));
                Instance->SetScalarParameterValue(TEXT("Roughness"), FMath::Clamp(Desc.roughness, 0.f, 1.f));
                if (Opacity < 1.f) Instance->SetScalarParameterValue(TEXT("Opacity"), Opacity);
                if (Desc.base_color_texture && *Desc.base_color_texture) {
                    UTexture2D* Texture = FImageUtils::ImportFileAsTexture2D(UTF8_TO_TCHAR(Desc.base_color_texture));
                    if (!Texture) { Error = TEXT("Failed to import base color texture."); return false; }
                    Instance->SetTextureParameterValue(TEXT("BaseColorTexture"), Texture);
                }
                Material = Instance;
            }
        }
        if (!Material) { Error = TEXT("Material asset missing. Prepare/cook content or check the native asset path."); return false; }
        Materials.Add(UTF8_TO_TCHAR(Desc.id), TStrongObjectPtr<UMaterialInterface>(Material));
    }
    for (uint32 Index = 0; Index < Batch.mesh_count; ++Index) {
        const auto& Desc = Batch.meshes[Index];
        const FString Id = UTF8_TO_TCHAR(Desc.id);
        AActor* Actor = Actors.FindRef(Id);
        const bool Native = Desc.native_asset && *Desc.native_asset;
        if (Actor && ((Actor->FindComponentByClass<UStaticMeshComponent>() != nullptr) != Native)) {
            Remove(Id); Actor = nullptr;
        }
        if (!Actor) {
            Actor = World->SpawnActor<AActor>();
            Actors.Add(Id, Actor);
            if (Native) {
                auto* Component = NewObject<UStaticMeshComponent>(Actor);
                Component->SetMobility(EComponentMobility::Movable);
                Actor->SetRootComponent(Component);
                Actor->AddInstanceComponent(Component);
                Component->RegisterComponent();
            } else {
                auto* Component = NewObject<UDynamicMeshComponent>(Actor);
                Component->SetMobility(EComponentMobility::Movable);
                Component->SetEnableRaytracing(true);
                Component->SetTangentsType(EDynamicMeshComponentTangentsMode::AutoCalculated);
                Actor->SetRootComponent(Component);
                Actor->AddInstanceComponent(Component);
                Component->RegisterComponent();
            }
        }
        Actor->SetActorHiddenInGame(!Desc.visible);
        FMatrix Matrix;
        for (int R = 0; R < 4; ++R) for (int C = 0; C < 4; ++C) Matrix.M[R][C] = Desc.transform[R * 4 + C];
        Actor->SetActorTransform(FTransform(Matrix));
        if (Native) {
            auto* Mesh = LoadObject<UStaticMesh>(nullptr, UTF8_TO_TCHAR(Desc.native_asset));
            if (!Mesh) { Error = TEXT("Native StaticMesh not found in cooked content."); return false; }
            if ((Desc.flags & HDUE_MESH_REQUIRE_NANITE) && !Mesh->HasValidNaniteData()) { Error = TEXT("Native asset has no cooked Nanite data."); return false; }
            UE_LOG(LogTemp, Display, TEXT("hdUE native mesh %s: NaniteData=%d"), *Mesh->GetPathName(), Mesh->HasValidNaniteData());
            Actor->FindComponentByClass<UStaticMeshComponent>()->SetStaticMesh(Mesh);
        } else {
            using namespace UE::Geometry;
            FDynamicMesh3 Mesh;
            Mesh.EnableAttributes();
            Mesh.Attributes()->SetNumNormalLayers(1);
            Mesh.Attributes()->SetNumUVLayers(1);
            auto* NormalOverlay = Mesh.Attributes()->PrimaryNormals();
            auto* UVOverlay = Mesh.Attributes()->PrimaryUV();
            for (uint32 V = 0; V < Desc.vertex_count; ++V) Mesh.AppendVertex(FVector3d(Desc.positions[V * 3], Desc.positions[V * 3 + 1], Desc.positions[V * 3 + 2]));
            for (uint32 I = 0; I < Desc.index_count; I += 3) {
                const FIndex3i Vertices(Desc.indices[I], Desc.indices[I + 1], Desc.indices[I + 2]);
                const int32 Triangle = Mesh.AppendTriangle(Vertices);
                if (Triangle < 0) continue;
                const FVector3f FaceNormal = FVector3f(Mesh.GetTriNormal(Triangle));
                FIndex3i NormalIds, UVIds;
                for (int C = 0; C < 3; ++C) {
                    const auto V = Vertices[C];
                    const FVector3f Normal = Desc.normals ? FVector3f(Desc.normals[V * 3], Desc.normals[V * 3 + 1], Desc.normals[V * 3 + 2]) : FaceNormal;
                    const FVector2f UV = Desc.uv ? FVector2f(Desc.uv[V * 2], Desc.uv[V * 2 + 1]) : FVector2f::ZeroVector;
                    NormalIds[C] = NormalOverlay->AppendElement(Normal);
                    UVIds[C] = UVOverlay->AppendElement(UV);
                }
                NormalOverlay->SetTriangle(Triangle, NormalIds);
                UVOverlay->SetTriangle(Triangle, UVIds);
            }
            Actor->FindComponentByClass<UDynamicMeshComponent>()->SetMesh(MoveTemp(Mesh));
        }
        MeshMaterials.Add(Id, Desc.material_id ? UTF8_TO_TCHAR(Desc.material_id) : TEXT(""));
    }
    for (uint32 Index = 0; Index < Batch.light_count; ++Index) {
        const auto& Desc = Batch.lights[Index];
        if (!Desc.id || Desc.type > HDUE_CAMERA_DISTANT_LIGHT) { Error = TEXT("Unsupported light."); return false; }
        const FString Id = UTF8_TO_TCHAR(Desc.id);
        Remove(Id);
        auto* Actor = World->SpawnActor<AActor>();
        Actors.Add(Id, Actor);
        ULightComponent* Light = (Desc.type == HDUE_DISTANT_LIGHT || Desc.type == HDUE_CAMERA_DISTANT_LIGHT) ? static_cast<ULightComponent*>(NewObject<UDirectionalLightComponent>(Actor)) :
            Desc.type == HDUE_SPHERE_LIGHT ? static_cast<ULightComponent*>(NewObject<UPointLightComponent>(Actor)) : static_cast<ULightComponent*>(NewObject<URectLightComponent>(Actor));
        Light->SetMobility(EComponentMobility::Movable);
        Actor->SetRootComponent(Light); Actor->AddInstanceComponent(Light);
        Light->RegisterComponent();
        Light->SetLightColor(FLinearColor(Desc.color[0], Desc.color[1], Desc.color[2]));
        Light->SetIntensity(FMath::Max(0.f, Desc.intensity));
        Actor->SetActorLocation(FVector(Desc.position[0], Desc.position[1], Desc.position[2]));
        Actor->SetActorRotation(FVector(Desc.direction[0], Desc.direction[1], Desc.direction[2]).Rotation());
        Actor->SetActorHiddenInGame(!Desc.visible);
        if (Desc.type == HDUE_CAMERA_DISTANT_LIGHT)
            CameraLightDirections.Add(Id, FVector(Desc.direction[0], Desc.direction[1], Desc.direction[2]));
        if (auto* Point = Cast<UPointLightComponent>(Light)) { Point->SetAttenuationRadius(100000); Point->SetSourceRadius(FMath::Max(0.1f, Desc.width_cm)); }
        if (auto* Rect = Cast<URectLightComponent>(Light)) { Rect->SetSourceWidth(Desc.width_cm); Rect->SetSourceHeight(Desc.height_cm); Rect->SetAttenuationRadius(100000); }
    }
    BindMaterials();
    return true;
}
void FHdueScene::UpdateCameraLights(const FVector& Eye, const FVector& Target, const FVector& Up) {
    Lighting->UpdateCamera(Eye,Target,Up);
    const FVector Forward = (Target - Eye).GetSafeNormal();
    const FVector Right = FVector::CrossProduct(Up, Forward).GetSafeNormal();
    const FVector CameraUp = FVector::CrossProduct(Forward, Right).GetSafeNormal();
    for (const auto& Entry : CameraLightDirections) {
        AActor* Actor = Actors.FindRef(Entry.Key);
        if (!IsValid(Actor)) continue;
        const FVector Direction = (Forward * Entry.Value.X + Right * Entry.Value.Y + CameraUp * Entry.Value.Z).GetSafeNormal();
        Actor->SetActorLocationAndRotation(Eye, Direction.Rotation());
    }
}
