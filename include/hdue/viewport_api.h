#ifndef HDUE_VIEWPORT_API_H
#define HDUE_VIEWPORT_API_H
#include "hdue/bridge.h"
#include "hdue/material_api.h"
#ifdef HDUE_VIEWPORT_EXPORTS
#define HDUE_VIEWPORT_EXPORT __declspec(dllexport)
#else
#define HDUE_VIEWPORT_EXPORT __declspec(dllimport)
#endif
#ifdef __cplusplus
extern "C" {
#endif
/* Host-side viewport extension, exported by the Houdini delegate DLL.
 * Call on the Houdini UI thread. No ownership crosses this boundary. */
typedef struct HdueViewportStatus {
    uint32_t struct_size, active, create_thread, execute_thread;
    HdueSession session;
    uint64_t generation, published_frames, scene_submissions;
    uint32_t mode, meshes, materials, lights;
    HdueFrameInfo frame;
    char error[512];
} HdueViewportStatus;
HDUE_VIEWPORT_EXPORT HdueResult HDUE_CALL hdue_get_viewport_status(HdueViewportStatus* status);
typedef struct HdueViewportOptions {
    uint32_t struct_size, mode, samples, lumen, auto_light, z_up;
    double meters_per_unit, time_seconds;
} HdueViewportOptions;
HDUE_VIEWPORT_EXPORT HdueResult HDUE_CALL hdue_set_viewport_options(const HdueViewportOptions* options);
typedef struct HdueViewportMaterial {
    const char* material_id;
    const char* native_asset;
    uint32_t parameter_count, reserved;
    const HdueMaterialParameter* parameters;
} HdueViewportMaterial;
typedef struct HdueViewportMaterials {
    uint32_t struct_size, abi_version;
    HdueSession session;
    uint32_t material_count, reserved;
    const HdueViewportMaterial* materials;
} HdueViewportMaterials;
/* Version 1. Replace the complete prepared-material binding set atomically.
 * Stale sessions and invalid batches leave existing settings untouched. */
HDUE_VIEWPORT_EXPORT HdueResult HDUE_CALL hdue_set_viewport_materials(const HdueViewportMaterials* materials);
#ifdef __cplusplus
}
#endif
#endif
