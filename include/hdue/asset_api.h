#ifndef HDUE_ASSET_API_H
#define HDUE_ASSET_API_H
#include "hdue/bridge.h"
#ifdef __cplusplus
extern "C" {
#endif
#define HDUE_ASSET_ABI_VERSION 1u
enum { HDUE_ASSET_UNKNOWN=0, HDUE_ASSET_STATIC_MESH=1, HDUE_ASSET_SKELETAL_MESH=2,
       HDUE_ASSET_ACTOR=3, HDUE_ASSET_LEVEL=4, HDUE_ASSET_RESOURCE=5 };
enum { HDUE_ASSET_LOADING=0, HDUE_ASSET_READY=1, HDUE_ASSET_FAILED=2 };
typedef struct HdueAssetDesc {
    uint32_t struct_size, visible;
    const char* id;
    const char* asset_path; /* Cooked UE object path; input copied during the call. */
    double transform[16]; /* Native asset cm -> UE world cm, row-major. */
} HdueAssetDesc;
typedef struct HdueAssetStatus {
    uint32_t struct_size, kind, state, actors, meshes, lights;
    double bounds_min[3], bounds_max[3]; /* UE world cm. */
    char message[512]; /* Caller owned; no allocation crosses the DLL boundary. */
} HdueAssetStatus;
typedef struct HdueAssetSceneStatus {
    uint32_t struct_size, instances, loading, failed, actors, meshes, lights, reserved;
    char message[512];
} HdueAssetSceneStatus;
typedef struct HdueAssetApi {
    uint32_t struct_size, abi_version;
    HdueResult (HDUE_CALL *upsert)(HdueSession session, const HdueAssetDesc* desc);
    HdueResult (HDUE_CALL *remove)(HdueSession session, const char* id);
    HdueResult (HDUE_CALL *get_instance_status)(HdueSession session, const char* id, HdueAssetStatus* status);
    HdueResult (HDUE_CALL *get_scene_status)(HdueSession session, HdueAssetSceneStatus* status);
} HdueAssetApi;
typedef HdueResult (HDUE_CALL *HdueGetAssetApiFn)(uint32_t version, uint32_t capacity, HdueAssetApi* api);
HDUE_EXPORT HdueResult HDUE_CALL hdue_get_asset_api(uint32_t version, uint32_t capacity, HdueAssetApi* api);
#ifdef __cplusplus
}
#endif
#endif
