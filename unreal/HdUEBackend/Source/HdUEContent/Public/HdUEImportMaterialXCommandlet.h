#pragma once
#include "Commandlets/Commandlet.h"
#include "HdUEImportMaterialXCommandlet.generated.h"

UCLASS()
class UHdUEImportMaterialXCommandlet : public UCommandlet {
    GENERATED_BODY()
public:
    UHdUEImportMaterialXCommandlet();
    virtual int32 Main(const FString& Params) override;
};
