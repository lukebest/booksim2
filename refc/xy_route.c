#include "xy_route.h"

port_t xy_route_next_hop(uint8_t router_x, uint8_t router_y, const flit_t *flit)
{
    if ((flit == NULL) || !mesh_valid_coord(flit->dst_x, flit->dst_y)) {
        return PORT_LOCAL;
    }
    if (flit->dst_x > router_x) {
        return PORT_EAST;
    }
    if (flit->dst_x < router_x) {
        return PORT_WEST;
    }
    if (flit->dst_y > router_y) {
        return PORT_SOUTH;
    }
    if (flit->dst_y < router_y) {
        return PORT_NORTH;
    }
    return PORT_LOCAL;
}
