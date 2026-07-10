#ifndef DPI_BRIDGE_H
#define DPI_BRIDGE_H

/*
 * Reserved C ABI for the future SystemVerilog DPI wrapper.  Trial 1 builds a
 * standalone C executable; the signatures document the stable comparison
 * boundary without requiring a simulator or SystemC installation.
 */
void noc_bfm_reset(void);
void noc_bfm_tick(void);
unsigned int noc_bfm_cycle(void);

#endif
