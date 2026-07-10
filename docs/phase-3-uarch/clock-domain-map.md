# Clock Domain Map — Trial 2 Arch-A2

| Domain | Frequency | Reset | Blocks | Crossing |
|---|---:|---|---|---|
| `noc_clk` | 2 GHz (0.5 ns) | `noc_rst_n`, synchronous release | all Arch-A2 router blocks and PE NI staging | none |

Single physical clock root. `H_LINK=7`, `V_LINK=9`, and `RAMP=1` are cycle counts
in the analytic BFM — not separate clocks or CDC boundaries.

**No DCA clock/domain.** Combine unit is absent; no merge-pipeline clocking.

Credit return uses the same domain; allocations remain 16 H / 20 V flits.
