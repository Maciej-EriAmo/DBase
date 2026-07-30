/* KarmazynOS substrate — C ABI (generated hand-header, matches ffi.rs) */
#ifndef KARMAZYN_SUBSTRATE_H
#define KARMAZYN_SUBSTRATE_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

const char *ksub_version(void);

uint64_t ksub_store_new(int thermal);
void     ksub_store_free(uint64_t handle);

uint32_t ksub_atom_new(uint64_t handle, const char *s, const char *e, double t);
int      ksub_atom_set_value(uint64_t handle, uint32_t aid, uint64_t token);
uint64_t ksub_atom_value(uint64_t handle, uint32_t aid);
int      ksub_has_atom(uint64_t handle, uint32_t aid);
int      ksub_delete_atom(uint64_t handle, uint32_t aid);
int      ksub_heat(uint64_t handle, uint32_t aid);
double   ksub_atom_t(uint64_t handle, uint32_t aid);
int      ksub_atom_set_t(uint64_t handle, uint32_t aid, double t);
int      ksub_atom_is_dead(uint64_t handle, uint32_t aid);
int      ksub_atom_upsert(uint64_t handle, uint32_t aid, const char *s, const char *e, double t, uint64_t token);
int32_t  ksub_atom_ids(uint64_t handle, uint32_t *out, uint32_t max_out);

uint32_t ksub_bubble_new(uint64_t handle, const char *label, int64_t parent /* -1 = none */);
int      ksub_bind(uint64_t handle, uint32_t bid, const char *name, uint32_t aid);
int64_t  ksub_lookup(uint64_t handle, uint32_t bid, const char *name);
int64_t  ksub_unbind(uint64_t handle, uint32_t bid, const char *name);
void     ksub_set_root(uint64_t handle, uint32_t bid);
void     ksub_unset_root(uint64_t handle, uint32_t bid);

void ksub_tick(uint64_t handle);
void ksub_settle(uint64_t handle, uint32_t n);

typedef struct KSubStats {
    uint64_t total, hot, warm, cold, tomb;
    uint64_t alive, dead, reaped, retained_tomb, bubbles;
} KSubStats;

int ksub_stats(uint64_t handle, KSubStats *out);

typedef uint32_t (*ksub_env_of_fn)(uint64_t value_token, void *userdata);
typedef uint32_t (*ksub_extra_reach_fn)(uint32_t *out, uint32_t max_out, void *userdata);

int ksub_register_env_of(uint64_t handle, ksub_env_of_fn cb, void *userdata);
int ksub_register_extra_reach(uint64_t handle, ksub_extra_reach_fn cb, void *userdata);

#ifdef __cplusplus
}
#endif
#endif
