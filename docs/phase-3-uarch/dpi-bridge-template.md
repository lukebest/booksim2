# Future RTL DPI Bridge Template

The Trial-1 BFM is portable C and uses the same public RefC header types. Phase
4 shall wrap the functions below with a SystemVerilog DPI-C package; it shall
not treat the BFM as RTL.

```c
void noc_bfm_reset(void);
int  noc_bfm_load_calendar(unsigned x, unsigned y, unsigned slot,
                           unsigned valid, unsigned in_port,
                           unsigned out_mask, unsigned opcode);
int  noc_bfm_inject(unsigned x, unsigned y, const unsigned long long lane[8],
                    unsigned flit_class, unsigned dst_x, unsigned dst_y);
void noc_bfm_tick(void);
int  noc_bfm_take_ejected(unsigned x, unsigned y, unsigned long long lane[8],
                           unsigned *flit_class);
unsigned noc_bfm_cycle(void);
```

## Comparison contract

1. Both RTL and BFM receive an identical calendar and flit-vector file.
2. A DPI monitor calls `noc_bfm_tick()` once per rising `noc_clk` edge after
   sampling RTL inputs.
3. Compare accepted/ejected transactions by `{cycle, router, port, class,
   dst, lane[0:7]}`. The Trial-1 JSON replay engine is timing-faithful for
   explicit H/V links and PE ramps. Its `COMBINE_*` paths validate slot
   placement, not the future tagged three-cycle arithmetic pipeline; RTL
   comparison must add that oracle before claiming bit-exact combine results.
4. Log a mismatch with expected BFM record, RTL record, and calendar slot;
   do not advance a scoreboarding expectation on an RTL backpressure stall.
5. The DCA interface is constrained inactive in Trial 1. A future enabled-DCA
   implementation needs a separately versioned bridge and tolerance contract.

`bfm/include/dpi_bridge.h` reserves the C ABI. Its default implementation is
a no-op template because the current harness is a smoke executable.
