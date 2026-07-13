#include "router.h"

#include <string.h>

#include "multicast_fork.h"
#include "xy_route.h"

static uint8_t shared_used_recompute(const bg_shared_pool_t *pool)
{
    uint32_t port_index;
    uint8_t used = 0U;

    for (port_index = 0U; port_index < PORT_COUNT; ++port_index) {
        uint8_t count = pool->port_q[port_index].count;
        if (count > BG_PER_PORT_RESERVE) {
            used = (uint8_t)(used + (count - BG_PER_PORT_RESERVE));
        }
    }
    return used;
}

static bool pool_can_enqueue(const bg_shared_pool_t *pool, port_t input_port)
{
    const bg_port_queue_t *q = &pool->port_q[(uint32_t)input_port];

    if (q->count >= BG_PORT_QUEUE_MAX) {
        return false;
    }
    if (q->count < BG_PER_PORT_RESERVE) {
        return true;
    }
    return pool->shared_used < BG_SHARED_POOL_SIZE;
}

static bool pool_push(bg_shared_pool_t *pool, port_t input_port,
                      const flit_t *flit)
{
    bg_port_queue_t *q;

    if ((pool == NULL) || (flit == NULL) ||
        ((uint32_t)input_port >= PORT_COUNT) ||
        !pool_can_enqueue(pool, input_port)) {
        return false;
    }
    q = &pool->port_q[(uint32_t)input_port];
    q->entry[q->tail] = *flit;
    q->tail = (uint8_t)((q->tail + 1U) % BG_PORT_QUEUE_MAX);
    q->count++;
    if (q->count > BG_PER_PORT_RESERVE) {
        pool->shared_used++;
    }
    return true;
}

static bool pool_peek(const bg_shared_pool_t *pool, port_t input_port,
                      flit_t *flit)
{
    const bg_port_queue_t *q;

    if ((pool == NULL) || (flit == NULL) ||
        ((uint32_t)input_port >= PORT_COUNT)) {
        return false;
    }
    q = &pool->port_q[(uint32_t)input_port];
    if (q->count == 0U) {
        return false;
    }
    *flit = q->entry[q->head];
    return true;
}

static void pool_pop(bg_shared_pool_t *pool, port_t input_port)
{
    bg_port_queue_t *q;

    if ((pool == NULL) || ((uint32_t)input_port >= PORT_COUNT)) {
        return;
    }
    q = &pool->port_q[(uint32_t)input_port];
    if (q->count == 0U) {
        return;
    }
    if (q->count > BG_PER_PORT_RESERVE) {
        pool->shared_used--;
    }
    q->head = (uint8_t)((q->head + 1U) % BG_PORT_QUEUE_MAX);
    q->count--;
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
    router->bg_pool.shared_used = 0U;
}

bool router_enqueue_bg(router_context_t *router, port_t input_port,
                       const flit_t *flit)
{
    if ((router == NULL) || ((uint32_t)input_port >= PORT_COUNT) ||
        (flit == NULL)) {
        return false;
    }
    return pool_push(&router->bg_pool, input_port, flit);
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

    /* BG/escape only — calendar never enters the shared pool. */
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
         * CalFork (Arch-A5): calendar-native atomic out_port_mask fork.
         * Legacy reduce opcodes are PE-handoff tags; payload forwarded unchanged.
         */
        fork_count = cal_fork_expand(output_mask, fork_ports);
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
        /* Demote→XY uses pool/reserves (lossless); never calendar storage. */
        if (watchdog_demote_take_escape(&router->watchdog, cycle, &demoted_flit,
                                        &released_once) &&
            router_enqueue_bg(router, PORT_LOCAL, &demoted_flit)) {
            if (released_once) {
                router->demotions++;
            }
        }
    }

    /*
     * Soft priority (Arch-A5): calendar wins only when a sparse event matches.
     * BG may use any non-matching / idle cycle. Shared pool does not affect
     * calendar path (zero-buffer). CalFork never consumes pool slots.
     */
    if (!calendar_store_replay(&router->calendar, cycle, &entry)) {
        for (port_index = 0U; port_index < PORT_COUNT; ++port_index) {
            port_t output_port;
            if (pool_peek(&router->bg_pool, (port_t)port_index,
                          &selected_flit)) {
                output_port =
                    xy_route_next_hop(router->x, router->y, &selected_flit);
                if (!outputs->valid[(uint32_t)output_port] &&
                    (router->credits.available[(uint32_t)output_port] > 0U)) {
                    outputs->flit[(uint32_t)output_port] = selected_flit;
                    outputs->valid[(uint32_t)output_port] = true;
                    router->credits.available[(uint32_t)output_port]--;
                    pool_pop(&router->bg_pool, (port_t)port_index);
                    router->bg_forwards++;
                }
            }
        }
    }

    /* Keep shared_used consistent if callers inspect it. */
    router->bg_pool.shared_used = shared_used_recompute(&router->bg_pool);
}
