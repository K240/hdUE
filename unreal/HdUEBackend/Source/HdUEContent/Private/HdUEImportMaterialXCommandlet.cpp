#include "HdUEImportMaterialXCommandlet.h"
#include "HdUEMaterialXAssetsPipeline.h"
#include "InterchangeManager.h"
#include "InterchangeSourceData.h"
#include "InterchangeGenericAssetsPipeline.h"
#include "InterchangeGenericMaterialPipeline.h"
#include "InterchangeMaterialXPipeline.h"
#include "Materials/Material.h"
#include "Materials/MaterialExpressionMaterialFunctionCall.h"
#include "Materials/MaterialExpressionFunctionInput.h"
#include "Materials/MaterialExpressionComponentMask.h"
#include "Materials/MaterialExpressionOneMinus.h"
#include "Materials/MaterialExpressionMultiply.h"
#include "Materials/MaterialExpressionAppendVector.h"
#include "Materials/MaterialExpressionNormalize.h"
#include "Materials/MaterialFunctionInterface.h"
#include "Engine/Texture.h"
#include "Engine/Texture2D.h"
#include "EditorFramework/AssetImportData.h"
#include "AssetCompilingManager.h"
#include "AssetRegistry/AssetRegistryModule.h"
#include "Dom/JsonObject.h"
#include "Serialization/JsonSerializer.h"
#include "HAL/IConsoleManager.h"
#include "HAL/FileManager.h"
#include "UDIMUtilities.h"
#include "Misc/FileHelper.h"
#include "Misc/PackageName.h"
#include "Misc/Parse.h"
#include "Misc/Paths.h"
#include "Misc/SecureHash.h"
#include "UObject/SavePackage.h"
#include "UObject/StrongObjectPtr.h"

namespace {
int32 CVar(const TCHAR* Name) {
    const IConsoleVariable* Value = IConsoleManager::Get().FindConsoleVariable(Name);
    return Value ? Value->GetInt() : -1;
}
bool SaveImported(UObject* Object) {
    UPackage* Package = Object->GetOutermost();
    FSavePackageArgs Args; Args.TopLevelFlags = RF_Public | RF_Standalone;
    const FString File = FPackageName::LongPackageNameToFilename(Package->GetName(), FPackageName::GetAssetPackageExtension());
    return UPackage::SavePackage(Package, Object, *File, Args);
}
FString FileHash(const FString& File) {
    TArray<uint8> Bytes;
    if (!FFileHelper::LoadFileToArray(Bytes, *File)) return TEXT("");
    FSHAHash Hash;
    FSHA1::HashBuffer(Bytes.GetData(), Bytes.Num(), Hash.Hash);
    return Hash.ToString().ToLower();
}
int32 CorrectNormalScale(UMaterial* Material) {
    // Interchange uses FlattenNormal's lerp-to-(0,0,1). MaterialX instead
    // scales XY and normalizes, keeping the decoded Z component unchanged.
    const auto Original = Material->GetExpressions();
    TArray<UMaterialExpression*> Expressions;
    for (UMaterialExpression* Expression : Original) Expressions.Add(Expression);
    int32 Count = 0;
    for (auto* Expression : Expressions) {
        auto* Call = Cast<UMaterialExpressionMaterialFunctionCall>(Expression);
        if (!Call || !Call->MaterialFunction || !Call->MaterialFunction->GetPathName().EndsWith(TEXT("/FlattenNormal.FlattenNormal"))) continue;
        FExpressionInput Normal, Flatness;
        for (const auto& Input : Call->FunctionInputs) {
            if (Input.ExpressionInput && Input.ExpressionInput->InputName == TEXT("Normal")) Normal = Input.Input;
            if (Input.ExpressionInput && Input.ExpressionInput->InputName == TEXT("Flatness")) Flatness = Input.Input;
        }
        if (!Normal.Expression || !Flatness.Expression) continue;
        auto* XY = NewObject<UMaterialExpressionComponentMask>(Material); XY->Input = Normal; XY->R = XY->G = true; XY->B = XY->A = false;
        auto* Z = NewObject<UMaterialExpressionComponentMask>(Material); Z->Input = Normal; Z->R = Z->G = Z->A = false; Z->B = true;
        auto* Scale = NewObject<UMaterialExpressionOneMinus>(Material); Scale->Input = Flatness;
        auto* Multiply = NewObject<UMaterialExpressionMultiply>(Material); Multiply->A.Connect(0, XY); Multiply->B.Connect(0, Scale);
        auto* Append = NewObject<UMaterialExpressionAppendVector>(Material); Append->A.Connect(0, Multiply); Append->B.Connect(0, Z);
        auto* Normalize = NewObject<UMaterialExpressionNormalize>(Material); Normalize->VectorInput.Connect(0, Append);
        for (auto* Consumer : Expressions) for (auto* Input : Consumer->GetInputsView())
            if (Input && Input->Expression == Call) Input->Connect(0, Normalize);
        for (UMaterialExpression* Added : TArray<UMaterialExpression*>{XY, Z, Scale, Multiply, Append, Normalize}) Material->GetExpressionCollection().AddExpression(Added);
        Material->GetExpressionCollection().RemoveExpression(Call);
        ++Count;
    }
    return Count;
}
}
UHdUEImportMaterialXCommandlet::UHdUEImportMaterialXCommandlet() {
    IsClient = false; IsServer = false; IsEditor = true; LogToConsole = true;
}
int32 UHdUEImportMaterialXCommandlet::Main(const FString& Params) {
    FString Source, Destination, Report;
    if (!FParse::Value(*Params, TEXT("Source="), Source) || !FParse::Value(*Params, TEXT("Destination="), Destination) ||
        !FParse::Value(*Params, TEXT("Report="), Report) || !Destination.StartsWith(TEXT("/Game/"))) {
        UE_LOG(LogTemp, Error, TEXT("Required: -Source=file.mtlx -Destination=/Game/... -Report=file.json")); return 1;
    }
    Source = FPaths::ConvertRelativePathToFull(Source);
    const FString SourceHash = FileHash(Source);
    if (!FPaths::FileExists(Source) || CVar(TEXT("r.Substrate")) != 1 || CVar(TEXT("r.Substrate.ProjectGBufferFormat")) != 1) {
        UE_LOG(LogTemp, Error, TEXT("MaterialX import requires an existing source and Substrate Adaptive GBuffer.")); return 2;
    }
    auto Pipeline = TStrongObjectPtr<UHdUEMaterialXAssetsPipeline>(NewObject<UHdUEMaterialXAssetsPipeline>());
    Pipeline->MaterialPipeline->bImportMaterials = true;
    Pipeline->MaterialPipeline->MaterialImport = EInterchangeMaterialImportOption::ImportAsMaterials;
    Pipeline->MaterialPipeline->bReuseExistingMaterials = false;
    auto SourceData = TStrongObjectPtr<UInterchangeSourceData>(UInterchangeManager::CreateSourceData(Source));
    FImportAssetParameters Import; Import.bIsAutomated = true; Import.bReplaceExisting = true;
    Import.OverridePipelines.Add(FSoftObjectPath(Pipeline.Get()));
    // MaterialX resolves its function-call factory nodes after the generic
    // material pipeline has created them, matching UE's normal import stack.
    auto MaterialX = TStrongObjectPtr<UInterchangeMaterialXPipeline>(NewObject<UInterchangeMaterialXPipeline>());
    Import.OverridePipelines.Add(FSoftObjectPath(MaterialX.Get()));
    TArray<UObject*> Objects;
    if (!UInterchangeManager::GetInterchangeManager().ImportAsset(Destination, SourceData.Get(), Import, Objects)) {
        UE_LOG(LogTemp, Error, TEXT("MaterialX import failed: %s"), *Source); return 3;
    }
    FAssetCompilingManager::Get().FinishAllCompilation();
    TArray<TSharedPtr<FJsonValue>> Assets;
    auto Dependencies = MakeShared<FJsonObject>();
    TSet<UTexture*> PreparedTextures;
    int32 TextureSourceErrors = 0;
    const auto PrepareTexture = [&](UTexture* Texture) {
        if (!Texture || PreparedTextures.Contains(Texture)) return;
        PreparedTextures.Add(Texture);
        if (Texture->Source.GetNumBlocks() > 1 && Texture->AssetImportData) {
            // UE's MaterialX blocked payload reports its last alternate path
            // as source 0. Rebuild provenance from the actual block locations.
            const auto OriginalFiles = Texture->AssetImportData->ExtractFilenames();
            auto Tiles = OriginalFiles.IsEmpty() ? TMap<int32, FString>() : UE::TextureUtilitiesCommon::GetUDIMBlocksFromSourceFile(OriginalFiles[0], UE::TextureUtilitiesCommon::DefaultUdimRegexPattern);
            TArray<FAssetImportInfo::FSourceFile> Sources;
            for (int32 I = 0; I < Texture->Source.GetNumBlocks(); ++I) {
                FTextureSourceBlock Block; Texture->Source.GetBlock(I, Block);
                const FString* File = Tiles.Find(1001 + Block.BlockX + 10 * Block.BlockY);
                if (!File) { ++TextureSourceErrors; break; }
                Sources.Emplace(*File, IFileManager::Get().GetTimeStamp(**File), FMD5Hash::HashFile(**File));
            }
            if (Sources.Num() == Texture->Source.GetNumBlocks()) Texture->AssetImportData->SetSourceFiles(MoveTemp(Sources));
        }
        if (Texture->CompressionSettings == TC_Normalmap) {
            // USD/MaterialX use +Y tangent normals; UE's flipped V basis
            // requires the DirectX green convention for the same direction.
            Texture->SRGB = false;
            Texture->bFlipGreenChannel = true;
            Texture->PostEditChange();
        }
        if (Texture->AssetImportData) for (const FString& File : Texture->AssetImportData->ExtractFilenames())
            Dependencies->SetStringField(File, FileHash(File));
        SaveImported(Texture);
    };
    Dependencies->SetStringField(Source, SourceHash);
    int32 MaterialCount = 0;
    for (UObject* Object : Objects) {
        if (!IsValid(Object)) continue;
        auto Entry = MakeShared<FJsonObject>();
        Entry->SetStringField(TEXT("name"), Object->GetName());
        Entry->SetStringField(TEXT("asset"), Object->GetPathName());
        Entry->SetStringField(TEXT("class"), Object->GetClass()->GetName());
        if (auto* Texture = Cast<UTexture>(Object); Texture && Texture->AssetImportData) {
            PrepareTexture(Texture);
            for (const FString& File : Texture->AssetImportData->ExtractFilenames())
                Dependencies->SetStringField(File, FileHash(File));
        }
        if (auto* Interface = Cast<UMaterialInterface>(Object)) {
            UMaterial* Material = Interface->GetMaterial();
            if (!Material || !Material->HasSubstrateFrontMaterialConnected()) {
                UE_LOG(LogTemp, Error, TEXT("MaterialX did not generate native Substrate: %s"), *Object->GetPathName()); return 4;
            }
            ++MaterialCount;
            Material->bUsedWithNanite = true;
            Entry->SetNumberField(TEXT("normal_scale_corrections"), CorrectNormalScale(Material));
            Material->PostEditChange();
            Entry->SetBoolField(TEXT("native_substrate"), true);
            Entry->SetNumberField(TEXT("blend_mode"), int32(Interface->GetBlendMode()));
            TArray<FMaterialParameterInfo> Info; TArray<FGuid> Guids;
            TArray<TSharedPtr<FJsonValue>> Parameters;
            Interface->GetAllScalarParameterInfo(Info, Guids);
            for (const auto& P : Info) {
                float Value = 0; Interface->GetScalarParameterValue(P, Value);
                auto Field = MakeShared<FJsonObject>(); Field->SetStringField(TEXT("name"), P.Name.ToString());
                Field->SetStringField(TEXT("type"), TEXT("scalar")); Field->SetNumberField(TEXT("default"), Value);
                Parameters.Add(MakeShared<FJsonValueObject>(Field));
            }
            Info.Empty(); Guids.Empty(); Interface->GetAllVectorParameterInfo(Info, Guids);
            for (const auto& P : Info) {
                FLinearColor Value; Interface->GetVectorParameterValue(P, Value);
                auto Field = MakeShared<FJsonObject>(); Field->SetStringField(TEXT("name"), P.Name.ToString()); Field->SetStringField(TEXT("type"), TEXT("vector"));
                Field->SetArrayField(TEXT("default"), {MakeShared<FJsonValueNumber>(Value.R), MakeShared<FJsonValueNumber>(Value.G),
                    MakeShared<FJsonValueNumber>(Value.B), MakeShared<FJsonValueNumber>(Value.A)});
                Parameters.Add(MakeShared<FJsonValueObject>(Field));
            }
            Info.Empty(); Guids.Empty(); Interface->GetAllTextureParameterInfo(Info, Guids);
            for (const auto& P : Info) {
                UTexture* Value = nullptr; Interface->GetTextureParameterValue(P, Value);
                PrepareTexture(Value);
                // Interchange may reuse a previously imported texture without
                // returning it in Objects. Keep those source dependencies too.
                if (Value && Value->AssetImportData) {
                    for (const FString& File : Value->AssetImportData->ExtractFilenames())
                        Dependencies->SetStringField(File, FileHash(File));
                }
                auto Field = MakeShared<FJsonObject>(); Field->SetStringField(TEXT("name"), P.Name.ToString()); Field->SetStringField(TEXT("type"), TEXT("texture"));
                Field->SetStringField(TEXT("default"), Value ? Value->GetPathName() : TEXT(""));
                if (Value) {
                    Field->SetBoolField(TEXT("virtual_texture"), Value->VirtualTextureStreaming);
                    Field->SetBoolField(TEXT("normal_map"), Value->CompressionSettings == TC_Normalmap);
                    Field->SetBoolField(TEXT("srgb"), Value->SRGB);
                    Field->SetBoolField(TEXT("flip_green"), Value->bFlipGreenChannel);
                    TArray<TSharedPtr<FJsonValue>> Files, Blocks;
                    if (Value->AssetImportData) for (const FString& File : Value->AssetImportData->ExtractFilenames()) Files.Add(MakeShared<FJsonValueString>(File));
                    for (int32 I = 0; I < Value->Source.GetNumBlocks(); ++I) {
                        FTextureSourceBlock Block; Value->Source.GetBlock(I, Block);
                        auto Data = MakeShared<FJsonObject>();
                        Data->SetNumberField(TEXT("x"), Block.BlockX); Data->SetNumberField(TEXT("y"), Block.BlockY);
                        Data->SetNumberField(TEXT("width"), Block.SizeX); Data->SetNumberField(TEXT("height"), Block.SizeY);
                        Blocks.Add(MakeShared<FJsonValueObject>(Data));
                    }
                    Field->SetArrayField(TEXT("source_files"), Files); Field->SetArrayField(TEXT("source_blocks"), Blocks);
                }
                Parameters.Add(MakeShared<FJsonValueObject>(Field));
            }
            Entry->SetArrayField(TEXT("parameters"), Parameters);
        }
        if (!SaveImported(Object)) return 5;
        Assets.Add(MakeShared<FJsonValueObject>(Entry));
    }
    if (!MaterialCount) { UE_LOG(LogTemp, Error, TEXT("MaterialX import generated no materials.")); return 6; }
    if (TextureSourceErrors) { UE_LOG(LogTemp, Error, TEXT("UDIM source files do not match imported texture blocks.")); return 8; }
    FAssetCompilingManager::Get().FinishAllCompilation();
    auto Result = MakeShared<FJsonObject>();
    Result->SetStringField(TEXT("source"), Source); Result->SetStringField(TEXT("destination"), Destination);
    Result->SetStringField(TEXT("source_sha1"), SourceHash); Result->SetObjectField(TEXT("dependencies_sha1"), Dependencies);
    Result->SetNumberField(TEXT("substrate"), CVar(TEXT("r.Substrate")));
    Result->SetNumberField(TEXT("gbuffer_format"), CVar(TEXT("r.Substrate.ProjectGBufferFormat")));
    Result->SetNumberField(TEXT("materials"), MaterialCount); Result->SetArrayField(TEXT("assets"), Assets);
    FString Json; auto Writer = TJsonWriterFactory<>::Create(&Json);
    FJsonSerializer::Serialize(Result, Writer);
    if (!FFileHelper::SaveStringToFile(Json, *Report, FFileHelper::EEncodingOptions::ForceUTF8WithoutBOM)) return 7;
    UE_LOG(LogTemp, Display, TEXT("hdUE imported %d native Substrate MaterialX materials."), MaterialCount);
    return 0;
}
