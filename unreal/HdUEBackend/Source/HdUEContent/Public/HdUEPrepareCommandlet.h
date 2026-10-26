#pragma once
#include "Commandlets/Commandlet.h"
#include "HdUEPrepareCommandlet.generated.h"

UCLASS()
class UHdUEPrepareCommandlet : public UCommandlet
{
    GENERATED_BODY()
public:
    UHdUEPrepareCommandlet();
    virtual int32 Main(const FString& Params) override;
};

