# Arch-A3 SparseCal-Hybrid-ZB-NoCombine Architecture

## Scope and fixed context

This Trial-3 architecture implements one router at each of 48 nodes in a 6×8 mesh.
Each router has north, east, south, west, and local ports on one 512-bit physical
NoC, in the single 2 GHz `noc_clk` domain. A granted direction transfers one flit
per cycle. Analytic inter-router delays are H=7, V=9; PE-router ramp is one cycle
with `ramp_bw=1`. There are no CDCs and no second physical network.

Arch-A3 is the sparse-calendar evolution of Trial-2 Arch-A2: conflict-free collective
work is replayed from a **per-router sparse ordered event list** without queued payload
storage; background (BG) and demoted traffic use an isolated, credited XY-DOR escape VC.
**DCA Tier A is binding:** the router has **no** `combine_unit` and **no** DCA datapath.
Reduce is scheduled gather plus PE-local compute; allreduce is gather → PE compute →
scheduled broadcast.

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
  classify -->|matching calendar event| multicast_fork[multicast_fork<br/>atomic 5-bit fork]
  multicast_fork --> switch_alloc[switch_alloc]
  classify -->|BG or demoted| vc_buffers[vc_buffers<br/>BG + escape payload queues]
  vc_buffers --> xy_route[xy_route<br/>X then Y]
  xy_route --> switch_alloc
  classify -->|mismatch / timeout| watchdog_demote[watchdog_demote FSM]
  watchdog_demote --> vc_buffers
  switch_alloc --> crossbar[5×5, 512-bit crossbar]
  crossbar --> mesh_out[Five 512-bit egress ports]
  credit_fc[credit_fc<br/>per-egress BG/escape credits] <--> vc_buffers
  credit_fc --> switch_alloc
  absent[ABSENT: combine_unit / DCA]
  multicast_fork -.-> absent
  pe_ni -.->|Tier-A PE compute outside router| absent
```

## Module decomposition and storage classification

| Block | Boundary and responsibility | Storage / clock-domain classification |
|---|---|---|
| `calendar_store` | Owns inactive-bank writes and active-bank event reads. Each entry is `{slot[9:0], valid, in_port[2:0], out_port_mask[4:0], opcode[3:0]}` = 23 bits, stored in **slot-sorted order** per bank. Bank header: `calendar_id[1:0]`, epoch, load-complete CRC/status; active bank changes only at slot 0 after old-epoch calendar flits retire. | Local same-domain SRAM; two banks, **128 entries × 23-bit** = 5,888 bits (0.72 KiB) plus headers. Dual-bank hot-swap retained: m=1 allreduce max_slot≈951; depth 128 covers observed max busy-router entries=49 with margin. |
| `next_event_match` | Compares global cycle/slot counter against the head (or CAM-indexed) sparse entry. On `slot == counter` and `valid`, qualifies ingress/mask/opcode and arms calendar path. Non-matching cycles are BG-eligible (**soft priority**). | Registers + small compare/match control (+0.003 area class vs Trial 2). |
| `xy_route` | Routes BG and demoted unicast using destination `x[2:0], y[2:0]`: X first, then Y, then local. | Combinational route decode plus registered request metadata. |
| `multicast_fork` | Converts a legal replay mask into simultaneous output requests; commits only when all selected outputs have availability. | Register-only accepted-branch and remaining-leaf context. |
| ~~`combine_unit`~~ | **Absent in Trial 3.** | — |
| `vc_buffers` | Owns payload queues for BG and escape; calendar traffic bypasses them. | Five per-input BG/escape FIFOs, each 20 flits (100 flits × 512 bits = 51,200 bits / 6.25 KiB per interior router). |
| `switch_alloc` / `crossbar` | **Soft priority:** calendar owns cycles where a matching sparse event fires; BG uses idle/non-matching slots. Hard 1-in-16 non-borrowable window is **relaxed** but retained as conservative bound reference. | Register-only control and 5×5 crossbar. |
| `credit_fc` | Downstream BG/escape credits; calendar uses no payload credits. | Counter registers: 5 bits H (0–16), 5 bits V (0–20). |
| `watchdog_demote` | Early/late/wrong-port/missing/blocked calendar arrivals → release once → lossless escape packets. | Register-only FSM + 5-bit remaining-leaf mask. |
| `pe_ni` | Local inject/eject; Tier-A PE handoff for reduce/allreduce compute **outside** the router datapath. **No DCA stub datapath.** | Small same-domain staging registers. |

Flit header (16-bit control in 512-bit flit): `class[1:0]`, `dst_x[2:0]`, `dst_y[2:0]`,
`calendar_id[1:0]`, `opcode[3:0]`, `flags[1:0]`; remaining 496 bits payload.

## Data and control flow

### Calendar event (sparse replay)

At each cycle, `next_event_match` compares the global slot counter (wrap 1024) against
the next valid sparse entry. When `entry.slot == counter` and ingress presents the
calendar-class flit, the event-owned path sends it to `multicast_fork` and issues its
compiled `switch_alloc` transfer. Pipeline: `event SRAM/qualification` then `masked
switch traversal`. No calendar payload enters `vc_buffers`. There is **no** combine
reservation.

Density evidence from `results/calendars/*_m1.json` (48 routers):

| Collective | Total entries | Avg/router | Max/router | Max slot |
|---|---:|---:|---:|---:|
| broadcast | 48 | 1 | 1 | 99 |
| allgather | 192 | 4 | 4 | 699 |
| gather / reduce | 336 | 7 | 48 | 851 |
| allreduce | 384 | 8 | **49** | **951** |

Density vs dense `48×1024` ≪ 1%. Depth 128 per bank is P0-safe with margin.

### BG XY flit (soft priority)

Classified as BG → `vc_buffers` (with credit) → `xy_route` X-before-Y → `switch_alloc`
in any cycle **without** a matching calendar event. Soft priority does not displace a
firing calendar event. Pipeline `RC → SA → ST`, one flit/cycle after fill.

### Multicast

Atomic `out_port_mask` fork: no selected copy launches unless every selected output is
available. On fault before commit, preserve flit + leaf mask; after partial acceptance,
demote only remaining leaves.

### Reduce / allreduce (Tier A — no router arithmetic)

- **Reduce:** calendar gather tree to root (or designated PE); PE performs local
  combine; result stays at PE (or is injected as a new calendar/BG message if needed).
- **Allreduce:** gather → PE-local compute → calendar broadcast of the result.
- Router opcodes may tag `CAL_OP_PE_HANDOFF` for observability; the router still only
  forwards. Legacy combine opcode encodings are reserved and have **no** arithmetic.

### Demotion

Watchdog: immediate early/wrong-port; missing/blocked after 32 `noc_clk` cycles from
expected arrival. Release calendar reservation once; emit one escape XY packet per
remaining leaf into `vc_buffers` without drop.

## VC, credit, and progress policy

- Calendar: zero-buffer, event-owned; never waits on BG buffers.
- BG and escape: one credited XY-DOR class; escape bit is observability only.
- **Soft priority (selected):** calendar wins on matching sparse events; BG uses
  idle/non-matching slots. Hard 1-in-16 TDM is relaxed but documented as conservative
  fallback.
- End-to-end bounds (source `pe_ni` enqueue → destination eject, 12-hop `|dx|=5`,
  `|dy|=7`):

  **Conservative hard-TDM** (Trial 2 formula, still valid upper bound):

  `T_hard ≤ 2×RAMP + |dx|×(BG_WINDOW + T_router + H_LINK) + |dy|×(BG_WINDOW + T_router + V_LINK)`

  with `RAMP=1`, `BG_WINDOW=16`, `T_router=3`, `H_LINK=7`, `V_LINK=9` → **328 cycles**.

  **Soft-prio occupancy-aware** (max calendar occupancy 49, horizon 952):

  `T_soft ≈ 160 cycles` on the same 12-hop worst case.

## Frozen architecture decisions (Trial 3)

| Decision | Trial-3 value |
|---|---|
| Architecture name | **Arch-A3 SparseCal-Hybrid-ZB-NoCombine** |
| Calendar organization | Sparse ordered event list; **2 × 128 × 23-bit**; next-event match; dual-bank hot-swap at slot 0 |
| BG service | **Soft priority** (calendar on match); conservative hard 1-in-16 bound 328 cy; soft bound ~160 cy |
| Watchdog | 32 cycles; immediate early/wrong-port; no-loss demotion |
| Reduction | **Tier A only** — no `combine_unit`, no DCA |
| Analytic area | **1.000×** IQ-XY (vs Trial 2 **1.028×**, Trial 1 **1.065×**) |
| Analytic power | **0.95×** IQ-XY (vs Trial 2 **0.96×**, Trial 1 **0.98×**) |

## Analytic constraints

Schedules export naturally as sparse JSON slot lists (`calendar-export-schema.md`).
Zero-buffer baselines remain comparison references. All PPA figures are analytic, not
synthesis. Shared BG buffer pool is **out of scope** (future Trial 3b only).
