#include "hdue/backend.hpp"
#include <windows.h>
#include <algorithm>
#include <chrono>
#include <cmath>
#include <fstream>
#include <iostream>
#include <thread>
#include <vector>

namespace {
hdue::Backend* active_backend = nullptr;
using Clock = std::chrono::steady_clock;
double seconds(Clock::time_point start) { return std::chrono::duration<double>(Clock::now() - start).count(); }
std::string utf8(const std::wstring& input) {
    const int size = WideCharToMultiByte(CP_UTF8, 0, input.data(), int(input.size()), nullptr, 0, nullptr, nullptr);
    std::string output(size, '\0');
    WideCharToMultiByte(CP_UTF8, 0, input.data(), int(input.size()), output.data(), size, nullptr, nullptr);
    return output;
}
LRESULT CALLBACK window_proc(HWND window, UINT message, WPARAM wparam, LPARAM lparam) {
    if (message == WM_CLOSE) { PostQuitMessage(0); return 0; }
    if (active_backend && active_backend->initialized()) {
        int64_t result = 0;
        if (active_backend->api().window_message(reinterpret_cast<uint64_t>(window), message,
            uint64_t(wparam), int64_t(lparam), &result) == HDUE_OK) return LRESULT(result);
    }
    return DefWindowProcW(window, message, wparam, lparam);
}
void pump() {
    MSG message{};
    while (PeekMessageW(&message, nullptr, 0, 0, PM_REMOVE)) {
        if (message.message == WM_QUIT) throw std::runtime_error("Host window closed");
        TranslateMessage(&message);
        DispatchMessageW(&message);
    }
}
struct Options {
    std::filesystem::path backend, project, output = "out/backend-smoke";
    std::string mode = "both";
    uint32_t width = 640, height = 360, cycles = 3, samples = 64;
    double timeout = 600;
    bool visible = false;
};
Options parse(int argc, wchar_t** argv) {
    Options options;
    for (int i = 1; i < argc; ++i) {
        const std::wstring key = argv[i];
        if (key == L"--visible") { options.visible = true; continue; }
        if (i + 1 == argc) throw std::runtime_error("Missing option value");
        const std::wstring value = argv[++i];
        if (key == L"--backend") options.backend = value;
        else if (key == L"--project") options.project = value;
        else if (key == L"--output") options.output = value;
        else if (key == L"--mode") options.mode = utf8(value);
        else if (key == L"--width") options.width = std::stoul(value);
        else if (key == L"--height") options.height = std::stoul(value);
        else if (key == L"--cycles") options.cycles = std::stoul(value);
        else if (key == L"--samples") options.samples = std::stoul(value);
        else if (key == L"--timeout") options.timeout = std::stod(value);
        else throw std::runtime_error("Unknown host option: " + utf8(key));
    }
    if (options.backend.empty() || options.project.empty() || !options.width || !options.height ||
        !options.cycles || !std::isfinite(options.timeout) || options.timeout <= 0 ||
        (options.mode != "realtime" && options.mode != "pathtraced" && options.mode != "both"))
        throw std::runtime_error("Usage: hdue_backend_smoke --backend DLL --project UPROJECT [--mode both|realtime|pathtraced] [--visible]");
    options.project = std::filesystem::absolute(options.project);
    return options;
}
void check(bool value, const char* message) { if (!value) throw std::runtime_error(message); }

void render(hdue::Backend& backend, HdueSession session, HdueRenderRequest request,
    const Options& options, const std::filesystem::path& file, std::ofstream& metrics, bool& comma) {
    backend.require(backend.api().request_render(session, &request), "request_render");
    const auto start = Clock::now();
    HdueFrameInfo frame{};
    frame.struct_size = sizeof(frame);
    unsigned ticks = 0;
    do {
        pump();
        backend.require(backend.api().tick(), "tick");
        ++ticks;
        const auto result = backend.api().poll_frame(session, &frame);
        if (result != HDUE_OK && result != HDUE_PENDING) backend.require(result, "poll_frame");
        if (result == HDUE_OK && frame.generation == request.generation && frame.converged) break;
        if (seconds(start) > options.timeout) throw std::runtime_error("Timed out waiting for a converged UE frame");
        std::this_thread::sleep_for(std::chrono::milliseconds(1));
    } while (true);
    check(frame.pixel_format == HDUE_RGBA32_FLOAT && frame.color_space == HDUE_SCENE_LINEAR_SRGB, "Unexpected frame format");
    check(frame.required_bytes == uint64_t(frame.width) * frame.height * 4 * sizeof(float), "Invalid image size");
    std::vector<float> pixels(size_t(frame.required_bytes / sizeof(float)), -12345.0f);
    check(backend.api().copy_frame(session, frame.frame_id, pixels.data(), frame.required_bytes - 1) == HDUE_BUFFER_TOO_SMALL,
        "Short output buffers must be rejected");
    check(pixels.front() == -12345.0f && pixels.back() == -12345.0f, "Short-buffer call modified caller memory");
    check(backend.api().copy_frame(session, 0, pixels.data(), frame.required_bytes) == HDUE_STALE_FRAME,
        "Stale frame IDs must be rejected");
    backend.require(backend.api().copy_frame(session, frame.frame_id, pixels.data(), frame.required_bytes), "copy_frame");
    HdueSceneBatch empty_batch{};
    empty_batch.struct_size = sizeof(empty_batch);
    backend.require(backend.api().apply_scene(session, &empty_batch), "apply_scene cancellation");
    check(backend.api().request_render(session, &request) == HDUE_STALE_FRAME, "A cancelled generation was accepted again");
    HdueFrameInfo cancelled{}; cancelled.struct_size = sizeof(cancelled);
    check(backend.api().poll_frame(session, &cancelled) == HDUE_PENDING, "Cancelled generation remained visible");
    double maximum = 0, sum = 0;
    for (size_t i = 0; i < pixels.size(); i += 4) {
        for (size_t channel = 0; channel < 3; ++channel) {
            check(std::isfinite(pixels[i + channel]), "UE returned non-finite pixels");
            maximum = std::max(maximum, double(pixels[i + channel]));
            sum += pixels[i + channel];
        }
    }
    check(maximum > 0.001 && sum > 0.01, "Image is black: rendering gate failed");
    if (request.mode == HDUE_PATH_TRACED)
        check(frame.completed_samples >= request.samples_per_pixel, "Path Tracer declared convergence before reaching the sample target");
    hdue::write_pfm(file, frame, pixels.data());
    if (comma) metrics << ",\n";
    comma = true;
    metrics << "    {\"mode\":\"" << (request.mode == HDUE_REALTIME ? "realtime" : "pathtraced")
        << "\",\"generation\":" << frame.generation << ",\"width\":" << frame.width
        << ",\"height\":" << frame.height << ",\"samples\":" << frame.completed_samples
        << ",\"request_to_frame_ms\":" << frame.request_to_frame_ms << ",\"readback_ms\":" << frame.readback_ms
        << ",\"wall_seconds\":" << seconds(start) << ",\"ticks\":" << ticks << ",\"max_rgb\":" << maximum << "}";
    metrics.flush();
    std::cout << file.string() << ": " << frame.width << 'x' << frame.height
        << ", samples=" << frame.completed_samples << ", elapsed=" << seconds(start) << " s\n";
}
}

int wmain(int argc, wchar_t** argv) {
    HWND window = nullptr;
    try {
        const Options options = parse(argc, argv);
        const auto instance = GetModuleHandleW(nullptr);
        WNDCLASSW cls{};
        cls.hInstance = instance;
        cls.lpfnWndProc = window_proc;
        cls.lpszClassName = L"HdUEEmbeddingHost";
        check(RegisterClassW(&cls) != 0, "RegisterClass failed");
        window = CreateWindowExW(0, cls.lpszClassName, L"hdUE embedding test", WS_OVERLAPPEDWINDOW,
            CW_USEDEFAULT, CW_USEDEFAULT, 640, 360, nullptr, nullptr, instance, nullptr);
        check(window != nullptr, "CreateWindow failed");
        if (options.visible) ShowWindow(window, SW_SHOW);
        hdue::Backend backend(options.backend);
        active_backend = &backend;
        const auto project = utf8(options.project.wstring());
        HdueInitDesc desc{};
        desc.struct_size = sizeof(desc);
        desc.instance_handle = reinterpret_cast<uint64_t>(instance);
        desc.window_handle = reinterpret_cast<uint64_t>(window);
        desc.project_path = project.c_str();
        desc.map_path = "/Game/HdUETest";
        const auto started = Clock::now();
        backend.initialize(desc);
        const double init_ms = seconds(started) * 1000;
        HdueStatus status{};
        status.struct_size = sizeof(status);
        backend.require(backend.api().get_status(&status), "get_status");
        check(status.state == HDUE_STATE_READY && status.world_ready && status.rhi_ready, "UE World/RHI is not ready");
        check(status.engine_major == 5 && status.engine_minor == 8 && status.engine_patch == 4, "Unexpected UE version");
        HdueRendererInfo renderer_info{}; renderer_info.struct_size = sizeof(renderer_info);
        check(backend.material_api().get_renderer_info != nullptr, "Material extension is missing");
        backend.require(backend.material_api().get_renderer_info(&renderer_info), "get_renderer_info");
        check(renderer_info.substrate_enabled && renderer_info.adaptive_gbuffer && renderer_info.project_closures_per_pixel >= 4 && renderer_info.bytes_per_pixel >= 128,
            "Expected Substrate Adaptive GBuffer with the configured material budget");
        HdueResult other_thread = HDUE_OK;
        std::thread worker([&] { HdueStatus s{}; s.struct_size = sizeof(s); other_thread = backend.api().get_status(&s); });
        worker.join();
        check(other_thread == HDUE_WRONG_THREAD, "UE owner-thread contract is not enforced");
        std::filesystem::create_directories(options.output);
        std::ofstream metrics(options.output / "metrics.json");
        check(bool(metrics), "Cannot write metrics");
        metrics << "{\n  \"backend\":\"Unreal Engine 5.8.4 / UELibrary\",\n  \"hidden_window\":"
            << (options.visible ? "false" : "true") << ",\n  \"initialization_ms\":" << init_ms
            << ",\n  \"dedicated_video_memory_bytes\":" << status.dedicated_video_memory_bytes
            << ",\n  \"substrate_enabled\":true,\n  \"adaptive_gbuffer\":true,\n  \"substrate_closure_budget\":" << renderer_info.project_closures_per_pixel
            << ",\n  \"substrate_bytes_per_pixel\":" << renderer_info.bytes_per_pixel << ",\n  \"frames\":[\n";
        bool comma = false;
        uint64_t generation = 1;
        for (uint32_t cycle = 0; cycle < options.cycles; ++cycle) {
            HdueSessionDesc session_desc{};
            session_desc.struct_size = sizeof(session_desc);
            session_desc.width = options.width + cycle * 16;
            session_desc.height = options.height + cycle * 8;
            HdueSession session = 0;
            backend.require(backend.api().create_session(&session_desc, &session), "create_session");
            HdueRenderRequest request{};
            request.struct_size = sizeof(request);
            request.samples_per_pixel = options.samples;
            request.warmup_frames = 16;
            request.eye[0] = 350; request.eye[1] = -350; request.eye[2] = 250;
            request.target[2] = 50;
            request.horizontal_fov_degrees = 60;
            request.near_clip_cm = 1;
            request.lumen_enabled = 1;
            request.up[2] = 1;
            const std::string prefix = "cycle-" + std::to_string(cycle);
            if (options.mode != "pathtraced") {
                request.mode = HDUE_REALTIME;
                request.generation = generation++;
                render(backend, session, request, options, options.output / (prefix + "-realtime.pfm"), metrics, comma);
            }
            if (options.mode != "realtime") {
                check((status.render_mode_mask & (1u << HDUE_PATH_TRACED)) != 0, "Backend does not support hardware Path Tracing");
                request.mode = HDUE_PATH_TRACED;
                request.generation = generation++;
                render(backend, session, request, options, options.output / (prefix + "-pathtraced.pfm"), metrics, comma);
                request.eye[0] += 60;
                request.generation = generation++;
                render(backend, session, request, options, options.output / (prefix + "-pathtraced-edited.pfm"), metrics, comma);
            }
            backend.require(backend.api().destroy_session(session), "destroy_session");
            check(backend.api().destroy_session(session) == HDUE_INVALID_ARGUMENT, "Destroyed session remained accessible");
        }
        // Exercise the optional extension after image comparisons: these
        // scene edits deliberately replace the commandlet's reference world.
        const auto& pick = backend.native_pick_api();
        check(pick.pick_native && pick.get_material_info, "Native picking extension is missing");
        const auto get_pick = reinterpret_cast<HdueGetNativePickApiFn>(GetProcAddress(GetModuleHandleW(L"HdUEBackend.dll"), "hdue_get_native_pick_api"));
        HdueNativePickApi table{};
        check(get_pick && get_pick(999, sizeof(table), &table) == HDUE_ABI_MISMATCH, "Native extension ABI mismatch was accepted");
        check(get_pick(1, sizeof(table) - 1, &table) == HDUE_BUFFER_TOO_SMALL && !table.pick_native, "Short extension table was modified");
        HduePickMaterialInfo material_info{}; material_info.struct_size = sizeof(material_info);
        backend.require(pick.get_material_info("/Game/HdUE/M_Native.M_Native", &material_info), "native material metadata");
        check(material_info.flags == HDUE_PICK_MATERIAL_OPAQUE, "Unexpected native material metadata");
        std::thread pick_worker([&] { other_thread = pick.get_material_info("/Game/HdUE/M_Native.M_Native", &material_info); });
        pick_worker.join();
        check(other_thread == HDUE_WRONG_THREAD, "Native picking ignored the UE owner thread");
        HdueSessionDesc pick_desc{}; pick_desc.struct_size = sizeof(pick_desc); pick_desc.width = pick_desc.height = 32;
        HdueSession pick_session = 0;
        backend.require(backend.api().create_session(&pick_desc, &pick_session), "pick session");
        const std::string long_id = "/" + std::string(300, 'x');
        HdueMeshDesc mesh{}; mesh.id = long_id.c_str(); mesh.native_asset = "/Game/HdUE/SM_Nanite.SM_Nanite"; mesh.visible = 1;
        mesh.transform[0] = -2; mesh.transform[5] = 3; mesh.transform[10] = 0.5; mesh.transform[15] = 1;
        mesh.transform[12] = 125; mesh.transform[13] = -70; mesh.transform[14] = 30;
        HdueSceneBatch pick_batch{}; pick_batch.struct_size = sizeof(pick_batch); pick_batch.reset_scene = 1; pick_batch.mesh_count = 1; pick_batch.meshes = &mesh;
        backend.require(backend.api().apply_scene(pick_session, &pick_batch), "native pick scene");
        HdueNativeRay ray{}; ray.struct_size = sizeof(ray); ray.origin[0] = 125; ray.origin[1] = -70; ray.origin[2] = 500;
        ray.direction[2] = -1; ray.maximum_cm = 1000;
        HdueNativeHit hit{}; hit.struct_size = sizeof(hit);
        char short_id[2] = {'a', 'b'};
        check(pick.pick_native(pick_session, &ray, &hit, short_id, sizeof(short_id)) == HDUE_BUFFER_TOO_SMALL, "Native pick did not report short ID buffer");
        check(hit.required_id_bytes == long_id.size() + 1 && short_id[0] == 'a' && short_id[1] == 'b', "Native pick truncated an ID / changed short caller memory");
        std::vector<char> picked_id(hit.required_id_bytes);
        backend.require(pick.pick_native(pick_session, &ray, &hit, picked_id.data(), uint32_t(picked_id.size())), "native pick hit");
        std::cout << "Native pick: hit=" << hit.hit << ", distance=" << hit.distance_cm << ", point=(" << hit.point[0] << ',' << hit.point[1] << ',' << hit.point[2]
            << "), normal=(" << hit.normal[0] << ',' << hit.normal[1] << ',' << hit.normal[2] << "), flags=" << hit.flags << '\n';
        check(hit.hit && picked_id.data() == long_id && std::abs(hit.distance_cm - 445) < 0.01 && std::abs(hit.point[2] - 55) < 0.01 && hit.normal[2] > 0.99,
            "Native pick did not use the actual transformed mesh");
        check((hit.flags & (HDUE_NATIVE_PICK_LOD0 | HDUE_NATIVE_PICK_NANITE_FALLBACK)) == (HDUE_NATIVE_PICK_LOD0 | HDUE_NATIVE_PICK_NANITE_FALLBACK), "Native geometry provenance is missing");
        ray.minimum_cm = 446;
        backend.require(pick.pick_native(pick_session, &ray, &hit, nullptr, 0), "native near clipping");
        check(!hit.hit && !hit.required_id_bytes, "Native pick ignored near clipping / front-face culling");
        ray.minimum_cm = 0; mesh.visible = 0; pick_batch.reset_scene = 0;
        backend.require(backend.api().apply_scene(pick_session, &pick_batch), "hide native mesh");
        backend.require(pick.pick_native(pick_session, &ray, &hit, nullptr, 0), "hidden native pick");
        check(!hit.hit, "Native pick ignored visibility");
        backend.require(backend.api().destroy_session(pick_session), "destroy native pick session");
        check(pick.pick_native(pick_session, &ray, &hit, nullptr, 0) == HDUE_INVALID_ARGUMENT, "Native pick accepted a destroyed session");
        backend.shutdown();
        check(backend.api().initialize(&desc) == HDUE_INVALID_STATE, "UE must not reinitialize after shutdown");
        active_backend = nullptr;
        DestroyWindow(window);
        metrics << "\n  ],\n  \"native_picking\":true,\n  \"passed\":true\n}\n";
        metrics.close();
        std::cout << "PASS: real UE embedding, image readback, session cycles and shutdown\n";
        return 0;
    } catch (const std::exception& error) {
        active_backend = nullptr;
        if (window) DestroyWindow(window);
        std::cerr << "FAIL: " << error.what() << '\n';
        return 1;
    }
}

