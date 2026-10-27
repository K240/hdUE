#pragma once
#include "CoreMinimal.h"
class UMaterial;
class FJsonObject;
// Creates separate assets; the imported base graph is never rewired in place.
TSharedPtr<FJsonObject> HdUEPrepareWetMaterials(UMaterial* Base);
