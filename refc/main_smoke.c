#include <stdio.h>
#include <string.h>

#include "calendar_store.h"

int main(void)
{
    calendar_entry_t entry;
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

    entry.valid = 1U;
    entry.opcode = CAL_OP_PE_HANDOFF;
    pass = pass && (calendar_entry_encode(&entry) == UINT16_C(0x1861));
    (void)memset(&entry, 0, sizeof(entry));
    pass = pass && calendar_entry_decode(UINT16_C(0x1861), &entry);
    pass = pass && (entry.opcode == CAL_OP_PE_HANDOFF);

    (void)printf("mesh_router_smoke %s\n", pass ? "PASS" : "FAIL");
    return pass ? 0 : 1;
}
