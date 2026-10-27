#pragma once
#include "hdue/bridge.h"
#include "hdue/material_api.h"
#include "hdue/residency_api.h"
#include "hdue/asset_api.h"
#include "hdue/depth_api.h"
#include "hdue/lighting_api.h"
#include <filesystem>
#include <stdexcept>
#include <string>
#include <optional>

namespace hdue {
class Backend {
public:
    explicit Backend(const std::filesystem::path& dll);
    Backend(const Backend&) = delete;
    Backend& operator=(const Backend&) = delete;
    ~Backend();
    const HdueApi& api() const { return api_; }
    const HdueNativePickApi& native_pick_api() const { return nativePickApi_; }
    const HdueMaterialApi& material_api() const { return materialApi_; }
    const HdueResidencyApi& residency_api() const { return residencyApi_; }
    const HdueAssetApi& asset_api() const { return assetApi_; }
    const HdueDepthApi& depth_api() const { return depthApi_; }
    const HdueLightingApi& lighting_api() const { return lightingApi_; }
    void initialize(const HdueInitDesc& desc);
    void shutdown();
    bool initialized() const { return initialized_; }
    std::string status_message() const;
    void require(HdueResult result, const char* operation) const;
private:
    void* module_ = nullptr;
    HdueApi api_{};
    HdueNativePickApi nativePickApi_{};
    HdueMaterialApi materialApi_{};
    HdueResidencyApi residencyApi_{};
    HdueAssetApi assetApi_{};
    HdueDepthApi depthApi_{};
    HdueLightingApi lightingApi_{};
    bool initialized_ = false;
};

void write_pfm(const std::filesystem::path& path, const HdueFrameInfo& frame, const float* rgba);
std::optional<uint64_t> gpu_memory_bytes();
}

