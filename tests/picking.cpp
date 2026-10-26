#include "picking.h"
#include <cmath>
#include <iostream>
#include <stdexcept>
PXR_NAMESPACE_USING_DIRECTIVE
namespace { void check(bool ok, const char* what) { if (!ok) throw std::runtime_error(what); } }
int main() {
    try {
        const std::vector<GfVec3f> points{{0,0,0},{1,0,0},{0,1,0},{0,0,-2},{1,0,-2},{0,1,-2}};
        HdUEPickMesh mesh(points); HdUEPickHit hit;
        const GfRay ray(GfVec3d(.25,.25,2), GfVec3d(0,0,-1));
        check(mesh.intersect(points, ray, 0, 10, &hit) && hit.triangle == 0 && std::abs(hit.distance - 2) < 1e-9, "nearest triangle/occlusion failed");
        check(mesh.intersect(points, ray, 2.5, 10, &hit) && hit.triangle == 1 && std::abs(hit.distance - 4) < 1e-9, "near clipping failed");
        check(!mesh.intersect(points, ray, 0, 1, &hit), "far clipping failed");
        check(!mesh.intersect(points, GfRay(GfVec3d(2,2,2), GfVec3d(0,0,-1)), 0, 10, &hit), "background ray hit geometry");
        const GfRay back(GfVec3d(.25,.25,-3), GfVec3d(0,0,1));
        check(!mesh.intersect(points, back, 0, 10, &hit), "backface culling failed");
        check(mesh.intersect(points, back, 0, 10, &hit, true), "two-sided intersection failed");
        const HdUEPickMesh empty({}); check(!empty.intersect({}, ray, 0, 10, &hit), "empty mesh hit");
        GfMatrix4d transform(1); transform.SetScale(GfVec3d(-2,3,.5)); transform.SetTranslateOnly(GfVec3d(10,2,1));
        const auto origin = transform.Transform(GfVec3d(.25,.25,0)) + GfVec3d(0,0,4);
        const auto inverse = transform.GetInverse();
        check(mesh.intersect(points, GfRay(inverse.Transform(origin), inverse.TransformDir(GfVec3d(0,0,-1))), 0, 10, &hit)
            && std::abs(hit.distance - 4) < 1e-9, "nonuniform/negative scale changed the ray distance");
        std::vector<GfVec3f> many;
        for (int i = 0; i < 128; ++i) { const float x = float(i) * 2; many.insert(many.end(), {{x,0,0},{x+1,0,0},{x,1,0}}); }
        HdUEPickMesh tree(many);
        check(tree.intersect(many, GfRay(GfVec3d(200.2,.2,3), GfVec3d(0,0,-1)), 0, 10, &hit) && hit.triangle == 100, "BVH branch traversal failed");
        std::cout << "PASS: nearest hit, clipping, misses, scaled transforms and BVH traversal\n";
        return 0;
    } catch (const std::exception& e) { std::cerr << e.what() << '\n'; return 1; }
}
