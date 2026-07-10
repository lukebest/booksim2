#include <inttypes.h>
#include <stdio.h>
#include <string.h>

#include "bfm_model.h"
#include "combine_unit.h"

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
    combine_unit_t combine;
    flit_t flit;
    flit_t ejected;
    flit_t combined;
    uint32_t step;
    bool pass = bfm_router_model_is_refc_compatible();

    bfm_mesh_reset(&mesh);
    (void)memset(&entry, 0, sizeof(entry));
    entry.valid = 1U;
    entry.in_port = PORT_LOCAL;
    entry.out_port_mask = PORT_MASK(PORT_EAST) | PORT_MASK(PORT_SOUTH);
    entry.opcode = CAL_OP_FORWARD;
    mesh_sim_load_calendar(&mesh, 0U, 0U, 0U, &entry);
    entry.in_port = PORT_WEST;
    entry.out_port_mask = PORT_MASK(PORT_LOCAL);
    mesh_sim_load_calendar(&mesh, 1U, 0U, 1U, &entry);
    entry.in_port = PORT_NORTH;
    mesh_sim_load_calendar(&mesh, 0U, 1U, 1U, &entry);

    flit = make_flit(FLIT_CLASS_CALENDAR, 0U, 0U, UINT64_C(0xCA1E));
    pass = pass && mesh_sim_inject(&mesh, 0U, 0U, &flit);
    mesh_sim_advance(&mesh);
    mesh_sim_advance(&mesh);
    pass = pass && mesh_sim_take_ejected(&mesh, 1U, 0U, &ejected);
    pass = pass && (ejected.lane[0] == UINT64_C(0xCA1E));
    pass = pass && mesh_sim_take_ejected(&mesh, 0U, 1U, &ejected);

    flit = make_flit(FLIT_CLASS_BACKGROUND, 2U, 0U, UINT64_C(0xB6));
    pass = pass && mesh_sim_inject(&mesh, 0U, 0U, &flit);
    for (step = 0U; step < 3U; ++step) {
        mesh_sim_advance(&mesh);
    }
    pass = pass && mesh_sim_take_ejected(&mesh, 2U, 0U, &ejected);
    pass = pass && (ejected.lane[0] == UINT64_C(0xB6));

    flit = make_flit(FLIT_CLASS_CALENDAR, 5U, 0U, UINT64_C(0xDEAD));
    pass = pass && mesh_sim_inject(&mesh, 0U, 0U, &flit);
    for (step = 0U; step < 40U; ++step) {
        mesh_sim_advance(&mesh);
    }
    pass = pass && mesh_sim_take_ejected(&mesh, 5U, 0U, &ejected);
    pass = pass && (ejected.flit_class == FLIT_CLASS_DEMOTED);

    combine_unit_init(&combine);
    flit = make_flit(FLIT_CLASS_CALENDAR, 0U, 0U, UINT64_C(7));
    (void)combine_unit_accept(&combine, CAL_OP_COMBINE_ADD, &flit, &combined);
    flit.lane[0] = UINT64_C(9);
    pass = pass && combine_unit_accept(&combine, CAL_OP_COMBINE_ADD, &flit,
                                        &combined);
    pass = pass && (combined.lane[0] == UINT64_C(16));

    bfm_write_module_logs(mesh.cycle, pass);
    (void)printf("%s cycles=%" PRIu32 " calendar=%" PRIu64
                 " bg=%" PRIu64 " demotions=%" PRIu64 "\n",
                 pass ? "PASS" : "FAIL", mesh.cycle,
                 mesh.router[0].calendar_forwards,
                 mesh.router[0].bg_forwards, mesh.router[0].demotions);
    return pass ? 0 : 1;
}
