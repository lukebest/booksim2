#include "mesh_sim.h"

#include <string.h>

#include "ext_mem.h"

static bool neighbor_for_port(uint8_t x, uint8_t y, port_t output_port,
                              uint8_t *next_x, uint8_t *next_y)
{
    *next_x = x;
    *next_y = y;
    if ((output_port == PORT_NORTH) && (y > 0U)) {
        *next_y = y - 1U;
    } else if ((output_port == PORT_EAST) && ((uint32_t)x + 1U < MESH_X)) {
        *next_x = x + 1U;
    } else if ((output_port == PORT_SOUTH) && ((uint32_t)y + 1U < MESH_Y)) {
        *next_y = y + 1U;
    } else if ((output_port == PORT_WEST) && (x > 0U)) {
        *next_x = x - 1U;
    } else {
        return false;
    }
    return true;
}

static uint32_t link_delay_for_port(port_t output_port)
{
    if ((output_port == PORT_EAST) || (output_port == PORT_WEST)) {
        return HORIZONTAL_LINK_DELAY;
    }
    return VERTICAL_LINK_DELAY;
}

static uint32_t credit_delay_for_port(port_t output_port)
{
    if ((output_port == PORT_EAST) || (output_port == PORT_WEST)) {
        return HORIZONTAL_CREDIT_DEPTH;
    }
    return VERTICAL_CREDIT_DEPTH;
}

void mesh_sim_init(mesh_sim_t *mesh)
{
    uint32_t x;
    uint32_t y;
    uint32_t id;
    uint32_t calendar_bytes = (uint32_t)CALENDAR_BANKS * CALENDAR_SPARSE_DEPTH *
                              (uint32_t)sizeof(calendar_sparse_event_t);

    if (mesh == NULL) {
        return;
    }
    (void)memset(mesh, 0, sizeof(*mesh));
    ext_mem_clear();
    for (y = 0U; y < MESH_Y; ++y) {
        for (x = 0U; x < MESH_X; ++x) {
            id = mesh_node_id((uint8_t)x, (uint8_t)y);
            router_init(&mesh->router[id], (uint8_t)x, (uint8_t)y,
                        id * calendar_bytes);
        }
    }
}

void mesh_sim_load_calendar(mesh_sim_t *mesh, uint8_t x, uint8_t y,
                            uint16_t slot, const calendar_entry_t *entry)
{
    if ((mesh != NULL) && mesh_valid_coord(x, y)) {
        calendar_store_load(&mesh->router[mesh_node_id(x, y)].calendar, 0U,
                            slot, entry);
    }
}

bool mesh_sim_inject(mesh_sim_t *mesh, uint8_t x, uint8_t y,
                     const flit_t *flit)
{
    uint32_t id;

    if ((mesh == NULL) || (flit == NULL) || !mesh_valid_coord(x, y)) {
        return false;
    }
    id = mesh_node_id(x, y);
    if (mesh->pe_ramp[id].valid[PORT_LOCAL] ||
        mesh->ingress[id].valid[PORT_LOCAL]) {
        return false;
    }
    mesh->pe_ramp[id].flit[PORT_LOCAL] = *flit;
    mesh->pe_ramp[id].valid[PORT_LOCAL] = true;
    return true;
}

void mesh_sim_advance(mesh_sim_t *mesh)
{
    router_outputs_t outputs[MESH_NODES];
    uint32_t id;
    uint32_t port_index;
    uint8_t next_x;
    uint8_t next_y;
    uint32_t next_id;
    port_t output_port;
    uint32_t delay;

    if (mesh == NULL) {
        return;
    }
    for (id = 0U; id < MESH_NODES; ++id) {
        if (mesh->eject_ramp_valid[id]) {
            mesh->ejected_flit[id] = mesh->eject_ramp_flit[id];
            mesh->ejected_valid[id] = true;
            mesh->eject_ramp_valid[id] = false;
        }
        for (port_index = 0U; port_index < PORT_COUNT; ++port_index) {
            if (mesh->link[0][id][port_index].valid) {
                output_port = (port_t)port_index;
                if (neighbor_for_port(mesh->router[id].x, mesh->router[id].y,
                                      output_port, &next_x, &next_y)) {
                    next_id = mesh_node_id(next_x, next_y);
                    mesh->ingress[next_id].flit[(uint32_t)port_opposite(output_port)] =
                        mesh->link[0][id][port_index].flit;
                    mesh->ingress[next_id].valid[(uint32_t)port_opposite(output_port)] =
                        true;
                }
                mesh->link[0][id][port_index].valid = false;
            }
            if (mesh->credit[0][id][port_index].valid) {
                router_add_credit(&mesh->router[mesh->credit[0][id][port_index].source_id],
                                  mesh->credit[0][id][port_index].output_port);
                mesh->credit[0][id][port_index].valid = false;
            }
            if (mesh->pe_ramp[id].valid[port_index]) {
                mesh->ingress[id].flit[port_index] = mesh->pe_ramp[id].flit[port_index];
                mesh->ingress[id].valid[port_index] = true;
                mesh->pe_ramp[id].valid[port_index] = false;
            }
        }
    }
    for (delay = 0U; delay + 1U < MESH_MAX_LINK_DELAY; ++delay) {
        (void)memcpy(mesh->link[delay], mesh->link[delay + 1U],
                     sizeof(mesh->link[delay]));
    }
    for (delay = 0U; delay + 1U < MESH_MAX_CREDIT_DELAY; ++delay) {
        (void)memcpy(mesh->credit[delay], mesh->credit[delay + 1U],
                     sizeof(mesh->credit[delay]));
    }
    (void)memset(mesh->link[MESH_MAX_LINK_DELAY - 1U], 0,
                 sizeof(mesh->link[MESH_MAX_LINK_DELAY - 1U]));
    (void)memset(mesh->credit[MESH_MAX_CREDIT_DELAY - 1U], 0,
                 sizeof(mesh->credit[MESH_MAX_CREDIT_DELAY - 1U]));
    (void)memset(outputs, 0, sizeof(outputs));
    for (id = 0U; id < MESH_NODES; ++id) {
        router_step(&mesh->router[id], mesh->cycle, &mesh->ingress[id],
                    &outputs[id]);
    }
    (void)memset(mesh->ingress, 0, sizeof(mesh->ingress));
    for (id = 0U; id < MESH_NODES; ++id) {
        for (port_index = 0U; port_index < PORT_COUNT; ++port_index) {
            output_port = (port_t)port_index;
            if (!outputs[id].valid[port_index]) {
                continue;
            }
            if (output_port == PORT_LOCAL) {
                mesh->eject_ramp_flit[id] = outputs[id].flit[port_index];
                mesh->eject_ramp_valid[id] = true;
            } else if (neighbor_for_port(mesh->router[id].x, mesh->router[id].y,
                                         output_port, &next_x, &next_y)) {
                delay = link_delay_for_port(output_port);
                mesh->link[delay - 1U][id][port_index].flit =
                    outputs[id].flit[port_index];
                mesh->link[delay - 1U][id][port_index].valid = true;
                delay = credit_delay_for_port(output_port);
                mesh->credit[delay - 1U][id][port_index].source_id = id;
                mesh->credit[delay - 1U][id][port_index].output_port = output_port;
                mesh->credit[delay - 1U][id][port_index].valid = true;
            }
        }
    }
    mesh->cycle++;
}

bool mesh_sim_take_ejected(mesh_sim_t *mesh, uint8_t x, uint8_t y,
                           flit_t *flit)
{
    uint32_t id;

    if ((mesh == NULL) || (flit == NULL) || !mesh_valid_coord(x, y)) {
        return false;
    }
    id = mesh_node_id(x, y);
    if (!mesh->ejected_valid[id]) {
        return false;
    }
    *flit = mesh->ejected_flit[id];
    mesh->ejected_valid[id] = false;
    return true;
}
