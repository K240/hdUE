#pragma once
#include "pxr/base/gf/matrix4d.h"
#include <algorithm>
#include <cmath>

PXR_NAMESPACE_OPEN_SCOPE
// UE's depth extension returns axial distance in centimeters. Reproject with
// the exact Hydra projection used for that generation, including finite far
// clipping. OpenGL/Hydra depth is [0,1], with 1 for background.
inline float HdUEProjectDepth(float centimeters,const GfMatrix4d& projection,double centimetersPerUnit) {
    if(!std::isfinite(centimeters) || centimeters<=0 || centimetersPerUnit<=0) return 1.f;
    const double z=-double(centimeters)/centimetersPerUnit;
    const double clipZ=z*projection[2][2]+projection[3][2];
    const double clipW=z*projection[2][3]+projection[3][3];
    if(!std::isfinite(clipZ) || !std::isfinite(clipW) || clipW<=0) return 1.f;
    return float(std::clamp((clipZ/clipW+1.)*.5,0.,1.));
}
PXR_NAMESPACE_CLOSE_SCOPE
