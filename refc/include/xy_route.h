#ifndef XY_ROUTE_H
#define XY_ROUTE_H

#include "noc_types.h"

port_t xy_route_next_hop(uint8_t router_x, uint8_t router_y, const flit_t *flit);

#endif
