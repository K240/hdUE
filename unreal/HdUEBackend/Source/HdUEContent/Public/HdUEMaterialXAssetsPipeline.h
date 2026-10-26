#pragma once
#include "InterchangeGenericAssetsPipeline.h"
#include "HdUEMaterialXAssetsPipeline.generated.h"

UCLASS()
class UHdUEMaterialXAssetsPipeline : public UInterchangeGenericAssetsPipeline {
    GENERATED_BODY()
protected:
    virtual void ExecutePipeline(UInterchangeBaseNodeContainer* Container, const TArray<UInterchangeSourceData*>& Sources, const FString& ContentBasePath) override;
};
