#pragma once
#include "pxr/imaging/hd/meshTopology.h"
#include "pxr/imaging/hd/meshUtil.h"
#include "pxr/base/vt/array.h"
#include <vector>
#include <algorithm>
#include <cmath>
#include <stdexcept>
PXR_NAMESPACE_OPEN_SCOPE
inline std::vector<float> HdUEExpandWetMask(const VtValue& raw, HdInterpolation domain, size_t pointCount,
    const HdMeshTopology& topology, const VtVec3iArray& triangles, const VtIntArray& parameters, HdMeshUtil& util) {
    VtFloatArray samples;
    if (domain==HdInterpolationConstant && raw.IsHolding<float>()) samples={raw.UncheckedGet<float>()};
    else if (raw.IsHolding<VtFloatArray>()) samples=raw.UncheckedGet<VtFloatArray>();
    else throw std::invalid_argument("Wet mask must be a float or float array");
    const size_t expected=domain==HdInterpolationConstant?1:domain==HdInterpolationUniform?topology.GetFaceVertexCounts().size():
        domain==HdInterpolationFaceVarying?topology.GetFaceVertexIndices().size():pointCount;
    if ((domain!=HdInterpolationConstant && domain!=HdInterpolationUniform && domain!=HdInterpolationVertex &&
         domain!=HdInterpolationVarying && domain!=HdInterpolationFaceVarying) || samples.size()!=expected ||
         !std::all_of(samples.begin(),samples.end(),[](float v){return std::isfinite(v);}))
        throw std::invalid_argument("Invalid wet mask interpolation, sample count or non-finite value");
    if (domain==HdInterpolationFaceVarying) {
        VtValue output;
        const auto result=util.ComputeTriangulatedFaceVaryingPrimvar(samples.cdata(),int(samples.size()),HdTypeFloat,&output);
        if (result==HdMeshComputationResult::Error) throw std::invalid_argument("Cannot triangulate face-varying wet mask");
        if (result!=HdMeshComputationResult::Unchanged) samples=output.Get<VtFloatArray>();
    }
    std::vector<float> result; result.reserve(triangles.size()*3);
    for (size_t tri=0;tri<triangles.size();++tri) for (int c:{0,1,2}) {
        const size_t index=domain==HdInterpolationConstant?0:domain==HdInterpolationUniform?
            size_t(HdMeshUtil::DecodeFaceIndexFromCoarseFaceParam(parameters[tri])):
            domain==HdInterpolationFaceVarying?tri*3+c:size_t(triangles[tri][c]);
        if (index>=samples.size()) throw std::invalid_argument("Wet mask index out of bounds");
        result.push_back(std::clamp(samples[index],0.f,1.f));
    }
    return result;
}
PXR_NAMESPACE_CLOSE_SCOPE
