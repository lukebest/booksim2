#ifndef MULTICAST_FORK_H
#define MULTICAST_FORK_H

#include <stdint.h>

#include "noc_types.h"

/*
 * CalFork / LeanMulticast (Arch-A5):
 *   Calendar-native atomic fork from sparse-event out_port_mask[4:0].
 *   Expands a 5-bit mask into port indices for all-or-nothing commit.
 *   NOT a general FlooNoC-class stream_fork engine — no multi-stream
 *   FSM, no independent stream state machines, no combine datapath.
 *   Area model charges CALFORK_MC_DELTA (~0.025) instead of FLOONOC 0.058.
 */
uint8_t multicast_expand(uint8_t out_port_mask, port_t ports[PORT_COUNT]);

/* Alias documenting the calendar-native path used by router_step. */
static inline uint8_t cal_fork_expand(uint8_t out_port_mask,
                                      port_t ports[PORT_COUNT])
{
    return multicast_expand(out_port_mask, ports);
}

#endif
