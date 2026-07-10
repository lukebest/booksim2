#include "multicast_fork.h"

uint8_t multicast_expand(uint8_t out_port_mask, port_t ports[PORT_COUNT])
{
    uint8_t count = 0U;
    uint32_t port_index;

    if (ports == NULL) {
        return 0U;
    }
    for (port_index = 0U; port_index < PORT_COUNT; ++port_index) {
        if ((out_port_mask & PORT_MASK((port_t)port_index)) != 0U) {
            ports[count] = (port_t)port_index;
            count++;
        }
    }
    return count;
}
