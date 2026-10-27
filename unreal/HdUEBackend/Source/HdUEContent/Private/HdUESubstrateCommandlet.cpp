#include "HdUESubstrateCommandlet.h"
#include "Materials/Material.h"
#include "Materials/MaterialExpressionSubstrate.h"
#include "Materials/MaterialExpressionScalarParameter.h"
#include "Materials/MaterialExpressionVectorParameter.h"
#include "Materials/MaterialExpressionConstant.h"
#include "Materials/MaterialExpressionConstant3Vector.h"
#include "Materials/MaterialExpressionConstant2Vector.h"
#include "Materials/MaterialExpressionAdd.h"
#include "Materials/MaterialExpressionSubtract.h"
#include "Materials/MaterialExpressionAppendVector.h"
#include "Materials/MaterialExpressionComponentMask.h"
#include "Materials/MaterialExpressionNormalize.h"
#include "Materials/MaterialExpressionTransform.h"
#include "Materials/MaterialExpressionTextureCoordinate.h"
#include "Materials/MaterialExpressionTextureSample.h"
#include "Materials/MaterialExpressionDotProduct.h"
#include "Materials/MaterialExpressionMultiply.h"
#include "Materials/MaterialExpressionSaturate.h"
#include "Materials/MaterialExpressionVertexColor.h"
#include "Materials/MaterialExpressionLinearInterpolate.h"
#include "AssetCompilingManager.h"
#include "AssetRegistry/AssetRegistryModule.h"
#include "Dom/JsonObject.h"
#include "Serialization/JsonSerializer.h"
#include "Misc/FileHelper.h"
#include "Misc/PackageName.h"
#include "Misc/Parse.h"
#include "UObject/SavePackage.h"
#include "Factories/TextureFactory.h"
#include "Engine/Texture2D.h"
#include "Misc/Paths.h"

namespace {
struct FOutput { UMaterialExpression* Expression=nullptr; int32 Type=0; int32 Index=0; };
struct FCompiler {
    UMaterial* Material=nullptr;
    TSharedPtr<FJsonObject> Nodes;
    TSharedPtr<FJsonObject> Textures;
    TMap<FString,UTexture2D*> ImportedTextures;
    TMap<FString,FOutput> Built;
    TMap<const FJsonObject*,FString> Owners;
    TMap<FString,TSharedPtr<FJsonObject>> Live;
    TMap<FString,FOutput> LiveBuilt;
    TSet<FString> Active;
    FExpressionInput Base;
    FString Error;
    TSet<FName> Parameters;
    template<class T> T* Add() { auto* E=NewObject<T>(Material); Material->GetExpressionCollection().AddExpression(E); return E; }
    float Number(const TSharedPtr<FJsonObject>& O,const FString& Name,float Default) {
        double Value=Default; O->TryGetNumberField(Name,Value);
        if (!FMath::IsFinite(Value) || !FMath::IsFinite(float(Value))) Error=TEXT("Non-finite graph number: ")+Name;
        return float(Value);
    }
    FString Text(const TSharedPtr<FJsonObject>& O,const FString& Name,const TCHAR* Default=TEXT("")) {
        FString Value=Default; O->TryGetStringField(Name,Value); return Value;
    }
    FOutput Constant(float V) { auto* E=Add<UMaterialExpressionConstant>(); E->R=V; return {E,0,0}; }
    FOutput Color(const TArray<TSharedPtr<FJsonValue>>& V) {
        if (V.Num()!=3) {Error=TEXT("Graph color requires three finite numbers."); return {};}
        FVector3f C;
        for (int I=0;I<3;++I) { C[I]=float(V[I]->AsNumber()); if (!FMath::IsFinite(C[I])) Error=TEXT("Non-finite graph color."); }
        auto* E=Add<UMaterialExpressionConstant3Vector>(); E->Constant=FLinearColor(C.X,C.Y,C.Z); return {E,1,0};
    }
    FOutput Vector2(const TSharedPtr<FJsonObject>& O,const TCHAR* Name,float X,float Y) {
        const TArray<TSharedPtr<FJsonValue>>* V=nullptr;
        if (O->TryGetArrayField(Name,V)) {
            if (V->Num()!=2) {Error=TEXT("Graph UV value requires two numbers."); return {};}
            X=float((*V)[0]->AsNumber()); Y=float((*V)[1]->AsNumber());
        }
        if (!FMath::IsFinite(X) || !FMath::IsFinite(Y)) {Error=TEXT("Non-finite UV value."); return {};}
        auto* E=Add<UMaterialExpressionConstant2Vector>(); E->R=X; E->G=Y; return {E,3,0};
    }
    FOutput Source(const TSharedPtr<FJsonObject>& Connection) {
        const auto Name=Text(Connection,TEXT("node")); auto R=Build(Name);
        const auto Port=Text(Connection,TEXT("output"),TEXT("out"));
        if (Port==TEXT("out")) return R;
        if (!Cast<UMaterialExpressionTextureSample>(R.Expression)) {Error=TEXT("Unsupported graph output: ")+Port; return {};}
        if (Port==TEXT("rgba")) return {R.Expression,4,5};
        const int32 Channel=Port==TEXT("r")?1:Port==TEXT("g")?2:Port==TEXT("b")?3:Port==TEXT("a")?4:0;
        if (!Channel) {Error=TEXT("Unsupported texture output: ")+Port; return {};}
        return {R.Expression,0,Channel};
    }
    FOutput Auto(const TSharedPtr<FJsonObject>& O,const TCHAR* Port) {
        const auto* Owner=Owners.Find(O.Get());
        const FString Key=Owner?*Owner+Port:FString();
        if (const auto* Existing=LiveBuilt.Find(Key)) return *Existing;
        const auto* Record=Live.Find(Key);
        if (!Record) {Error=TEXT("Missing generated runtime parameter: ")+Key; return {};}
        const auto Name=Text(*Record,TEXT("name")); const auto Type=Text(*Record,TEXT("type"));
        if (!Name.StartsWith(TEXT("HdUE_Auto_")) || Parameters.Contains(FName(*Name))) {
            Error=TEXT("Invalid or colliding generated parameter: ")+Name; return {};
        }
        Parameters.Add(FName(*Name)); FOutput R;
        if (Type==TEXT("float")) {
            auto* E=Add<UMaterialExpressionScalarParameter>(); E->ParameterName=FName(*Name);
            E->DefaultValue=Number(*Record,TEXT("value"),0); R={E,0,0};
        } else {
            const auto& V=(*Record)->GetArrayField(TEXT("value"));
            const int32 Count=Type==TEXT("vector2")?2:Type==TEXT("color3")?3:Type==TEXT("color4")?4:0;
            if (!Count || V.Num()!=Count) {Error=TEXT("Invalid generated vector parameter: ")+Key; return {};}
            FLinearColor Value(0,0,0,0);
            for (int32 I=0;I<Count;++I) {
                Value.Component(I)=float(V[I]->AsNumber());
                if (!FMath::IsFinite(Value.Component(I))) Error=TEXT("Non-finite generated vector.");
            }
            auto* E=Add<UMaterialExpressionVectorParameter>(); E->ParameterName=FName(*Name); E->DefaultValue=Value;
            R={E,Count==4?4:1,Count==4?5:0};
            if (Count==2) {
                auto* Mask=Add<UMaterialExpressionComponentMask>(); Mask->Input.Connect(0,E);
                Mask->R=Mask->G=true; Mask->B=Mask->A=false; R={Mask,3,0};
            }
        }
        LiveBuilt.Add(Key,R); return R;
    }
    FOutput Input(const TSharedPtr<FJsonObject>& O,const TCHAR* Name,int32 Type,float Default=0.f,FLinearColor C=FLinearColor(.18f,.18f,.18f)) {
        const auto* V=O->Values.Find(Name);
        if (V && (*V)->Type==EJson::Object) {
            auto R=Source((*V)->AsObject());
            if (R.Type!=Type) Error=FString(TEXT("Graph input type mismatch: "))+Name;
            return R;
        }
        if (const auto* Owner=Owners.Find(O.Get()); Owner && Live.Contains(*Owner+Name)) {
            auto R=Auto(O,Name); if (R.Type!=Type) Error=TEXT("Generated input type mismatch: ")+FString(Name); return R;
        }
        if (Type==2 || Type==4) {Error=FString(TEXT("Missing required connection: "))+Name; return {};}
        if (Type==3) return Vector2(O,Name,Default,Default);
        if (Type==1) {
            if (V) return Color((*V)->AsArray());
            auto* E=Add<UMaterialExpressionConstant3Vector>(); E->Constant=C; return {E,1,0};
        }
        return Constant(Number(O,Name,Default));
    }
    void Connect(FExpressionInput& To,FOutput From) { if (From.Expression) To.Connect(From.Index,From.Expression); }
    UTexture2D* Texture(const FString& Id) {
        const TSharedPtr<FJsonObject>* Record=nullptr;
        if (!Textures || !Textures->TryGetObjectField(Id,Record)) {Error=TEXT("Missing prepared texture: ")+Id; return nullptr;}
        const auto Asset=Text(*Record,TEXT("asset")); const auto File=Text(*Record,TEXT("source"));
        if (auto** Cached=ImportedTextures.Find(Asset)) return *Cached;
        if (!Asset.StartsWith(TEXT("/Game/HdUE/")) || !FPackageName::IsValidLongPackageName(Asset) || !FPaths::FileExists(File)) {
            Error=TEXT("Invalid prepared texture source or package: ")+Id; return nullptr;
        }
        auto* TexturePackage=CreatePackage(*Asset); TexturePackage->FullyLoad();
        const auto Name=FPackageName::GetLongPackageAssetName(Asset);
        // Snapshot directories contain exactly the requested UDIM tiles.
        auto* Factory=NewObject<UTextureFactory>(); Factory->bCreateMaterial=false;
        bool SRGB=false; (*Record)->TryGetBoolField(TEXT("srgb"),SRGB);
        Factory->ColorSpaceMode=SRGB?ETextureSourceColorSpace::SRGB:ETextureSourceColorSpace::Linear;
        Factory->CompressionSettings=SRGB?TC_Default:TC_VectorDisplacementmap;
        UTextureFactory::SuppressImportOverwriteDialog(true);
        auto* Texture=Cast<UTexture2D>(UFactory::StaticImportObject(UTexture2D::StaticClass(),TexturePackage,*Name,
            RF_Public|RF_Standalone,*File,nullptr,Factory));
        if (!Texture) {Error=TEXT("Texture import failed: ")+File; return nullptr;}
        const auto& Tiles=(*Record)->GetArrayField(TEXT("tiles"));
        if (Texture->Source.GetNumBlocks()!=Tiles.Num()) {Error=TEXT("Texture UDIM block count mismatch: ")+Id; return nullptr;}
        TSet<int32> Expected; for (const auto& Tile:Tiles) Expected.Add(int32(Tile->AsNumber()));
        for (int32 I=0;I<Texture->Source.GetNumBlocks();++I) {
            FTextureSourceBlock Block; Texture->Source.GetBlock(I,Block);
            if (!Expected.Contains(1001+Block.BlockX+10*Block.BlockY)) {Error=TEXT("Texture UDIM block coordinate mismatch: ")+Id; return nullptr;}
        }
        Texture->VirtualTextureStreaming=Tiles.Num()>1; Texture->SRGB=SRGB;
        Texture->CompressionSettings=SRGB?TC_Default:TC_VectorDisplacementmap;
        Texture->bFlipGreenChannel=false; Texture->CompressionNoAlpha=false;
        const auto Address=Text(*Record,TEXT("address"),TEXT("wrap"));
        if (Address!=TEXT("wrap") && Address!=TEXT("clamp") && Address!=TEXT("mirror")) {Error=TEXT("Invalid texture address mode."); return nullptr;}
        Texture->AddressX=Texture->AddressY=Address==TEXT("clamp")?TA_Clamp:Address==TEXT("mirror")?TA_Mirror:TA_Wrap;
        Texture->PostEditChange(); FAssetCompilingManager::Get().FinishAllCompilation();
        FAssetRegistryModule::AssetCreated(Texture); FSavePackageArgs Args; Args.TopLevelFlags=RF_Public|RF_Standalone;
        if (!UPackage::SavePackage(TexturePackage,Texture,*FPackageName::LongPackageNameToFilename(Asset,FPackageName::GetAssetPackageExtension()),Args)) {
            Error=TEXT("Could not save prepared texture: ")+Asset; return nullptr;
        }
        ImportedTextures.Add(Asset,Texture); return Texture;
    }
    FName Parameter(const TSharedPtr<FJsonObject>& Props) {
        auto TextName=Text(Props,TEXT("parameter_name")); FName Name(*TextName);
        if (TextName.IsEmpty() || TextName.StartsWith(TEXT("HdUE_")) || Parameters.Contains(Name)) Error=TEXT("Duplicate, empty or reserved graph parameter: ")+TextName;
        Parameters.Add(Name); return Name;
    }
    FOutput Build(const FString& Id) {
        if (const auto* R=Built.Find(Id)) return *R;
        const auto* Raw=Nodes->Values.Find(*Id);
        if (!Raw || (*Raw)->Type!=EJson::Object || Active.Contains(Id)) { Error=TEXT("Missing graph node or cycle: ")+Id; return {}; }
        Active.Add(Id);
        const auto N=(*Raw)->AsObject(); const auto Kind=Text(N,TEXT("type"));
        const TSharedPtr<FJsonObject>* P=nullptr;
        auto Inputs=N->TryGetObjectField(TEXT("inputs"),P)?*P:MakeShared<FJsonObject>();
        auto Props=N->TryGetObjectField(TEXT("properties"),P)?*P:MakeShared<FJsonObject>();
        Owners.Add(Inputs.Get(),Id+TEXT(".inputs.")); Owners.Add(Props.Get(),Id+TEXT(".properties."));
        FOutput R;
        if (Kind==TEXT("materialx")) {
            if (!Base.Expression) Error=TEXT("MaterialX input was not resolved to a prepared opaque base.");
            R={Base.Expression,2,Base.OutputIndex};
        } else if (Kind==TEXT("slab")) {
            auto* E=Add<UMaterialExpressionSubstrateSlabBSDF>(); R={E,2,0};
            Connect(E->DiffuseAlbedo,Input(Inputs,TEXT("diffuse_albedo"),1));
            Connect(E->F0,Input(Inputs,TEXT("f0"),1,0,FLinearColor(.04f,.04f,.04f)));
            Connect(E->Roughness,Input(Inputs,TEXT("roughness"),0,.5f));
            Connect(E->SSSMFP,Input(Inputs,TEXT("mfp"),1,0,FLinearColor::Black));
            if (Inputs->HasField(TEXT("normal"))) Connect(E->Normal,Input(Inputs,TEXT("normal"),1));
            auto Mode=Text(Props,TEXT("subsurface"),TEXT("none"));
            if (Mode!=TEXT("none") && Mode!=TEXT("simple_volume")) Error=TEXT("Unsupported Substrate subsurface mode.");
            E->SubSurfaceType=Mode==TEXT("simple_volume")?MSS_SimpleVolume:MSS_None;
        } else if (Kind==TEXT("weight")) {
            auto* E=Add<UMaterialExpressionSubstrateWeight>(); R={E,2,0};
            Connect(E->A,Input(Inputs,TEXT("surface"),2)); Connect(E->Weight,Input(Inputs,TEXT("weight"),0,1));
        } else if (Kind==TEXT("vertical")) {
            auto* E=Add<UMaterialExpressionSubstrateVerticalLayering>(); R={E,2,0};
            Connect(E->Top,Input(Inputs,TEXT("top"),2)); Connect(E->Base,Input(Inputs,TEXT("base"),2));
            Connect(E->Thickness,Input(Inputs,TEXT("thickness"),0,.01f));
            bool Blend=false; Props->TryGetBoolField(TEXT("parameter_blending"),Blend); E->bUseParameterBlending=Blend;
        } else if (Kind==TEXT("horizontal")) {
            auto* E=Add<UMaterialExpressionSubstrateHorizontalMixing>(); R={E,2,0};
            Connect(E->Background,Input(Inputs,TEXT("background"),2)); Connect(E->Foreground,Input(Inputs,TEXT("foreground"),2));
            Connect(E->Mix,Input(Inputs,TEXT("mix"),0,.5f));
            bool Blend=false; Props->TryGetBoolField(TEXT("parameter_blending"),Blend); E->bUseParameterBlending=Blend;
        } else if (Kind==TEXT("scalar")) {
            auto* E=Add<UMaterialExpressionScalarParameter>(); R={E,0,0}; E->ParameterName=Parameter(Props); E->DefaultValue=Number(Props,TEXT("value"),1);
        } else if (Kind==TEXT("color")) {
            auto* E=Add<UMaterialExpressionVectorParameter>(); R={E,1,0}; E->ParameterName=Parameter(Props);
            const auto* Value=Props->Values.Find(TEXT("value")); if (Value) {
                auto* C=Cast<UMaterialExpressionConstant3Vector>(Color((*Value)->AsArray()).Expression); if (C) E->DefaultValue=C->Constant;
            }
        } else if (Kind==TEXT("constant")) R=Input(Props,TEXT("value"),0,1);
        else if (Kind==TEXT("constant_color")) R=Input(Props,TEXT("value"),1,0,FLinearColor::White);
        else if (Kind==TEXT("multiply")) {
            auto* E=Add<UMaterialExpressionMultiply>(); R={E,0,0}; Connect(E->A,Input(Inputs,TEXT("a"),0,1)); Connect(E->B,Input(Inputs,TEXT("b"),0,1));
        } else if (Kind==TEXT("color_from_float")) {
            auto* E=Add<UMaterialExpressionMultiply>(); R={E,1,0}; Connect(E->A,Input(Inputs,TEXT("value"),0));
            auto* White=Add<UMaterialExpressionConstant3Vector>(); White->Constant=FLinearColor::White; E->B.Connect(0,White);
        } else if (Kind==TEXT("unlit")) {
            auto* E=Add<UMaterialExpressionSubstrateUnlitBSDF>(); R={E,2,0}; Connect(E->EmissiveColor,Input(Inputs,TEXT("emission"),1,0,FLinearColor::Black));
        } else if (Kind==TEXT("saturate")) {
            auto* E=Add<UMaterialExpressionSaturate>(); R={E,0,0}; Connect(E->Input,Input(Inputs,TEXT("value"),0));
        } else if (Kind==TEXT("primvar")) {
            auto* ColorNode=Add<UMaterialExpressionVertexColor>();
            auto* Enabled=Add<UMaterialExpressionScalarParameter>(); Enabled->ParameterName=TEXT("HdUE_PrimvarMaskEnabled"); Enabled->DefaultValue=0;
            auto* Mix=Add<UMaterialExpressionLinearInterpolate>(); R={Mix,0,0}; Connect(Mix->A,Input(Props,TEXT("default"),0,1));
            Mix->B.Connect(1,ColorNode); Mix->Alpha.Connect(0,Enabled);
        } else if (Kind==TEXT("uv")) {
            if (Number(Props,TEXT("index"),0)!=0) Error=TEXT("Only UV0 (USD st) is supported.");
            auto* UV=Add<UMaterialExpressionTextureCoordinate>(); UV->CoordinateIndex=0;
            auto* Multiply=Add<UMaterialExpressionMultiply>(); Multiply->A.Connect(0,UV);
            Connect(Multiply->B,Input(Inputs,TEXT("scale"),3,1));
            auto* Offset=Add<UMaterialExpressionAdd>(); Offset->A.Connect(0,Multiply);
            Connect(Offset->B,Input(Inputs,TEXT("offset"),3,0)); R={Offset,3,0};
        } else if (Kind==TEXT("texture")) {
            auto* T=Texture(Id); if (!T) return {};
            auto* E=Add<UMaterialExpressionTextureSample>(); E->Texture=T; E->AutoSetSampleType(); R={E,1,0};
            if (Inputs->HasField(TEXT("uv"))) Connect(E->Coordinates,Input(Inputs,TEXT("uv"),3));
        } else if (Kind==TEXT("channel")) {
            auto* E=Add<UMaterialExpressionDotProduct>(); R={E,0,0};
            Connect(E->A,Input(Inputs,TEXT("value"),4)); Connect(E->B,Auto(Props,TEXT("channel")));
        } else if (Kind==TEXT("normalmap")) {
            auto* Decode=Add<UMaterialExpressionMultiply>(); Connect(Decode->A,Input(Inputs,TEXT("color"),1,0,FLinearColor(.5f,.5f,1.f))); Decode->ConstB=2;
            auto* Signed=Add<UMaterialExpressionSubtract>(); Signed->A.Connect(0,Decode); Signed->ConstB=1;
            auto* XY=Add<UMaterialExpressionComponentMask>(); XY->Input.Connect(0,Signed); XY->R=XY->G=true; XY->B=XY->A=false;
            auto* Z=Add<UMaterialExpressionComponentMask>(); Z->Input.Connect(0,Signed); Z->B=true; Z->R=Z->G=Z->A=false;
            auto* Convention=Add<UMaterialExpressionMultiply>(); Convention->A.Connect(0,XY);
            auto* TwiceFlip=Add<UMaterialExpressionMultiply>(); Connect(TwiceFlip->A,Auto(Props,TEXT("flip_green"))); TwiceFlip->ConstB=2;
            auto* Green=Add<UMaterialExpressionSubtract>(); Green->ConstA=1; Green->B.Connect(0,TwiceFlip);
            auto* Sign=Add<UMaterialExpressionAppendVector>(); Sign->A.Connect(0,Constant(1).Expression); Sign->B.Connect(0,Green);
            Convention->B.Connect(0,Sign);
            auto* Strength=Add<UMaterialExpressionMultiply>(); Strength->A.Connect(0,Convention); Connect(Strength->B,Input(Inputs,TEXT("strength"),0,1));
            auto* XYZ=Add<UMaterialExpressionAppendVector>(); XYZ->A.Connect(0,Strength); XYZ->B.Connect(0,Z);
            auto* Normal=Add<UMaterialExpressionNormalize>(); Normal->VectorInput.Connect(0,XYZ); R={Normal,1,0};
            if (!Material->bTangentSpaceNormal) {
                auto* World=Add<UMaterialExpressionTransform>(); World->Input.Connect(0,Normal);
                World->TransformSourceType=TRANSFORMSOURCE_Tangent; World->TransformType=TRANSFORM_World; R={World,1,0};
            }
        } else Error=TEXT("Unsupported Substrate graph operation: ")+Kind;
        Active.Remove(Id); Built.Add(Id,R); return R;
    }
};
}
UHdUESubstrateCommandlet::UHdUESubstrateCommandlet() { IsClient=false; IsServer=false; IsEditor=true; LogToConsole=true; }
int32 UHdUESubstrateCommandlet::Main(const FString& Params) {
    FString File,Report,Json;
    if (!FParse::Value(*Params,TEXT("Graph="),File) || !FParse::Value(*Params,TEXT("Report="),Report) || !FFileHelper::LoadFileToString(Json,*File)) return 1;
    TSharedPtr<FJsonObject> Root;
    if (!FJsonSerializer::Deserialize(TJsonReaderFactory<>::Create(Json),Root) || !Root.IsValid()) return 2;
    const TSharedPtr<FJsonObject>* GraphPtr=nullptr;
    if (!Root->TryGetObjectField(TEXT("graph"),GraphPtr) || (*GraphPtr)->GetIntegerField(TEXT("version"))!=1) return 2;
    auto Graph=*GraphPtr;
    if (!Root->HasField(TEXT("compiler")) || Root->GetIntegerField(TEXT("compiler"))!=2 || !Root->HasTypedField<EJson::Array>(TEXT("parameters"))) return 2;
    const FString Asset=Root->GetStringField(TEXT("asset"));
    if (!Asset.StartsWith(TEXT("/Game/HdUE/")) || !FPackageName::IsValidLongPackageName(Asset)) return 2;
    FString BaseAsset; Root->TryGetStringField(TEXT("base_asset"),BaseAsset);
    UMaterial* Base=BaseAsset.IsEmpty()?nullptr:LoadObject<UMaterial>(nullptr,*BaseAsset);
    if (!BaseAsset.IsEmpty() && (!Base || Base->GetBlendMode()!=BLEND_Opaque || !Base->HasSubstrateFrontMaterialConnected())) return 3;
    auto* Package=CreatePackage(*Asset); Package->FullyLoad(); const auto Name=FPackageName::GetLongPackageAssetName(Asset);
    if (auto* Old=FindObject<UMaterial>(Package,*Name)) { Old->ClearFlags(RF_Public|RF_Standalone); Old->Rename(nullptr,GetTransientPackage(),REN_DontCreateRedirectors|REN_NonTransactional); }
    auto* Material=Base?DuplicateObject<UMaterial>(Base,Package,*Name):NewObject<UMaterial>(Package,*Name,RF_Public|RF_Standalone);
    FCompiler Compiler; Compiler.Material=Material; Compiler.Nodes=Graph->GetObjectField(TEXT("nodes"));
    for (const auto& Value:Root->GetArrayField(TEXT("parameters"))) {
        const auto Parameter=Value->AsObject(); const auto Key=Parameter->GetStringField(TEXT("key"));
        if (Compiler.Live.Contains(Key)) return 2;
        Compiler.Live.Add(Key,Parameter);
    }
    const TSharedPtr<FJsonObject>* TextureRecords=nullptr;
    if (Root->TryGetObjectField(TEXT("textures"),TextureRecords)) Compiler.Textures=*TextureRecords;
    if (Compiler.Nodes->Values.IsEmpty() || Compiler.Nodes->Values.Num()>256) return 2;
    if (Base) {
        Compiler.Base=Material->GetEditorOnlyData()->FrontMaterial;
        TArray<FMaterialParameterInfo> Info; TArray<FGuid> Guids;
        Base->GetAllScalarParameterInfo(Info,Guids); for (const auto& P:Info) Compiler.Parameters.Add(P.Name); Info.Empty(); Guids.Empty();
        Base->GetAllVectorParameterInfo(Info,Guids); for (const auto& P:Info) Compiler.Parameters.Add(P.Name); Info.Empty(); Guids.Empty();
        Base->GetAllTextureParameterInfo(Info,Guids); for (const auto& P:Info) Compiler.Parameters.Add(P.Name);
    }
    auto Output=Compiler.Build(Graph->GetStringField(TEXT("output")));
    if (!Compiler.Error.IsEmpty() || !Output.Expression || Output.Type!=2 || Compiler.Built.Num()!=Compiler.Nodes->Values.Num() || Compiler.LiveBuilt.Num()!=Compiler.Live.Num()) {
        UE_LOG(LogTemp,Error,TEXT("Substrate Builder: %s"),*Compiler.Error); return 4;
    }
    Material->GetEditorOnlyData()->FrontMaterial.Connect(Output.Index,Output.Expression);
    Material->PostEditChange(); FAssetCompilingManager::Get().FinishAllCompilation();
    FAssetRegistryModule::AssetCreated(Material);
    FSavePackageArgs Args; Args.TopLevelFlags=RF_Public|RF_Standalone;
    if (!UPackage::SavePackage(Package,Material,*FPackageName::LongPackageNameToFilename(Asset,FPackageName::GetAssetPackageExtension()),Args)) return 5;
    auto Result=MakeShared<FJsonObject>(); Result->SetNumberField(TEXT("version"),1); Result->SetStringField(TEXT("asset"),Material->GetPathName());
    Result->SetNumberField(TEXT("compiler"),2); Result->SetNumberField(TEXT("automatic_parameters"),Compiler.LiveBuilt.Num());
    Result->SetNumberField(TEXT("expressions"),Material->GetExpressions().Num()); Result->SetBoolField(TEXT("native_substrate"),Material->HasSubstrateFrontMaterialConnected());
    Result->SetNumberField(TEXT("textures"),Compiler.ImportedTextures.Num());
    FString Receipt; FJsonSerializer::Serialize(Result,TJsonWriterFactory<>::Create(&Receipt));
    return FFileHelper::SaveStringToFile(Receipt,*Report,FFileHelper::EEncodingOptions::ForceUTF8WithoutBOM)?0:6;
}
