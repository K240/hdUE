#pragma once
#include "pxr/base/js/json.h"
#include "pxr/base/gf/vec4f.h"
#include "pxr/usd/usdShade/materialBindingAPI.h"
#include "pxr/usd/usdGeom/subset.h"

namespace {
template<class T> T surfaceField(const VtDictionary& dict, const char* name, T fallback) {
    const auto it=dict.find(name); return it!=dict.end() && it->second.IsHolding<T>()?it->second.UncheckedGet<T>():fallback;
}
VtValue rendererValue(const JsValue& value) {
    if (value.IsObject()) {
        VtDictionary result;
        for (const auto& [name, field]:value.GetJsObject()) result[name]=rendererValue(field);
        return VtValue(result);
    }
    if (value.IsString()) return VtValue(value.GetString());
    if (value.IsBool()) return VtValue(value.GetBool());
    if (value.IsInt()) return VtValue(float(value.GetInt()));
    if (value.IsReal()) return VtValue(float(value.GetReal()));
    if (value.IsArray()) {
        const auto& array=value.GetJsArray();
        if (array.size()!=3 && array.size()!=4) throw std::runtime_error("Only material parameter vectors are accepted in renderer settings");
        GfVec4f result(0);
        for (size_t i=0;i<array.size();++i) {
            const auto v=rendererValue(array[i]);
            if (!v.IsHolding<float>()) throw std::runtime_error("Invalid material vector in renderer settings");
            result[i]=v.UncheckedGet<float>();
        }
        return array.size()==3?VtValue(GfVec3f(result[0],result[1],result[2])):VtValue(result);
    }
    throw std::runtime_error("Unsupported renderer settings value");
}
VtDictionary readRendererSettings(const std::filesystem::path& path) {
    if (path.empty()) return {};
    std::ifstream stream(path);
    JsParseError error;
    const auto json=JsParseStream(stream,&error);
    if (!json.IsObject()) throw std::runtime_error("Invalid renderer settings: "+error.reason);
    const auto& root=json.GetJsObject();
    if (!root.count("version") || !root.at("version").IsInt() || root.at("version").GetInt()!=1 || !root.count("settings"))
        throw std::runtime_error("Unsupported renderer settings version");
    return rendererValue(root.at("settings")).Get<VtDictionary>();
}
template<class T> T wetOpinion(UsdPrim prim, const char* name, UsdTimeCode time, T fallback) {
    for (;prim && !prim.IsPseudoRoot();prim=prim.GetParent()) {
        const auto attr=prim.GetAttribute(TfToken(std::string("hdue:surface:wet:")+name));
        if (!attr || !attr.HasAuthoredValueOpinion()) continue;
        T value;
        if (!attr.Get(&value,time)) throw std::runtime_error(attr.GetPath().GetString()+": invalid wet attribute type/value");
        return value;
    }
    return fallback;
}
void updateWetSettings(const UsdStageRefPtr& stage, HdRenderDelegate* delegate, const VtDictionary& profiles, UsdTimeCode time) {
    VtDictionary effects;
    for (const auto& prim:stage->Traverse()) {
        if (!prim.IsA<UsdGeomMesh>()) continue;
        std::vector<UsdPrim> targets{prim};
        for (const auto& subset:UsdShadeMaterialBindingAPI(prim).GetMaterialBindSubsets()) targets.push_back(subset.GetPrim());
        for (const auto& target:targets) {
            if (!wetOpinion<bool>(target,"enabled",time,false)) continue;
            float amount=wetOpinion<float>(target,"amount",time,1.f), roughness=wetOpinion<float>(target,"roughness",time,.08f);
            if (!std::isfinite(amount) || !std::isfinite(roughness)) throw std::runtime_error(target.GetPath().GetString()+": non-finite wet setting");
            if (amount<=0) continue;
            const auto material=UsdShadeMaterialBindingAPI(target).ComputeBoundMaterial();
            const auto it=profiles.find(material.GetPath().GetString());
            if (it==profiles.end() || !it->second.IsHolding<VtDictionary>())
                throw std::runtime_error(target.GetPath().GetString()+": prepare the material with -WetProfile and pass --renderer-settings");
            const auto& profile=it->second.UncheckedGet<VtDictionary>();
            if (!surfaceField<bool>(profile,"supported",false) || surfaceField<float>(profile,"version",0)!=1.f)
                throw std::runtime_error(target.GetPath().GetString()+": "+surfaceField<std::string>(profile,"reason","Wet profile unavailable"));
            effects[target.GetPath().GetString()]=VtValue(VtDictionary{
                {"asset",VtValue(surfaceField<std::string>(profile,"asset",""))}, {"previewAsset",VtValue(surfaceField<std::string>(profile,"preview_asset",""))},
                {"amount",VtValue(std::clamp(amount,0.f,1.f))}, {"roughness",VtValue(std::clamp(roughness,0.f,1.f))},
                {"maskPrimvar",VtValue(wetOpinion<TfToken>(target,"maskPrimvar",time,TfToken()).GetString())}});
        }
    }
    delegate->SetRenderSetting(TfToken("surfaceEffects"),VtValue(effects));
}
}
