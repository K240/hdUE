#include "HdUEInspectAssetsCommandlet.h"
#include "AssetRegistry/AssetRegistryModule.h"
#include "Components/StaticMeshComponent.h"
#include "Components/LightComponentBase.h"
#include "Engine/StaticMesh.h"
#include "Engine/World.h"
#include "Engine/Blueprint.h"
#include "Materials/MaterialInterface.h"
#include "EngineUtils.h"
#include "FileHelpers.h"
#include "Dom/JsonObject.h"
#include "Serialization/JsonSerializer.h"
#include "Misc/FileHelper.h"
#include "Misc/Parse.h"
#include "Misc/PackageName.h"
#include "UObject/LinkerLoad.h"

UHdUEInspectAssetsCommandlet::UHdUEInspectAssetsCommandlet() {
    IsClient = false; IsServer = false; IsEditor = true; LogToConsole = true;
}
namespace {
TSharedPtr<FJsonObject> BoundsJson(const FBox& Box) {
    auto O = MakeShared<FJsonObject>();
    if (Box.IsValid) {
        TArray<TSharedPtr<FJsonValue>> Min, Max;
        for (int I = 0; I < 3; ++I) { Min.Add(MakeShared<FJsonValueNumber>(Box.Min[I])); Max.Add(MakeShared<FJsonValueNumber>(Box.Max[I])); }
        O->SetArrayField(TEXT("min"), Min); O->SetArrayField(TEXT("max"), Max);
    }
    return O;
}
void MeshInfo(const TSharedPtr<FJsonObject>& O, UStaticMesh* Mesh) {
    O->SetObjectField(TEXT("bounds_cm"),BoundsJson(Mesh->GetBoundingBox()));
    O->SetBoolField(TEXT("nanite"),Mesh->GetNaniteSettings().bEnabled);
    TArray<TSharedPtr<FJsonValue>> Materials;
    for (const auto& Slot : Mesh->GetStaticMaterials()) Materials.Add(MakeShared<FJsonValueString>(Slot.MaterialInterface ? Slot.MaterialInterface->GetPathName() : TEXT("")));
    O->SetArrayField(TEXT("default_materials"),Materials);
}
}
int32 UHdUEInspectAssetsCommandlet::Main(const FString& Params) {
    FString Output, Map = TEXT("/Game/HdUETest.HdUETest");
    if (!FParse::Value(*Params, TEXT("Output="), Output)) return 1;
    FParse::Value(*Params, TEXT("InspectMap="), Map);
    auto& Registry = FModuleManager::LoadModuleChecked<FAssetRegistryModule>(TEXT("AssetRegistry")).Get();
    Registry.SearchAllAssets(true);
    TArray<FAssetData> Assets; Registry.GetAssetsByPath(FName(TEXT("/Game")), Assets, true);
    auto Root = MakeShared<FJsonObject>();
    auto Entries = MakeShared<FJsonObject>();
    for (const auto& A : Assets) {
        auto O = MakeShared<FJsonObject>();
        O->SetStringField(TEXT("class"), A.AssetClassPath.ToString());
        const FString C = A.AssetClassPath.GetAssetName().ToString();
        O->SetStringField(TEXT("kind"), C == TEXT("World") ? TEXT("level") :
            C == TEXT("StaticMesh") ? TEXT("static_mesh") : C == TEXT("SkeletalMesh") ? TEXT("skeletal_mesh") :
            C == TEXT("Blueprint") ? TEXT("blueprint") : TEXT("resource"));
        Entries->SetObjectField(A.GetSoftObjectPath().ToString(), O);
    }
    const FString Package = FSoftObjectPath(Map).GetLongPackageName();
    auto* World = UEditorLoadingAndSavingUtils::LoadMap(FPackageName::LongPackageNameToFilename(Package, FPackageName::GetMapPackageExtension()));
    if (!World) { UE_LOG(LogTemp, Error, TEXT("Cannot inspect %s"), *Map); return 2; }
    FBox WorldBounds(ForceInit);
    int Actors = 0, Meshes = 0, Lights = 0;
    TMap<FString, int> Classes;
    for (TActorIterator<AActor> It(World); It; ++It) {
        ++Actors; ++Classes.FindOrAdd(It->GetClass()->GetPathName());
        TArray<UStaticMeshComponent*> Components; It->GetComponents(Components);
        for (auto* C : Components) {
            if (!C->GetStaticMesh()) continue;
            ++Meshes;
            const FString Path = C->GetStaticMesh()->GetPathName();
            if (Entries->HasField(Path)) {
                const auto O = Entries->GetObjectField(Path);
                MeshInfo(O,C->GetStaticMesh());
                O->SetBoolField(TEXT("used_in_level"), true);
            }
            if (!It->IsHidden() && C->IsVisible()) WorldBounds += C->Bounds.GetBox();
        }
        TArray<ULightComponentBase*> L; It->GetComponents(L); Lights += L.Num();
    }
    auto Level = Entries->GetObjectField(Map);
    Level->SetObjectField(TEXT("bounds_cm"), BoundsJson(WorldBounds));
    Level->SetNumberField(TEXT("actors"), Actors); Level->SetNumberField(TEXT("meshes"), Meshes); Level->SetNumberField(TEXT("lights"), Lights);
    Level->SetBoolField(TEXT("world_partition"), World->IsPartitionedWorld());
    auto ClassCounts = MakeShared<FJsonObject>();
    for (const auto& C : Classes) ClassCounts->SetNumberField(C.Key, C.Value);
    Level->SetObjectField(TEXT("actor_classes"), ClassCounts);
    TArray<TSharedPtr<FJsonValue>> MeshActors;
    for (TActorIterator<AActor> It(World); It; ++It) {
        TArray<UMeshComponent*> Components; It->GetComponents(Components);
        if (Components.IsEmpty()) continue;
        FBox Box(ForceInit); TSet<FString> MaterialPaths;
        for (auto* C : Components) {
            Box+=C->Bounds.GetBox();
            for (int I=0; I<C->GetNumMaterials(); ++I) if (auto* M=C->GetMaterial(I)) MaterialPaths.Add(M->GetPathName());
        }
        auto O=MakeShared<FJsonObject>(); O->SetStringField(TEXT("label"),It->GetActorLabel());
        O->SetStringField(TEXT("class"),It->GetClass()->GetPathName()); O->SetObjectField(TEXT("bounds_cm"),BoundsJson(Box));
        TArray<TSharedPtr<FJsonValue>> M; for (const auto& P : MaterialPaths) M.Add(MakeShared<FJsonValueString>(P));
        O->SetArrayField(TEXT("materials"),M); MeshActors.Add(MakeShared<FJsonValueObject>(O));
    }
    Level->SetArrayField(TEXT("mesh_actors"),MeshActors);
    TArray<TSharedPtr<FJsonValue>> Missing;
    if (const auto* Linker=World->GetOutermost()->GetLinker()) for (const auto& Import : Linker->ImportMap) {
        const FString Name=Import.ObjectName.ToString();
        if (Name.StartsWith(TEXT("/Script/")) && !FindPackage(nullptr,*Name)) Missing.Add(MakeShared<FJsonValueString>(Name));
    }
    Level->SetArrayField(TEXT("unavailable_script_packages"),Missing);
    FString InspectPaths;
    if (FParse::Value(*Params,TEXT("InspectAsset="),InspectPaths,false)) {
        TArray<FString> Paths; InspectPaths.ParseIntoArray(Paths,TEXT(","),true);
        for (const auto& Path : Paths) if (Entries->HasField(Path)) {
            auto* Object=LoadObject<UObject>(nullptr,*Path);
            if (auto* Mesh=Cast<UStaticMesh>(Object)) MeshInfo(Entries->GetObjectField(Path),Mesh);
            else if (auto* BP=Cast<UBlueprint>(Object); BP && BP->GeneratedClass && BP->GeneratedClass->IsChildOf(AActor::StaticClass())) {
                auto* A=World->SpawnActor<AActor>(BP->GeneratedClass,FTransform::Identity);
                if (A) { Entries->GetObjectField(Path)->SetObjectField(TEXT("bounds_cm"),BoundsJson(A->GetComponentsBoundingBox(true))); World->DestroyActor(A); }
            }
        }
    }
    Root->SetNumberField(TEXT("version"), 1); Root->SetObjectField(TEXT("assets"), Entries);
    FString Json; const auto Writer = TJsonWriterFactory<>::Create(&Json);
    FJsonSerializer::Serialize(Root, Writer);
    if (!FFileHelper::SaveStringToFile(Json, *Output)) return 3;
    UE_LOG(LogTemp, Display, TEXT("hdUE inspected %d assets; level actors=%d meshes=%d lights=%d partitioned=%d"), Assets.Num(), Actors, Meshes, Lights, World->IsPartitionedWorld());
    return 0;
}
