#include "picking.h"
#include <algorithm>
#include <numeric>
#include <stdexcept>

PXR_NAMESPACE_OPEN_SCOPE
HdUEPickMesh::HdUEPickMesh(const std::vector<GfVec3f>& points) {
    if (points.size() % 3) throw std::invalid_argument("Pick mesh must contain expanded triangles");
    triangles_.resize(points.size() / 3);
    std::iota(triangles_.begin(), triangles_.end(), 0u);
    if (!triangles_.empty()) build(points, 0, uint32_t(triangles_.size()));
}
uint32_t HdUEPickMesh::build(const std::vector<GfVec3f>& points, uint32_t begin, uint32_t end) {
    const uint32_t index = uint32_t(nodes_.size());
    nodes_.emplace_back();
    GfRange3d bounds;
    for (uint32_t i = begin; i < end; ++i)
        for (uint32_t c = 0; c < 3; ++c) bounds.UnionWith(GfVec3d(points[size_t(triangles_[i]) * 3 + c]));
    nodes_[index].bounds = bounds;
    if (end - begin <= 8) {
        nodes_[index].begin = begin; nodes_[index].count = end - begin;
    } else {
        const auto size = bounds.GetSize();
        const int axis = size[0] > size[1] ? (size[0] > size[2] ? 0 : 2) : (size[1] > size[2] ? 1 : 2);
        const uint32_t middle = begin + (end - begin) / 2;
        const auto centroid = [&](uint32_t tri) {
            const size_t p = size_t(tri) * 3;
            return double(points[p][axis]) + points[p + 1][axis] + points[p + 2][axis];
        };
        std::nth_element(triangles_.begin() + begin, triangles_.begin() + middle, triangles_.begin() + end,
            [&](uint32_t a, uint32_t b) { return centroid(a) < centroid(b); });
        const uint32_t left = build(points, begin, middle);
        const uint32_t right = build(points, middle, end);
        nodes_[index].left = left; nodes_[index].right = right;
    }
    return index;
}
bool HdUEPickMesh::intersect(const std::vector<GfVec3f>& points, const GfRay& ray,
                            double minimum, double maximum, HdUEPickHit* hit, bool twoSided) const {
    if (nodes_.empty() || !hit || minimum > maximum) return false;
    std::vector<uint32_t> stack; stack.reserve(64); stack.push_back(0);
    bool found = false;
    while (!stack.empty()) {
        const Node& node = nodes_[stack.back()]; stack.pop_back();
        double enter = 0, exit = 0;
        if (!ray.Intersect(node.bounds, &enter, &exit) || enter > maximum || exit < minimum) continue;
        if (!node.count) { stack.push_back(node.left); stack.push_back(node.right); continue; }
        for (uint32_t i = node.begin; i < node.begin + node.count; ++i) {
            const uint32_t tri = triangles_[i]; const size_t p = size_t(tri) * 3;
            double distance = 0; GfVec3d barycentric; bool front = false;
            if (ray.Intersect(GfVec3d(points[p]), GfVec3d(points[p + 1]), GfVec3d(points[p + 2]),
                              &distance, &barycentric, &front, maximum) && distance >= minimum && (front || twoSided)) {
                maximum = distance;
                *hit = {distance, tri, barycentric}; found = true;
            }
        }
    }
    return found;
}
PXR_NAMESPACE_CLOSE_SCOPE
