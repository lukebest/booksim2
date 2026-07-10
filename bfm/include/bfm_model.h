#ifndef BFM_MODEL_H
#define BFM_MODEL_H

#include <stdbool.h>
#include <stdint.h>

#include "mesh_sim.h"

void bfm_mesh_reset(mesh_sim_t *mesh);
bool bfm_router_model_is_refc_compatible(void);
void bfm_write_module_logs(uint32_t cycles, bool pass);

#endif
