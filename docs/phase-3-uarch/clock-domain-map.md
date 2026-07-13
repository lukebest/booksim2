# Clock Domain Map — Trial 5 Arch-A5

| Domain | Frequency | Reset | Blocks | Crossing |
|---|---:|---|---|---|
| `noc_clk` | 2 GHz (0.5 ns) | `noc_rst_n`, synchronous release | all Arch-A5 router blocks (SparseCal, CalFork, SharedPool-BG, xy_route, SA/XB, watchdog, PE NI staging) | none |

Single physical clock root. `H_LINK=7`, `V_LINK=9`, and `RAMP=1` are cycle counts
in the analytic BFM — not separate clocks or CDC boundaries.

**No DCA clock/domain.** Combine unit is absent; no merge-pipeline clocking.
**No stream_fork clocking.** CalFork is combinational mask expand + registered commit.

Credit return uses the same domain; allocations remain 16 H / 20 V flits.
SharedPool accounting (28 shared + 5×2 reserve) is same-domain.
