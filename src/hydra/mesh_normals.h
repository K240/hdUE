#pragma once
#include "pxr/pxr.h"
#include "pxr/base/gf/vec3d.h"
#include "pxr/base/gf/vec3f.h"
#include "pxr/base/gf/vec3i.h"
#include "pxr/base/vt/array.h"
#include <vector>
#include <cmath>
#include <stdexcept>

PXR_NAMESPACE_OPEN_SCOPE
inline std::vector<GfVec3f> HdUEComputeSmoothNormals(const VtVec3fArray& points, const VtVec3iArray& triangles) {
    std::vector<GfVec3d> sums(points.size(),GfVec3d(0));
    for (const auto& triangle:triangles) {
        for (int corner:{0,1,2}) if (triangle[corner]<0 || size_t(triangle[corner])>=points.size())
            throw std::invalid_argument("Invalid normal-generation vertex index");
        const auto area=GfCross(GfVec3d(points[triangle[1]])-GfVec3d(points[triangle[0]]),
                                GfVec3d(points[triangle[2]])-GfVec3d(points[triangle[0]]));
        if (!std::isfinite(area.GetLengthSq())) throw std::invalid_argument("Nonfinite mesh normal");
        for (int corner:{0,1,2}) sums[triangle[corner]]+=area;
    }
    for (auto& normal:sums) if (normal.GetLengthSq()>1e-24) normal.Normalize();
    std::vector<GfVec3f> expanded; expanded.reserve(triangles.size()*3);
    for (const auto& triangle:triangles) for (int corner:{0,1,2}) expanded.emplace_back(sums[triangle[corner]]);
    return expanded;
}
PXR_NAMESPACE_CLOSE_SCOPE
