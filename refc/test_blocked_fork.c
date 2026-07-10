#include <stdio.h>
#include <string.h>

#include "router.h"

int main(void)
{
    router_context_t router;
    router_inputs_t inputs;
    router_outputs_t outputs;
    calendar_entry_t entry;
    flit_t input;
    bool pass = true;

    router_init(&router, 0U, 0U, 0U);
    (void)memset(&entry, 0, sizeof(entry));
    entry.valid = 1U;
    entry.in_port = PORT_LOCAL;
    entry.out_port_mask = PORT_MASK(PORT_EAST) | PORT_MASK(PORT_SOUTH);
    entry.opcode = CAL_OP_FORWARD;
    calendar_store_load(&router.calendar, 0U, 0U, &entry);
    entry.in_port = PORT_NORTH;
    entry.out_port_mask = PORT_MASK(PORT_EAST);
    calendar_store_load(&router.calendar, 0U, WATCHDOG_TIMEOUT_CYCLES, &entry);
    calendar_store_load(&router.calendar, 0U, WATCHDOG_TIMEOUT_CYCLES + 1U, &entry);
    router.credits.available[PORT_SOUTH] = 0U;
    (void)memset(&inputs, 0, sizeof(inputs));
    (void)memset(&input, 0, sizeof(input));
    input.flit_class = FLIT_CLASS_CALENDAR;
    input.lane[0] = UINT64_C(0xD00D);
    inputs.valid[PORT_LOCAL] = true;
    inputs.flit[PORT_LOCAL] = input;
    router_step(&router, 0U, &inputs, &outputs);
    (void)memset(&inputs, 0, sizeof(inputs));
    router_step(&router, WATCHDOG_TIMEOUT_CYCLES, &inputs, &outputs);
    router_step(&router, WATCHDOG_TIMEOUT_CYCLES + 1U, &inputs, &outputs);
    pass = pass && (router.bg_fifo[PORT_LOCAL].count == 2U);
    pass = pass && (router.bg_fifo[PORT_LOCAL].entry[0].remaining_mask ==
                    PORT_MASK(PORT_EAST));
    pass = pass && (router.bg_fifo[PORT_LOCAL].entry[1].remaining_mask ==
                    PORT_MASK(PORT_SOUTH));
    (void)printf("test_blocked_fork %s\n", pass ? "PASS" : "FAIL");
    return pass ? 0 : 1;
}
