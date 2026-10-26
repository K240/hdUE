#include "HdUEMaterialXAssetsPipeline.h"
#include "Nodes/InterchangeBaseNodeContainer.h"
#include "InterchangeTexture2DNode.h"
#include "InterchangeTexture2DFactoryNode.h"
#include "Engine/Texture.h"
#include "UDIMUtilities.h"

void UHdUEMaterialXAssetsPipeline::ExecutePipeline(UInterchangeBaseNodeContainer* Container, const TArray<UInterchangeSourceData*>& Sources, const FString& ContentBasePath) {
    UE_LOG(LogTemp, Display, TEXT("hdUE MaterialX texture block pipeline"));
    TSet<FString> Normals;
    Container->IterateNodesOfType<UInterchangeTexture2DNode>([&](const FString& Uid, UInterchangeTexture2DNode* Texture) {
        const auto Payload = Texture->GetPayLoadKey();
        if (!Payload) return;
        FString File = Payload.GetValue();
        int32 Separator;
        // MaterialX 1.39 / UE 5.8 encodes compression as filename{number.
        // The generic UDIM filename detector does not strip this suffix.
        if (File.FindChar(TEXT('{'), Separator)) {
            if (FCString::Atoi(*File.Mid(Separator + 1)) == TC_Normalmap) Normals.Add(Uid);
            File.LeftInline(Separator);
        }
        auto Blocks = UE::TextureUtilitiesCommon::GetUDIMBlocksFromSourceFile(File, UE::TextureUtilitiesCommon::DefaultUdimRegexPattern);
        if (Blocks.Num() > 1) {
            UE_LOG(LogTemp, Display, TEXT("hdUE UDIM source %s: %d blocks"), *File, Blocks.Num());
            Texture->SetSourceBlocks(MoveTemp(Blocks));
        }
    });
    Super::ExecutePipeline(Container, Sources, ContentBasePath);
    Container->IterateNodesOfType<UInterchangeTexture2DFactoryNode>([&](const FString&, UInterchangeTexture2DFactoryNode* Factory) {
        FString Translated;
        Factory->GetCustomTranslatedTextureNodeUid(Translated);
        if (Factory->GetSourceBlocks().Num() > 1) Factory->SetCustomVirtualTextureStreaming(true);
        if (Normals.Contains(Translated)) {
            Factory->SetCustomCompressionSettings(TC_Normalmap);
            Factory->SetCustomSRGB(false);
            Factory->SetCustombFlipGreenChannel(true);
        }
    });
}
