#include <stdio.h>
#include <string.h>

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
    calendar_entry_t entry;
    combine_unit_t combine;
    flit_t flit;
    flit_t combined;
    bool pass = true;

    (void)memset(&entry, 0, sizeof(entry));
    entry.valid = 1U;
    entry.in_port = PORT_LOCAL;
    entry.out_port_mask = PORT_MASK(PORT_EAST) | PORT_MASK(PORT_SOUTH);
    entry.opcode = CAL_OP_FORWARD;
    pass = pass && (calendar_entry_encode(&entry) == UINT16_C(0x1860));
    (void)memset(&entry, 0, sizeof(entry));
    pass = pass && calendar_entry_decode(UINT16_C(0x1860), &entry);
    pass = pass && (entry.valid == 1U) && (entry.in_port == PORT_LOCAL) &&
           (entry.out_port_mask == (PORT_MASK(PORT_EAST) | PORT_MASK(PORT_SOUTH))) &&
           (entry.opcode == CAL_OP_FORWARD);

    combine_unit_init(&combine);
    flit = make_flit(FLIT_CLASS_CALENDAR, 0U, 0U, UINT64_C(7));
    (void)combine_unit_accept(&combine, CAL_OP_COMBINE_ADD, &flit);
    flit.lane[0] = UINT64_C(9);
    pass = pass && combine_unit_accept(&combine, CAL_OP_COMBINE_ADD, &flit);
    (void)combine_unit_advance(&combine, &combined);
    (void)combine_unit_advance(&combine, &combined);
    pass = pass && combine_unit_advance(&combine, &combined);
    pass = pass && (combined.lane[0] == UINT64_C(16));

    (void)printf("mesh_router_smoke %s\n", pass ? "PASS" : "FAIL");
    return pass ? 0 : 1;
}
