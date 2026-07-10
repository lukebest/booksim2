#include "watchdog_demote.h"

#include <string.h>

void watchdog_demote_init(watchdog_demote_t *watchdog)
{
    if (watchdog != NULL) {
        (void)memset(watchdog, 0, sizeof(*watchdog));
    }
}

bool watchdog_demote_arm(watchdog_demote_t *watchdog, uint32_t cycle,
                         const flit_t *flit, uint8_t epoch, uint16_t sequence,
                         uint8_t out_port_mask)
{
    uint32_t index;

    if ((watchdog == NULL) || (flit == NULL) || (out_port_mask == 0U)) {
        return false;
    }
    for (index = 0U; index < WATCHDOG_CONTEXTS; ++index) {
        if (!watchdog->context[index].armed) {
            watchdog->context[index].armed = true;
            watchdog->context[index].armed_cycle = cycle;
            watchdog->context[index].retained_flit = *flit;
            watchdog->context[index].epoch = epoch;
            watchdog->context[index].sequence = sequence;
            watchdog->context[index].pending_leaf_mask = out_port_mask;
            watchdog->context[index].accepted_leaf_mask = 0U;
            watchdog->context[index].reservation_released = false;
            return true;
        }
    }
    return false;
}

bool watchdog_demote_mark_accepted(watchdog_demote_t *watchdog, uint8_t epoch,
                                   uint16_t sequence, uint8_t accepted_mask)
{
    uint32_t index;

    if (watchdog == NULL) {
        return false;
    }
    for (index = 0U; index < WATCHDOG_CONTEXTS; ++index) {
        if (watchdog->context[index].armed &&
            (watchdog->context[index].epoch == epoch) &&
            (watchdog->context[index].sequence == sequence)) {
            watchdog->context[index].accepted_leaf_mask |= accepted_mask;
            watchdog->context[index].pending_leaf_mask &=
                (uint8_t)~accepted_mask;
            return true;
        }
    }
    return false;
}

bool watchdog_demote_take_escape(watchdog_demote_t *watchdog, uint32_t cycle,
                                 flit_t *demoted_flit, bool *released_once)
{
    uint32_t index;
    uint32_t bit;

    if ((watchdog == NULL) || (demoted_flit == NULL) || (released_once == NULL)) {
        return false;
    }
    for (index = 0U; index < WATCHDOG_CONTEXTS; ++index) {
        watchdog_context_t *context = &watchdog->context[index];
        if (!context->armed ||
            ((cycle - context->armed_cycle) < WATCHDOG_TIMEOUT_CYCLES)) {
            continue;
        }
        *released_once = !context->reservation_released;
        context->reservation_released = true;
        for (bit = 0U; bit < PORT_COUNT; ++bit) {
            uint8_t leaf = PORT_MASK((port_t)bit);
            if ((context->pending_leaf_mask & leaf) != 0U) {
                *demoted_flit = context->retained_flit;
                demoted_flit->flit_class = FLIT_CLASS_DEMOTED;
                demoted_flit->remaining_mask = leaf;
                context->pending_leaf_mask &= (uint8_t)~leaf;
                if (context->pending_leaf_mask == 0U) {
                    context->armed = false;
                }
                return true;
            }
        }
    }
    return false;
}

void watchdog_demote_disarm(watchdog_demote_t *watchdog)
{
    if (watchdog != NULL) {
        (void)memset(watchdog, 0, sizeof(*watchdog));
    }
}
