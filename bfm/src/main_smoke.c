#include <inttypes.h>
#include <stdio.h>
#include <string.h>

#include "bfm_model.h"

static flit_t make_flit(uint8_t flit_class, uint8_t dst_x, uint8_t dst_y,
                        uint64_t lane_zero)
{
    flit_t flit;

    (void)memset(&flit, 0, sizeof(flit));
    flit.flit_class = flit_class;
    flit.dst_x = dst_x;
    flit.dst_y = dst_y;
    flit.lane[0] = lane_zero;
    return flit;
}

int main(void)
{
    mesh_sim_t mesh;
    calendar_entry_t entry;
    flit_t flit;
    flit_t ejected;
    uint32_t step;
    bool calendar_pass = bfm_router_model_is_refc_compatible();
    bool background_pass = true;
    bool demote_pass = true;
    bool pass;

    bfm_mesh_reset(&mesh);
    (void)memset(&entry, 0, sizeof(entry));
    entry.valid = 1U;
    entry.in_port = PORT_LOCAL;
    entry.out_port_mask = PORT_MASK(PORT_EAST) | PORT_MASK(PORT_SOUTH);
    entry.opcode = CAL_OP_FORWARD;
    mesh_sim_load_calendar(&mesh, 0U, 0U, 0U, &entry);
    entry.in_port = PORT_WEST;
    entry.out_port_mask = PORT_MASK(PORT_LOCAL);
    mesh_sim_load_calendar(&mesh, 1U, 0U, HORIZONTAL_LINK_DELAY, &entry);
    entry.in_port = PORT_NORTH;
    mesh_sim_load_calendar(&mesh, 0U, 1U, VERTICAL_LINK_DELAY, &entry);

    flit = make_flit(FLIT_CLASS_CALENDAR, 0U, 0U, UINT64_C(0xCA1E));
    calendar_pass = calendar_pass && mesh_sim_inject(&mesh, 0U, 0U, &flit);
    for (step = 0U; step < VERTICAL_LINK_DELAY + PE_RAMP_DELAY + 1U; ++step) {
        mesh_sim_advance(&mesh);
    }
    calendar_pass = calendar_pass && mesh_sim_take_ejected(&mesh, 1U, 0U, &ejected);
    calendar_pass = calendar_pass && (ejected.lane[0] == UINT64_C(0xCA1E));
    calendar_pass = calendar_pass && mesh_sim_take_ejected(&mesh, 0U, 1U, &ejected);

    flit = make_flit(FLIT_CLASS_BACKGROUND, 2U, 0U, UINT64_C(0xB6));
    background_pass = background_pass && mesh_sim_inject(&mesh, 0U, 0U, &flit);
    for (step = 0U; step < (2U * HORIZONTAL_LINK_DELAY) + PE_RAMP_DELAY + 1U;
         ++step) {
        mesh_sim_advance(&mesh);
    }
    background_pass = background_pass && mesh_sim_take_ejected(&mesh, 2U, 0U, &ejected);
    background_pass = background_pass && (ejected.lane[0] == UINT64_C(0xB6));

    flit = make_flit(FLIT_CLASS_CALENDAR, 5U, 0U, UINT64_C(0xDEAD));
    demote_pass = demote_pass && mesh_sim_inject(&mesh, 0U, 0U, &flit);
    for (step = 0U; step < 100U; ++step) {
        mesh_sim_advance(&mesh);
    }
    demote_pass = demote_pass && mesh_sim_take_ejected(&mesh, 5U, 0U, &ejected);
    demote_pass = demote_pass && (ejected.flit_class == FLIT_CLASS_DEMOTED);

    /*
     * Tier A reduce/allreduce uses gather forwarding plus PE-local compute.
     * The router BFM never combines flit payloads.
     */
    pass = calendar_pass && background_pass && demote_pass;

    bfm_write_module_logs(mesh.cycle, pass);
    (void)printf("%s calendar_pass=%u background_pass=%u demote_pass=%u "
                 "cycles=%" PRIu32 " calendar=%" PRIu64
                 " bg=%" PRIu64 " demotions=%" PRIu64 "\n",
                 pass ? "PASS" : "FAIL", calendar_pass ? 1U : 0U,
                 background_pass ? 1U : 0U, demote_pass ? 1U : 0U,
                 mesh.cycle,
                 mesh.router[0].calendar_forwards,
                 mesh.router[0].bg_forwards, mesh.router[0].demotions);
    return pass ? 0 : 1;
}
