#include "hdue/backend.hpp"
#include <bit>
#include <cmath>
#include <fstream>
#include <limits>

namespace hdue {
void write_pfm(const std::filesystem::path& path, const HdueFrameInfo& frame, const float* rgba) {
    if (!rgba || !frame.width || !frame.height || frame.pixel_format != HDUE_RGBA32_FLOAT ||
        frame.row_stride_bytes != frame.width * 4ull * sizeof(float))
        throw std::runtime_error("Invalid float image for PFM output");
    if (!path.parent_path().empty()) std::filesystem::create_directories(path.parent_path());
    std::ofstream out(path, std::ios::binary);
    if (!out) throw std::runtime_error("Cannot create PFM output");
    out << "PF\n" << frame.width << ' ' << frame.height << "\n"
        << (std::endian::native == std::endian::little ? "-1.0\n" : "1.0\n");
    // ABI images are top to bottom; PFM is bottom to top, with RGB float pixels.
    for (uint32_t y = frame.height; y-- > 0;) {
        for (uint32_t x = 0; x < frame.width; ++x) {
            const float* pixel = rgba + (uint64_t(y) * frame.width + x) * 4;
            for (unsigned c = 0; c < 3; ++c) {
                if (!std::isfinite(pixel[c])) throw std::runtime_error("Non-finite pixel in backend output");
            }
            out.write(reinterpret_cast<const char*>(pixel), 3 * sizeof(float));
        }
    }
    if (!out) throw std::runtime_error("Failed writing PFM output");
}
}

