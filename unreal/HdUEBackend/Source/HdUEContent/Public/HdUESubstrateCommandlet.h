#pragma once
#include "Commandlets/Commandlet.h"
#include "HdUESubstrateCommandlet.generated.h"
UCLASS()
class UHdUESubstrateCommandlet : public UCommandlet {
    GENERATED_BODY()
public:
    UHdUESubstrateCommandlet();
    virtual int32 Main(const FString& Params) override;
};
