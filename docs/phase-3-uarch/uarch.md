# Arch-A2 CalSlot-Hybrid-ZB-NoCombine Microarchitecture

## Fixed implementation parameters

`MESH_X=6`, `MESH_Y=8`, `FLIT_W=512`, `PAYLOAD_W=496`,
`CAL_SLOTS=1024`, `CAL_ENTRY_W=13`, `BG_WINDOW=16`,
`H_LINK=7`, `V_LINK=9`, `RAMP=1`, and `noc_clk=2 GHz`.

Calendar traffic is a compiled, zero-payload-buffer path. BG and escape traffic use
the single credited XY-DOR class. One clock domain. **No `combine_unit`. No DCA.**

Dedicated diagrams: [`uarch-diagram.md`](uarch-diagram.md).

```mermaid
flowchart LR
  C0[S0: calendar SRAM read] -->|entry[12:0]| C1[S1: qualify and masked switch]
  B0[B0: route compute] -->|request metadata| B1[B1: switch allocate]
  B1 -->|flit[511:0]| B2[B2: switch traverse]
  C1 --> FORK[multicast fork]
  FORK --> XBAR[5x5 crossbar]
  B2 --> XBAR
  XBAR --> LINK[H=7 / V=9 pipeline registers]
  C1 -.->|mismatch or timeout| WD[watchdog demote]
  WD --> B0
  ABSENT[ABSENT: combine pipeline / DCA]
  FORK -.-> ABSENT
```

## Module specifications

### `calendar_store`
- **Clock:** `noc_clk` only.
- **Storage:** two SRAM banks, `DEPTH=1024`, `WIDTH=13`, 1-cycle registered read.
  Total 26,624 bits. Headers: `calendar_id[1:0]`, epoch, CRC-good, load-complete.
- **Hazards:** activation only at slot zero after old-epoch retirement; active writes rejected.

### `calendar_replay`
- Qualifies valid entry: legal input port and nonzero mask for forwarding opcodes.
- `CAL_OP_PE_HANDOFF` (opcode 1): forward/tag for PE-local Tier-A handoff — **no arithmetic**.
- Legacy combine encodings are reserved and illegal for router compute.
- Missing/early/wrong-port → `watchdog_demote`; never enters `vc_buffers`.

### `xy_route`
- Combinational X-before-Y decode + registered RC request.
- Route: E/W while `dst_x != x`, then N/S while `dst_y != y`, else local.

### `multicast_fork`
- Atomic commit on `out_port_mask[4:0]`; all-or-nothing at slot boundary.
- On partial external acceptance: clear accepted bits; demote `remaining_leaf_mask`.

### ~~`combine_unit`~~ — **ABSENT (Trial 2)**
- Not instantiated. Reduce/allreduce arithmetic is PE-local (Tier A).
- No 3-cycle merge pipeline, no lane ALU, no DCA request/result path.

### `vc_buffers`
- Five ingress FIFO banks for BG/escape only; calendar has no FIFO.
- Interior provision: 100 flits (5×20) / 51,200 bits; H credit 16, V credit 20.

### `switch_alloc`
- Calendar owns legal compiled slots; BG gets one non-borrowable opportunity each
  `BG_WINDOW=16` slots. Eligible BG hop grant ≤ 16 slots.

### `crossbar`
- Registered 5×5 select + `FLIT_W` traverse; one granted flit/cycle after fill.

### `credit_fc`
- H counters 0..16; V counters 0..20. Calendar fork samples availability but does
  not consume payload VC credit.

### `watchdog_demote`
- FSM: `IDLE → ARMED → DEMOTE_WAIT → EMIT → IDLE`.
- Immediate early/wrong-port; 32-cycle timeout for missing/blocked.
- Emit one escape flit per remaining leaf when `vc_buffers` ready; no loss.

### `pe_ni`
- Local ready/valid inject/eject; Tier-A PE handoff for reduce/allreduce compute
  **outside** the router datapath.
- **No DCA stub datapath** in Trial 2.

## Pipelines and throughput

| Path | Stages | Rate |
|---|---|---|
| Calendar | S0 → S1 (+ fork) → ST | 1 legal transfer / compiled slot |
| BG / escape | RC → SA → ST | 1 eligible flit/cycle after fill at grant |
| Combine | — | **N/A (absent)** |

Both granted paths meet `rate_per_cycle * 2 GHz` per direction.

## Signal conventions (future RTL — not written in DSE)

`i_`/`o_`/`io_` prefixes, `noc_clk`/`noc_rst_n`, `u_` instances, `UPPER_SNAKE_CASE`
parameters. Phase 3 deliverable remains C BFM only.
