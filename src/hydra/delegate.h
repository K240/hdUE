#pragma once
#include "hdue/backend.hpp"
#include "picking.h"
#include "pxr/pxr.h"
#include "pxr/imaging/hd/renderDelegate.h"
#include "pxr/imaging/hd/renderBuffer.h"
#include "pxr/base/gf/matrix4d.h"
#include "pxr/base/gf/vec2f.h"
#include "pxr/base/gf/vec3f.h"
#include <map>
#include <set>
#include <mutex>
#include <vector>

#ifdef HDUE_HYDRA_EXPORTS
#define HDUE_HYDRA_API __declspec(dllexport)
#else
#define HDUE_HYDRA_API __declspec(dllimport)
#endif

PXR_NAMESPACE_OPEN_SCOPE
struct HdUEMeshData {
    std::string id, material, scenePath, sceneAsset;
    std::vector<GfVec3f> points, normals;
    std::vector<GfVec2f> uv;
    GfMatrix4d transform{1};
    bool visible = true;
    std::shared_ptr<const HdUEPickMesh> pickMesh;
};
struct HdUEMaterialData {
    std::string id, texture;
    GfVec3f color{0.7f};
    float metallic = 0, roughness = 0.5f, opacity = 1;
};
struct HdUELightData {
    std::string id;
    GfMatrix4d transform{1};
    GfVec3f color{1};
    uint32_t type = HDUE_DISTANT_LIGHT;
    float intensity = 1, width = 1, height = 1;
    bool visible = true, viewer = false;
};
struct HdUEState : HdRenderParam {
    std::mutex mutex;
    std::map<std::string, HdUEMeshData> meshes;
    std::map<std::string, HdUEMaterialData> materials;
    std::map<std::string, HdUELightData> lights;
    std::set<std::string> dirtyMeshes, dirtyMaterials, dirtyLights, removed;
    HdRenderSettingsMap settings;
    std::shared_ptr<hdue::Backend> backend;
    HdueSession session = 0;
    uint32_t width = 0, height = 0;
    uint64_t revision = 1, submittedRevision = 0, generation = 0;
    uint64_t requestSettingsRevision = 1;
    uint64_t submittedRequestSettingsRevision = 0;
    uint64_t publishedFrames = 0, sceneSubmissions = 0, coalescedCameraUpdates = 0;
    uint64_t staleFrameRetries = 0;
    uint64_t submittedMeshes = 0, submittedMaterials = 0, materialParameterUpdates = 0;
    uint64_t gpuLocalMemoryBytes = 0;
    bool gpuMemoryAvailable = false;
    int viewerLights = 0;
    bool autoLightActive = false;
    double requestCpuMsTotal = 0;
    std::string error;
    HdueFrameInfo lastFrame{};
    HdueRendererInfo rendererInfo{sizeof(HdueRendererInfo)};
    uint32_t createThread = 0, executeThread = 0;
    VtDictionary pickResult;
    std::map<std::string, uint32_t> pickMaterialFlags;
    bool firstBatch = true;
    bool assetRequestPending = false;
    HdueAssetSceneStatus nativeAssets{sizeof(HdueAssetSceneStatus)};
    uint32_t submittedNativeLights = 0;
    ~HdUEState() override;
    void erase(const std::string& id);
    template<class T> T setting(const char* key, T fallback) const {
        const auto it = settings.find(TfToken(key));
        return it != settings.end() && it->second.IsHolding<T>() ? it->second.UncheckedGet<T>() : fallback;
    }
    GfVec3d convert(const GfVec3d& point, bool direction = false) const;
};

class HDUE_HYDRA_API HdUERenderBuffer final : public HdRenderBuffer {
public:
    explicit HdUERenderBuffer(const SdfPath& id);
    bool Allocate(const GfVec3i&, HdFormat, bool) override;
    unsigned int GetWidth() const override;
    unsigned int GetHeight() const override;
    unsigned int GetDepth() const override { return 1; }
    HdFormat GetFormat() const override { std::lock_guard lock(mutex_); return format_; }
    bool IsMultiSampled() const override { std::lock_guard lock(mutex_); return multiSampled_; }
    void* Map() override;
    void Unmap() override;
    bool IsMapped() const override;
    void Resolve() override {}
    bool IsConverged() const override;
    void publish(const HdueFrameInfo&, std::vector<float> pixels);
    void invalidate();
    std::string allocationError() const { std::lock_guard lock(mutex_); return allocationError_; }
protected:
    void _Deallocate() override;
private:
    mutable std::mutex mutex_;
    uint32_t width_ = 0, height_ = 0, maps_ = 0;
    HdFormat format_ = HdFormatFloat32Vec4;
    bool converged_ = false, multiSampled_ = false;
    std::string allocationError_;
    std::shared_ptr<std::vector<float>> pixels_, pinned_;
};

class HDUE_HYDRA_API HdUERenderDelegate final : public HdRenderDelegate {
public:
    explicit HdUERenderDelegate(const HdRenderSettingsMap& settings);
    ~HdUERenderDelegate() override;
    const TfTokenVector& GetSupportedRprimTypes() const override;
    const TfTokenVector& GetSupportedSprimTypes() const override;
    const TfTokenVector& GetSupportedBprimTypes() const override;
    HdRenderParam* GetRenderParam() const override { return state_.get(); }
    HdResourceRegistrySharedPtr GetResourceRegistry() const override { return registry_; }
    HdRenderPassSharedPtr CreateRenderPass(HdRenderIndex*, const HdRprimCollection&) override;
    HdInstancer* CreateInstancer(HdSceneDelegate*, const SdfPath&) override;
    void DestroyInstancer(HdInstancer*) override;
    HdRprim* CreateRprim(const TfToken&, const SdfPath&) override;
    void DestroyRprim(HdRprim*) override;
    HdSprim* CreateSprim(const TfToken&, const SdfPath&) override;
    HdSprim* CreateFallbackSprim(const TfToken&) override;
    void DestroySprim(HdSprim*) override;
    HdBprim* CreateBprim(const TfToken&, const SdfPath&) override;
    HdBprim* CreateFallbackBprim(const TfToken&) override;
    void DestroyBprim(HdBprim*) override;
    void CommitResources(HdChangeTracker*) override {}
    HdAovDescriptor GetDefaultAovDescriptor(const TfToken&) const override;
    HdRenderSettingDescriptorList GetRenderSettingDescriptors() const override;
    void SetRenderSetting(const TfToken&, const VtValue&) override;
    VtDictionary GetRenderStats() const override;
    bool InvokeCommand(const TfToken&, const HdCommandArgs&) override;
private:
    std::shared_ptr<HdUEState> state_;
    HdResourceRegistrySharedPtr registry_;
};
PXR_NAMESPACE_CLOSE_SCOPE

