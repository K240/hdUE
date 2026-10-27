#include "delegate.h"
#include "depth.h"
#include "pxr/imaging/hd/renderPass.h"
#include "pxr/imaging/hd/renderIndex.h"
#include "pxr/imaging/hd/rprim.h"
#include "pxr/imaging/hd/renderPassState.h"
#include "pxr/imaging/hd/camera.h"
#include "pxr/imaging/hd/instancer.h"
#include "pxr/imaging/hd/resourceRegistry.h"
#include "pxr/imaging/hd/tokens.h"
#include "pxr/base/gf/vec4f.h"
#include "pxr/base/tf/diagnostic.h"
#include "pxr/base/tf/getenv.h"
#include <cmath>
#include <chrono>
#include <numeric>
#include <limits>
#include <algorithm>
#if defined(HDUE_HOUDINI)
#include "../houdini/diagnostics.h"
#include <windows.h>
#endif

PXR_NAMESPACE_OPEN_SCOPE
HdRprim* HdUECreateMesh(const SdfPath&);
HdSprim* HdUECreateMaterial(const SdfPath&);
HdSprim* HdUECreateLight(const SdfPath&, uint32_t);

HdUEState::~HdUEState() { if (session && backend) backend->api().destroy_session(session); }
void HdUEState::erase(const std::string& id) {
    std::lock_guard lock(mutex);
    meshes.erase(id); materials.erase(id); lights.erase(id);
    dirtyMeshes.erase(id); dirtyMaterials.erase(id); dirtyLights.erase(id);
    removed.insert(id); ++revision;
}
GfVec3d HdUEState::convert(const GfVec3d& p, bool direction) const {
    const double scale = direction ? 1 : setting<double>("metersPerUnit", 0.01) * 100;
    return (setting<std::string>("upAxis", "Y") == "Z" ? GfVec3d(p[0], -p[1], p[2]) : GfVec3d(-p[2], p[0], p[1])) * scale;
}
namespace {
constexpr const char* AutoKey = "hdUE:viewer:key";
constexpr const char* AutoFill = "hdUE:viewer:fill";
bool active(const HdUELightData& light) {
    return light.visible && std::isfinite(light.intensity) && light.intensity > 0 &&
        (light.color[0] > 0 || light.color[1] > 0 || light.color[2] > 0);
}
std::string asset(const HdUEState& state, const char* key, const std::string& id) {
    const auto mapping = state.setting<VtDictionary>(key, {});
    const auto it = mapping.find(id);
    return it != mapping.end() && it->second.IsHolding<std::string>() ? it->second.UncheckedGet<std::string>() : "";
}
void submit(HdUEState& state) {
    std::vector<HdueMeshDesc> meshes;
    std::vector<HdueAssetDesc> sceneAssets;
    std::vector<std::vector<float>> positions, normals, uvs;
    std::vector<std::vector<uint32_t>> indices;
    std::vector<std::string> nativeMeshes, nativeMaterials;
    std::vector<HdueMaterialDesc> materials;
    std::vector<HdueLightDesc> lights;
    std::vector<HdueLight> extendedLights;
    std::vector<const char*> removed;
    // Sync has joined before Execute. The lock also protects stats/settings access.
    std::lock_guard lock(state.mutex);
    const auto count = state.dirtyMeshes.size();
    positions.reserve(count); normals.reserve(count); uvs.reserve(count); indices.reserve(count); nativeMeshes.reserve(count);
    nativeMaterials.reserve(state.dirtyMaterials.size());
    for (const auto& id : state.dirtyMaterials) {
        const auto& data = state.materials.at(id);
        nativeMaterials.push_back(asset(state, "nativeMaterialAssets", id));
        HdueMaterialDesc desc{};
        desc.id = data.id.c_str(); desc.native_asset = nativeMaterials.back().c_str(); desc.base_color_texture = data.texture.c_str();
        for (int i = 0; i < 3; ++i) desc.base_color[i] = data.color[i];
        desc.base_color[3] = data.opacity; desc.metallic = data.metallic; desc.roughness = data.roughness;
        materials.push_back(desc);
        if (TfGetenvBool("HDUE_TRACE_SCENE", false)) TF_STATUS("hdUE material %s rgb=(%g,%g,%g)", desc.id, desc.base_color[0], desc.base_color[1], desc.base_color[2]);
    }
    for (const auto& id : state.dirtyMeshes) {
        const auto& data = state.meshes.at(id);
        if (!data.sceneAsset.empty()) {
            HdueAssetDesc d{}; d.struct_size=sizeof(d); d.id=data.id.c_str(); d.asset_path=data.sceneAsset.c_str(); d.visible=data.visible;
            GfMatrix4d basis(1);
            for (int r=0; r<3; ++r) { GfVec3d e(0); e[r]=1; const auto v=state.convert(e); for (int c=0; c<3; ++c) basis[r][c]=v[c]; }
            const auto t=basis.GetInverse()*data.transform*basis;
            for (int r=0; r<4; ++r) for (int c=0; c<4; ++c) d.transform[r*4+c]=t[r][c];
            sceneAssets.push_back(d); removed.push_back(data.id.c_str());
            continue;
        }
        nativeMeshes.push_back(asset(state, "nativeMeshAssets", id));
        positions.emplace_back(); normals.emplace_back(); uvs.emplace_back(); indices.emplace_back();
        auto& p = positions.back(); auto& n = normals.back(); auto& uv = uvs.back(); auto& index = indices.back();
        const auto normalTransform = data.transform.GetInverse().GetTranspose();
        for (size_t i = 0; i < data.points.size(); ++i) {
            const auto point = state.convert(data.transform.Transform(GfVec3d(data.points[i])));
            for (int c = 0; c < 3; ++c) p.push_back(float(point[c]));
            if (data.normals.size() == data.points.size()) {
                auto normal = state.convert(normalTransform.TransformDir(GfVec3d(data.normals[i])), true).GetNormalized();
                for (int c = 0; c < 3; ++c) n.push_back(float(normal[c]));
            }
            if (data.uv.size() == data.points.size()) { uv.push_back(data.uv[i][0]); uv.push_back(1 - data.uv[i][1]); }
        }
        index.resize(data.points.size()); std::iota(index.begin(), index.end(), 0u);
        // World transforms are baked into dynamic vertices. Preserve front
        // faces when an authored transform adds another handedness reversal.
        if (data.transform.GetDeterminant() < 0)
            for (size_t i = 0; i + 2 < index.size(); i += 3) std::swap(index[i + 1], index[i + 2]);
        HdueMeshDesc desc{};
        desc.id = data.id.c_str(); desc.material_id = data.material.c_str(); desc.native_asset = nativeMeshes.back().c_str();
        desc.positions = p.data(); desc.normals = n.empty() ? nullptr : n.data(); desc.uv = uv.empty() ? nullptr : uv.data();
        desc.indices = index.data(); desc.vertex_count = uint32_t(data.points.size()); desc.index_count = uint32_t(index.size()); desc.visible = data.visible;
        desc.flags = state.setting<bool>("requireNanite", false) && !nativeMeshes.back().empty() ? HDUE_MESH_REQUIRE_NANITE : 0;
        for (int i = 0; i < 4; ++i) desc.transform[i * 4 + i] = 1;
        if (!nativeMeshes.back().empty()) {
            // The cooked sample cube has side 100 cm. Native geometry keeps its
            // own dimensions; only its placement transform comes from Hydra.
            GfMatrix4d basis(1);
            for (int r = 0; r < 3; ++r) {
                GfVec3d e(0); e[r] = 1;
                const auto v = state.convert(e);
                for (int c = 0; c < 3; ++c) basis[r][c] = v[c];
            }
            const auto transform = basis.GetInverse() * data.transform * basis;
            for (int r = 0; r < 4; ++r) for (int c = 0; c < 4; ++c) desc.transform[r * 4 + c] = transform[r][c];
        }
        meshes.push_back(desc);
        if (TfGetenvBool("HDUE_TRACE_SCENE", false)) TF_STATUS("hdUE mesh %s material=%s first=(%g,%g,%g)", desc.id, desc.material_id, p.empty()?0:p[0], p.empty()?0:p[1], p.empty()?0:p[2]);
    }
    const double viewerIntensity = state.setting<double>("viewerLightIntensity", state.setting<float>("viewerLightIntensity", 4.f));
    if (!std::isfinite(viewerIntensity) || viewerIntensity < 0 || viewerIntensity > 100)
        throw std::runtime_error("viewerLightIntensity must be between 0 and 100");
    int sceneLights = 0, cameraLights = 0;
    for (const auto& [id, light] : state.lights) if (active(light)) {
        if (light.viewer) ++cameraLights;
        else if (state.setting<bool>("sceneLightsEnabled", true)) ++sceneLights;
    }
    const bool autoLight = state.setting<bool>("autoViewerLight", false) && !sceneLights && !cameraLights && !state.nativeAssets.lights;
    const auto extend=[&](const HdueLightDesc& legacy,const HdUELightData* source) {
        HdueLight light{}; light.struct_size=sizeof(light); light.type=legacy.type; light.id=legacy.id;
        light.visible=legacy.visible; light.intensity=legacy.intensity;
        light.flags=source?source->flags:HDUE_LIGHT_SHADOWS|HDUE_LIGHT_MEGALIGHTS;
        light.diffuse=source?source->diffuse:1; light.specular=source?source->specular:1;
        light.cone_angle=source?source->coneAngle:180; light.cone_softness=source?source->coneSoftness:0;
        light.angle_degrees=source?source->angle:.53f;
        light.cube_resolution=uint32_t(state.setting<int>("domeResolution",512));
        light.texture_format=source?source->textureFormat:HDUE_ENVIRONMENT_AUTOMATIC;
        light.texture_file=source?source->texture.c_str():"";
        for(int c=0;c<3;++c) {light.position_cm[c]=legacy.position[c]; light.color[c]=legacy.color[c];}
        for(int axis=0;axis<3;++axis) {
            GfVec3d direction(0); direction[axis]=1;
            direction=source?state.convert(source->transform.TransformDir(direction),true).GetNormalized():direction;
            for(int c=0;c<3;++c) light.axes[axis*3+c]=direction[c];
        }
        light.width_cm=legacy.width_cm; light.height_cm=legacy.height_cm;
        const double units=state.setting<double>("metersPerUnit",.01)*100;
        if(source) {
            light.length_cm=float(source->length*source->transform.TransformDir(GfVec3d(1,0,0)).GetLength()*units);
            if(source->type==HDUE_CYLINDER_LIGHT || source->type==HDUE_SPHERE_LIGHT) {
                const double x=source->transform.TransformDir(GfVec3d(1,0,0)).GetLength();
                const double y=source->transform.TransformDir(GfVec3d(0,1,0)).GetLength();
                const double z=source->transform.TransformDir(GfVec3d(0,0,1)).GetLength();
                light.width_cm=float(source->width*(source->type==HDUE_CYLINDER_LIGHT?std::max(y,z):std::max({x,y,z}))*units);
                if(light.width_cm==0 && source->type==HDUE_SPHERE_LIGHT) light.flags|=HDUE_LIGHT_POINT;
            }
        }
        if(legacy.type==HDUE_CAMERA_DISTANT_LIGHT) {
            for(int i=0;i<9;++i) light.axes[i]=(i%4)==0?1.:0.;
            for(int c=0;c<3;++c) light.axes[c]=legacy.direction[c];
        }
        extendedLights.push_back(light);
    };
    for (const auto& id : state.dirtyLights) {
        const auto& data = state.lights.at(id);
        HdueLightDesc desc{}; desc.id = data.id.c_str(); desc.type = data.type;
        desc.visible = data.visible && (data.viewer || state.setting<bool>("sceneLightsEnabled", true));
        const auto position = state.convert(data.transform.ExtractTranslation());
        const auto direction = state.convert(data.transform.TransformDir(GfVec3d(0, 0, -1)), true).GetNormalized();
        for (int c = 0; c < 3; ++c) { desc.position[c] = position[c]; desc.direction[c] = direction[c]; desc.color[c] = data.color[c]; }
        desc.intensity = data.intensity;
        if (data.viewer) {
            desc.intensity = float(viewerIntensity);
            desc.direction[0] = 1; desc.direction[1] = desc.direction[2] = 0;
        }
        const double widthScale=data.transform.TransformDir(GfVec3d(1,0,0)).GetLength();
        const double heightScale=data.transform.TransformDir(GfVec3d(0,1,0)).GetLength();
        desc.width_cm = float(data.width * widthScale * state.setting<double>("metersPerUnit", 0.01) * 100);
        desc.height_cm = float(data.height * heightScale * state.setting<double>("metersPerUnit", 0.01) * 100);
        lights.push_back(desc);
        extend(desc,&data);
        if (TfGetenvBool("HDUE_TRACE_SCENE", false)) TF_STATUS("hdUE light %s intensity=%g rgb=(%g,%g,%g)", desc.id, desc.intensity, desc.color[0], desc.color[1], desc.color[2]);
    }
    for (const auto& id : state.removed) removed.push_back(id.c_str());
    if (autoLight) {
        for (int i = 0; i < 2; ++i) {
            HdueLightDesc desc{};
            desc.id = i ? AutoFill : AutoKey; desc.type = HDUE_CAMERA_DISTANT_LIGHT; desc.visible = 1;
            desc.color[0] = desc.color[1] = desc.color[2] = 1;
            desc.intensity = float(viewerIntensity * (i ? 0.1875 : 1.0));
            desc.direction[0] = 1; desc.direction[1] = i ? -0.6 : 0.4; desc.direction[2] = i ? -0.2 : -0.5;
            lights.push_back(desc);
            extend(desc,nullptr);
        }
    } else if (state.autoLightActive) {
        removed.push_back(AutoKey); removed.push_back(AutoFill);
    }
    HdueSceneBatch batch{};
    batch.struct_size = sizeof(batch); batch.reset_scene = state.firstBatch;
    batch.meshes = meshes.data(); batch.mesh_count = uint32_t(meshes.size());
    batch.materials = materials.data(); batch.material_count = uint32_t(materials.size());
    const auto& lightingApi=state.backend->lighting_api();
    if(!lightingApi.upsert) for(const auto& light:extendedLights)
        if(light.type>HDUE_CAMERA_DISTANT_LIGHT || (light.texture_file && *light.texture_file))
            throw std::runtime_error("This scene requires the backend lighting extension; build/cook/stage a current runtime.");
    batch.lights = lightingApi.upsert?nullptr:lights.data(); batch.light_count = lightingApi.upsert?0:uint32_t(lights.size());
    batch.removed_ids = removed.data(); batch.removed_count = uint32_t(removed.size());
    state.backend->require(state.backend->api().apply_scene(state.session, &batch), "apply_scene");
    if(lightingApi.upsert && !extendedLights.empty()) {
        HdueLightBatch update{sizeof(HdueLightBatch),uint32_t(extendedLights.size()),extendedLights.data()};
        state.backend->require(lightingApi.upsert(state.session,&update),"upsert_lights");
    }
    const auto& assetApi=state.backend->asset_api();
    if (!sceneAssets.empty() && !assetApi.upsert) throw std::runtime_error("Backend has no native scene asset extension");
    if (assetApi.remove) {
        for (const auto& id : state.removed) state.backend->require(assetApi.remove(state.session,id.c_str()),"remove_asset");
        for (const auto& id : state.dirtyMeshes) if (state.meshes.at(id).sceneAsset.empty())
            state.backend->require(assetApi.remove(state.session,id.c_str()),"remove_asset");
    }
    for (const auto& desc : sceneAssets) state.backend->require(assetApi.upsert(state.session,&desc),"upsert_asset");
    state.submittedMeshes += meshes.size(); state.submittedMaterials += materials.size();
    const auto overrides = state.setting<VtDictionary>("nativeMaterialParameters", {});
    for (const auto& id : state.dirtyMaterials) {
        if (state.materials.find(id) == state.materials.end()) continue;
        const auto entry = overrides.find(id);
        if (entry == overrides.end()) continue;
        if (asset(state, "nativeMaterialAssets", id).empty() || !entry->second.IsHolding<VtDictionary>())
            throw std::runtime_error("Native material parameters require a mapped cooked material and a dictionary");
        const auto& api = state.backend->material_api();
        if (!api.set_parameters) throw std::runtime_error("Backend has no material parameter extension");
        const auto& fields = entry->second.UncheckedGet<VtDictionary>();
        std::vector<HdueMaterialParameter> parameters;
        for (const auto& [name, value] : fields) {
            HdueMaterialParameter p{}; p.name = name.c_str();
            if (value.IsHolding<float>() || value.IsHolding<double>() || value.IsHolding<int>()) {
                p.type = HDUE_PARAMETER_SCALAR;
                p.value[0] = value.IsHolding<float>() ? value.UncheckedGet<float>() : value.IsHolding<double>() ? float(value.UncheckedGet<double>()) : float(value.UncheckedGet<int>());
            } else if (value.IsHolding<GfVec3f>() || value.IsHolding<GfVec3d>()) {
                p.type = HDUE_PARAMETER_VECTOR; p.value[3] = 1;
                const auto v = value.IsHolding<GfVec3f>() ? GfVec3d(value.UncheckedGet<GfVec3f>()) : value.UncheckedGet<GfVec3d>();
                for (int c = 0; c < 3; ++c) p.value[c] = float(v[c]);
            } else if (value.IsHolding<GfVec4f>()) {
                p.type = HDUE_PARAMETER_VECTOR;
                for (int c = 0; c < 4; ++c) p.value[c] = value.UncheckedGet<GfVec4f>()[c];
            } else if (value.IsHolding<std::string>()) {
                p.type = HDUE_PARAMETER_TEXTURE; p.texture_asset = value.UncheckedGet<std::string>().c_str();
            } else throw std::runtime_error("Unsupported native material parameter value: " + name);
            parameters.push_back(p);
        }
        HdueMaterialUpdate update{}; update.struct_size = sizeof(update); update.material_id = id.c_str();
        update.parameter_count = uint32_t(parameters.size()); update.parameters = parameters.data();
        state.backend->require(api.set_parameters(state.session, &update), "set_material_parameters");
        ++state.materialParameterUpdates;
    }
    state.autoLightActive = autoLight; state.viewerLights = cameraLights + (autoLight ? 2 : 0);
    state.submittedNativeLights = state.nativeAssets.lights;
    ++state.sceneSubmissions;
    state.dirtyMeshes.clear(); state.dirtyMaterials.clear(); state.dirtyLights.clear(); state.removed.clear();
    state.submittedRevision = state.revision; state.firstBatch = false;
}
class Pass final : public HdRenderPass {
    std::shared_ptr<HdUEState> state_;
    GfMatrix4d view_{0.0}, projection_{0.0};
    uint64_t settingsVersion_ = ~uint64_t(0), lastFrame_ = 0;
    double centimetersPerUnit_ = 1;
    bool depthEnabled_ = false;
    bool converged_ = false;
public:
    Pass(HdRenderIndex* index, const HdRprimCollection& collection, std::shared_ptr<HdUEState> state)
        : HdRenderPass(index, collection), state_(std::move(state)) {}
    bool IsConverged() const override {
        if (!state_->setting<bool>("materialGraphValid",true)) return true;
        return converged_ && settingsVersion_==state_->requestSettingsRevision && state_->submittedRevision==state_->revision;
    }
    void _Execute(const HdRenderPassStateSharedPtr& pass, const TfTokenVector&) override {
        try {
            auto& s = *state_;
#if defined(HDUE_HOUDINI)
            s.executeThread=GetCurrentThreadId();
#endif
            // Keep the last valid AOV while a prepared material graph is
            // invalid. Dirty USD data stays queued until preparation/undo.
            if (!s.setting<bool>("materialGraphValid", true)) { converged_ = true; return; }
            HdUERenderBuffer* buffer = nullptr;
            HdUERenderBuffer* depth = nullptr;
            for (const auto& aov : pass->GetAovBindings()) {
                auto* renderBuffer=aov.renderBuffer;
                if (!renderBuffer && !aov.renderBufferId.IsEmpty())
                    renderBuffer=static_cast<HdRenderBuffer*>(GetRenderIndex()->GetBprim(HdPrimTypeTokens->renderBuffer,aov.renderBufferId));
                if(aov.aovName==HdAovTokens->color) buffer=dynamic_cast<HdUERenderBuffer*>(renderBuffer);
                if(aov.aovName==HdAovTokens->depth) depth=dynamic_cast<HdUERenderBuffer*>(renderBuffer);
            }
            if (!buffer || !buffer->GetWidth() || !buffer->GetHeight()) {
                std::string bindings;
                for (const auto& aov:pass->GetAovBindings()) bindings+=" "+aov.aovName.GetString()+":"+aov.renderBufferId.GetString();
                throw std::runtime_error("hdUE requires an allocated float32 RGBA color AOV; bindings:"+bindings+
                    (buffer?" allocation: "+buffer->allocationError():" (missing buffer)"));
            }
            bool changed = s.submittedRevision != s.revision;
            if (!s.session || s.width != buffer->GetWidth() || s.height != buffer->GetHeight()) {
                if (s.session) s.backend->require(s.backend->api().destroy_session(s.session), "destroy_session");
                HdueSessionDesc desc{}; desc.struct_size = sizeof(desc); desc.width = buffer->GetWidth(); desc.height = buffer->GetHeight();
                s.backend->require(s.backend->api().create_session(&desc, &s.session), "create_session");
                s.width = desc.width; s.height = desc.height; s.firstBatch = true;
                depthEnabled_=false;
                for (const auto& [id, data] : s.meshes) s.dirtyMeshes.insert(id);
                for (const auto& [id, data] : s.materials) s.dirtyMaterials.insert(id);
                for (const auto& [id, data] : s.lights) s.dirtyLights.insert(id);
                changed = true;
            }
            if(depth && !depthEnabled_) {
                if(!s.backend->depth_api().set_enabled) throw std::runtime_error("Solaris requires the updated backend depth extension; run Update-HoudiniRuntime.ps1");
                s.backend->require(s.backend->depth_api().set_enabled(s.session,1),"enable_depth");
                depthEnabled_=true; changed=true;
            }
            if (changed) submit(s);
            if (s.backend->asset_api().get_scene_status) {
                HdueAssetSceneStatus assets{}; assets.struct_size=sizeof(assets);
                s.backend->require(s.backend->asset_api().get_scene_status(s.session,&assets),"asset_scene_status");
                const bool lightsChanged=assets.lights != s.submittedNativeLights;
                s.nativeAssets=assets;
                if (assets.loading) { s.error.clear(); s.assetRequestPending=true; converged_=false; return; }
                if (assets.failed) { s.error=assets.message; s.assetRequestPending=true; converged_=true; return; }
                if (lightsChanged) { ++s.revision; submit(s); changed=true; }
            }
            if (s.assetRequestPending) { changed=true; s.assetRequestPending=false; }
            const auto view = pass->GetWorldToViewMatrix();
            const auto projection = pass->GetProjectionMatrix();
            const auto settingsVersion = s.requestSettingsRevision;
            // Consume a completed image before changing its generation. Otherwise
            // continuous camera input can discard every image before presentation.
            HdueFrameInfo frame{}; frame.struct_size = sizeof(frame);
            const auto result = s.backend->api().poll_frame(s.session, &frame);
            if (result == HDUE_OK && frame.generation == s.generation && frame.frame_id != lastFrame_) {
                std::vector<float> pixels(size_t(frame.required_bytes / sizeof(float)));
                const auto copy = s.backend->api().copy_frame(s.session, frame.frame_id, pixels.data(), frame.required_bytes);
                std::vector<float> depthPixels;
                HdueResult depthCopy=HDUE_OK;
                if(copy==HDUE_OK && depth) {
                    depthPixels.resize(size_t(frame.width)*frame.height);
                    depthCopy=s.backend->depth_api().copy_depth(s.session,frame.frame_id,depthPixels.data(),depthPixels.size()*sizeof(float));
                }
                if (copy == HDUE_STALE_FRAME || depthCopy==HDUE_STALE_FRAME) {
                    // The Render Thread may publish another completed frame
                    // between poll and copy. Retry on the next Execute without
                    // blocking or publishing bytes from the wrong frame.
                    ++s.staleFrameRetries;
                } else {
                    s.backend->require(copy, "copy_frame");
                    if(depth) {
                        s.backend->require(depthCopy,"copy_depth");
                        for(auto& value:depthPixels) {
#if defined(HDUE_HOUDINI)
                            value=value>0 && std::isfinite(value) ? float(value/centimetersPerUnit_) : std::numeric_limits<float>::max();
#else
                            value=HdUEProjectDepth(value,projection_,centimetersPerUnit_);
#endif
                        }
                        depth->publish(frame,std::move(depthPixels));
                    }
                    buffer->publish(frame, std::move(pixels));
                    ++s.publishedFrames;
                    s.lastFrame = frame; lastFrame_ = frame.frame_id;
                }
            } else if (result != HDUE_PENDING && result != HDUE_OK) s.backend->require(result, "poll_frame");
            converged_ = s.lastFrame.frame_id && s.lastFrame.converged;
            const bool settingsChanged = settingsVersion != settingsVersion_;
            const bool cameraChanged = view != view_ || projection != projection_;
            const bool needsRequest = changed || settingsChanged || cameraChanged;
            const bool deferCamera = needsRequest && !changed && !settingsChanged && s.generation && !s.lastFrame.frame_id;
            if (deferCamera) {
                // Read the newest matrices on the next Execute; never queue every
                // mouse event or invalidate an in-flight camera image repeatedly.
                ++s.coalescedCameraUpdates;
                converged_ = false;
            } else if (needsRequest) {
                HdueRenderRequest request{};
                request.struct_size = sizeof(request); request.generation = s.generation+1;
                request.mode = s.setting<std::string>("renderMode", "realtime") == "pathtraced" ? HDUE_PATH_TRACED : HDUE_REALTIME;
                request.samples_per_pixel = uint32_t(std::max(1, s.setting<int>("samplesPerPixel", 64)));
                request.warmup_frames = uint32_t(std::max(1, s.setting<int>("warmupFrames", 16)));
                request.lumen_enabled = s.setting<bool>("lumenEnabled", true);
                request.time_seconds = s.setting<double>("timeSeconds", 0);
                const auto camera = view.GetInverse();
                const auto eye = s.convert(camera.ExtractTranslation());
                const auto target = s.convert(camera.Transform(GfVec3d(0, 0, -1)));
                const auto up = s.convert(camera.TransformDir(GfVec3d(0, 1, 0)), true);
                for (int c = 0; c < 3; ++c) { request.eye[c] = eye[c]; request.target[c] = target[c]; request.up[c] = up[c]; }
                request.horizontal_fov_degrees = float(2 * std::atan(1.0 / projection[0][0]) * 180 / 3.14159265358979323846);
                if (TfGetenvBool("HDUE_TRACE_SCENE", false))
                    TF_STATUS("hdUE camera %s eye=(%g,%g,%g) target=(%g,%g,%g) fov=%g size=%ux%u units=%g axis=%s",
                        pass->GetCamera() ? pass->GetCamera()->GetId().GetText() : "none",
                        eye[0], eye[1], eye[2], target[0], target[1], target[2], request.horizontal_fov_degrees,
                        s.width, s.height, s.setting<double>("metersPerUnit", .01), s.setting<std::string>("upAxis", "Y").c_str());
                request.near_clip_cm = pass->GetCamera() ? float(pass->GetCamera()->GetClippingRange().GetMin() * s.setting<double>("metersPerUnit", 0.01) * 100) : 0.1f;
                if (pass->GetCamera() && (pass->GetCamera()->GetProjection() != HdCamera::Perspective ||
                    pass->GetCamera()->GetHorizontalApertureOffset() != 0 || pass->GetCamera()->GetVerticalApertureOffset() != 0))
                    throw std::runtime_error("This initial hdUE supports centered perspective cameras");
                const auto requestStart = std::chrono::steady_clock::now();
                const auto requested=s.backend->api().request_render(s.session, &request);
                if(requested==HDUE_PENDING) {
                    s.assetRequestPending=true; converged_=false; s.lastFrame={};
                    buffer->invalidate(); if(depth) depth->invalidate(); s.error.clear(); return;
                }
                s.backend->require(requested, "request_render");
                s.generation=request.generation;
                s.requestCpuMsTotal += std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - requestStart).count();
                view_ = view; projection_ = projection; settingsVersion_ = settingsVersion;
                s.submittedRequestSettingsRevision=settingsVersion;
                centimetersPerUnit_=s.setting<double>("metersPerUnit",.01)*100.;
                converged_ = false; s.lastFrame = {}; buffer->invalidate();
                if(depth) depth->invalidate();
            }
            { std::lock_guard lock(s.mutex); s.error.clear(); }
        } catch (const std::exception& error) {
            std::lock_guard lock(state_->mutex); state_->error = error.what(); converged_ = false;
        }
    }
};
}

HdUERenderDelegate::HdUERenderDelegate(const HdRenderSettingsMap& settings)
    : HdRenderDelegate(settings), state_(std::make_shared<HdUEState>()), registry_(std::make_shared<HdResourceRegistry>()) {
    state_->settings = settings;
    for(const auto& [key,value]:settings) if(key.GetString().starts_with("hdUE:"))
        state_->settings[TfToken(key.GetString().substr(5))]=value;
    const auto stageUnits=settings.find(TfToken("stageMetersPerUnit"));
    if (stageUnits!=settings.end() && stageUnits->second.IsHolding<double>())
        state_->settings[TfToken("metersPerUnit")]=stageUnits->second;
    const auto dll = state_->setting<std::string>("backendLibrary", TfGetenv("HDUE_BACKEND_DLL"));
    if (dll.empty()) throw std::runtime_error("hdUE requires backendLibrary and a host-initialized UELibrary runtime");
    state_->backend = std::make_shared<hdue::Backend>(std::filesystem::u8path(dll));
    HdueStatus status{}; status.struct_size = sizeof(status);
    state_->backend->require(state_->backend->api().get_status(&status), "get_status");
    if (status.state != HDUE_STATE_READY || !status.world_ready || !status.rhi_ready)
        throw std::runtime_error("hdUE requires a ready runtime; launch usdview with scripts/Launch-Usdview.ps1");
    if (state_->backend->material_api().get_renderer_info)
        state_->backend->require(state_->backend->material_api().get_renderer_info(&state_->rendererInfo), "get_renderer_info");
#if defined(HDUE_HOUDINI)
    state_->createThread=GetCurrentThreadId();
    HdUERegisterViewport(state_,this);
#endif
    for (const auto& desc : GetRenderSettingDescriptors()) {
        if (!state_->settings.count(desc.key)) state_->settings[desc.key]=desc.defaultValue;
        HdRenderDelegate::SetRenderSetting(desc.key,state_->settings[desc.key]);
    }
}
HdUERenderDelegate::~HdUERenderDelegate() {
#if defined(HDUE_HOUDINI)
    HdUEUnregisterViewport(this);
#endif
}
const TfTokenVector& HdUERenderDelegate::GetSupportedRprimTypes() const { static TfTokenVector t{HdPrimTypeTokens->mesh}; return t; }
const TfTokenVector& HdUERenderDelegate::GetSupportedSprimTypes() const { static TfTokenVector t{HdPrimTypeTokens->camera, HdPrimTypeTokens->material,
    HdPrimTypeTokens->distantLight, HdPrimTypeTokens->sphereLight, HdPrimTypeTokens->rectLight,
    HdPrimTypeTokens->domeLight,HdPrimTypeTokens->diskLight,HdPrimTypeTokens->cylinderLight}; return t; }
const TfTokenVector& HdUERenderDelegate::GetSupportedBprimTypes() const { static TfTokenVector t{HdPrimTypeTokens->renderBuffer}; return t; }
HdRenderPassSharedPtr HdUERenderDelegate::CreateRenderPass(HdRenderIndex* index, const HdRprimCollection& collection) { return std::make_shared<Pass>(index, collection, state_); }
HdInstancer* HdUERenderDelegate::CreateInstancer(HdSceneDelegate*, const SdfPath&) { TF_RUNTIME_ERROR("Instancing is not implemented in the initial hdUE delegate"); return nullptr; }
void HdUERenderDelegate::DestroyInstancer(HdInstancer* p) { delete p; }
HdRprim* HdUERenderDelegate::CreateRprim(const TfToken& type, const SdfPath& id) { return type == HdPrimTypeTokens->mesh ? HdUECreateMesh(id) : nullptr; }
void HdUERenderDelegate::DestroyRprim(HdRprim* p) { delete p; }
HdSprim* HdUERenderDelegate::CreateSprim(const TfToken& type, const SdfPath& id) {
    if (type == HdPrimTypeTokens->camera) return new HdCamera(id);
    if (type == HdPrimTypeTokens->material) return HdUECreateMaterial(id);
    if (type == HdPrimTypeTokens->distantLight) return HdUECreateLight(id, HDUE_DISTANT_LIGHT);
    if (type == HdPrimTypeTokens->sphereLight) return HdUECreateLight(id, HDUE_SPHERE_LIGHT);
    if (type == HdPrimTypeTokens->rectLight) return HdUECreateLight(id, HDUE_RECT_LIGHT);
    if (type == HdPrimTypeTokens->domeLight) return HdUECreateLight(id,HDUE_DOME_LIGHT);
    if (type == HdPrimTypeTokens->diskLight) return HdUECreateLight(id,HDUE_DISK_LIGHT);
    if (type == HdPrimTypeTokens->cylinderLight) return HdUECreateLight(id,HDUE_CYLINDER_LIGHT);
    return nullptr;
}
HdSprim* HdUERenderDelegate::CreateFallbackSprim(const TfToken& type) { return CreateSprim(type, SdfPath::EmptyPath()); }
void HdUERenderDelegate::DestroySprim(HdSprim* p) { delete p; }
HdBprim* HdUERenderDelegate::CreateBprim(const TfToken& type, const SdfPath& id) {
    return type == HdPrimTypeTokens->renderBuffer ? new HdUERenderBuffer(id) : nullptr;
}
HdBprim* HdUERenderDelegate::CreateFallbackBprim(const TfToken& type) { return CreateBprim(type, SdfPath::EmptyPath()); }
void HdUERenderDelegate::DestroyBprim(HdBprim* p) { delete p; }
HdAovDescriptor HdUERenderDelegate::GetDefaultAovDescriptor(const TfToken& name) const {
    if(name==HdAovTokens->color) return HdAovDescriptor(HdFormatFloat32Vec4,false,VtValue(GfVec4f(0)));
#if defined(HDUE_HOUDINI)
    if(name==HdAovTokens->depth) return HdAovDescriptor(HdFormatFloat32,false,VtValue(std::numeric_limits<float>::max()));
#endif
    return HdAovDescriptor();
}
HdRenderSettingDescriptorList HdUERenderDelegate::GetRenderSettingDescriptors() const { return {
    {"Render mode", TfToken("renderMode"), VtValue(std::string("realtime"))},
    {"Samples per pixel", TfToken("samplesPerPixel"), VtValue(64)},
    {"Warmup frames", TfToken("warmupFrames"), VtValue(16)}, {"Lumen", TfToken("lumenEnabled"), VtValue(true)},
    {"Viewer light intensity", TfToken("viewerLightIntensity"), VtValue(4.f)},
    {"Environment cube resolution",TfToken("domeResolution"),VtValue(512)} }; }
void HdUERenderDelegate::SetRenderSetting(const TfToken& key, const VtValue& value) {
    if(key.GetString().starts_with("hdUE:")) { SetRenderSetting(TfToken(key.GetString().substr(5)),value); return; }
    if (key==TfToken("stageMetersPerUnit")) {
        SetRenderSetting(TfToken("metersPerUnit"),value); return;
    }
    std::lock_guard lock(state_->mutex);
    const auto old=state_->settings.find(key);
    if (old!=state_->settings.end() && old->second==value) return;
    HdRenderDelegate::SetRenderSetting(key, value);
    if (key == TfToken("nativeMaterialParameters")) {
        const auto previous = state_->setting<VtDictionary>("nativeMaterialParameters", {});
        const auto current = value.IsHolding<VtDictionary>() ? value.UncheckedGet<VtDictionary>() : VtDictionary();
        for (const auto& [id, fields] : previous) if (state_->materials.count(id) && (!current.count(id) || current.find(id)->second != fields)) state_->dirtyMaterials.insert(id);
        for (const auto& [id, fields] : current) if (state_->materials.count(id) && (!previous.count(id) || previous.find(id)->second != fields)) state_->dirtyMaterials.insert(id);
        ++state_->revision;
    }
    state_->settings[key] = value;
    if (key==TfToken("renderMode") || key==TfToken("samplesPerPixel") || key==TfToken("warmupFrames") ||
        key==TfToken("lumenEnabled") || key==TfToken("timeSeconds")) ++state_->requestSettingsRevision;
    if (key==TfToken("houdini:frame") || key==TfToken("houdini:fps")) {
        const double frame=state_->setting<double>("houdini:frame",1.);
        const double fps=state_->setting<double>("houdini:fps",24.);
        if (std::isfinite(frame) && std::isfinite(fps) && fps>0) {
            state_->settings[TfToken("timeSeconds")]=VtValue(frame/fps);
            ++state_->requestSettingsRevision;
        }
    }
    if (key == TfToken("autoViewerLight") || key == TfToken("viewerLightIntensity")) {
        for (const auto& [id, light] : state_->lights) if (light.viewer) state_->dirtyLights.insert(id);
        ++state_->revision;
    }
    if (key == TfToken("sceneLightsEnabled") || key==TfToken("domeResolution")) {
        for (const auto& [id, light] : state_->lights) if (!light.viewer) state_->dirtyLights.insert(id);
        ++state_->revision;
    }
    if (key == TfToken("nativeMaterialAssets")) {
        for (const auto& [id, v] : state_->materials) state_->dirtyMaterials.insert(id);
        ++state_->revision;
    }
    if (key == TfToken("nativeMeshAssets") || key == TfToken("requireNanite") || key == TfToken("metersPerUnit") || key == TfToken("upAxis")) {
        for (const auto& [id, v] : state_->materials) state_->dirtyMaterials.insert(id);
        for (const auto& [id, v] : state_->meshes) state_->dirtyMeshes.insert(id);
        for (const auto& [id, v] : state_->lights) state_->dirtyLights.insert(id);
        ++state_->revision;
    }
}
VtDictionary HdUERenderDelegate::GetRenderStats() const {
    std::lock_guard lock(state_->mutex);
    HdueResidencyStatus residency{}; residency.struct_size = sizeof(residency);
    const auto& residencyApi = state_->backend->residency_api();
    if (state_->session && residencyApi.get_status) residencyApi.get_status(state_->session, &residency);
    VtDictionary nativeAssetIds;
    int triangles = 0;
    for (const auto& [id, mesh] : state_->meshes) {
        triangles += int(mesh.points.size() / 3);
        if (!mesh.sceneAsset.empty()) nativeAssetIds[mesh.scenePath]=VtValue(id);
    }
    int translucentMaterials = 0;
    for (const auto& [id, material] : state_->materials) if (material.opacity < 1.f && asset(*state_, "nativeMaterialAssets", id).empty()) ++translucentMaterials;
    int sceneLights = int(state_->nativeAssets.lights);
    HdueLightingStatus lighting{}; lighting.struct_size=sizeof(lighting);
    const auto& lightingApi=state_->backend->lighting_api();
    if(lightingApi.get_status && state_->session) state_->backend->require(lightingApi.get_status(state_->session,&lighting),"lighting_status");
    if (state_->setting<bool>("sceneLightsEnabled", true))
        for (const auto& [id, light] : state_->lights) if (!light.viewer && active(light)) ++sceneLights;
    return { {"error", VtValue(residency.phase == HDUE_RESIDENCY_FAILED ? std::string(residency.message) : state_->error)}, {"generation", VtValue(state_->generation)},
        {"vtResidencyPhase", VtValue(int(residency.phase))}, {"vtResidencyGeneration", VtValue(residency.generation)},
        {"vtRequiredPages", VtValue(residency.required_pages)}, {"vtResidentPages", VtValue(residency.resident_pages)},
        {"vtLockedTiles", VtValue(residency.locked_tiles)}, {"vtRequiredMappings", VtValue(residency.required_mappings)},
        {"vtReadyMappings", VtValue(residency.ready_mappings)}, {"vtTextures", VtValue(int(residency.textures))},
        {"vtPreparationFrames", VtValue(int(residency.preparation_frames))}, {"vtPreparationMs", VtValue(residency.preparation_ms)},
        {"materialGraphBlocked", VtValue(!state_->setting<bool>("materialGraphValid", true))},
        {"translucentMaterials", VtValue(translucentMaterials)},
        {"megaLightsProject",VtValue(bool(lighting.mega_lights_project))}, {"megaLightsAllowed",VtValue(bool(lighting.mega_lights_allowed))},
        {"megaLightsActive",VtValue(bool(lighting.mega_lights_active))}, {"domeLights",VtValue(int(lighting.dome_count))},
        {"environmentPending",VtValue(bool(lighting.environment_pending))}, {"environmentCubeResolution",VtValue(int(lighting.cube_resolution))},
        {"environmentMaxRadiance",VtValue(lighting.environment_max_radiance)},
        {"nativeAssetInstances", VtValue(int(state_->nativeAssets.instances))},
        {"nativeAssetIds", VtValue(nativeAssetIds)},
        {"nativeAssetLoading", VtValue(int(state_->nativeAssets.loading))},
        {"nativeAssetFailed", VtValue(int(state_->nativeAssets.failed))},
        {"nativeActors", VtValue(int(state_->nativeAssets.actors))},
        {"nativeMeshes", VtValue(int(state_->nativeAssets.meshes))},
        {"nativeLights", VtValue(int(state_->nativeAssets.lights))},
        {"sessionHandle", VtValue(uint64_t(state_->session))},
        {"submittedMeshes", VtValue(state_->submittedMeshes)}, {"submittedMaterials", VtValue(state_->submittedMaterials)},
        {"gpuLocalMemoryBytes", VtValue(state_->gpuLocalMemoryBytes)}, {"gpuMemoryAvailable", VtValue(state_->gpuMemoryAvailable)},
        {"materialParameterUpdates", VtValue(state_->materialParameterUpdates)},
        {"substrateEnabled", VtValue(bool(state_->rendererInfo.substrate_enabled))},
        {"adaptiveGBuffer", VtValue(bool(state_->rendererInfo.adaptive_gbuffer))},
        {"substrateClosureBudget", VtValue(int(state_->rendererInfo.project_closures_per_pixel))},
        {"substrateBytesPerPixel", VtValue(int(state_->rendererInfo.bytes_per_pixel))},
        {"pick", VtValue(state_->pickResult)},
        {"publishedFrames", VtValue(state_->publishedFrames)}, {"sceneSubmissions", VtValue(state_->sceneSubmissions)},
        {"staleFrameRetries", VtValue(state_->staleFrameRetries)},
        {"coalescedCameraUpdates", VtValue(state_->coalescedCameraUpdates)}, {"requestCpuMsTotal", VtValue(state_->requestCpuMsTotal)},
        {"meshes", VtValue(int(state_->meshes.size()))}, {"materials", VtValue(int(state_->materials.size()))}, {"lights", VtValue(int(state_->lights.size()))},
        {"sceneLights", VtValue(sceneLights)}, {"viewerLights", VtValue(state_->viewerLights)}, {"autoLightActive", VtValue(state_->autoLightActive)},
        {"triangles", VtValue(triangles)}, {"frameId", VtValue(state_->lastFrame.frame_id)}, {"completedSamples", VtValue(int(state_->lastFrame.completed_samples))},
        {"requestToFrameMs", VtValue(state_->lastFrame.request_to_frame_ms)}, {"readbackMs", VtValue(state_->lastFrame.readback_ms)} };
}
bool HdUERenderDelegate::InvokeCommand(const TfToken& command, const HdCommandArgs& args) {
    if (command == TfToken("hdue:sampleMemory")) {
        const auto memory = hdue::gpu_memory_bytes();
        std::lock_guard lock(state_->mutex);
        state_->gpuMemoryAvailable = memory.has_value(); state_->gpuLocalMemoryBytes = memory.value_or(0);
        return true;
    }
    if (command != TfToken("hdue:pickRay")) return false;
    std::lock_guard lock(state_->mutex);
    auto& result = state_->pickResult;
    result = {{"hit", VtValue(false)}, {"path", VtValue(std::string())}, {"error", VtValue(std::string())}};
    const auto argument = [&](const char* key) { const auto it = args.find(key); return it == args.end() ? VtValue() : it->second; };
    const auto originValue = argument("origin"), directionValue = argument("direction");
    const auto minValue = argument("minimum"), maxValue = argument("maximum");
    if (!originValue.IsHolding<GfVec3d>() || !directionValue.IsHolding<GfVec3d>() ||
        !minValue.IsHolding<double>() || !maxValue.IsHolding<double>()) return false;
    const auto origin = originValue.UncheckedGet<GfVec3d>();
    auto direction = directionValue.UncheckedGet<GfVec3d>();
    double minimum = minValue.UncheckedGet<double>(), maximum = maxValue.UncheckedGet<double>();
    for (int i = 0; i < 3; ++i) if (!std::isfinite(origin[i]) || !std::isfinite(direction[i])) return false;
    if (!std::isfinite(minimum) || !std::isfinite(maximum) || minimum < 0 || maximum < minimum || direction.GetLengthSq() < 1e-20) return false;
    direction.Normalize();
    bool nativeScene = false;
    for (const auto& [id, mesh] : state_->meshes) {
        if (mesh.visible && (!asset(*state_, "nativeMeshAssets", id).empty() ||
                             !asset(*state_, "nativeMaterialAssets", mesh.material).empty())) nativeScene = true;
    }
    const auto store = [&](const std::string& path, const GfVec3d& point, const GfVec3d& normal, double distance,
                           const char* geometry, bool naniteFallback = false) {
        result["hit"] = VtValue(true); result["path"] = VtValue(path);
        result["px"] = VtValue(point[0]); result["py"] = VtValue(point[1]); result["pz"] = VtValue(point[2]);
        result["nx"] = VtValue(normal[0]); result["ny"] = VtValue(normal[1]); result["nz"] = VtValue(normal[2]);
        result["distance"] = VtValue(distance); result["geometry"] = VtValue(std::string(geometry));
        result["naniteFallback"] = VtValue(naniteFallback);
    };
    if (nativeScene) {
        if (!state_->backend || !state_->session || state_->firstBatch || state_->submittedRevision != state_->revision) {
            result["error"] = VtValue(std::string("Native picking requires the current scene to be submitted to UE"));
            return true;
        }
        const auto& api = state_->backend->native_pick_api();
        if (!api.pick_native) {
            result["error"] = VtValue(std::string("Backend has no native picking extension"));
            return true;
        }
        // Native materials can change culling or deform even a generic USD
        // mesh. Validate them before returning any hit in the scene.
        for (const auto& [id, mesh] : state_->meshes) {
            if (!mesh.visible) continue;
            const auto path = asset(*state_, "nativeMaterialAssets", mesh.material);
            if (path.empty() || state_->pickMaterialFlags.count(path)) continue;
            HduePickMaterialInfo info{}; info.struct_size = sizeof(info);
            const auto code = api.get_material_info(path.c_str(), &info);
            if (code != HDUE_OK || !(info.flags & HDUE_PICK_MATERIAL_OPAQUE) || (info.flags & HDUE_PICK_MATERIAL_DEFORMED)) {
                result["error"] = VtValue(code == HDUE_OK ? std::string("Picking requires opaque native materials without deformation") : state_->backend->status_message());
                return true;
            }
            state_->pickMaterialFlags[path] = info.flags;
        }
        const double scale = state_->setting<double>("metersPerUnit", 0.01) * 100;
        if (!(scale > 0) || !std::isfinite(scale)) return false;
        HdueNativeRay ray{}; ray.struct_size = sizeof(ray);
        const auto ueOrigin = state_->convert(origin), ueDirection = state_->convert(direction, true);
        for (int i = 0; i < 3; ++i) { ray.origin[i] = ueOrigin[i]; ray.direction[i] = ueDirection[i]; }
        ray.minimum_cm = minimum * scale; ray.maximum_cm = maximum * scale;
        HdueNativeHit hit{}; hit.struct_size = sizeof(hit);
        std::vector<char> objectId(256);
        auto code = api.pick_native(state_->session, &ray, &hit, objectId.data(), uint32_t(objectId.size()));
        if (code == HDUE_BUFFER_TOO_SMALL && hit.required_id_bytes > objectId.size()) {
            objectId.resize(hit.required_id_bytes);
            code = api.pick_native(state_->session, &ray, &hit, objectId.data(), uint32_t(objectId.size()));
        }
        if (code != HDUE_OK) {
            result["error"] = VtValue(state_->backend->status_message());
            return true;
        }
        if (hit.hit) {
            const auto mesh = state_->meshes.find(objectId.data());
            if (mesh == state_->meshes.end()) { result["error"] = VtValue(std::string("UE returned an unknown pick object")); return true; }
            const auto fromUE = [&](const double* v) {
                return state_->setting<std::string>("upAxis", "Y") == "Z" ? GfVec3d(v[0], -v[1], v[2]) : GfVec3d(v[1], v[2], -v[0]);
            };
            maximum = hit.distance_cm / scale;
            store(mesh->second.scenePath, fromUE(hit.point) / scale, fromUE(hit.normal).GetNormalized(), maximum,
                  "ue-lod0", (hit.flags & HDUE_NATIVE_PICK_NANITE_FALLBACK) != 0);
        }
    }
    for (const auto& [id, mesh] : state_->meshes) {
        if (!mesh.visible || !mesh.pickMesh || !asset(*state_, "nativeMeshAssets", id).empty() || std::abs(mesh.transform.GetDeterminant()) < 1e-20) continue;
        const auto material = asset(*state_, "nativeMaterialAssets", mesh.material);
        const bool twoSided = !material.empty() && (state_->pickMaterialFlags.at(material) & HDUE_PICK_MATERIAL_TWO_SIDED);
        const auto inverse = mesh.transform.GetInverse();
        // Preserve the ray parameter through nonuniform/negative scale.
        const GfRay local(inverse.Transform(origin), inverse.TransformDir(direction));
        HdUEPickHit hit;
        if (!mesh.pickMesh->intersect(mesh.points, local, minimum, maximum, &hit, twoSided)) continue;
        maximum = hit.distance;
        const GfVec3d point = origin + direction * hit.distance;
        GfVec3d normal(0);
        for (int c = 0; c < 3; ++c) normal += GfVec3d(mesh.normals[size_t(hit.triangle) * 3 + c]) * hit.barycentric[c];
        normal = inverse.GetTranspose().TransformDir(normal).GetNormalized();
        if (twoSided && GfDot(normal, direction) > 0) normal = -normal;
        store(mesh.scenePath, point, normal, hit.distance, mesh.sceneAsset.empty() ? "usd" : "ue-asset-bounds");
    }
    return true;
}
PXR_NAMESPACE_CLOSE_SCOPE

