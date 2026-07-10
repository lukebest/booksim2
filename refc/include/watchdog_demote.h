#ifndef WATCHDOG_DEMOTE_H
#define WATCHDOG_DEMOTE_H

#include <stdbool.h>
#include <stdint.h>

#include "noc_types.h"

#define WATCHDOG_TIMEOUT_CYCLES 32U
#define WATCHDOG_CONTEXTS PORT_COUNT

typedef struct {
    bool armed;
    uint32_t armed_cycle;
    flit_t retained_flit;
    uint8_t epoch;
    uint16_t sequence;
    uint8_t pending_leaf_mask;
    uint8_t accepted_leaf_mask;
    bool reservation_released;
} watchdog_context_t;

typedef struct {
    watchdog_context_t context[WATCHDOG_CONTEXTS];
} watchdog_demote_t;

void watchdog_demote_init(watchdog_demote_t *watchdog);
bool watchdog_demote_arm(watchdog_demote_t *watchdog, uint32_t cycle,
                         const flit_t *flit, uint8_t epoch, uint16_t sequence,
                         uint8_t out_port_mask);
bool watchdog_demote_mark_accepted(watchdog_demote_t *watchdog, uint8_t epoch,
                                   uint16_t sequence, uint8_t accepted_mask);
bool watchdog_demote_take_escape(watchdog_demote_t *watchdog, uint32_t cycle,
                                 flit_t *demoted_flit, bool *released_once);
void watchdog_demote_disarm(watchdog_demote_t *watchdog);

#endif
