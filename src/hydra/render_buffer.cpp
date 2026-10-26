#include "delegate.h"
#include <algorithm>
PXR_NAMESPACE_OPEN_SCOPE
HdUERenderBuffer::HdUERenderBuffer(const SdfPath& id) : HdRenderBuffer(id) {}
bool HdUERenderBuffer::Allocate(const GfVec3i& dimensions, HdFormat format, bool multi) {
    std::lock_guard lock(mutex_);
    if (maps_ || dimensions[0] < 1 || dimensions[1] < 1 || dimensions[0] > 8192 || dimensions[1] > 8192 ||
        dimensions[2] != 1 || (format != HdFormatFloat32Vec4 && format != HdFormatFloat32)) {
        allocationError_="dimensions="+std::to_string(dimensions[0])+","+std::to_string(dimensions[1])+","+std::to_string(dimensions[2])+
            " format="+std::to_string(int(format))+" multisampled="+std::to_string(multi)+" maps="+std::to_string(maps_);
        return false;
    }
    allocationError_.clear();
    width_ = dimensions[0]; height_ = dimensions[1]; format_=format;
    // UE publishes already resolved progressive images. Map and Resolve see
    // that same completed image, including Houdini's multiSampled request.
    multiSampled_=multi;
    pixels_ = std::make_shared<std::vector<float>>(size_t(width_) * height_ * HdGetComponentCount(format_), 0.0f);
    converged_ = false;
    return true;
}
unsigned HdUERenderBuffer::GetWidth() const { std::lock_guard lock(mutex_); return width_; }
unsigned HdUERenderBuffer::GetHeight() const { std::lock_guard lock(mutex_); return height_; }
void* HdUERenderBuffer::Map() {
    std::lock_guard lock(mutex_);
    if ((!pixels_ || pixels_->empty()) && !maps_) return nullptr;
    // Pin the immutable published allocation. A newer frame replaces pixels_
    // without copying this image or invalidating any active Map pointers.
    if (maps_++ == 0) pinned_=pixels_;
    return pinned_->data();
}
void HdUERenderBuffer::Unmap() { std::lock_guard lock(mutex_); if (maps_ && --maps_ == 0) pinned_.reset(); }
bool HdUERenderBuffer::IsMapped() const { std::lock_guard lock(mutex_); return maps_ > 0; }
bool HdUERenderBuffer::IsConverged() const { std::lock_guard lock(mutex_); return converged_; }
void HdUERenderBuffer::invalidate() { std::lock_guard lock(mutex_); converged_ = false; }
void HdUERenderBuffer::_Deallocate() { std::lock_guard lock(mutex_); pixels_.reset(); if (!maps_) pinned_.reset(); width_ = height_ = 0; converged_ = false;
}
void HdUERenderBuffer::publish(const HdueFrameInfo& frame, std::vector<float> pixels) {
    std::lock_guard lock(mutex_);
    const size_t channels=HdGetComponentCount(format_);
    if (frame.width != width_ || frame.height != height_ || pixels.size() != size_t(width_) * height_ * channels) return;
    // UE's C ABI is top-down. Hydra/Hdx CPU AOVs use a lower-left origin.
    const size_t row = size_t(width_) * channels;
    for (uint32_t y = 0; y < height_ / 2; ++y)
        std::swap_ranges(pixels.begin() + y * row, pixels.begin() + (y + 1) * row,
                         pixels.begin() + (height_ - 1 - y) * row);
    pixels_ = std::make_shared<std::vector<float>>(std::move(pixels));
    converged_ = frame.converged != 0;
}
PXR_NAMESPACE_CLOSE_SCOPE

