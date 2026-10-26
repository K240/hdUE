#define HDUE_VIEWPORT_EXPORTS 1
#include "hdue/viewport_api.h"
#include "delegate.h"
#include "diagnostics.h"
#include <windows.h>
#include <cstring>
#include <cmath>
namespace { std::weak_ptr<PXR_NS::HdUEState> Viewport; PXR_NS::HdUERenderDelegate* Delegate=nullptr; }
PXR_NAMESPACE_OPEN_SCOPE
void HdUERegisterViewport(const std::shared_ptr<HdUEState>& state,HdUERenderDelegate* delegate) { Viewport=state; Delegate=delegate; }
void HdUEUnregisterViewport(HdUERenderDelegate* delegate) { if(Delegate==delegate) { Delegate=nullptr; Viewport.reset(); } }
PXR_NAMESPACE_CLOSE_SCOPE
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
