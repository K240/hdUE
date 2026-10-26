#include "NativePicking.h"
#if !WITH_EDITOR
#include "Scene.h"
#include "Components/StaticMeshComponent.h"
#include "Components/MeshComponent.h"
#include "DynamicMesh/DynamicMesh3.h"
#include "DynamicMesh/DynamicMeshAABBTree3.h"
#include "Engine/StaticMesh.h"
#include "Materials/Material.h"
#include "MaterialDomain.h"
#include "MaterialShared.h"
#include "RHI.h"
#include "StaticMeshResources.h"

namespace {
HdueResult InspectMaterial(UMaterialInterface* Material, uint32& Flags, FString& Error) {
    Flags = 0;
    if (!Material) Material = UMaterial::GetDefaultMaterial(MD_Surface);
    if (Material->GetNaniteOverride()) { Error = TEXT("CPU picking does not support Nanite override materials."); return HDUE_NOT_SUPPORTED; }
    const FMaterial* Resource = Material->GetMaterialResource(EShaderPlatform(GMaxRHIShaderPlatform));
    if (!Resource) { Error = TEXT("Material shader metadata is not available for picking."); return HDUE_NOT_SUPPORTED; }
    if (Material->GetBlendMode() == BLEND_Opaque && Resource->GetMaterialDomain() == MD_Surface) Flags |= HDUE_PICK_MATERIAL_OPAQUE;
    if (Material->IsTwoSided()) Flags |= HDUE_PICK_MATERIAL_TWO_SIDED;
    if (Resource->HasVertexPositionOffsetConnected() || Resource->HasDisplacementConnected() || Resource->HasPixelDepthOffsetConnected()) Flags |= HDUE_PICK_MATERIAL_DEFORMED;
    return HDUE_OK;
}
}
HdueResult HdueGetPickMaterialInfo(const char* Path, HduePickMaterialInfo& Info, FString& Error) {
    if (!Path || !*Path) return HDUE_INVALID_ARGUMENT;
    auto* Material = LoadObject<UMaterialInterface>(nullptr, UTF8_TO_TCHAR(Path));
    if (!Material) { Error = TEXT("Native material was not found in cooked content."); return HDUE_INVALID_ARGUMENT; }
    return InspectMaterial(Material, Info.flags, Error);
}

struct FNativePickGeometry {
    UE::Geometry::FDynamicMesh3 Mesh;
    UE::Geometry::FDynamicMeshAABBTree3 Tree;
    TArray<int32> MaterialIndices;
    uint32 Flags = HDUE_NATIVE_PICK_LOD0;
    FString Error;
    explicit FNativePickGeometry(UStaticMesh* Source) : Tree(&Mesh, false) {
        if (!Source->bAllowCPUAccess || !Source->NeverStream) {
            Error = TEXT("Native picking requires Allow CPU Access and Never Stream on the cooked StaticMesh."); return;
        }
        auto* Data = Source->GetRenderData();
        if (!Data || Data->LODResources.Num() == 0 || Data->CurrentFirstLODIdx != 0) {
            Error = TEXT("Native StaticMesh LOD0 is not resident."); return;
        }
        TRefCountPtr<FStaticMeshLODResources> LOD = &Data->LODResources[0];
        const auto& Positions = LOD->VertexBuffers.PositionVertexBuffer;
        const auto& Tangents = LOD->VertexBuffers.StaticMeshVertexBuffer;
        if (!Positions.GetVertexData() || !Tangents.GetTangentData() || !LOD->IndexBuffer.GetAllowCPUAccess() || LOD->IndexBuffer.GetNumIndices() == 0) {
            Error = TEXT("Cooked StaticMesh has no readable LOD0 vertex/index data."); return;
        }
        for (const auto& Section : LOD->Sections) for (uint32 T = 0; T < Section.NumTriangles; ++T) {
            FVector3d P[3]; FVector3d Normal(0);
            for (uint32 C = 0; C < 3; ++C) {
                const uint32 V = LOD->IndexBuffer.GetIndex(Section.FirstIndex + T * 3 + C);
                if (V >= Positions.GetNumVertices() || V >= Tangents.GetNumVertices()) { Error = TEXT("Invalid native mesh indices."); return; }
                P[C] = FVector3d(Positions.VertexPosition(V));
                Normal += FVector3d(Tangents.VertexTangentZ(V));
            }
            // GeometryCore defines normals with UE's left-handed winding.
            // Match the cooked normals using that same convention.
            if (FVector3d::DotProduct(UE::Geometry::VectorUtil::Normal(P[0], P[1], P[2]), Normal) < 0) Swap(P[1], P[2]);
            const int32 A = Mesh.AppendVertex(P[0]), B = Mesh.AppendVertex(P[1]), C = Mesh.AppendVertex(P[2]);
            const int32 Triangle = Mesh.AppendTriangle(A, B, C);
            if (Triangle < 0) { Error = TEXT("Could not build native pick triangles."); return; }
            MaterialIndices.Add(Section.MaterialIndex);
        }
        if (Source->HasValidNaniteData()) Flags |= HDUE_NATIVE_PICK_NANITE_FALLBACK;
        Tree.Build();
    }
};

HdueResult FHdueScene::PickNative(const HdueNativeRay& Request, HdueNativeHit& Hit, FString& ObjectId, FString& Error) {
    using namespace UE::Geometry;
    const FVector3d Origin(Request.origin[0], Request.origin[1], Request.origin[2]);
    const FVector3d Direction = FVector3d(Request.direction[0], Request.direction[1], Request.direction[2]).GetSafeNormal();
    double Nearest = Request.maximum_cm;
    for (const auto& Entry : Actors) {
        AActor* Actor = Entry.Value;
        if (!IsValid(Actor) || Actor->IsHidden()) continue;
        auto* Component = Actor->FindComponentByClass<UMeshComponent>();
        if (!Component) continue;
        TArray<uint32> MaterialFlags;
        for (int32 I = 0; I < Component->GetNumMaterials(); ++I) {
            uint32 Flags = 0;
            const auto Code = InspectMaterial(Component->GetMaterial(I), Flags, Error);
            if (Code != HDUE_OK) return Code;
            if (!(Flags & HDUE_PICK_MATERIAL_OPAQUE) || (Flags & HDUE_PICK_MATERIAL_DEFORMED)) {
                Error = TEXT("Picking requires opaque materials without vertex/displacement deformation."); return HDUE_NOT_SUPPORTED;
            }
            MaterialFlags.Add(Flags);
        }
        auto* Static = Cast<UStaticMeshComponent>(Component);
        if (!Static || !Static->GetStaticMesh()) continue;
        UStaticMesh* Source = Static->GetStaticMesh();
        auto& Geometry = NativePickGeometry.FindOrAdd(Source->GetPathName());
        if (!Geometry) Geometry = MakeShared<FNativePickGeometry>(Source);
        if (!Geometry->Error.IsEmpty()) { Error = Geometry->Error; return HDUE_NOT_SUPPORTED; }
        const FMatrix LocalToWorld = Component->GetComponentTransform().ToMatrixWithScale();
        if (FMath::Abs(LocalToWorld.Determinant()) < UE_SMALL_NUMBER) continue;
        const FMatrix Inverse = LocalToWorld.Inverse();
        const FVector3d LocalOrigin(Inverse.TransformPosition(Origin));
        const FVector3d Unnormalized(Inverse.TransformVector(Direction));
        const double Scale = Unnormalized.Length();
        if (Scale <= UE_SMALL_NUMBER) continue;
        const FRay3d Ray(LocalOrigin, Unnormalized / Scale);
        const double Minimum = Request.minimum_cm * Scale;
        IMeshSpatial::FQueryOptions Options(Nearest * Scale);
        Options.TriangleFilterF = [&](int32 T) {
            const FVector3d Normal = Geometry->Mesh.GetTriNormal(T);
            const double Denominator = FVector3d::DotProduct(Ray.Direction, Normal);
            const int32 Slot = Geometry->MaterialIndices[T];
            const bool TwoSided = MaterialFlags.IsValidIndex(Slot) && (MaterialFlags[Slot] & HDUE_PICK_MATERIAL_TWO_SIDED);
            if (FMath::Abs(Denominator) < 1e-15 || (!TwoSided && Denominator >= 0)) return false;
            const auto Vertices = Geometry->Mesh.GetTriangle(T);
            const double Distance = FVector3d::DotProduct(Geometry->Mesh.GetVertex(Vertices.A) - Ray.Origin, Normal) / Denominator;
            return Distance >= Minimum;
        };
        double Distance = 0; int32 Triangle = -1;
        if (!Geometry->Tree.FindNearestHitTriangle(Ray, Distance, Triangle, Options)) continue;
        Nearest = Distance / Scale;
        const FVector3d Point = Origin + Direction * Nearest;
        FVector3d Normal(Inverse.GetTransposed().TransformVector(Geometry->Mesh.GetTriNormal(Triangle)));
        Normal.Normalize();
        const int32 Slot = Geometry->MaterialIndices[Triangle];
        if (MaterialFlags.IsValidIndex(Slot) && (MaterialFlags[Slot] & HDUE_PICK_MATERIAL_TWO_SIDED) && FVector3d::DotProduct(Normal, Direction) > 0) Normal = -Normal;
        Hit.hit = 1; Hit.flags = Geometry->Flags; Hit.distance_cm = Nearest; ObjectId = Entry.Key;
        for (int32 I = 0; I < 3; ++I) { Hit.point[I] = Point[I]; Hit.normal[I] = Normal[I]; }
    }
    return HDUE_OK;
}
#endif
