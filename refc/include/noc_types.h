#ifndef NOC_TYPES_H
#define NOC_TYPES_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define MESH_X 6U
#define MESH_Y 8U
#define MESH_NODES (MESH_X * MESH_Y)
#define FLIT_LANES 8U
#define PORT_COUNT 5U
#define BG_FIFO_DEPTH 20U
#define BG_WINDOW_PERIOD 16U
#define HORIZONTAL_CREDIT_DEPTH 16U
#define VERTICAL_CREDIT_DEPTH 20U
#define HORIZONTAL_LINK_DELAY 7U
#define VERTICAL_LINK_DELAY 9U
#define PE_RAMP_DELAY 1U

typedef enum {
    PORT_NORTH = 0,
    PORT_EAST = 1,
    PORT_SOUTH = 2,
    PORT_WEST = 3,
    PORT_LOCAL = 4
} port_t;

#define PORT_MASK(port) ((uint8_t)(1U << (uint32_t)(port)))

typedef enum {
    FLIT_CLASS_CALENDAR = 0,
    FLIT_CLASS_BACKGROUND = 1,
    FLIT_CLASS_DEMOTED = 2
} flit_class_t;

typedef struct {
    uint64_t lane[FLIT_LANES];
    uint8_t src_x;
    uint8_t src_y;
    uint8_t dst_x;
    uint8_t dst_y;
    uint8_t remaining_mask;
    uint8_t flit_class;
    uint8_t reserved;
} flit_t;

typedef struct {
    uint16_t available[PORT_COUNT];
} credits_t;

static inline uint32_t mesh_node_id(uint8_t x, uint8_t y)
{
    return ((uint32_t)y * MESH_X) + (uint32_t)x;
}

static inline bool mesh_valid_coord(uint8_t x, uint8_t y)
{
    return (x < MESH_X) && (y < MESH_Y);
}

static inline port_t port_opposite(port_t port)
{
    static const port_t opposite[PORT_COUNT] = {
        PORT_SOUTH, PORT_WEST, PORT_NORTH, PORT_EAST, PORT_LOCAL
    };
    return opposite[(uint32_t)port];
}

#endif
