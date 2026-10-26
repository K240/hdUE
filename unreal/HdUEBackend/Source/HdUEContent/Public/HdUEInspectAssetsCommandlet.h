#pragma once
#include "Commandlets/Commandlet.h"
#include "HdUEInspectAssetsCommandlet.generated.h"

UCLASS()
class UHdUEInspectAssetsCommandlet : public UCommandlet {
    GENERATED_BODY()
public:
    UHdUEInspectAssetsCommandlet();
    int32 Main(const FString& Params) override;
};
