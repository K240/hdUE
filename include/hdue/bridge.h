#ifndef HDUE_BRIDGE_H
#define HDUE_BRIDGE_H

#include <stdint.h>

#if defined(_WIN32)
#define HDUE_CALL __cdecl
#if defined(HDUE_BRIDGE_EXPORTS)
#define HDUE_EXPORT __declspec(dllexport)
#else
#define HDUE_EXPORT
#endif
#else
#define HDUE_CALL
#define HDUE_EXPORT
#endif

#ifdef __cplusplus
extern "C" {
#endif

#define HDUE_ABI_VERSION 1u
#define HDUE_ENGINE_MAJOR 5u
#define HDUE_ENGINE_MINOR 8u
#define HDUE_ENGINE_PATCH 4u

typedef int32_t HdueResult;
typedef uint64_t HdueSession;
enum {
    HDUE_OK = 0,
    HDUE_PENDING = 1,
    HDUE_INVALID_ARGUMENT = -1,
    HDUE_ABI_MISMATCH = -2,
    HDUE_WRONG_THREAD = -3,
    HDUE_INVALID_STATE = -4,
    HDUE_NOT_SUPPORTED = -5,
    HDUE_BUFFER_TOO_SMALL = -6,
    HDUE_STALE_FRAME = -7,
    HDUE_ENGINE_ERROR = -8
};
enum {
    HDUE_STATE_COLD = 0,
    HDUE_STATE_STARTING = 1,
    HDUE_STATE_READY = 2,
    HDUE_STATE_FAILED = 3,
    HDUE_STATE_STOPPED = 4
};
enum { HDUE_REALTIME = 0, HDUE_PATH_TRACED = 1 };
enum { HDUE_RGBA32_FLOAT = 1, HDUE_SCENE_LINEAR_SRGB = 1, HDUE_MESH_REQUIRE_NANITE = 1 };

/* Every descriptor is caller-owned. Set struct_size to sizeof(the descriptor).
 * Strings are UTF-8 and are copied during the call. No UE/pxr/STL type, native
 * allocation or exception crosses this ABI. All calls except get_api execute
 * on the thread that called initialize. One engine lifetime per process. */
typedef struct HdueInitDesc {
    uint32_t struct_size;
    uint32_t reserved;
    uint64_t instance_handle;
    uint64_t window_handle;
    const char* project_path;
    const char* map_path;
    const char* extra_arguments;
} HdueInitDesc;

typedef struct HdueStatus {
    uint32_t struct_size;
    uint32_t state;
    uint32_t world_ready;
    uint32_t rhi_ready;
    uint32_t engine_major;
    uint32_t engine_minor;
    uint32_t engine_patch;
    uint32_t render_mode_mask;
    uint64_t dedicated_video_memory_bytes;
    char message[512];
} HdueStatus;

typedef struct HdueSessionDesc {
    uint32_t struct_size;
    uint32_t width;
    uint32_t height;
    uint32_t reserved;
} HdueSessionDesc;

typedef struct HdueRenderRequest {
    uint32_t struct_size;
    uint32_t mode;
    uint64_t generation;
    uint32_t samples_per_pixel;
    uint32_t warmup_frames;
    double time_seconds;
    /* UE coordinates in cm, +X forward, +Z up. Hydra performs conversion. */
    double eye[3];
    double target[3];
    float horizontal_fov_degrees;
    float near_clip_cm;
    uint32_t lumen_enabled;
    uint32_t reserved;
    double up[3];
} HdueRenderRequest;

typedef struct HdueFrameInfo {
    uint32_t struct_size;
    uint32_t width;
    uint32_t height;
    uint32_t pixel_format;
    uint32_t color_space;
    uint32_t row_stride_bytes;
    uint64_t generation;
    uint64_t frame_id;
    uint64_t required_bytes;
    uint32_t completed_samples;
    uint32_t converged;
    double request_to_frame_ms;
    double readback_ms;
} HdueFrameInfo;

enum { HDUE_DISTANT_LIGHT = 0, HDUE_SPHERE_LIGHT = 1, HDUE_RECT_LIGHT = 2,
    HDUE_CAMERA_DISTANT_LIGHT = 3 };
typedef struct HdueMaterialDesc {
    const char* id;
    const char* native_asset;
    const char* base_color_texture;
    float base_color[4];
    float metallic;
    float roughness;
} HdueMaterialDesc;
typedef struct HdueMeshDesc {
    const char* id;
    const char* material_id;
    const char* native_asset;
    const float* positions;
    const float* normals;
    const float* uv;
    const uint32_t* indices;
    uint32_t vertex_count;
    uint32_t index_count;
    uint32_t visible;
    uint32_t flags;
    /* Row-major UE world transform. Dynamic vertices are in local UE cm. */
    double transform[16];
} HdueMeshDesc;
typedef struct HdueLightDesc {
    const char* id;
    uint32_t type;
    uint32_t visible;
    double position[3];
    /* World direction, or (forward,right,up) relative to the render camera
       for HDUE_CAMERA_DISTANT_LIGHT. The latter updates with request_render. */
    double direction[3];
    float color[3];
    float intensity;
    float width_cm;
    float height_cm;
} HdueLightDesc;
typedef struct HdueSceneBatch {
    uint32_t struct_size;
    uint32_t reset_scene;
    const HdueMaterialDesc* materials;
    uint32_t material_count;
    uint32_t mesh_count;
    const HdueMeshDesc* meshes;
    const HdueLightDesc* lights;
    uint32_t light_count;
    uint32_t removed_count;
    const char* const* removed_ids;
} HdueSceneBatch;

typedef struct HdueApi {
    uint32_t struct_size;
    uint32_t abi_version;
    HdueResult (HDUE_CALL *initialize)(const HdueInitDesc* desc);
    HdueResult (HDUE_CALL *get_status)(HdueStatus* status);
    HdueResult (HDUE_CALL *tick)(void);
    HdueResult (HDUE_CALL *create_session)(const HdueSessionDesc* desc, HdueSession* session);
    HdueResult (HDUE_CALL *destroy_session)(HdueSession session);
    HdueResult (HDUE_CALL *request_render)(HdueSession session, const HdueRenderRequest* request);
    HdueResult (HDUE_CALL *poll_frame)(HdueSession session, HdueFrameInfo* frame);
    HdueResult (HDUE_CALL *copy_frame)(HdueSession session, uint64_t frame_id, void* buffer, uint64_t capacity);
    HdueResult (HDUE_CALL *window_message)(uint64_t hwnd, uint32_t message, uint64_t wparam, int64_t lparam, int64_t* result);
    HdueResult (HDUE_CALL *shutdown)(void);
    HdueResult (HDUE_CALL *apply_scene)(HdueSession session, const HdueSceneBatch* batch);
} HdueApi;

/* Does not initialize the engine. The DLL must remain loaded until process exit. */
typedef HdueResult (HDUE_CALL *HdueGetApiFn)(uint32_t version, uint32_t capacity, HdueApi* api);
HDUE_EXPORT HdueResult HDUE_CALL hdue_get_api(uint32_t version, uint32_t capacity, HdueApi* api);

/* Optional versioned extension. The original v1 function table stays unchanged. */
#define HDUE_NATIVE_PICK_ABI_VERSION 1u
enum { HDUE_NATIVE_PICK_LOD0 = 1, HDUE_NATIVE_PICK_NANITE_FALLBACK = 2 };
enum { HDUE_PICK_MATERIAL_OPAQUE = 1, HDUE_PICK_MATERIAL_TWO_SIDED = 2, HDUE_PICK_MATERIAL_DEFORMED = 4 };
typedef struct HdueNativeRay {
    uint32_t struct_size;
    uint32_t reserved;
    /* UE world cm. Direction is normalized by the backend. */
    double origin[3];
    double direction[3];
    double minimum_cm;
    double maximum_cm;
} HdueNativeRay;
typedef struct HdueNativeHit {
    uint32_t struct_size;
    uint32_t hit;
    uint32_t flags;
    uint32_t required_id_bytes;
    double distance_cm;
    double point[3];
    double normal[3];
} HdueNativeHit;
typedef struct HduePickMaterialInfo {
    uint32_t struct_size;
    uint32_t flags;
} HduePickMaterialInfo;
typedef struct HdueNativePickApi {
    uint32_t struct_size;
    uint32_t abi_version;
    /* Owner thread only. No Tick/render wait. On a hit, ID includes a trailing
       NUL; BUFFER_TOO_SMALL fills hit.required_id_bytes without touching ID.
       StaticMesh uses CPU-readable resident LOD0, Nanite uses fallback LOD0. */
    HdueResult (HDUE_CALL *pick_native)(HdueSession session, const HdueNativeRay* ray,
        HdueNativeHit* hit, char* object_id, uint32_t id_capacity);
    HdueResult (HDUE_CALL *get_material_info)(const char* asset_path, HduePickMaterialInfo* info);
} HdueNativePickApi;
typedef HdueResult (HDUE_CALL *HdueGetNativePickApiFn)(uint32_t version, uint32_t capacity, HdueNativePickApi* api);
HDUE_EXPORT HdueResult HDUE_CALL hdue_get_native_pick_api(uint32_t version, uint32_t capacity, HdueNativePickApi* api);

#ifdef __cplusplus
}
#endif
#endif

