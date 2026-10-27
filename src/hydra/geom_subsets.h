#pragma once
#include "delegate.h"
#include "pxr/imaging/hd/meshTopology.h"
#include "pxr/imaging/hd/meshUtil.h"
#include <stdexcept>
#include <algorithm>

PXR_NAMESPACE_OPEN_SCOPE
// The bridge has one material per mesh. Partition expanded triangles by their
// coarse USD face, preserving face-varying attributes and the original pick path.
inline std::vector<HdUEMeshData> HdUEPartitionSubsets(
    HdUEMeshData mesh, const HdMeshTopology& topology, const VtIntArray& faceParams) {
    if (topology.GetGeomSubsets().empty() || !mesh.sceneAsset.empty()) {
        std::vector<HdUEMeshData> result; result.emplace_back(std::move(mesh)); return result;
    }
    if (mesh.points.size() != faceParams.size() * 3 || mesh.normals.size() != mesh.points.size() ||
        (!mesh.uv.empty() && mesh.uv.size() != mesh.points.size()))
        throw std::invalid_argument("Invalid expanded subset attributes");
    std::vector<int> owners(topology.GetFaceVertexCounts().size(), 0);
    HdUEMeshData prototype;
    prototype.id=mesh.id; prototype.material=mesh.material; prototype.scenePath=mesh.scenePath;
    prototype.sceneAsset=mesh.sceneAsset; prototype.transform=mesh.transform; prototype.visible=mesh.visible;
    prototype.bindingPath=mesh.bindingPath; prototype.maskErrors=mesh.maskErrors;
    prototype.sourceId=mesh.sourceId;
    std::vector<HdUEMeshData> parts{prototype};
    for (const auto& subset : topology.GetGeomSubsets()) {
        if (subset.type != HdGeomSubset::TypeFaceSet || subset.materialId.IsEmpty()) continue;
        const int owner = int(parts.size());
        parts.push_back(prototype);
        parts.back().id = subset.id.GetString();
        parts.back().material = subset.materialId.GetString();
        parts.back().bindingPath = SdfPath(mesh.scenePath).AppendChild(subset.id.GetNameToken()).GetString();
        for (int face : subset.indices) {
            if (face < 0 || size_t(face) >= owners.size() || owners[face] != 0)
                throw std::invalid_argument("Invalid or overlapping materialBind face subsets");
            owners[face] = owner;
        }
    }
    for (auto& part : parts) {
        part.points.clear(); part.normals.clear(); part.uv.clear(); part.pickMesh.reset();
    }
    for (size_t triangle = 0; triangle < faceParams.size(); ++triangle) {
        const int face = HdMeshUtil::DecodeFaceIndexFromCoarseFaceParam(faceParams[triangle]);
        if (face < 0 || size_t(face) >= owners.size()) throw std::invalid_argument("Invalid subset coarse face");
        auto& part = parts[owners[face]];
        for (size_t corner = triangle * 3; corner < triangle * 3 + 3; ++corner) {
            part.points.push_back(mesh.points[corner]); part.normals.push_back(mesh.normals[corner]);
            if (!mesh.uv.empty()) part.uv.push_back(mesh.uv[corner]);
            part.sourceCorners.push_back(uint32_t(corner));
            for (const auto& [name, values] : mesh.masks) part.masks[name].push_back(values.at(corner));
        }
    }
    parts.erase(std::remove_if(parts.begin(), parts.end(), [](const auto& part) { return part.points.empty(); }), parts.end());
    return parts;
}
PXR_NAMESPACE_CLOSE_SCOPE
