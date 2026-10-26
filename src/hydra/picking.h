#pragma once
#include "pxr/pxr.h"
#include "pxr/base/gf/ray.h"
#include "pxr/base/gf/range3d.h"
#include "pxr/base/gf/vec3f.h"
#include <cstdint>
#include <vector>

PXR_NAMESPACE_OPEN_SCOPE
struct HdUEPickHit {
    double distance = 0;
    uint32_t triangle = 0;
    GfVec3d barycentric{0};
};

// Immutable local-space BVH. Triangle storage stays with the corresponding
// HdUEMeshData; both are replaced together by Sync, under the scene mutex.
class HdUEPickMesh {
public:
    explicit HdUEPickMesh(const std::vector<GfVec3f>& points);
    bool intersect(const std::vector<GfVec3f>& points, const GfRay& ray,
                   double minimum, double maximum, HdUEPickHit* hit, bool twoSided = false) const;
private:
    struct Node { GfRange3d bounds; uint32_t begin = 0, count = 0, left = 0, right = 0; };
    std::vector<uint32_t> triangles_;
    std::vector<Node> nodes_;
    uint32_t build(const std::vector<GfVec3f>& points, uint32_t begin, uint32_t end);
};
PXR_NAMESPACE_CLOSE_SCOPE
