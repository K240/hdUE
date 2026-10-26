#include "delegate.h"
#include "pxr/imaging/hd/rendererPlugin.h"
#include "pxr/imaging/hd/rendererPluginRegistry.h"
#include "pxr/base/tf/registryManager.h"
#include "pxr/base/tf/type.h"
PXR_NAMESPACE_OPEN_SCOPE
#if defined(HDUE_HOUDINI)
#define HDUE_RENDERER_CLASS HdUEHoudiniRendererPlugin
#else
#define HDUE_RENDERER_CLASS HdUERendererPlugin
#endif
class HDUE_RENDERER_CLASS final : public HdRendererPlugin {
public:
    HdRenderDelegate* CreateRenderDelegate() override { return CreateRenderDelegate({}); }
    HdRenderDelegate* CreateRenderDelegate(const HdRenderSettingsMap& settings) override {
        try { return new HdUERenderDelegate(settings); }
        catch (const std::exception& e) { TF_RUNTIME_ERROR("hdUE: %s", e.what()); return nullptr; }
    }
    void DeleteRenderDelegate(HdRenderDelegate* delegate) override { delete delegate; }
    bool IsSupported(const HdRendererCreateArgs&, std::string* reason) const override { if (reason) reason->clear(); return true; }
};
TF_REGISTRY_FUNCTION(TfType) { HdRendererPluginRegistry::Define<HDUE_RENDERER_CLASS>(); }
PXR_NAMESPACE_CLOSE_SCOPE

