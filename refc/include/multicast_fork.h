#ifndef MULTICAST_FORK_H
#define MULTICAST_FORK_H

#include <stdint.h>

#include "noc_types.h"

uint8_t multicast_expand(uint8_t out_port_mask, port_t ports[PORT_COUNT]);

#endif
