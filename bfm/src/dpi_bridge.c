#include "dpi_bridge.h"

static unsigned int dpi_cycle;

void noc_bfm_reset(void)
{
    dpi_cycle = 0U;
}

void noc_bfm_tick(void)
{
    dpi_cycle++;
}

unsigned int noc_bfm_cycle(void)
{
    return dpi_cycle;
}
