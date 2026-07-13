# Arch-A5 SparseCal-SharedPool-CalFork-ZB-NoCombine Architecture

## Scope and fixed context

This Trial-5 architecture implements one router at each of 48 nodes in a 6×8 mesh.
Each router has north, east, south, west, and local ports on one 512-bit physical
NoC, in the single 2 GHz `noc_clk` domain. A granted direction transfers one flit
per cycle. Analytic inter-router delays are H=7, V=9; PE-router ramp is one cycle
with `ramp_bw=1`. There are no CDCs and no second physical network.

Arch-A5 keeps Trial-4 **Arch-A4** SparseCal + SharedPool + Tier A + soft-prio +
zero-buffer calendar + demote→XY, and applies two area levers:

1. **CalFork / LeanMulticast (primary):** calendar-native atomic `out_port_mask`
   fork — **not** a general FlooNoC-class stream_fork engine.
2. **Aggressive SharedPool (secondary):** shared pool **28** + reserve **5×2** =
   **38** flits (was 40+2=50).

Dedicated diagrams: [`architecture-diagram.md`](architecture-diagram.md).

## Router block diagram

```mermaid
flowchart LR
  mesh_in[Five 512-bit ingress ports] --> classify{calendar event<br/>or BG/escape?}
  pe_ni[pe_ni<br/>local inject/eject] --> classify
  slot_ctr[global slot counter<br/>wrap 1024]
  calendar_store[(calendar_store<br/>2 × 128 × 23-bit<br/>sparse event list)]
  calendar_store --> next_match[next_event_match<br/>slot == counter?]
  slot_ctr --> next_match
  next_match --> classify
  classify -->|matching calendar event| cal_fork[CalFork<br/>lean mask fork]
  cal_fork --> switch_alloc[switch_alloc]
  classify -->|BG or demoted| vc_buffers[vc_buffers<br/>shared pool 28 + reserve 5×2]
  vc_buffers --> xy_route[xy_route<br/>X then Y]
  xy_route --> switch_alloc
  classify -->|mismatch / timeout| watchdog_demote[watchdog_demote FSM]
  watchdog_demote --> vc_buffers
  switch_alloc --> crossbar[5×5, 512-bit crossbar]
  crossbar --> mesh_out[Five 512-bit egress ports]
  credit_fc[credit_fc<br/>per-egress BG/escape credits] <--> vc_buffers
  credit_fc --> switch_alloc
  absent[ABSENT: combine_unit / DCA / stream_fork]
  cal_fork -.-> absent
  pe_ni -.->|Tier-A PE compute outside router| absent
```

## Module decomposition and storage classification

| Block | Boundary and responsibility | Storage / clock-domain classification |
|---|---|---|
| `calendar_store` | Inactive-bank writes / active-bank event reads. Entry `{slot[9:0], valid, in_port[2:0], out_port_mask[4:0], opcode[3:0]}` = 23 bits, slot-sorted. Dual-bank hot-swap at slot 0. | Local SRAM; **2 × 128 × 23** = 5,888 bits. Unchanged. |
| `next_event_match` | `slot == counter` qualify; non-match → BG-eligible (soft priority). | Registers + match control (+0.003). |
| `xy_route` | BG/demoted unicast: X then Y then local. | Combinational + registered metadata. |
| **`cal_fork` (CalFork)** | Expand calendar `out_port_mask[4:0]` into atomic all-or-nothing SA grants. Wire-level fanout + credit AND — **no** multi-stream FSM / stream_fork pipeline. | Register-only leaf context. Area **0.025** (was FlooNoC 0.058). |
| ~~`combine_unit`~~ | **Absent (Tier A).** | — |
| `vc_buffers` | **SharedPool-BG:** shared free pool **28** flits + per-port reserve **2** (5×2=10) → **38 flits total**. Calendar never consumes pool slots. | Same-domain SRAM/regs + free-list / reserve accounting (+0.005 control). |
| `switch_alloc` / `crossbar` | Soft priority: calendar on match; BG on idle. | Register control + 5×5 crossbar. |
| `credit_fc` | Downstream BG/escape credits; calendar uses no payload credits. | H 0–16, V 0–20. |
| `watchdog_demote` | Early/late/wrong-port/missing/blocked → release once → lossless escape into pool. | Register FSM. |
| `pe_ni` | Local inject/eject; Tier-A PE handoff outside router. | Staging registers. |

Flit header (16-bit control in 512-bit flit): `class[1:0]`, `dst_x[2:0]`, `dst_y[2:0]`,
`calendar_id[1:0]`, `opcode[3:0]`, `flags[1:0]`; remaining 496 bits payload.

## CalFork vs FlooNoC stream_fork

| Aspect | FlooNoC-class stream_fork (T1–T4 model) | **CalFork (T5)** |
|---|---|---|
| Trigger | Independent multi-stream engine | Sparse calendar event `out_port_mask` |
| State | Per-stream FSMs / outstanding forks | Single mask + leaf demote context |
| Area charge | **0.058** | **0.025** (−0.033) |
| Semantics | General stream duplication | Calendar-native atomic fork only |

## SharedPool-BG allocation policy

```
can_enqueue(port):
  if port_count[port] < RESERVE(2): accept   # guaranteed progress
  else if shared_used < POOL(28): accept
  else: backpressure

shared_used = Σ_p max(0, port_count[p] − RESERVE)
```

**Rationale for 28+2 (not 40+2):** buffer area 0.139; with CalFork total **0.746×**.
Sensitivity: **24+2=34** also RefC PASS → total ~0.731; default **28** keeps
extra shared depth for adversarial BG bursts. Zero-reserve rejected (starvation).

## Deadlock freedom (shared pool + XY-DOR)

1. **XY-DOR** on a mesh is acyclic → no routing-induced circular wait.
2. **Calendar path is zero-buffer** and never takes pool credits.
3. **Per-port reserve = 2** guarantees ingress progress when shared is exhausted.
4. Pool slots release on DOR departure; `credit_fc` bounds downstream occupancy.
5. **Demote→XY** enqueues into pool/reserves (lossless); same XY class.

⇒ No circular wait on pool credits under legal traffic.

## Calendar path unaffected

- Matching sparse events use event-owned zero-buffer forwarding via **CalFork**.
- Calendar flits are **never** enqueued into `vc_buffers` / shared pool.
- Soft priority: BG never displaces a firing calendar event.

## VC, credit, and progress policy

- Calendar: zero-buffer; never waits on BG pool.
- BG/escape: one credited XY-DOR class; shared pool + reserves.
- End-to-end bounds (12-hop `|dx|=5`, `|dy|=7`):

  | Policy | Bound | Notes |
  |---|---:|---|
  | Conservative hard 1-in-16 | **328** | SA upper bound |
  | Soft-prio reserve-covered | **~160** | Single-flit / ≤2 deep uses reserve |
  | Soft + shared-pool contention | **~188** | Soft 160 + ≤28 pool-turnover |

## Frozen architecture decisions (Trial 5)

| Decision | Trial-5 value |
|---|---|
| Architecture name | **Arch-A5 SparseCal-SharedPool-CalFork-ZB-NoCombine** |
| Calendar | Sparse **2 × 128 × 23**; next-event match; dual-bank |
| Multicast | **CalFork** lean mask fork (MC **0.025**) |
| BG buffers | **Shared pool 28 + reserve 5×2 = 38 flits** |
| BG service | Soft priority; hard 328; soft ~160; pool-stress ~188 |
| Watchdog | 32 cycles; lossless demote→XY via pool/reserves |
| Reduction | **Tier A only** — no combine, no DCA |
| Analytic area | **0.746×** IQ-XY (vs A4 **0.822×**) |
| Analytic power | **0.90×** IQ-XY (vs A4 **0.92×**) |

## Analytic constraints

All PPA figures are analytic, not synthesis. Reproducible via
`python3 utils/ppa_analytic_model.py`.
