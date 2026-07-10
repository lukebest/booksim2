# Arch-A4 SparseCal-SharedPool-ZB-NoCombine Microarchitecture

## Fixed implementation parameters

`MESH_X=6`, `MESH_Y=8`, `FLIT_W=512`, `PAYLOAD_W=496`,
`CAL_DEPTH=128`, `CAL_BANKS=2`, `CAL_EVENT_W=23`, `SLOT_WRAP=1024`,
`BG_SHARED_POOL=40`, `BG_PER_PORT_RESERVE=2`, `BG_TOTAL_FLITS=50`,
`H_LINK=7`, `V_LINK=9`, `RAMP=1`, and `noc_clk=2 GHz`.

Calendar traffic is a compiled, zero-payload-buffer path driven by **sparse next-event
match**. BG and escape traffic use SharedPool-BG on non-matching cycles (soft priority).
One clock domain. **No `combine_unit`. No DCA.**

Dedicated diagrams: [`uarch-diagram.md`](uarch-diagram.md).

```mermaid
flowchart LR
  C0[S0: sparse event SRAM read] -->|entry 22:0| C1[S1: slot==counter qualify]
  CTR[slot counter] --> C1
  C1 -->|match| FORK[multicast fork]
  FORK --> XBAR[5x5 crossbar]
  B0[B0: route compute] -->|request metadata| B1[B1: switch allocate]
  B1 -->|flit 511:0| B2[B2: switch traverse]
  B2 --> XBAR
  XBAR --> LINK[H=7 / V=9 pipeline registers]
  C1 -.->|mismatch or timeout| WD[watchdog demote]
  WD --> POOL[SharedPool enqueue]
  POOL --> B0
  ABSENT[ABSENT: combine pipeline / DCA]
  FORK -.-> ABSENT
```

## Module specifications

### `calendar_store`
- **Clock:** `noc_clk` only.
- **Storage:** two SRAM banks, `DEPTH=128`, `WIDTH=23`, 1-cycle registered read.
  Total 5,888 bits. Unchanged from Trial 3.

### `next_event_match`
- Global slot counter wrap 1024; compare against sparse head.
- Match → calendar path; non-match → BG-eligible.

### `xy_route`
- Combinational X-before-Y decode + registered RC request.

### `multicast_fork`
- Atomic commit on `out_port_mask[4:0]`.

### ~~`combine_unit`~~ — **ABSENT (Trial 4 / Tier A)**

### `vc_buffers` (SharedPool-BG)
- Shared free pool **40** flits + per-port reserve **2** → **50** flits / 25,600 bits.
- `shared_used = Σ max(0, port_count − 2)`.
- Enqueue if `port_count < 2` OR `shared_used < 40`.
- **Calendar never enqueues** here.
- Demote/escape uses pool/reserves (lossless when capacity exists).
- Deadlock freedom: XY-DOR + reserves + calendar isolation (see architecture.md).

### `switch_alloc`
- Soft priority: calendar on match; BG on idle.
- Bounds: hard 328; soft ~160; soft+pool ~200.

### `crossbar` / `credit_fc` / `watchdog_demote` / `pe_ni`
- Same roles as Trial 3; demote emits into SharedPool-BG.

## Pipelines and throughput

| Path | Stages | Rate |
|---|---|---|
| Calendar | S0 → S1 → ST (+ fork) | 1 legal transfer / matched event |
| BG / escape | enqueue(pool) → RC → SA → ST | 1 eligible flit/cycle after fill |
| Combine | — | **N/A (absent)** |

## Signal conventions (future RTL — not written in DSE)

`i_`/`o_`/`io_` prefixes, `noc_clk`/`noc_rst_n`. Phase 3 deliverable remains C BFM only.
