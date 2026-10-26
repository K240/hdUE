#include "hdue/backend.hpp"
#include <dxgi1_4.h>
#include <wrl/client.h>
namespace hdue {
std::optional<uint64_t> gpu_memory_bytes() {
    using Microsoft::WRL::ComPtr;
    ComPtr<IDXGIFactory1> factory;
    if (FAILED(CreateDXGIFactory1(IID_PPV_ARGS(&factory)))) return std::nullopt;
    bool available = false;
    uint64_t total = 0;
    for (UINT index = 0; ; ++index) {
        ComPtr<IDXGIAdapter1> adapter;
        if (factory->EnumAdapters1(index, &adapter) == DXGI_ERROR_NOT_FOUND) break;
        if (!adapter) break;
        ComPtr<IDXGIAdapter3> memory;
        if (FAILED(adapter.As(&memory))) continue;
        DXGI_QUERY_VIDEO_MEMORY_INFO info{};
        if (SUCCEEDED(memory->QueryVideoMemoryInfo(0, DXGI_MEMORY_SEGMENT_GROUP_LOCAL, &info))) {
            available = true;
            total += info.CurrentUsage; // Per-process local-segment usage, summed over adapters.
        }
    }
    return available ? std::optional<uint64_t>(total) : std::nullopt;
}
}
