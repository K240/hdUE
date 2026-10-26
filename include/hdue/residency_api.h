#ifndef HDUE_RESIDENCY_API_H
#define HDUE_RESIDENCY_API_H
#include "hdue/bridge.h"
#ifdef __cplusplus
extern "C" {
#endif
#define HDUE_RESIDENCY_ABI_VERSION 1u
enum { HDUE_RESIDENCY_IDLE, HDUE_RESIDENCY_PREPARING, HDUE_RESIDENCY_READY, HDUE_RESIDENCY_FAILED };
typedef struct HdueResidencyOptions {
    uint32_t struct_size;
    uint32_t maximum_pages; /* 0: use physical pool capacity; otherwise also enforce this total budget. */
    double timeout_seconds; /* Positive, preparation only. Default 120 seconds. */
} HdueResidencyOptions;
typedef struct HdueResidencyStatus {
    uint32_t struct_size;
    uint32_t phase;
    uint64_t generation;
    uint64_t required_pages;
    uint64_t resident_pages;
    uint64_t locked_tiles;
    uint64_t required_mappings;
    uint64_t ready_mappings;
    uint32_t textures;
    uint32_t preparation_frames;
    double preparation_ms;
    char message[512];
} HdueResidencyStatus;
typedef struct HdueResidencyApi {
    uint32_t struct_size;
    uint32_t abi_version;
    /* Owner thread. Configure before request_render; changes cancel the current request. */
    HdueResult (HDUE_CALL *configure)(HdueSession session, const HdueResidencyOptions* options);
    HdueResult (HDUE_CALL *get_status)(HdueSession session, HdueResidencyStatus* status);
} HdueResidencyApi;
typedef HdueResult (HDUE_CALL *HdueGetResidencyApiFn)(uint32_t version, uint32_t capacity, HdueResidencyApi* api);
HDUE_EXPORT HdueResult HDUE_CALL hdue_get_residency_api(uint32_t version, uint32_t capacity, HdueResidencyApi* api);
#ifdef __cplusplus
}
#endif
#endif
