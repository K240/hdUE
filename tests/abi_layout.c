#include "hdue/bridge.h"
#include "hdue/material_api.h"
#include "hdue/residency_api.h"
#include "hdue/asset_api.h"
#include "hdue/viewport_api.h"
#include "hdue/depth_api.h"
#include "hdue/lighting_api.h"
_Static_assert(sizeof(HdueLight) == 184, "extended light layout");
_Static_assert(sizeof(HdueLightBatch) == 16, "light batch layout");
_Static_assert(sizeof(HdueLightingStatus) == 56, "lighting status layout");
_Static_assert(sizeof(HdueLightingApi) == 24, "lighting API layout");
#include <stddef.h>
_Static_assert(sizeof(HdueViewportMaterial) == 32, "viewport material binding layout");
_Static_assert(sizeof(HdueViewportMaterials) == 32, "viewport materials layout");

_Static_assert(sizeof(HdueSession) == 8, "64-bit session handles");
_Static_assert(offsetof(HdueInitDesc, instance_handle) == 8, "C ABI alignment");
_Static_assert(sizeof(HdueInitDesc) == 48, "InitDesc C ABI layout");
_Static_assert(sizeof(HdueStatus) == 552, "Status C ABI layout");
_Static_assert(sizeof(HdueSessionDesc) == 16, "SessionDesc C ABI layout");
_Static_assert(sizeof(HdueRenderRequest) == 120, "RenderRequest C ABI layout");
_Static_assert(sizeof(HdueFrameInfo) == 72, "FrameInfo C ABI layout");
_Static_assert(sizeof(HdueSceneBatch) == 56, "Scene batch C ABI layout");
_Static_assert(sizeof(HdueApi) == 96, "Function table C ABI layout");
_Static_assert(sizeof(HdueNativeRay) == 72, "Native ray C ABI layout");
_Static_assert(sizeof(HdueNativeHit) == 72, "Native hit C ABI layout");
_Static_assert(sizeof(HduePickMaterialInfo) == 8, "Material info C ABI layout");
_Static_assert(sizeof(HdueNativePickApi) == 24, "Native pick extension C ABI layout");
_Static_assert(sizeof(HdueRendererInfo) == 20, "Renderer info C ABI layout");
_Static_assert(sizeof(HdueMaterialParameter) == 40, "Material parameter C ABI layout");
_Static_assert(sizeof(HdueMaterialUpdate) == 24, "Material update C ABI layout");
_Static_assert(sizeof(HdueMaterialStats) == 32, "Material stats C ABI layout");
_Static_assert(sizeof(HdueMaterialApi) == 32, "Material extension C ABI layout");
_Static_assert(sizeof(HdueResidencyOptions) == 16, "Residency options C ABI layout");
_Static_assert(sizeof(HdueResidencyStatus) == 584, "Residency status C ABI layout");
_Static_assert(sizeof(HdueResidencyApi) == 24, "Residency extension C ABI layout");
int main(void) { return 0; }
_Static_assert(sizeof(HdueAssetDesc) == 152, "Asset descriptor C ABI layout");
_Static_assert(sizeof(HdueAssetStatus) == 584, "Asset status C ABI layout");
_Static_assert(sizeof(HdueAssetSceneStatus) == 544, "Asset scene status C ABI layout");
_Static_assert(sizeof(HdueAssetApi) == 40, "Asset extension C ABI layout");
_Static_assert(sizeof(HdueViewportStatus) == 648, "Houdini viewport status C ABI layout");
_Static_assert(sizeof(HdueViewportOptions) == 40, "Houdini viewport options C ABI layout");
_Static_assert(sizeof(HdueDepthApi) == 24, "Optional depth extension C ABI layout");

