#include "router.h"

#include <string.h>

#include "multicast_fork.h"
#include "xy_route.h"

static bool fifo_push(bg_vc_fifo_t *fifo, const flit_t *flit)
{
    if ((fifo == NULL) || (flit == NULL) || (fifo->count >= BG_FIFO_DEPTH)) {
        return false;
    }
    fifo->entry[fifo->tail] = *flit;
    fifo->tail = (uint8_t)((fifo->tail + 1U) % BG_FIFO_DEPTH);
    fifo->count++;
    return true;
}

static uint16_t credit_depth_for_port(port_t port)
{
    if ((port == PORT_EAST) || (port == PORT_WEST)) {
        return HORIZONTAL_CREDIT_DEPTH;
    }
    return VERTICAL_CREDIT_DEPTH;
}

static uint8_t calendar_output_mask(const calendar_entry_t *entry)
{
    if ((entry->opcode == CAL_OP_PE_HANDOFF) && (entry->out_port_mask == 0U)) {
        return PORT_MASK(PORT_LOCAL);
    }
    return entry->out_port_mask;
}

static bool fifo_peek(const bg_vc_fifo_t *fifo, flit_t *flit)
{
    if ((fifo == NULL) || (flit == NULL) || (fifo->count == 0U)) {
        return false;
    }
    *flit = fifo->entry[fifo->head];
    return true;
}

static void fifo_pop(bg_vc_fifo_t *fifo)
{
    if ((fifo != NULL) && (fifo->count > 0U)) {
        fifo->head = (uint8_t)((fifo->head + 1U) % BG_FIFO_DEPTH);
        fifo->count--;
    }
}

static bool calendar_outputs_available(const router_context_t *router,
                                       const router_outputs_t *outputs,
                                       uint8_t mask)
{
    uint32_t port_index;

    for (port_index = 0U; port_index < PORT_COUNT; ++port_index) {
        if (((mask & PORT_MASK((port_t)port_index)) != 0U) &&
            ((router->credits.available[port_index] == 0U) ||
             outputs->valid[port_index])) {
            return false;
        }
    }
    return mask != 0U;
}

void router_init(router_context_t *router, uint8_t x, uint8_t y,
                 uint32_t calendar_external_base)
{
    uint32_t port_index;

    if (router == NULL) {
        return;
    }
    (void)memset(router, 0, sizeof(*router));
    router->x = x;
    router->y = y;
    calendar_store_init(&router->calendar, calendar_external_base);
    watchdog_demote_init(&router->watchdog);
    for (port_index = 0U; port_index < PORT_COUNT; ++port_index) {
        router->credits.available[port_index] =
            credit_depth_for_port((port_t)port_index);
    }
}

bool router_enqueue_bg(router_context_t *router, port_t input_port,
                       const flit_t *flit)
{
    if ((router == NULL) || ((uint32_t)input_port >= PORT_COUNT) ||
        (flit == NULL)) {
        return false;
    }
    return fifo_push(&router->bg_fifo[(uint32_t)input_port], flit);
}

void router_add_credit(router_context_t *router, port_t output_port)
{
    if ((router != NULL) && ((uint32_t)output_port < PORT_COUNT) &&
        (router->credits.available[(uint32_t)output_port] <
         credit_depth_for_port(output_port))) {
        router->credits.available[(uint32_t)output_port]++;
    }
}

void router_step(router_context_t *router, uint32_t cycle,
                 const router_inputs_t *inputs, router_outputs_t *outputs)
{
    calendar_entry_t entry;
    flit_t selected_flit;
    flit_t demoted_flit;
    port_t fork_ports[PORT_COUNT];
    uint8_t fork_count;
    uint8_t output_mask;
    uint32_t port_index;

    if ((router == NULL) || (inputs == NULL) || (outputs == NULL)) {
        return;
    }
    (void)memset(outputs, 0, sizeof(*outputs));

    for (port_index = 0U; port_index < PORT_COUNT; ++port_index) {
        if (inputs->valid[port_index] &&
            (inputs->flit[port_index].flit_class != FLIT_CLASS_CALENDAR)) {
            (void)router_enqueue_bg(router, (port_t)port_index,
                                    &inputs->flit[port_index]);
        }
    }

    if (calendar_store_replay(&router->calendar, cycle, &entry) &&
        (entry.in_port < PORT_COUNT) && inputs->valid[entry.in_port] &&
        (inputs->flit[entry.in_port].flit_class == FLIT_CLASS_CALENDAR) &&
        calendar_outputs_available(router, outputs, calendar_output_mask(&entry))) {
        selected_flit = inputs->flit[entry.in_port];
        output_mask = calendar_output_mask(&entry);
        /*
         * Calendar opcodes describe routing only.  Legacy reduce opcode
         * encodings are PE-handoff tags; their payload is forwarded unchanged.
         */
        fork_count = multicast_expand(output_mask, fork_ports);
        for (port_index = 0U; port_index < fork_count; ++port_index) {
            outputs->flit[(uint32_t)fork_ports[port_index]] = selected_flit;
            outputs->valid[(uint32_t)fork_ports[port_index]] = true;
            router->credits.available[(uint32_t)fork_ports[port_index]]--;
        }
        router->calendar_forwards++;
    } else {
        if (calendar_store_replay(&router->calendar, cycle, &entry) &&
            (entry.in_port < PORT_COUNT) && inputs->valid[entry.in_port] &&
            (inputs->flit[entry.in_port].flit_class == FLIT_CLASS_CALENDAR)) {
            (void)watchdog_demote_arm(&router->watchdog, cycle,
                                      &inputs->flit[entry.in_port], 0U,
                                      (uint16_t)cycle,
                                      calendar_output_mask(&entry));
        } else {
            for (port_index = 0U; port_index < PORT_COUNT; ++port_index) {
                if (inputs->valid[port_index] &&
                    (inputs->flit[port_index].flit_class == FLIT_CLASS_CALENDAR)) {
                    (void)watchdog_demote_arm(&router->watchdog, cycle,
                                              &inputs->flit[port_index], 0U,
                                              (uint16_t)cycle,
                                              PORT_MASK(PORT_LOCAL));
                }
            }
        }
    }

    {
        bool released_once;
        if (watchdog_demote_take_escape(&router->watchdog, cycle, &demoted_flit,
                                        &released_once) &&
            router_enqueue_bg(router, PORT_LOCAL, &demoted_flit)) {
            if (released_once) {
                router->demotions++;
            }
        }
    }

    if ((cycle % BG_WINDOW_PERIOD) == (BG_WINDOW_PERIOD - 1U) ||
        !calendar_store_replay(&router->calendar, cycle, &entry)) {
        for (port_index = 0U; port_index < PORT_COUNT; ++port_index) {
        port_t output_port;
        if (fifo_peek(&router->bg_fifo[port_index], &selected_flit)) {
            output_port = xy_route_next_hop(router->x, router->y, &selected_flit);
            if (!outputs->valid[(uint32_t)output_port] &&
                (router->credits.available[(uint32_t)output_port] > 0U)) {
                outputs->flit[(uint32_t)output_port] = selected_flit;
                outputs->valid[(uint32_t)output_port] = true;
                router->credits.available[(uint32_t)output_port]--;
                fifo_pop(&router->bg_fifo[port_index]);
                router->bg_forwards++;
            }
        }
        }
    }
}
