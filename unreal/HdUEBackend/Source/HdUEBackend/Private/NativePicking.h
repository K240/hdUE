#pragma once
#include "CoreMinimal.h"
#include "hdue/bridge.h"
HdueResult HdueGetPickMaterialInfo(const char* Path, HduePickMaterialInfo& Info, FString& Error);
