#define HDUE_VIEWPORT_EXPORTS 1
#include "hdue/viewport_api.h"
#include "delegate.h"
#include "diagnostics.h"
#include <windows.h>
#include <cstring>
#include <cmath>
#include "pxr/base/gf/vec4f.h"
namespace { std::weak_ptr<PXR_NS::HdUEState> Viewport; PXR_NS::HdUERenderDelegate* Delegate=nullptr; }
PXR_NAMESPACE_OPEN_SCOPE
void HdUERegisterViewport(const std::shared_ptr<HdUEState>& state,HdUERenderDelegate* delegate) { Viewport=state; Delegate=delegate; }
void HdUEUnregisterViewport(HdUERenderDelegate* delegate) { if(Delegate==delegate) { Delegate=nullptr; Viewport.reset(); } }
PXR_NAMESPACE_CLOSE_SCOPE
extern "C" HDUE_VIEWPORT_EXPORT HdueResult HDUE_CALL hdue_set_viewport_materials(const HdueViewportMaterials* update) {
    if (!update || update->struct_size!=sizeof(*update) || update->abi_version!=1 || update->reserved ||
        update->material_count>65536 || (update->material_count && !update->materials)) return HDUE_INVALID_ARGUMENT;
    const auto state=Viewport.lock();
    if (!state || !Delegate) return HDUE_PENDING;
    if (GetCurrentThreadId()!=state->createThread) return HDUE_WRONG_THREAD;
    if (!update->session || update->session!=state->session) return HDUE_INVALID_STATE;
    using namespace PXR_NS;
    VtDictionary assets, overrides;
    const auto text=[](const char* value) {return value && value[0] && strnlen(value,4097)<=4096;};
    for (uint32_t i=0;i<update->material_count;++i) {
        const auto& material=update->materials[i];
        if (!text(material.material_id) || !SdfPath::IsValidPathString(material.material_id) ||
            !SdfPath(material.material_id).IsAbsolutePath() || !SdfPath(material.material_id).IsPrimPath() || !text(material.native_asset) || material.native_asset[0]!='/' ||
            material.reserved || material.parameter_count>4096 || (material.parameter_count && !material.parameters) ||
            assets.count(material.material_id)) return HDUE_INVALID_ARGUMENT;
        assets[material.material_id]=VtValue(std::string(material.native_asset));
        VtDictionary fields;
        for (uint32_t p=0;p<material.parameter_count;++p) {
            const auto& parameter=material.parameters[p];
            if (!text(parameter.name) || parameter.reserved || fields.count(parameter.name)) return HDUE_INVALID_ARGUMENT;
            if (parameter.type==HDUE_PARAMETER_TEXTURE) {
                if (!text(parameter.texture_asset) || parameter.texture_asset[0]!='/') return HDUE_INVALID_ARGUMENT;
                fields[parameter.name]=VtValue(std::string(parameter.texture_asset));
            } else if (parameter.type==HDUE_PARAMETER_SCALAR || parameter.type==HDUE_PARAMETER_VECTOR) {
                for (float value : parameter.value) if (!std::isfinite(value)) return HDUE_INVALID_ARGUMENT;
                fields[parameter.name]=parameter.type==HDUE_PARAMETER_SCALAR ? VtValue(parameter.value[0]) :
                    VtValue(GfVec4f(parameter.value[0],parameter.value[1],parameter.value[2],parameter.value[3]));
            } else return HDUE_INVALID_ARGUMENT;
        }
        overrides[material.material_id]=VtValue(fields);
    }
    Delegate->SetRenderSetting(TfToken("nativeMaterialAssets"),VtValue(assets));
    Delegate->SetRenderSetting(TfToken("nativeMaterialParameters"),VtValue(overrides));
    return HDUE_OK;
}
extern "C" HDUE_VIEWPORT_EXPORT HdueResult HDUE_CALL hdue_set_viewport_options(const HdueViewportOptions* options) {
    if(!options || options->struct_size!=sizeof(*options) || options->mode>1 || !options->samples || options->samples>65536 ||
        options->lumen>1 || options->auto_light>1 || options->z_up>1 || !std::isfinite(options->meters_per_unit) || options->meters_per_unit<=0 || !std::isfinite(options->time_seconds)) return HDUE_INVALID_ARGUMENT;
    const auto state=Viewport.lock();
    if(!state || !Delegate) return HDUE_PENDING;
    if(GetCurrentThreadId()!=state->createThread) return HDUE_WRONG_THREAD;
    using namespace PXR_NS;
    Delegate->SetRenderSetting(TfToken("renderMode"),VtValue(std::string(options->mode?"pathtraced":"realtime")));
    Delegate->SetRenderSetting(TfToken("samplesPerPixel"),VtValue(int(options->samples)));
    Delegate->SetRenderSetting(TfToken("lumenEnabled"),VtValue(bool(options->lumen)));
    Delegate->SetRenderSetting(TfToken("autoViewerLight"),VtValue(bool(options->auto_light)));
    Delegate->SetRenderSetting(TfToken("upAxis"),VtValue(std::string(options->z_up?"Z":"Y")));
    Delegate->SetRenderSetting(TfToken("metersPerUnit"),VtValue(options->meters_per_unit));
    Delegate->SetRenderSetting(TfToken("timeSeconds"),VtValue(options->time_seconds));
    return HDUE_OK;
}
extern "C" HDUE_VIEWPORT_EXPORT HdueResult HDUE_CALL hdue_get_viewport_status(HdueViewportStatus* status) {
    if (!status || status->struct_size!=sizeof(*status)) return HDUE_INVALID_ARGUMENT;
    HdueViewportStatus out{}; out.struct_size=sizeof(out);
    if (const auto s=Viewport.lock()) {
        std::lock_guard lock(s->mutex);
        out.active=1; out.create_thread=s->createThread; out.execute_thread=s->executeThread;
        out.session=s->session; out.generation=s->generation; out.published_frames=s->publishedFrames; out.scene_submissions=s->sceneSubmissions;
        out.mode=s->setting<std::string>("renderMode","realtime")=="pathtraced"?HDUE_PATH_TRACED:HDUE_REALTIME;
        out.meshes=uint32_t(s->meshes.size()); out.materials=uint32_t(s->materials.size()); out.lights=uint32_t(s->lights.size());
        // Settings can change before Hydra gets its next Execute. Do not label
        // the previous mode/scene's completed image as the new request's result.
        if(s->submittedRequestSettingsRevision==s->requestSettingsRevision && s->submittedRevision==s->revision)
            out.frame=s->lastFrame;
        std::memcpy(out.error,s->error.c_str(),std::min(s->error.size(),sizeof(out.error)-1));
    }
    *status=out; return HDUE_OK;
}
