#include "calendar_replay.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define MAX_EVENTS 16384U
#define MAX_INJECTIONS 128U

#include "calendar_store.h"

typedef struct {
    unsigned cycle;
    unsigned x;
    unsigned y;
    unsigned in_port;
} event_t;

typedef struct {
    unsigned valid;
    unsigned in_port;
    unsigned out_mask;
    unsigned opcode;
} slot_t;

static int add_event(event_t *events, unsigned *count, unsigned cycle,
                     unsigned x, unsigned y, unsigned in_port)
{
    if (*count >= MAX_EVENTS) {
        return 0;
    }
    events[*count] = (event_t){cycle, x, y, in_port};
    ++*count;
    return 1;
}

static unsigned node_id(unsigned x, unsigned y)
{
    return y * MESH_X + x;
}

static int parse_file(const char *path, slot_t slots[MESH_NODES][CALENDAR_SLOTS],
                      event_t *injections, unsigned *injection_count,
                      unsigned *expected_count, unsigned *expected_makespan)
{
    FILE *file = fopen(path, "rb");
    char *text;
    long length;
    char *router;
    char *end;

    if (file == NULL) {
        return 0;
    }
    (void)fseek(file, 0L, SEEK_END);
    length = ftell(file);
    (void)fseek(file, 0L, SEEK_SET);
    text = malloc((size_t)length + 1U);
    if ((text == NULL) || (fread(text, 1U, (size_t)length, file) != (size_t)length)) {
        free(text);
        (void)fclose(file);
        return 0;
    }
    text[length] = '\0';
    (void)fclose(file);
    *injection_count = 0U;
    *expected_count = 0U;
    *expected_makespan = 0U;

    router = text;
    while ((router = strstr(router, "\"router\":[")) != NULL) {
        unsigned x, y, slot, in_port, out_mask, opcode;
        char *entry;
        if (sscanf(router, "\"router\":[%u,%u]", &x, &y) != 2 ||
            x >= MESH_X || y >= MESH_Y) {
            free(text);
            return 0;
        }
        end = strstr(router + 1, "\"router\":[");
        if (end == NULL) {
            end = text + length;
        }
        entry = router;
        while ((entry = strstr(entry, "{\"slot\":")) != NULL && entry < end) {
            if (sscanf(entry, "{\"slot\":%u,\"valid\":true,\"in_port\":%u,"
                       "\"out_port_mask\":%u,\"opcode\":%u}",
                       &slot, &in_port, &out_mask, &opcode) != 4 ||
                slot >= CALENDAR_SLOTS || in_port >= PORT_COUNT ||
                out_mask >= (1U << PORT_COUNT) || opcode > CAL_OP_COMBINE_MAX ||
                slots[node_id(x, y)][slot].valid) {
                free(text);
                return 0;
            }
            slots[node_id(x, y)][slot] =
                (slot_t){1U, in_port, out_mask, opcode};
            ++entry;
        }
        router = end;
    }

    router = text;
    while ((router = strstr(router, "\"source\":[")) != NULL) {
        unsigned x, y, slot;
        if (*injection_count >= MAX_INJECTIONS ||
            sscanf(router, "\"source\":[%u,%u],\"slot\":%u", &x, &y, &slot) != 3 ||
            x >= MESH_X || y >= MESH_Y) {
            free(text);
            return 0;
        }
        injections[*injection_count] = (event_t){slot + PE_RAMP_DELAY, x, y, PORT_LOCAL};
        ++*injection_count;
        ++router;
    }
    router = text;
    while ((router = strstr(router, "[")) != NULL) {
        if (router > text && router[-1] == ':' && router[1] == '[') {
            break;
        }
        ++router;
    }
    router = strstr(text, "\"expected_ejections\":[");
    if (router == NULL ||
        sscanf(strstr(text, "\"expected_makespan\":"), "\"expected_makespan\":%u",
               expected_makespan) != 1) {
        free(text);
        return 0;
    }
    router = strchr(router, '[');
    ++router;
    while ((router = strchr(router, '[')) != NULL) {
        ++*expected_count;
        ++router;
    }
    free(text);
    return 1;
}

int bfm_replay_calendar(const char *path, bfm_calendar_result_t *result)
{
    slot_t slots[MESH_NODES][CALENDAR_SLOTS] = {{{0U}}};
    event_t events[MAX_EVENTS];
    event_t injections[MAX_INJECTIONS];
    unsigned event_count = 0U, injection_count, expected_count, expected_makespan;
    unsigned ejections = 0U, makespan = 0U, combine_ops = 0U, index, cycle;

    if (result == NULL || !parse_file(path, slots, injections, &injection_count,
                                      &expected_count, &expected_makespan)) {
        return 0;
    }
    for (index = 0U; index < injection_count; ++index) {
        if (!add_event(events, &event_count, injections[index].cycle, injections[index].x,
                       injections[index].y, injections[index].in_port)) {
            return 0;
        }
    }
    for (cycle = 0U; cycle < CALENDAR_SLOTS + 128U; ++cycle) {
        for (index = 0U; index < event_count; ++index) {
            event_t event = events[index];
            slot_t entry;
            unsigned port;
            if (event.cycle != cycle) {
                continue;
            }
            entry = slots[node_id(event.x, event.y)][cycle];
            if (!entry.valid || entry.in_port != event.in_port) {
                return 0;
            }
            if (entry.opcode != CAL_OP_FORWARD) {
                ++combine_ops;
            }
            for (port = 0U; port < PORT_COUNT; ++port) {
                if ((entry.out_mask & (1U << port)) == 0U) {
                    continue;
                }
                if (port == PORT_LOCAL) {
                    ++ejections;
                    makespan = cycle + PE_RAMP_DELAY;
                } else {
                    int dx = (port == PORT_EAST) - (port == PORT_WEST);
                    int dy = (port == PORT_SOUTH) - (port == PORT_NORTH);
                    unsigned arrival = cycle + ((dx != 0) ? HORIZONTAL_LINK_DELAY :
                                                 VERTICAL_LINK_DELAY);
                    if (!add_event(events, &event_count, arrival,
                                   (unsigned)((int)event.x + dx),
                                   (unsigned)((int)event.y + dy),
                                   (port + 2U) % 4U)) {
                        return 0;
                    }
                }
            }
        }
    }
    result->expected_ejections = expected_count;
    result->ejections = ejections;
    result->expected_makespan = expected_makespan;
    result->makespan = makespan;
    result->combine_ops = combine_ops;
    return (ejections == expected_count) && (makespan == expected_makespan);
}
