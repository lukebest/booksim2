#ifndef ROUTER_H
#define ROUTER_H

#include <stdbool.h>
#include <stdint.h>

#include "calendar_store.h"
#include "combine_unit.h"
#include "watchdog_demote.h"

typedef struct {
    flit_t entry[BG_FIFO_DEPTH];
    uint8_t head;
    uint8_t tail;
    uint8_t count;
} bg_vc_fifo_t;

typedef struct {
    bool valid[PORT_COUNT];
    flit_t flit[PORT_COUNT];
} router_inputs_t;

typedef router_inputs_t router_outputs_t;

typedef struct {
    uint8_t x;
    uint8_t y;
    calendar_store_t calendar;
    bg_vc_fifo_t bg_fifo[PORT_COUNT];
    credits_t credits;
    combine_unit_t combine;
    watchdog_demote_t watchdog;
    uint64_t calendar_forwards;
    uint64_t bg_forwards;
    uint64_t demotions;
} router_context_t;

void router_init(router_context_t *router, uint8_t x, uint8_t y,
                 uint32_t calendar_external_base);
bool router_enqueue_bg(router_context_t *router, port_t input_port,
                       const flit_t *flit);
void router_add_credit(router_context_t *router, port_t output_port);
void router_step(router_context_t *router, uint32_t cycle,
                 const router_inputs_t *inputs, router_outputs_t *outputs);

#endif
