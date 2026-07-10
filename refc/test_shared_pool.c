#include <stdio.h>
#include <string.h>

#include "router.h"

/*
 * Shared-pool stress / progress (Arch-A4):
 * - One port may consume reserve + entire shared pool (2+40).
 * - Other ports retain per-port reserve even when shared is exhausted.
 * - Freeing a shared-backed flit restores shared capacity.
 * - Calendar path is separate (not exercised here); demote uses enqueue_bg.
 */
int main(void)
{
    router_context_t router;
    flit_t flit;
    router_inputs_t inputs;
    router_outputs_t outputs;
    uint32_t i;
    port_t other;
    bool pass = true;

    router_init(&router, 0U, 0U, 0U);
    (void)memset(&flit, 0, sizeof(flit));
    flit.flit_class = FLIT_CLASS_BACKGROUND;
    flit.dst_x = 1U;
    flit.dst_y = 0U;

    /* Fill LOCAL with reserve + entire shared pool. */
    for (i = 0U; i < BG_PORT_QUEUE_MAX; ++i) {
        flit.lane[0] = i;
        if (!router_enqueue_bg(&router, PORT_LOCAL, &flit)) {
            (void)printf("test_shared_pool FAIL: local fill at %u\n", i);
            return 1;
        }
    }
    pass = pass &&
           (router.bg_pool.port_q[PORT_LOCAL].count == BG_PORT_QUEUE_MAX);
    pass = pass && (router.bg_pool.shared_used == BG_SHARED_POOL_SIZE);
    flit.lane[0] = 0xFFFFU;
    pass = pass && !router_enqueue_bg(&router, PORT_LOCAL, &flit);

    /* Drain one LOCAL flit via soft-prio BG grant (empty calendar). */
    (void)memset(&inputs, 0, sizeof(inputs));
    router.credits.available[PORT_EAST] = HORIZONTAL_CREDIT_DEPTH;
    router_step(&router, 0U, &inputs, &outputs);
    pass = pass && outputs.valid[PORT_EAST];
    pass = pass && (outputs.flit[PORT_EAST].lane[0] == 0U);
    pass = pass && (router.bg_pool.port_q[PORT_LOCAL].count ==
                    (uint8_t)(BG_PORT_QUEUE_MAX - 1U));
    pass = pass && (router.bg_pool.shared_used ==
                    (uint8_t)(BG_SHARED_POOL_SIZE - 1U));

    flit.lane[0] = 0xDEADU;
    pass = pass && router_enqueue_bg(&router, PORT_LOCAL, &flit);
    pass = pass && (router.bg_pool.shared_used == BG_SHARED_POOL_SIZE);

    /* With shared exhausted, other ports still get per-port reserve. */
    for (other = PORT_NORTH; other <= PORT_WEST; other = (port_t)(other + 1)) {
        flit.lane[0] = 0x100U + (uint32_t)other;
        pass = pass && router_enqueue_bg(&router, other, &flit);
        flit.lane[0] = 0x200U + (uint32_t)other;
        pass = pass && router_enqueue_bg(&router, other, &flit);
        flit.lane[0] = 0x300U + (uint32_t)other;
        pass = pass && !router_enqueue_bg(&router, other, &flit);
        pass = pass &&
               (router.bg_pool.port_q[other].count == BG_PER_PORT_RESERVE);
    }

    /* Demote/escape class also uses the pool (lossless path). */
    flit.flit_class = FLIT_CLASS_DEMOTED;
    flit.lane[0] = 0xE5CU;
    /* LOCAL is full again — demote must fail enqueue until space; reserves on
     * NORTH are full too. Free one NORTH slot by granting westbound? Use EAST
     * from NORTH: dst (1,0) from (0,0) → EAST. Clear LOCAL first via steps. */
    {
        uint32_t drained = 0U;
        while ((router.bg_pool.port_q[PORT_LOCAL].count > 0U) &&
               (drained < 64U)) {
            (void)memset(&inputs, 0, sizeof(inputs));
            router.credits.available[PORT_EAST] = HORIZONTAL_CREDIT_DEPTH;
            router_step(&router, drained + 1U, &inputs, &outputs);
            if (outputs.valid[PORT_EAST]) {
                drained++;
            } else {
                break;
            }
        }
        /* After LOCAL drains, shared frees; demote can use LOCAL reserve. */
        flit.dst_x = 1U;
        flit.dst_y = 0U;
        pass = pass && router_enqueue_bg(&router, PORT_LOCAL, &flit);
    }

    (void)printf(
        "test_shared_pool %s pool=%u reserve=%u total=%u shared_used=%u\n",
        pass ? "PASS" : "FAIL", (unsigned)BG_SHARED_POOL_SIZE,
        (unsigned)BG_PER_PORT_RESERVE, (unsigned)BG_TOTAL_FLITS,
        (unsigned)router.bg_pool.shared_used);
    return pass ? 0 : 1;
}
