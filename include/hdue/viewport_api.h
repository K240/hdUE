#ifndef HDUE_VIEWPORT_API_H
#define HDUE_VIEWPORT_API_H
#include "hdue/bridge.h"
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
#ifdef __cplusplus
}
#endif
#endif
