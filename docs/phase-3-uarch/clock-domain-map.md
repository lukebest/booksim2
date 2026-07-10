# Clock Domain Map

| Domain | Frequency | Reset | Blocks | Crossing |
|---|---:|---|---|---|
| `noc_clk` | 2 GHz (0.5 ns) | `noc_rst_n`, synchronous release | all Trial-1 router blocks and PE NI staging | none |

Trial 1 has one physical clock root. `H_LINK=7`, `V_LINK=9`, and `RAMP=1`
are cycle counts represented by pipeline registers in the analytic BFM; they do
not create clocks, generated clocks, or CDC boundaries. All internal state is
reset before calendar activation. Future DCA signals remain inactive, so they
form no active crossing.

Each inter-router link carries a registered flit and timing token. A horizontal
transfer takes seven `noc_clk` edges and a vertical transfer takes nine. Credit
return scheduling uses the same domain and its allocation is 16/20 flits,
respectively.
