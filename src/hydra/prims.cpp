#include "delegate.h"
#include "pxr/imaging/hd/mesh.h"
#include "pxr/imaging/hd/meshUtil.h"
#include "pxr/imaging/hd/material.h"
#include "pxr/imaging/hd/light.h"
#include "pxr/imaging/hd/sceneDelegate.h"
#include "pxr/imaging/hd/tokens.h"
#include "pxr/imaging/glf/simpleLight.h"
#include "pxr/usd/sdf/assetPath.h"
#include "pxr/usd/usdLux/blackbody.h"
#include <cmath>
#include <algorithm>
#include "geom_subsets.h"
#include "mesh_normals.h"

PXR_NAMESPACE_OPEN_SCOPE
namespace {
template<class T> T value(const VtValue& v, T fallback) { return v.IsHolding<T>() ? v.UncheckedGet<T>() : fallback; }
HdInterpolation interpolation(HdSceneDelegate* delegate, const SdfPath& id, const TfToken& name) {
    for (int i = 0; i < HdInterpolationCount; ++i)
        for (const auto& p : delegate->GetPrimvarDescriptors(id, HdInterpolation(i))) if (p.name == name) return HdInterpolation(i);
    return HdInterpolationConstant;
}
template<class T> std::vector<T> expanded(HdSceneDelegate* delegate, const SdfPath& id, const TfToken& name,
    const VtVec3iArray& triangles, const VtIntArray& parameters, HdMeshUtil& util, HdType type) {
    const auto raw = delegate->Get(id, name);
    if (!raw.IsHolding<VtArray<T>>()) return {};
    const auto& array = raw.UncheckedGet<VtArray<T>>();
    if (array.empty()) return {};
    const auto domain = interpolation(delegate, id, name);
    VtArray<T> faceValues;
    if (domain == HdInterpolationFaceVarying) {
        VtValue output;
        const auto result = util.ComputeTriangulatedFaceVaryingPrimvar(array.cdata(), int(array.size()), type, &output);
        if (result == HdMeshComputationResult::Error) return {};
        faceValues = result == HdMeshComputationResult::Unchanged ? array : value(output, VtArray<T>());
    }
    std::vector<T> out;
    out.reserve(triangles.size() * 3);
    for (size_t tri = 0; tri < triangles.size(); ++tri) for (int corner : {0, 1, 2}) {
        const size_t index = domain == HdInterpolationFaceVarying ? tri * 3 + corner :
            domain == HdInterpolationConstant ? 0 : domain == HdInterpolationUniform ?
            size_t(HdMeshUtil::DecodeFaceIndexFromCoarseFaceParam(parameters[tri])) : size_t(triangles[tri][corner]);
        const auto& source = domain == HdInterpolationFaceVarying ? faceValues : array;
        if (index >= source.size()) return {};
        out.push_back(source[index]);
    }
    return out;
}
class Mesh final : public HdMesh {
    std::set<std::string> emitted_;
public:
    explicit Mesh(const SdfPath& id) : HdMesh(id) {}
    HdDirtyBits GetInitialDirtyBitsMask() const override { return HdChangeTracker::AllDirty; }
    void Sync(HdSceneDelegate* delegate, HdRenderParam* param, HdDirtyBits* bits, const TfToken&) override {
        auto* state = static_cast<HdUEState*>(param);
        HdUEMeshData mesh;
        mesh.id = GetId().GetString();
        mesh.scenePath = delegate->GetScenePrimPath(GetId(), -1).GetString();
        mesh.material = delegate->GetMaterialId(GetId()).GetString();
        mesh.transform = delegate->GetTransform(GetId());
        mesh.visible = delegate->GetVisible(GetId());
        mesh.sceneAsset = value(delegate->Get(GetId(), TfToken("hdue:asset")), std::string());
        if (!mesh.sceneAsset.empty() && SdfPath(mesh.scenePath).GetName() == "__HdUEAssetProxy")
            mesh.scenePath = SdfPath(mesh.scenePath).GetParentPath().GetString();
        const auto points = value(delegate->Get(GetId(), HdTokens->points), VtVec3fArray());
        const auto topology = delegate->GetMeshTopology(GetId());
        HdMeshUtil util(&topology, GetId());
        VtVec3iArray triangles;
        VtIntArray parameters;
        util.ComputeTriangleIndices(&triangles, &parameters);
        // The USD -> UE basis already reverses handedness. UE's clockwise
        // front faces must not receive a second index reversal.
        for (const auto& triangle : triangles) for (int corner : {0, 1, 2}) {
            if (triangle[corner] < 0 || size_t(triangle[corner]) >= points.size()) { TF_RUNTIME_ERROR("Invalid mesh index at %s", GetId().GetText()); return; }
            mesh.points.push_back(points[triangle[corner]]);
        }
        mesh.normals = expanded<GfVec3f>(delegate, GetId(), HdTokens->normals, triangles, parameters, util, HdTypeFloatVec3);
        if (mesh.normals.size() != mesh.points.size()) mesh.normals.resize(mesh.points.size(), GfVec3f(0));
        if (std::any_of(mesh.normals.begin(),mesh.normals.end(),[](const auto& normal) {return normal.GetLengthSq()<1e-12f;})) {
            try {
                const auto smooth=HdUEComputeSmoothNormals(points,triangles);
                for (size_t i=0;i<mesh.normals.size();++i)
                    if (mesh.normals[i].GetLengthSq()<1e-12f) mesh.normals[i]=smooth[i];
            } catch (const std::invalid_argument& error) { TF_RUNTIME_ERROR("%s at %s",error.what(),GetId().GetText()); return; }
        }
        mesh.uv = expanded<GfVec2f>(delegate, GetId(), TfToken("st"), triangles, parameters, util, HdTypeFloatVec2);
        std::lock_guard lock(state->mutex);
        if (mesh.material.empty() && mesh.sceneAsset.empty()) {
            HdUEMaterialData material;
            material.id = mesh.id + ".displayColor";
            const auto color = value(delegate->Get(GetId(), HdTokens->displayColor), VtVec3fArray());
            if (!color.empty()) material.color = color[0];
            const auto opacity = value(delegate->Get(GetId(), HdTokens->displayOpacity), VtFloatArray());
            if (!opacity.empty()) {
                // The generic ABI carries one opacity per material. Constant
                // primvars (and identical values in other domains) are exact.
                if (!std::all_of(opacity.begin(), opacity.end(), [&](float a) { return a == opacity[0]; }))
                    TF_WARN("Varying displayOpacity is unsupported at %s; using its first value", GetId().GetText());
                if (std::isfinite(opacity[0])) material.opacity = std::clamp(opacity[0], 0.f, 1.f);
            }
            mesh.material = material.id;
            const auto previous = state->materials.find(material.id);
            if (previous == state->materials.end() || previous->second.color != material.color || previous->second.opacity != material.opacity) {
                state->materials[material.id] = material;
                state->dirtyMaterials.insert(material.id);
            }
        }
        std::vector<HdUEMeshData> parts;
        try { parts = HdUEPartitionSubsets(std::move(mesh), topology, parameters); }
        catch (const std::invalid_argument& error) { TF_RUNTIME_ERROR("%s at %s", error.what(), GetId().GetText()); return; }
        std::set<std::string> current;
        for (auto& part : parts) {
            const auto previous = state->meshes.find(part.id);
            if (previous != state->meshes.end() && previous->second.points == part.points) part.pickMesh = previous->second.pickMesh;
            if (!part.pickMesh) part.pickMesh = std::make_shared<HdUEPickMesh>(part.points);
            const bool onlyNativeBounds = previous != state->meshes.end() && !part.sceneAsset.empty() &&
                previous->second.sceneAsset==part.sceneAsset && previous->second.transform==part.transform && previous->second.visible==part.visible;
            const bool meshChanged = !onlyNativeBounds && (previous == state->meshes.end() || previous->second.points != part.points ||
                previous->second.normals != part.normals || previous->second.uv != part.uv || previous->second.transform != part.transform ||
                previous->second.material != part.material || previous->second.visible != part.visible || previous->second.sceneAsset != part.sceneAsset);
            const auto id = part.id;
            state->meshes[id] = std::move(part);
            state->removed.erase(id);
            if (meshChanged) { state->dirtyMeshes.insert(id); ++state->revision; }
            current.insert(id);
        }
        for (const auto& id : emitted_) if (!current.count(id)) {
            state->meshes.erase(id); state->dirtyMeshes.erase(id); state->removed.insert(id); ++state->revision;
        }
        emitted_ = std::move(current);
        *bits = HdChangeTracker::Clean;
    }
    void Finalize(HdRenderParam* param) override {
        auto* state = static_cast<HdUEState*>(param);
        for (const auto& id : emitted_) state->erase(id);
        state->erase(GetId().GetString() + ".displayColor");
    }
protected:
    void _InitRepr(const TfToken& token, HdDirtyBits*) override { _reprs.emplace_back(token, HdReprSharedPtr(new HdRepr())); }
    HdDirtyBits _PropagateDirtyBits(HdDirtyBits bits) const override { return bits; }
};
class Material final : public HdMaterial {
public:
    explicit Material(const SdfPath& id) : HdMaterial(id) {}
    HdDirtyBits GetInitialDirtyBitsMask() const override { return AllDirty; }
    void Sync(HdSceneDelegate* delegate, HdRenderParam* param, HdDirtyBits* bits) override {
        HdUEMaterialData data; data.id = GetId().GetString();
        const auto resource = delegate->GetMaterialResource(GetId());
        HdMaterialNetwork2 network;
        if (resource.IsHolding<HdMaterialNetwork2>()) network = resource.UncheckedGet<HdMaterialNetwork2>();
        else if (resource.IsHolding<HdMaterialNetworkMap>()) network = HdConvertToHdMaterialNetwork2(resource.UncheckedGet<HdMaterialNetworkMap>());
        for (const auto& [path, node] : network.nodes) {
            if (node.nodeTypeId != TfToken("UsdPreviewSurface")) continue;
            const auto get = [&](const char* name) { const auto it = node.parameters.find(TfToken(name)); return it == node.parameters.end() ? VtValue() : it->second; };
            data.color = value(get("diffuseColor"), data.color);
            data.metallic = value(get("metallic"), data.metallic);
            data.roughness = value(get("roughness"), data.roughness);
            data.opacity = value(get("opacity"), data.opacity);
            const auto connection = node.inputConnections.find(TfToken("diffuseColor"));
            if (connection != node.inputConnections.end() && !connection->second.empty()) {
                const auto upstream = network.nodes.find(connection->second.front().upstreamNode);
                if (upstream != network.nodes.end()) {
                    const auto file = upstream->second.parameters.find(TfToken("file"));
                    if (file != upstream->second.parameters.end() && file->second.IsHolding<SdfAssetPath>()) {
                        const auto& asset = file->second.UncheckedGet<SdfAssetPath>();
                        data.texture = asset.GetResolvedPath().empty() ? asset.GetAssetPath() : asset.GetResolvedPath();
                    }
                }
            }
        }
        auto* state = static_cast<HdUEState*>(param);
        std::lock_guard lock(state->mutex);
        state->materials[data.id] = data; state->dirtyMaterials.insert(data.id); ++state->revision;
        *bits = Clean;
    }
    void Finalize(HdRenderParam* param) override { static_cast<HdUEState*>(param)->erase(GetId().GetString()); }
};
class Light final : public HdLight {
    uint32_t type_;
public:
    Light(const SdfPath& id, uint32_t type) : HdLight(id), type_(type) {}
    HdDirtyBits GetInitialDirtyBitsMask() const override { return AllDirty; }
    void Sync(HdSceneDelegate* delegate, HdRenderParam* param, HdDirtyBits* bits) override {
        HdUELightData data; data.id = GetId().GetString(); data.type = type_;
        data.transform = delegate->GetTransform(GetId()); data.visible = delegate->GetVisible(GetId());
        const auto get = [&](const char* name) { return delegate->GetLightParamValue(GetId(), TfToken(name)); };
        const auto applicationLight = get("params");
        data.viewer = applicationLight.IsHolding<GlfSimpleLight>() && !applicationLight.UncheckedGet<GlfSimpleLight>().IsDomeLight();
        data.color = value(get("color"), data.color);
        data.intensity = value(get("intensity"), 1.f) * std::exp2(value(get("exposure"), 0.f));
        if(value(get("enableColorTemperature"),false)) {
            const auto temperature=UsdLuxBlackbodyTemperatureAsRgb(value(get("colorTemperature"),6500.f));
            for(int c=0;c<3;++c) data.color[c]*=temperature[c];
        }
        if(value(get("normalize"),false)) data.flags|=HDUE_LIGHT_NORMALIZE;
        if(!value(get("shadow:enable"),true)) data.flags&=~HDUE_LIGHT_SHADOWS;
        if(value(get("treatAsPoint"),false)) data.flags|=HDUE_LIGHT_POINT;
        if(!value(get("hdue:visibleBackground"),true)) data.flags&=~HDUE_LIGHT_BACKGROUND;
        data.diffuse=value(get("diffuse"),1.f); data.specular=value(get("specular"),1.f);
        data.angle=value(get("angle"),.53f);
        data.coneAngle=value(get("shaping:cone:angle"),180.f); data.coneSoftness=value(get("shaping:cone:softness"),0.f);
        auto texture=get("texture:file");
        if(applicationLight.IsHolding<GlfSimpleLight>() && applicationLight.UncheckedGet<GlfSimpleLight>().IsDomeLight())
            texture=VtValue(applicationLight.UncheckedGet<GlfSimpleLight>().GetDomeLightTextureFile());
        if(texture.IsHolding<SdfAssetPath>()) {
            const auto& file=texture.UncheckedGet<SdfAssetPath>();
            data.texture=file.GetResolvedPath().empty()?file.GetAssetPath():file.GetResolvedPath();
        }
        const auto format=value(get("texture:format"),TfToken("automatic"));
        data.textureFormat=format==TfToken("automatic")?HDUE_ENVIRONMENT_AUTOMATIC:format==TfToken("latlong")?HDUE_ENVIRONMENT_LATLONG:99;
        if (data.viewer) {
            // Hdx uses 15000 for its camera light when the renderer is not
            // Storm. Treat this application light separately from USD lights.
            const auto diffuse = applicationLight.UncheckedGet<GlfSimpleLight>().GetDiffuse();
            data.color = GfVec3f(diffuse[0], diffuse[1], diffuse[2]);
            data.intensity = 1;
            data.transform = GfMatrix4d(1);
            data.type = HDUE_CAMERA_DISTANT_LIGHT;
        }
        const bool radial=type_==HDUE_SPHERE_LIGHT || type_==HDUE_DISK_LIGHT || type_==HDUE_CYLINDER_LIGHT;
        data.width=value(get(radial?"radius":"width"),radial?.5f:1.f);
        data.height=type_==HDUE_DISK_LIGHT?data.width:value(get("height"),1.f);
        data.length=value(get("length"),1.f);
        auto* state = static_cast<HdUEState*>(param);
        std::lock_guard lock(state->mutex);
        const auto old = state->lights.find(data.id);
        if(old!=state->lights.end() && old->second.transform==data.transform && old->second.texture==data.texture &&
            old->second.color==data.color && old->second.intensity==data.intensity && old->second.visible==data.visible &&
            old->second.type==data.type && old->second.width==data.width && old->second.height==data.height && old->second.length==data.length &&
            old->second.flags==data.flags && old->second.diffuse==data.diffuse && old->second.specular==data.specular &&
            old->second.angle==data.angle && old->second.coneAngle==data.coneAngle && old->second.coneSoftness==data.coneSoftness &&
            old->second.textureFormat==data.textureFormat) {*bits=Clean; return;}
        if (data.viewer && old != state->lights.end() && old->second.viewer && old->second.color == data.color && old->second.visible == data.visible) {
            // The backend follows the render camera at request time. Moving
            // Hdx's camera light must not submit/cancel the entire scene.
            *bits = Clean;
            return;
        }
        state->lights[data.id] = data; state->dirtyLights.insert(data.id); ++state->revision;
        *bits = Clean;
    }
    void Finalize(HdRenderParam* param) override { static_cast<HdUEState*>(param)->erase(GetId().GetString()); }
};
}
HdRprim* HdUECreateMesh(const SdfPath& id) { return new Mesh(id); }
HdSprim* HdUECreateMaterial(const SdfPath& id) { return new Material(id); }
HdSprim* HdUECreateLight(const SdfPath& id, uint32_t type) { return new Light(id, type); }
PXR_NAMESPACE_CLOSE_SCOPE
