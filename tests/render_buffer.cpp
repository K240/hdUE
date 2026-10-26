#include "delegate.h"
#include "depth.h"
#include <iostream>
#include <chrono>
#include <iomanip>
#include <string_view>
#include <thread>
PXR_NAMESPACE_USING_DIRECTIVE
namespace {
void check(bool ok, const char* text) { if (!ok) throw std::runtime_error(text); }
}
int main(int argc, char** argv) {
    try {
        if (argc == 2 && std::string_view(argv[1]) == "--benchmark") {
            std::cout << "{\"map_cycles\":50,\"measurements\":[";
            bool comma = false;
            for (const GfVec3i size : {GfVec3i(1920, 1080, 1), GfVec3i(3840, 2160, 1)}) {
                HdUERenderBuffer image(SdfPath("/benchmark"));
                check(image.Allocate(size, HdFormatFloat32Vec4, false), "benchmark allocation failed");
                double checksum = 0;
                const auto start = std::chrono::steady_clock::now();
                for (int i = 0; i < 50; ++i) {
                    const float* pixels = static_cast<const float*>(image.Map());
                    check(pixels != nullptr, "benchmark map failed");
                    checksum += pixels[(size_t(size[0]) * size[1] - 1) * 4];
                    image.Unmap();
                }
                const auto milliseconds = std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - start).count() / 50;
                if (comma) std::cout << ',';
                comma = true;
                std::cout << std::setprecision(9) << "{\"width\":" << size[0] << ",\"height\":" << size[1]
                          << ",\"map_unmap_mean_ms\":" << milliseconds << ",\"checksum\":" << checksum << '}';
            }
            std::cout << "]}\n";
            return 0;
        }
        HdUERenderBuffer buffer(SdfPath("/test"));
        check(!buffer.Allocate(GfVec3i(-1, 1, 1), HdFormatFloat32Vec4, false), "negative allocation accepted");
        check(!buffer.Allocate(GfVec3i(2, 1, 1), HdFormatFloat16Vec4, false), "unsupported format accepted");
        check(buffer.Allocate(GfVec3i(2, 1, 1), HdFormatFloat32Vec4, false), "valid allocation rejected");
        HdueFrameInfo frame{}; frame.width = 2; frame.height = 1; frame.converged = 1;
        buffer.publish(frame, std::vector<float>(8, 1));
        const auto* pinned = static_cast<const float*>(buffer.Map());
        check(pinned && pinned[0] == 1, "initial image missing");
        check(!buffer.Allocate(GfVec3i(3, 2, 1), HdFormatFloat32Vec4, false), "mapped image was reallocated");
        std::thread publisher([&] { for (int i = 0; i < 100; ++i) buffer.publish(frame, std::vector<float>(8, 2)); });
        publisher.join();
        for (int i = 0; i < 8; ++i) check(pinned[i] == 1, "published frames modified mapped memory");
        buffer.Unmap();
        const auto* fresh = static_cast<const float*>(buffer.Map());
        for (int i = 0; i < 8; ++i) check(fresh[i] == 2, "next Map did not see the completed frame");
        buffer.Unmap();
        buffer.invalidate(); check(!buffer.IsConverged(), "new generation kept old convergence");
        check(buffer.Allocate(GfVec3i(2, 2, 1), HdFormatFloat32Vec4, false), "2D allocation failed");
        frame.height = 2;
        std::vector<float> asymmetric(16, 0);
        asymmetric[0] = 3; asymmetric[8] = 7;
        buffer.publish(frame, std::move(asymmetric));
        const auto* oriented = static_cast<const float*>(buffer.Map());
        check(oriented[0] == 7 && oriented[8] == 3, "UE top-down image was not converted to Hydra lower-left AOV");
        const auto* nested = static_cast<const float*>(buffer.Map());
        check(nested == oriented, "nested Map changed the pinned allocation");
        buffer.publish(frame, std::vector<float>(16, 9));
        buffer.Unmap();
        check(buffer.IsMapped() && oriented[0] == 7, "partial Unmap released a live snapshot");
        buffer.Finalize(nullptr);
        check(oriented[0] == 7 && buffer.Map() == oriented, "deallocation invalidated a live mapped snapshot");
        buffer.Unmap(); buffer.Unmap();
        check(!buffer.IsMapped() && buffer.Map() == nullptr, "deallocated buffer exposed stale pixels");
        check(buffer.Allocate(GfVec3i(1, 1, 1), HdFormatFloat32Vec4, false), "reallocation after snapshot release failed");
        check(static_cast<const float*>(buffer.Map())[0] == 0, "reallocation retained stale image data");
        buffer.Unmap();
        check(buffer.Allocate(GfVec3i(2, 1, 1), HdFormatFloat32Vec4, true), "progressive AOV allocation rejected");
        check(buffer.IsMultiSampled(), "multiSampled descriptor was lost");
        frame.height=1; buffer.publish(frame,std::vector<float>(8,5)); buffer.Resolve();
        check(static_cast<const float*>(buffer.Map())[0]==5, "resolved progressive image missing"); buffer.Unmap();
        HdUERenderBuffer depth(SdfPath("/depth"));
        check(depth.Allocate(GfVec3i(2,2,1),HdFormatFloat32,false),"scalar depth allocation rejected");
        check(depth.GetFormat()==HdFormatFloat32,"depth format lost");
        frame.height=2; depth.publish(frame,{.1f,.2f,.7f,1.f});
        const auto* z=static_cast<const float*>(depth.Map());
        check(z[0]==.7f && z[1]==1.f && z[2]==.1f && z[3]==.2f,"depth row orientation incorrect");
        depth.Unmap();
        GfMatrix4d projection(0.);
        const double nearPlane=1,farPlane=1000;
        projection[0][0]=projection[1][1]=1;
        projection[2][2]=-(farPlane+nearPlane)/(farPlane-nearPlane);
        projection[2][3]=-1;
        projection[3][2]=-2*farPlane*nearPlane/(farPlane-nearPlane);
        check(HdUEProjectDepth(0,projection,1)==1,"background depth must be far");
        check(std::abs(HdUEProjectDepth(1,projection,1))<1e-6,"near depth projection incorrect");
        check(std::abs(HdUEProjectDepth(1000,projection,1)-1)<1e-6,"far depth projection incorrect");
        check(std::abs(HdUEProjectDepth(10,projection,1)-.9009009f)<1e-6,"perspective depth projection incorrect");
        check(std::abs(HdUEProjectDepth(1000,projection,100)-.9009009f)<1e-6,"meter stage depth conversion incorrect");
        std::cout << "PASS: stable mapped snapshots, concurrent publication, allocation and convergence\n";
        return 0;
    } catch (const std::exception& e) { std::cerr << e.what() << '\n'; return 1; }
}
