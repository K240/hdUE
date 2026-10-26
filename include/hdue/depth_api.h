#ifndef HDUE_DEPTH_API_H
#define HDUE_DEPTH_API_H
#include "hdue/bridge.h"
#ifdef __cplusplus
extern "C" {
#endif
#define HDUE_DEPTH_ABI_VERSION 1u
/* Optional color/depth readback. Enable before requesting a generation.
 * copy_depth uses the color frame_id and caller-owned float32 storage.
 * Pixels are top-down axial camera distance in centimeters; zero is background.
 * A stale color/depth pair must be discarded and polled again. All calls use
 * the runtime owner thread. No allocation or UE type crosses the boundary. */
typedef struct HdueDepthApi {
    uint32_t struct_size, abi_version;
    HdueResult (HDUE_CALL *set_enabled)(HdueSession session, uint32_t enabled);
    HdueResult (HDUE_CALL *copy_depth)(HdueSession session, uint64_t frame_id, float* output, uint64_t capacity_bytes);
} HdueDepthApi;
typedef HdueResult (HDUE_CALL *HdueGetDepthApiFn)(uint32_t version,uint32_t capacity,HdueDepthApi* api);
HDUE_EXPORT HdueResult HDUE_CALL hdue_get_depth_api(uint32_t version,uint32_t capacity,HdueDepthApi* api);
#ifdef __cplusplus
}
#endif
#endif
