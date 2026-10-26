#ifndef HDUE_MATERIAL_API_H
#define HDUE_MATERIAL_API_H
#include "hdue/bridge.h"
#ifdef __cplusplus
extern "C" {
#endif

#define HDUE_MATERIAL_ABI_VERSION 1u
enum { HDUE_PARAMETER_SCALAR = 0, HDUE_PARAMETER_VECTOR = 1, HDUE_PARAMETER_TEXTURE = 2 };
typedef struct HdueRendererInfo {
    uint32_t struct_size;
    uint32_t substrate_enabled;
    uint32_t adaptive_gbuffer;
    uint32_t project_closures_per_pixel;
    uint32_t bytes_per_pixel;
} HdueRendererInfo;
typedef struct HdueMaterialParameter {
    const char* name;
    uint32_t type;
    uint32_t reserved;
    float value[4];
    /* A cooked UE texture path for HDUE_PARAMETER_TEXTURE. Caller owned UTF-8. */
    const char* texture_asset;
} HdueMaterialParameter;
typedef struct HdueMaterialUpdate {
    uint32_t struct_size;
    uint32_t parameter_count;
    const char* material_id;
    const HdueMaterialParameter* parameters;
} HdueMaterialUpdate;
typedef struct HdueMaterialStats {
    uint32_t struct_size;
    uint32_t substrate_enabled;
    uint32_t closure_count;
    uint32_t bytes_per_pixel;
    uint32_t scalar_parameters;
    uint32_t vector_parameters;
    uint32_t texture_parameters;
    uint32_t blend_mode;
} HdueMaterialStats;
typedef struct HdueMaterialApi {
    uint32_t struct_size;
    uint32_t abi_version;
    HdueResult (HDUE_CALL *get_renderer_info)(HdueRendererInfo* info);
    /* Owner thread. Validates the complete batch before changing any value.
       Successful edits cancel the current render; request a newer generation. */
    HdueResult (HDUE_CALL *set_parameters)(HdueSession session, const HdueMaterialUpdate* update);
    HdueResult (HDUE_CALL *get_material_stats)(HdueSession session, const char* material_id, HdueMaterialStats* stats);
} HdueMaterialApi;
typedef HdueResult (HDUE_CALL *HdueGetMaterialApiFn)(uint32_t version, uint32_t capacity, HdueMaterialApi* api);
HDUE_EXPORT HdueResult HDUE_CALL hdue_get_material_api(uint32_t version, uint32_t capacity, HdueMaterialApi* api);
#ifdef __cplusplus
}
#endif
#endif
