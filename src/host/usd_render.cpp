#include "hdue/backend.hpp"
#include <windows.h>
#include "pxr/base/plug/registry.h"
#include "pxr/base/tf/errorMark.h"
#include "pxr/base/vt/dictionary.h"
#include "pxr/imaging/hd/engine.h"
#include "pxr/imaging/hd/task.h"
#include "pxr/imaging/hd/renderIndex.h"
#include "pxr/imaging/hd/renderPass.h"
#include "pxr/imaging/hd/renderPassState.h"
#include "pxr/imaging/hd/renderBuffer.h"
#include "pxr/imaging/hd/camera.h"
#include "pxr/imaging/hd/rendererPlugin.h"
#include "pxr/imaging/hd/rendererPluginRegistry.h"
#include "pxr/imaging/hd/tokens.h"
#include "pxr/usd/usd/stage.h"
#include "pxr/usd/usd/primRange.h"
#include "pxr/usd/usdGeom/camera.h"
#include "pxr/usd/usdGeom/mesh.h"
#include "pxr/usd/usdGeom/imageable.h"
#include "pxr/usd/usdGeom/metrics.h"
#include "pxr/usd/usdGeom/xformable.h"
#include "pxr/usd/usdGeom/pointInstancer.h"
#include "pxr/usd/usdLux/domeLight.h"
#include "pxr/usd/usdLux/rectLight.h"
#include "pxr/usdImaging/usdImaging/delegate.h"
#include <chrono>
#include <cmath>
#include <fstream>
#include <iostream>
#include <thread>

PXR_NAMESPACE_USING_DIRECTIVE
namespace {
hdue::Backend* backend = nullptr;
using Clock = std::chrono::steady_clock;
void check(bool condition, const char* message) { if (!condition) throw std::runtime_error(message); }
std::string utf8(const std::filesystem::path& path) { const auto u8 = path.u8string(); return std::string(u8.begin(), u8.end()); }
LRESULT CALLBACK window_proc(HWND window, UINT message, WPARAM wparam, LPARAM lparam) {
    if (message == WM_CLOSE) { PostQuitMessage(0); return 0; }
    if (backend && backend->initialized()) {
        int64_t result = 0;
        if (backend->api().window_message(reinterpret_cast<uint64_t>(window), message, uint64_t(wparam), int64_t(lparam), &result) == HDUE_OK) return LRESULT(result);
    }
    return DefWindowProcW(window, message, wparam, lparam);
}
void pump() {
    MSG message{};
    while (PeekMessageW(&message, nullptr, 0, 0, PM_REMOVE)) {
        check(message.message != WM_QUIT, "Host closed");
        TranslateMessage(&message); DispatchMessageW(&message);
    }
}
struct Options {
    std::filesystem::path dll, project, input, plugins, output = "out/hydra";
    std::string mode = "both", camera = "/Camera";
    uint32_t width = 640, height = 360, cycles = 1;
    double time = 0, timeout = 120;
    bool selfTest = false, lightingTest = false;
    VtDictionary meshes, materials;
};
Options parse(int argc, wchar_t** argv) {
    Options out;
    for (int i = 1; i < argc; ++i) {
        const std::wstring key = argv[i];
        if (key == L"--self-test") { out.selfTest = true; continue; }
        if (key == L"--lighting-test") { out.lightingTest = true; continue; }
        if (i + 1 == argc) throw std::runtime_error("Missing option value");
        const std::filesystem::path value = argv[++i];
        if (key == L"--backend") out.dll = std::filesystem::absolute(value);
        else if (key == L"--project") out.project = std::filesystem::absolute(value);
        else if (key == L"--input") out.input = std::filesystem::absolute(value);
        else if (key == L"--plugins") out.plugins = std::filesystem::absolute(value);
        else if (key == L"--output") out.output = std::filesystem::absolute(value);
        else if (key == L"--mode") out.mode = utf8(value);
        else if (key == L"--camera") out.camera = utf8(value);
        else if (key == L"--width") out.width = uint32_t(std::stoul(value.wstring()));
        else if (key == L"--height") out.height = uint32_t(std::stoul(value.wstring()));
        else if (key == L"--cycles") out.cycles = uint32_t(std::stoul(value.wstring()));
        else if (key == L"--time") out.time = std::stod(value.wstring());
        else if (key == L"--timeout") out.timeout = std::stod(value.wstring());
        else if (key == L"--native-mesh" || key == L"--native-material") {
            const auto mapping = utf8(value); const auto eq = mapping.find('=');
            check(eq != std::string::npos, "Native asset mapping must be /USD/Path=/Game/Asset.Asset");
            (key == L"--native-mesh" ? out.meshes : out.materials)[mapping.substr(0, eq)] = VtValue(mapping.substr(eq + 1));
        } else throw std::runtime_error("Unknown argument: " + utf8(key));
    }
    check(!out.dll.empty() && !out.project.empty() && !out.input.empty() && !out.plugins.empty(),
        "Required: --backend DLL --project UPROJECT --input USD --plugins DIRECTORY");
    check(out.mode == "both" || out.mode == "realtime" || out.mode == "pathtraced", "Invalid mode");
    check(out.width && out.height && out.cycles && std::isfinite(out.time) && out.timeout > 0, "Invalid dimensions/time/cycles");
    return out;
}
class Task final : public HdTask {
    HdRenderPassSharedPtr pass_;
    HdRenderPassStateSharedPtr state_;
    SdfPath camera_;
public:
    Task(HdRenderPassSharedPtr pass, HdRenderPassStateSharedPtr state, SdfPath camera)
        : HdTask(SdfPath::EmptyPath()), pass_(std::move(pass)), state_(std::move(state)), camera_(std::move(camera)) {}
    void Sync(HdSceneDelegate*, HdTaskContext*, HdDirtyBits* bits) override { pass_->Sync(); *bits = HdChangeTracker::Clean; }
    void Prepare(HdTaskContext*, HdRenderIndex* index) override {
        const auto* camera = dynamic_cast<HdCamera*>(index->GetSprim(HdPrimTypeTokens->camera, camera_));
        check(camera != nullptr, "USD camera not present in Hydra render index"); state_->SetCamera(camera);
    }
    void Execute(HdTaskContext*) override { pass_->Execute(state_, GetRenderTags()); }
    const TfTokenVector& GetRenderTags() const override { static TfTokenVector tags{HdRenderTagTokens->geometry}; return tags; }
    bool IsConverged() const override { return pass_->IsConverged(); }
};
template<class T> T stat(const VtDictionary& dict, const char* name, T fallback) {
    const auto it = dict.find(name); return it != dict.end() && it->second.IsHolding<T>() ? it->second.UncheckedGet<T>() : fallback;
}
std::vector<float> render(HdEngine& engine, HdRenderIndex* index, HdTaskSharedPtrVector& tasks, HdRenderBuffer* buffer,
    HdRenderDelegate* delegate, const Options& options, const std::string& label, std::ofstream& metrics, bool& comma) {
    const auto start = Clock::now();
    unsigned delivered = 0; uint64_t lastId = 0;
    double firstFrameMs = 0;
    do {
        pump();
        TfErrorMark errors;
        engine.Execute(index, &tasks);
        const auto stats = delegate->GetRenderStats();
        const auto error = stat<std::string>(stats, "error", "");
        if (!error.empty()) throw std::runtime_error(error);
        check(errors.IsClean(), "Hydra reported an error during rendering");
        const auto id = stat<uint64_t>(stats, "frameId", 0);
        if (id && id != lastId) { if (!delivered) firstFrameMs = stat<double>(stats, "requestToFrameMs", 0); ++delivered; lastId = id; }
        if (buffer->IsConverged()) break;
        backend->require(backend->api().tick(), "tick");
        check(std::chrono::duration<double>(Clock::now() - start).count() < options.timeout, "Hydra render timed out");
        std::this_thread::sleep_for(std::chrono::milliseconds(1));
    } while (true);
    const auto width = buffer->GetWidth(), height = buffer->GetHeight();
    const auto* mapped = static_cast<const float*>(buffer->Map());
    check(mapped != nullptr, "No CPU color AOV");
    std::vector<float> pixels(mapped, mapped + uint64_t(width) * height * 4);
    buffer->Unmap();
    // Convert Hydra's lower-left AOV to the image writer's top-down ABI layout.
    const size_t row = size_t(width) * 4;
    for (uint32_t y = 0; y < height / 2; ++y)
        std::swap_ranges(pixels.begin() + y * row, pixels.begin() + (y + 1) * row,
                         pixels.begin() + (height - 1 - y) * row);
    double maximum = 0;
    for (size_t i = 0; i < pixels.size(); ++i) { check(std::isfinite(pixels[i]), "Non-finite AOV data"); if (i % 4 != 3) maximum = std::max(maximum, double(pixels[i])); }
    check(maximum > 0.001, "Black Hydra image");
    const auto stats = delegate->GetRenderStats();
    HdueFrameInfo frame{}; frame.width = width; frame.height = height; frame.pixel_format = HDUE_RGBA32_FLOAT; frame.row_stride_bytes = width * 16;
    hdue::write_pfm(options.output / (label + ".pfm"), frame, pixels.data());
    const double elapsed = std::chrono::duration<double>(Clock::now() - start).count();
    const auto mode = delegate->GetRenderSetting(TfToken("renderMode"));
    const std::string modeName = mode.IsHolding<std::string>() ? mode.UncheckedGet<std::string>() : "realtime";
    const int samples = stat<int>(stats, "completedSamples", 0);
    if (modeName == "pathtraced") check(samples >= 64, "Path Tracer did not reach 64 samples through Hydra");
    if (options.lightingTest) {
        check(!stat<bool>(stats,"environmentPending",true),"Environment was not ready at convergence");
        check(stat<bool>(stats,"megaLightsProject",false) && stat<bool>(stats,"megaLightsAllowed",false),"MegaLights project settings are disabled");
        check(stat<bool>(stats,"megaLightsActive",false)==(modeName=="realtime"),"Unexpected active MegaLights rendering path");
    }
    if (comma) metrics << ",\n"; comma = true;
    metrics << "    {\"label\":\"" << label << "\",\"mode\":\"" << modeName << "\",\"width\":" << width << ",\"height\":" << height
        << ",\"generation\":" << stat<uint64_t>(stats, "generation", 0) << ",\"samples\":" << samples << ",\"seconds\":" << elapsed
        << ",\"delivered_fps\":" << delivered / std::max(elapsed, 1e-9) << ",\"readback_ms\":" << stat<double>(stats, "readbackMs", 0)
        << ",\"meshes\":" << stat<int>(stats, "meshes", 0) << ",\"materials\":" << stat<int>(stats, "materials", 0)
        << ",\"lights\":" << stat<int>(stats, "lights", 0) << ",\"triangles\":" << stat<int>(stats, "triangles", 0)
        << ",\"maximum_rgb\":" << maximum << ",\"dome_lights\":" << stat<int>(stats,"domeLights",0)
        << ",\"environment_pending\":" << stat<bool>(stats,"environmentPending",false)
        << ",\"environment_cube_resolution\":" << stat<int>(stats,"environmentCubeResolution",0)
        << ",\"environment_max_radiance\":" << stat<float>(stats,"environmentMaxRadiance",0)
        << ",\"mega_lights_project\":" << stat<bool>(stats,"megaLightsProject",false)
        << ",\"mega_lights_allowed\":" << stat<bool>(stats,"megaLightsAllowed",false)
        << ",\"mega_lights_active\":" << stat<bool>(stats,"megaLightsActive",false)
        << ",\"first_frame_ms\":" << firstFrameMs << ",\"gpu_local_memory_bytes\":";
    const auto gpuMemory = hdue::gpu_memory_bytes();
    if (gpuMemory) metrics << *gpuMemory; else metrics << "null";
    metrics << "}";
    metrics.flush();
    std::cout << label << ": " << elapsed << " s, samples=" << samples << '\n';
    return pixels;
}
void changed(const std::vector<float>& a, const std::vector<float>& b, const char* what) {
    check(a.size() == b.size(), "Image comparison size mismatch");
    double difference = 0;
    for (size_t i = 0; i < a.size(); ++i) difference += std::abs(a[i] - b[i]);
    if (difference / a.size() < 1e-5) throw std::runtime_error(std::string("Edit did not change the rendered image: ") + what);
}
void cycle(HdRendererPlugin* plugin, const Options& options, uint32_t cycleIndex, std::ofstream& metrics, bool& comma) {
    const auto stage = UsdStage::Open(utf8(options.input));
    check(bool(stage), "Cannot open USD stage");
    stage->SetEditTarget(stage->GetSessionLayer());
    for (const auto& prim : stage->Traverse()) check(!prim.IsA<UsdGeomPointInstancer>(), "Point instancing is outside this initial delegate");
    check(stage->GetPrimAtPath(SdfPath(options.camera)).IsA<UsdGeomCamera>(), "A perspective USD camera is required");
    HdRenderSettingsMap settings;
    settings[TfToken("backendLibrary")] = VtValue(utf8(options.dll));
    settings[TfToken("metersPerUnit")] = VtValue(UsdGeomGetStageMetersPerUnit(stage));
    settings[TfToken("upAxis")] = VtValue(UsdGeomGetStageUpAxis(stage).GetString());
    settings[TfToken("samplesPerPixel")] = VtValue(64);
    settings[TfToken("nativeMeshAssets")] = VtValue(options.meshes);
    settings[TfToken("nativeMaterialAssets")] = VtValue(options.materials);
    settings[TfToken("timeSeconds")] = VtValue(options.time / stage->GetTimeCodesPerSecond());
    auto* raw = plugin->CreateRenderDelegate(settings);
    check(raw != nullptr, "Cannot create hdUE delegate");
    const auto destroy = [plugin](HdRenderDelegate* p) { plugin->DeleteRenderDelegate(p); };
    std::unique_ptr<HdRenderDelegate, decltype(destroy)> delegate(raw, destroy);
    std::unique_ptr<HdRenderIndex> index(HdRenderIndex::New(raw, {}));
    check(bool(index), "Cannot create Hydra render index");
    {
        UsdImagingDelegate imaging(index.get(), SdfPath::AbsoluteRootPath());
        imaging.Populate(stage->GetPseudoRoot()); imaging.SetTime(UsdTimeCode(options.time));
        HdEngine engine;
        auto pass = raw->CreateRenderPass(index.get(), HdRprimCollection(HdTokens->geometry, HdReprSelector(HdReprTokens->smoothHull)));
        auto state = raw->CreateRenderPassState();
        auto* buffer = static_cast<HdRenderBuffer*>(raw->CreateBprim(HdPrimTypeTokens->renderBuffer, SdfPath("/hdUEColor")));
        const auto releaseBuffer = [raw](HdRenderBuffer* p) { raw->DestroyBprim(p); };
        std::unique_ptr<HdRenderBuffer, decltype(releaseBuffer)> ownedBuffer(buffer, releaseBuffer);
        check(buffer->Allocate(GfVec3i(options.width, options.height, 1), HdFormatFloat32Vec4, false), "Cannot allocate CPU AOV");
        HdRenderPassAovBinding binding;
        binding.aovName = HdAovTokens->color; binding.renderBuffer = buffer; binding.clearValue = VtValue(GfVec4f(0));
        state->SetAovBindings({binding});
        state->SetViewport(GfVec4d(0, 0, options.width, options.height));
        auto task = std::make_shared<Task>(pass, state, imaging.ConvertCachePathToIndexPath(SdfPath(options.camera)));
        HdTaskSharedPtrVector tasks{task};
        const auto prefix = "cycle-" + std::to_string(cycleIndex) + "-";
        const auto capture = [&](const std::string& label) {
            imaging.ApplyPendingUpdates();
            return render(engine, index.get(), tasks, buffer, raw, options, prefix + label, metrics, comma);
        };
        std::vector<float> previous, realtimeReference;
        if (options.mode != "pathtraced") {
            raw->SetRenderSetting(TfToken("renderMode"), VtValue(std::string("realtime")));
            previous = capture("realtime");
            if(options.lightingTest) realtimeReference=previous;
        }
        if (options.mode != "realtime") {
            raw->SetRenderSetting(TfToken("renderMode"), VtValue(std::string("pathtraced")));
            previous = capture("pathtraced");
        }
        if (options.lightingTest) {
            auto dome=UsdLuxDomeLight(stage->GetPrimAtPath(SdfPath("/World/Lights/Dome")));
            auto rect=UsdLuxRectLight(stage->GetPrimAtPath(SdfPath("/World/Lights/Rect")));
            check(bool(dome) && bool(rect),"Lighting test requires samples/lighting/lighting.usda");
            check(backend->lighting_api().get_status!=nullptr,"Lighting extension is missing");
            const auto session=stat<uint64_t>(raw->GetRenderStats(),"sessionHandle",0);
            HdueLightingStatus status{}; status.struct_size=sizeof(status);
            backend->require(backend->lighting_api().get_status(session,&status),"lighting status");
            check(status.dome_count==1 && status.environment_max_radiance>1,"HDR environment lost its dynamic range");
            HdueLight invalid{}; invalid.struct_size=sizeof(invalid); invalid.id="invalid";
            HdueLightBatch invalidBatch{sizeof(HdueLightBatch),1,&invalid};
            check(backend->lighting_api().upsert(session,&invalidBatch)==HDUE_INVALID_ARGUMENT,"Invalid light was accepted");
            if(!realtimeReference.empty()) previous=realtimeReference;
            raw->SetRenderSetting(TfToken("renderMode"),VtValue(std::string("realtime")));
            dome.GetExposureAttr().Set(-1.f);
            auto next=capture("hdri-exposure"); changed(previous,next,"HDRI exposure"); previous=std::move(next);
            dome.GetPrim().GetAttribute(TfToken("xformOp:rotateY")).Set(180.f);
            next=capture("hdri-rotation"); changed(previous,next,"HDRI rotation"); previous=std::move(next);
            dome.GetColorAttr().Set(GfVec3f(.15f,.5f,1.f));
            next=capture("hdri-tint"); changed(previous,next,"HDRI color"); previous=std::move(next);
            UsdGeomImageable(dome.GetPrim()).MakeInvisible();
            next=capture("hdri-hidden"); changed(previous,next,"HDRI visibility"); previous=std::move(next);
            check(stat<int>(raw->GetRenderStats(),"domeLights",-1)==0,"Hidden dome remains active");
            UsdGeomImageable(dome.GetPrim()).MakeVisible();
            dome.GetExposureAttr().Set(-2.f); dome.GetColorAttr().Set(GfVec3f(1));
            dome.GetPrim().GetAttribute(TfToken("xformOp:rotateY")).Set(0.f);
            raw->SetRenderSetting(TfToken("renderMode"),VtValue(std::string("pathtraced")));
            next=capture("hdri-restored-pathtraced"); changed(previous,next,"HDRI restoration"); previous=std::move(next);
            raw->SetRenderSetting(TfToken("renderMode"),VtValue(std::string("realtime")));
            if(!realtimeReference.empty()) previous=realtimeReference;
            raw->SetRenderSetting(TfToken("domeResolution"),VtValue(256));
            rect.GetWidthAttr().Set(1.8f); rect.GetHeightAttr().Set(.3f);
            next=capture("rect-dimensions"); changed(previous,next,"RectLight dimensions"); previous=std::move(next);
            check(stat<int>(raw->GetRenderStats(),"environmentCubeResolution",0)==256,"Dome resolution edit was ignored");
            rect.GetEnableColorTemperatureAttr().Set(true); rect.GetColorTemperatureAttr().Set(3000.f);
            rect.GetNormalizeAttr().Set(true);
            next=capture("temperature-normalize"); changed(previous,next,"color temperature and normalization"); previous=std::move(next);
            dome.GetPrim().SetActive(false);
            next=capture("hdri-deleted"); changed(previous,next,"DomeLight deletion");
            check(stat<int>(raw->GetRenderStats(),"domeLights",-1)==0,"Deleted dome remains active");
        }
        if (options.selfTest) {
            check(bool(stage->GetPrimAtPath(SdfPath("/World/Cube"))), "Self-test requires samples/smoke.usda");
            const auto cameraPrim = stage->GetPrimAtPath(SdfPath(options.camera));
            GfMatrix4d camera; bool reset = false;
            UsdGeomXformable(cameraPrim).GetLocalTransformation(&camera, &reset, UsdTimeCode::Default());
            camera.SetTranslateOnly(camera.ExtractTranslation() + GfVec3d(50, 0, 0));
            UsdGeomXformable(cameraPrim).MakeMatrixXform().Set(camera);
            auto next = capture("camera-edit");
            changed(previous, next, "camera"); previous = std::move(next);
            UsdGeomXformable(stage->GetPrimAtPath(SdfPath("/World/Cube"))).AddTranslateOp(UsdGeomXformOp::PrecisionDouble, TfToken("hdUEEdit")).Set(GfVec3d(70, 0, 0));
            UsdGeomXformable(stage->GetPrimAtPath(SdfPath("/World/Cube"))).AddScaleOp().Set(GfVec3f(-1, 1, 1));
            next = capture("transform-edit");
            changed(previous, next, "transform"); previous = std::move(next);
            auto pointsAttr = UsdGeomMesh(stage->GetPrimAtPath(SdfPath("/World/Cube"))).GetPointsAttr();
            VtVec3fArray points; pointsAttr.Get(&points, UsdTimeCode(options.time)); points[6][2] += 60; pointsAttr.Set(points, UsdTimeCode(options.time));
            next = capture("points-edit");
            changed(previous, next, "points"); previous = std::move(next);
            auto mesh = UsdGeomMesh(stage->GetPrimAtPath(SdfPath("/World/Cube")));
            VtIntArray faceIndices; mesh.GetFaceVertexIndicesAttr().Get(&faceIndices);
            VtIntArray faceCounts; mesh.GetFaceVertexCountsAttr().Get(&faceCounts);
            mesh.GetFaceVertexIndicesAttr().Set(VtIntArray{faceIndices[0], faceIndices[1], faceIndices[2], faceIndices[3]});
            mesh.GetFaceVertexCountsAttr().Set(VtIntArray{4});
            next = capture("topology-edit");
            changed(previous, next, "topology"); previous = std::move(next);
            mesh.GetFaceVertexIndicesAttr().Set(faceIndices);
            mesh.GetFaceVertexCountsAttr().Set(faceCounts);
            next = capture("topology-restored");
            changed(previous, next, "restored topology"); previous = std::move(next);
            stage->GetPrimAtPath(SdfPath("/Looks/Red/Shader")).GetAttribute(TfToken("inputs:diffuseColor")).Set(GfVec3f(0.05f, 0.7f, 0.1f));
            next = capture("material-edit");
            changed(previous, next, "material"); previous = std::move(next);
            stage->GetPrimAtPath(SdfPath("/KeyLight")).GetAttribute(TfToken("inputs:intensity")).Set(2.0f);
            next = capture("light-edit");
            changed(previous, next, "light"); previous = std::move(next);
            imaging.SetTime(UsdTimeCode(1)); raw->SetRenderSetting(TfToken("timeSeconds"), VtValue(1.0 / stage->GetTimeCodesPerSecond()));
            next = capture("time-edit");
            changed(previous, next, "time"); previous = std::move(next);
            UsdGeomImageable(stage->GetPrimAtPath(SdfPath("/World/Cube"))).CreateVisibilityAttr().Set(UsdGeomTokens->invisible);
            next = capture("hidden");
            changed(previous, next, "visibility");
            UsdGeomImageable(stage->GetPrimAtPath(SdfPath("/World/Cube"))).CreateVisibilityAttr().Set(UsdGeomTokens->inherited);
            capture("shown");
            VtDictionary nativeMesh{{"/World/Cube", VtValue(std::string("/Game/HdUE/SM_Nanite.SM_Nanite"))}, {"/World/Ground", VtValue(std::string("/Game/HdUE/SM_Nanite.SM_Nanite"))}};
            VtDictionary nativeMaterial{{"/Looks/Red", VtValue(std::string("/Game/HdUE/M_Native.M_Native"))}};
            raw->SetRenderSetting(TfToken("nativeMeshAssets"), VtValue(nativeMesh));
            raw->SetRenderSetting(TfToken("nativeMaterialAssets"), VtValue(nativeMaterial));
            raw->SetRenderSetting(TfToken("requireNanite"), VtValue(true));
            raw->SetRenderSetting(TfToken("renderMode"), VtValue(std::string("realtime")));
            previous = capture("native-assets-realtime");
            raw->SetRenderSetting(TfToken("lumenEnabled"), VtValue(false));
            next = capture("native-assets-no-lumen");
            changed(previous, next, "Lumen GI/reflections enabled versus disabled");
            raw->SetRenderSetting(TfToken("lumenEnabled"), VtValue(true));
            raw->SetRenderSetting(TfToken("renderMode"), VtValue(std::string("pathtraced")));
            previous = capture("native-assets-pathtraced");
            stage->GetPrimAtPath(SdfPath("/World/Cube")).SetActive(false);
            next = capture("deleted");
            changed(previous, next, "deletion");
            check(buffer->Allocate(GfVec3i(options.width + 64, options.height + 32, 1), HdFormatFloat32Vec4, false), "Resize failed");
            state->SetViewport(GfVec4d(0, 0, options.width + 64, options.height + 32));
            capture("resized");
        }
        tasks.clear(); task.reset(); pass.reset(); state.reset();
    }
    index.reset();
    delegate.reset(); // Session is destroyed; process-owned UE continues ticking.
    backend->require(backend->api().tick(), "tick after delegate destruction");
}
}

int wmain(int argc, wchar_t** argv) {
    HWND window = nullptr;
    try {
        const auto options = parse(argc, argv);
        const auto instance = GetModuleHandleW(nullptr);
        WNDCLASSW cls{}; cls.hInstance = instance; cls.lpfnWndProc = window_proc; cls.lpszClassName = L"HdUEHydraHost";
        check(RegisterClassW(&cls) != 0, "Cannot register host window");
        window = CreateWindowExW(0, cls.lpszClassName, L"hdUE Hydra host", WS_OVERLAPPEDWINDOW, 0, 0, 640, 360, nullptr, nullptr, instance, nullptr);
        check(window != nullptr, "Cannot create host window");
        hdue::Backend runtime(options.dll); backend = &runtime;
        const auto project = utf8(options.project);
        HdueInitDesc init{}; init.struct_size = sizeof(init); init.instance_handle = reinterpret_cast<uint64_t>(instance);
        init.window_handle = reinterpret_cast<uint64_t>(window); init.project_path = project.c_str(); init.map_path = "/Game/HdUETest";
        const auto start = Clock::now(); runtime.initialize(init);
        const double initMs = std::chrono::duration<double, std::milli>(Clock::now() - start).count();
        PlugRegistry::GetInstance().RegisterPlugins(utf8(options.plugins));
        auto& registry = HdRendererPluginRegistry::GetInstance();
#if defined(HDUE_HOUDINI)
        // A separate Tf type prevents discovery of the standalone USD ABI.
        auto* plugin = registry.GetRendererPlugin(TfToken("HdUEHoudiniRendererPlugin"));
#else
        auto* plugin = registry.GetRendererPlugin(TfToken("HdUERendererPlugin"));
#endif
        check(plugin != nullptr, "Hydra could not discover the hdUE renderer plugin");
        std::filesystem::create_directories(options.output);
        std::ofstream metrics(options.output / "metrics.json");
        check(bool(metrics), "Cannot write metrics");
        metrics << "{\n  \"initialization_ms\":" << initMs << ",\n  \"frames\":[\n";
        bool comma = false;
        for (uint32_t i = 0; i < options.cycles; ++i) cycle(plugin, options, i, metrics, comma);
        registry.ReleasePlugin(plugin);
        runtime.shutdown(); backend = nullptr; DestroyWindow(window);
        metrics << "\n  ],\n  \"passed\":true\n}\n"; metrics.close();
        std::cout << "PASS: USD -> Hydra -> in-process Unreal Engine -> CPU color AOV\n";
        return 0;
    } catch (const std::exception& error) {
        backend = nullptr; if (window) DestroyWindow(window);
        std::cerr << "FAIL: " << error.what() << '\n'; return 1;
    }
}
