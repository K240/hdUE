#ifndef HDUE_SURFACE_API_H
#define HDUE_SURFACE_API_H
#include "hdue/bridge.h"
#ifdef __cplusplus
extern "C" {
#endif
#define HDUE_SURFACE_ABI_VERSION 1u
enum { HDUE_WET_MASK_PREVIEW = 1u, HDUE_SURFACE_GRAPH = 2u };
/* A full snapshot of enabled bindings. An empty snapshot restores dry bindings.
 * geometry_revision starts at 1 and increments on every base mesh upsert in
 * this session, resetting when removed. Masks address expanded mesh vertices.
 * count=0 selects uniform coverage. All data is copied before return. */
typedef struct HdueWetBinding {
    uint32_t struct_size, flags;
    const char* object_id;
    const char* variant_asset;
    const char* preview_asset;
    uint64_t geometry_revision;
    float amount, roughness;
    uint32_t mask_count, reserved;
    const float* mask;
} HdueWetBinding;
typedef struct HdueSurfaceBatch {
    uint32_t struct_size, binding_count;
    const HdueWetBinding* bindings;
} HdueSurfaceBatch;
typedef struct HdueSurfaceStatus {
    uint32_t struct_size, binding_count, upload_pending, reserved;
    uint64_t mask_uploads, mask_bytes, geometry_uploads, updates;
} HdueSurfaceStatus;
typedef struct HdueSurfaceApi {
    uint32_t struct_size, abi_version;
    HdueResult (HDUE_CALL *apply)(HdueSession, const HdueSurfaceBatch*);
    HdueResult (HDUE_CALL *get_status)(HdueSession, HdueSurfaceStatus*);
} HdueSurfaceApi;
typedef HdueResult (HDUE_CALL *HdueGetSurfaceApiFn)(uint32_t, uint32_t, HdueSurfaceApi*);
HDUE_EXPORT HdueResult HDUE_CALL hdue_get_surface_api(uint32_t, uint32_t, HdueSurfaceApi*);
#ifdef __cplusplus
}
#endif
#endif
