#ifndef MESH_SIM_H
#define MESH_SIM_H

#include <stdbool.h>
#include <stdint.h>

#include "router.h"

#define MESH_MAX_LINK_DELAY VERTICAL_LINK_DELAY
#define MESH_MAX_CREDIT_DELAY VERTICAL_CREDIT_DEPTH

typedef struct {
    bool valid;
    flit_t flit;
} mesh_link_event_t;

typedef struct {
    bool valid;
    uint32_t source_id;
    port_t output_port;
} mesh_credit_event_t;

typedef struct {
    router_context_t router[MESH_NODES];
    router_inputs_t ingress[MESH_NODES];
    router_inputs_t pe_ramp[MESH_NODES];
    bool eject_ramp_valid[MESH_NODES];
    flit_t eject_ramp_flit[MESH_NODES];
    mesh_link_event_t link[MESH_MAX_LINK_DELAY][MESH_NODES][PORT_COUNT];
    mesh_credit_event_t credit[MESH_MAX_CREDIT_DELAY][MESH_NODES][PORT_COUNT];
    bool ejected_valid[MESH_NODES];
    flit_t ejected_flit[MESH_NODES];
    uint32_t cycle;
} mesh_sim_t;

void mesh_sim_init(mesh_sim_t *mesh);
void mesh_sim_load_calendar(mesh_sim_t *mesh, uint8_t x, uint8_t y,
                            uint16_t slot, const calendar_entry_t *entry);
bool mesh_sim_inject(mesh_sim_t *mesh, uint8_t x, uint8_t y,
                     const flit_t *flit);
void mesh_sim_advance(mesh_sim_t *mesh);
bool mesh_sim_take_ejected(mesh_sim_t *mesh, uint8_t x, uint8_t y,
                           flit_t *flit);

#endif
