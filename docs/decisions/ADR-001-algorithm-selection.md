# ADR-001: Algorithm Selection for DSE Trial 2

| Field | Value |
|---|---|
| **Status** | Accepted (Trial 2, USER_CONFIRMED) |
| **Date** | 2026-07-10 |
| **Decision source** | `USER_CONFIRMED` — Tier A and area-first direction are binding for Trial 2 |
| **Related analysis** | [domain-analysis.md](../phase-1-research/domain-analysis.md), [dca-tier-analysis.md](../phase-1-research/dca-tier-analysis.md) |
| **Input spec** | [dse-input-spec.md](../dse-input-spec.md) |

---

## Context

DSE Trial 2 targets an area-first calendar-collective router for a 6×8 mesh (48 tiles, single 512-bit link, 64 B/flit, H=7 / V=9 wire delays, `ramp_bw=1`). The router must replay offline zero-buffer collective schedules while sharing the physical network with XY background unicast traffic. The selected design must target relative router area below 1.065× the baseline five-port XY router.

Binding optimization priorities (from `dse-input-spec.md`):

| Priority | Goal |
|---|---|
| **P0** | Functional correctness and robustness: REQ-CAL, REQ-BG, REQ-MC, REQ-ROB (deadlock/hang freedom, no packet loss, graceful violation handling) |
| **P1** | Minimize router power and area (analytic model; FlooNoC calibration anchors: multicast ~+5.8%, parallel reduce ~+2.7%, wide+DCA ~+16.9%) |
| **P2** | Minimize makespan overhead vs existing zero-buffer schedule baselines (`results/superpose_6x8.json`) |

Phase 1 domain analysis evaluated candidates across six functional dimensions (calendar storage, calendar/BG isolation, buffering, multicast fork, reduction tier, violation handling). Quantitative comparison matrices and area/makespan estimates are documented in the linked analysis files. This ADR records the USER_CONFIRMED Trial 2 stack; required diagrams are delivered by the architecture team. No Phase 4 work is authorized.

---

## Decision

Adopt the following algorithm stack for DSE Trial 2 architecture and Phase 3 BFM development:

### 1. Calendar: double-buffered per-router timeslot table

- **Mechanism:** Per-router slot table mapping `slot → {valid, in_port, out_port_mask, opcode}`; 1,024 slots × 13 bits; double-banked for epoch load/replay handoff (26,624 bits/router ≈ 3.25 KiB).
- **Rationale:** Schedule exists offline and is conflict-free. One registered SRAM read per slot preserves exact zero-buffer makespan replay without route/arbitration decisions during replay. Bank switch occurs only after all old-epoch calendar flits retire; epoch/CRC stored in bank header.

### 2. Isolation: hybrid TDM windows + buffered XY BG VC

- **Mechanism:** Calendar transmissions compile only into calendar windows; a periodic background window is reserved (Trial-1 initial setting: **1 of every 16 slots, 6.25%**). Background traffic uses a dedicated XY-DOR credit VC whose channel dependency graph remains acyclic. Idle BG windows may be borrowed by calendar only if preemptible until the slot boundary.
- **Rationale:** Proves finite BG service (REQ-BG) without the permanent-starvation risk of strict calendar priority (candidate A) or the rigid bandwidth waste of hard TDM (candidate B). Calendar overhead is bounded to configured windows before offline recompaction.

### 3. Buffering: zero-buffer calendar + RTT-sized BG buffers

- **Mechanism:** Calendar path: zero queued flits (cut-through, slot-owned; at most an elastic timing register modeled as part of the slot). BG VC: payload storage sized for link-credit round trip — 16 flits on H links, 20 on V links, plus conservative router/control margin (interior worst case: 74 flits, 37,888 bits ≈ 4.6 KiB).
- **Rationale:** Preserves schedule fidelity for calendar traffic while giving BG sufficient depth to sustain full-rate unicast without shallow-buffer bubbles. Avoids full input-queued VC buffering unjustified for static calendar traffic.

### 4. Multicast: calendar `out_port_mask` atomic fork

- **Mechanism:** In a calendar slot, copy one accepted input flit to every credited output selected by `out_port_mask` (5-bit port mask, 3-bit `in_port`). Slot fires only when every selected output can accept; partial fork emission is forbidden.
- **Rationale:** Schedule already identifies legal branches. Retains multicast's +5.8% area class with zero added replay cycles vs broadcast/allgather baselines (91–95 cycle bcast, 170–653 cycle AG). Offline compiler must avoid selecting unavailable ports.

### 5. Reduction: Tier A gather + PE-local compute; no router combine or DCA

| Tier | Role in Trial 2 | Mechanism |
|---|---|---|
| **A (selected)** | All supported reductions | Gather to PE → local compute → broadcast for allreduce; +0% router arithmetic area |
| **B (rejected)** | Trial 1 history / comparison only | Router-local two-input integer/bitwise combine; removed from the Trial 2 router |
| **C (rejected)** | Comparison only | DCA to tile FPU; no Trial 2 interface or datapath |

- **Rationale:** Trial 1 selected Tier B for its lower tested allreduce cycles (101–228). Trial 2 instead prioritizes router area below 1.065× baseline. Tier A removes all router arithmetic, operand-holding, reduction opcode/tag, and DCA request/response hardware. It retains PE-supported operation coverage, including operations that require explicit numerical contracts.

### 6. Violations: watchdog demotion to buffered XY escape VC, never drop

- **Mechanism:** Per-slot watchdog armed after expected slot, exceeding calibrated control/credit margin (not the one-cycle data slot). On expiry or calendar mismatch: atomically remove calendar reservation, preserve flit, enqueue in credited BG escape VC. For multicast violations, retain unserved leaf set; emit one XY packet per remaining leaf without duplicating already-accepted branches. Demotion is rate-limited and logged.
- **Rationale:** Bounded recovery with zero packet loss (REQ-ROB). Avoids NACK/retry protocol expansion (candidate B) and indefinite stall for resynchronization (candidate C).

---

## Alternatives Considered

| Dimension | Rejected alternative | Why not selected |
|---|---|---|
| Calendar storage | B. Per-flow tag match | Weak schedule fidelity without growing into a timetable; CAM/compare arbitration adds area and replay bubbles |
| Calendar storage | C. Source-routed fork/turn header | Cannot reserve conflicting link slots; higher link/header energy and NI complexity |
| Isolation | A. Dedicated calendar VC + strict priority | BG can starve indefinitely under dense calendar — violates REQ-BG |
| Isolation | B. Hard TDM reservation | Safe but wastes bandwidth/energy on idle reserved slots; too rigid for P2 |
| Buffering | B. Shallow shared buffers (8–16 flits) | Below 20-cycle V credit RTT; calendar/BG contention causes avoidable stalls |
| Buffering | C. Full input-queued VC buffers (~12.5 KiB) | General-purpose but unjustified SRAM/allocator/leakage cost for static calendar |
| Multicast | B. XY address-mask fork | 6×8 is not a single power-of-two region; no dynamic region requirement; adds decode latency |
| Multicast | C. Software multi-unicast | Cannot approach 91-cycle tree baseline under `ramp_bw=1`; rejects P2 |
| Reduction | B. Router-local 2-input combine | Trial 1 selection (~+2.7% class); removed to satisfy Trial 2 area-first direction |
| Reduction | C. DCA to tile FPU | Fails P1 (+16.9% area class) and loses P2 for m=1..5; latency-bound, not bandwidth-bound |
| Violations | B. NACK/retry at source | Unnecessary protocol expansion; congestion amplification risk |
| Violations | C. Stall for calendar resync | No finite progress bound; violates REQ-ROB hang freedom |

---

## Consequences

### Positive

- **P0 satisfied by construction:** Conflict-free calendar replay, finite BG service, acyclic XY-DOR escape VC, watchdog demotion with zero drop policy.
- **P1 optimized:** Tier A removes Trial 1's ~+2.7% router-reduction class and avoids the +16.9% DCA class. The selected design must analytically demonstrate area below 1.065× baseline; calendar control SRAM (3.25 KiB) and BG payload buffering (≤4.6 KiB interior) remain material contributors.
- **P2 preserved for calendar path:** Zero-buffer calendar replay retains reference makespan fidelity. Hybrid isolation may add up to 6.25% overhead before offline recompaction — must be verified against actual post-window schedules, not claimed unchanged from pre-window baselines.
- **Deterministic datapath:** Router is deliberately not a general dynamic collective router; static calendar path is small and predictable; BG escape VC is the correctness backstop.

### Negative / trade-offs

- **Not general-purpose:** Per-flow tag match and source-routed headers deferred; router cannot adapt schedules at runtime.
- **PE-local reduction latency:** All reductions use the gather+PE path, so Trial 1's in-router makespan advantage is intentionally relinquished.
- **No DCA capability:** Wide FP/vector reduction acceleration is unavailable in Trial 2.
- **BG bandwidth tax:** 6.25% reserved windows reduce peak calendar throughput; actual overhead depends on schedule recompaction after window insertion.
- **Area check required:** The <1.065× target is analytic and must be reported using the documented assumptions.

### Phase 2 / Phase 3 follow-ups

- Resolve open architecture items (calendar load protocol, header bit widths, PE-compute handoff, watchdog timeout numerics, demotion FSM details) per `open-requirements.json`.
- Record numeric BG progress bound and watchdog parameters before BFM acceptance.
- Produce architecture diagrams and Phase 3 BFM work for the selected calendar, credit flow, fork, PE-handoff, and violation-demotion behavior.
- Do not instantiate a router combine datapath, DCA interface, or Phase 4 RTL.

---

## References

- [Domain Analysis — 6×8 Calendar-Collective NoC](../phase-1-research/domain-analysis.md) — sections 1–6, recommended algorithm stack table
- [DCA Tier Analysis — Reduce and Allreduce](../phase-1-research/dca-tier-analysis.md) — quantitative A/B/C comparison, area/power classes, Trial-1 recommendation
- [DSE Input Spec](../dse-input-spec.md) — functional requirements (REQ-CAL, REQ-BG, REQ-MC, REQ-ROB), optimization priorities P0–P2
- Schedule baselines: `results/superpose_6x8.json`, `results/report_superpose_6x8.html`
