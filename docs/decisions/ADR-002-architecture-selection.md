# ADR-002: Architecture Selection for DSE Trial 1

| Field | Value |
|---|---|
| **Status** | Accepted (Trial-1 default; pending user confirmation) |
| **Date** | 2026-07-10 |
| **Decision source** | `AGENT_ASSUMED` — recorded per DSE policy when AskUserQuestion is unavailable; subject to user confirmation at Trial satisfaction check |
| **Related analysis** | [architecture-candidates.md](../phase-2-architecture/architecture-candidates.md) |
| **Prior decision** | [ADR-001: Algorithm Selection](ADR-001-algorithm-selection.md) |
| **Input spec** | [dse-input-spec.md](../dse-input-spec.md) |

---

## Context

DSE Trial 1 requires a concrete router microarchitecture that instantiates the algorithm stack ratified in ADR-001. Phase 2 compared three P0-capable candidates for the 6×8 mesh calendar-collective router: each preserves a single 512-bit physical link, one flit/cycle per granted direction at 2 GHz, and the specified 7-cycle horizontal, 9-cycle vertical, and 1-cycle PE-ramp delays. All candidates support calendar replay, XY-DOR background unicast, calendar multicast, and no-drop recovery within a single `noc_clk` domain (no CDC).

Binding optimization priorities (from `dse-input-spec.md`):

| Priority | Goal |
|---|---|
| **P0** | Functional correctness and robustness: REQ-CAL, REQ-BG, REQ-MC, REQ-ROB |
| **P1** | Minimize router power and area (analytic model; FlooNoC calibration anchors) |
| **P2** | Minimize makespan overhead vs zero-buffer schedule baselines |

ADR-001 selected the algorithm stack: double-buffered per-router calendar SRAM, hybrid TDM windows plus dedicated BG XY VC, zero-buffer calendar forwarding, calendar `out_port_mask` atomic fork, Tier-B two-input integer/bitwise combine with Tier-A fallback, and watchdog demotion to the XY escape VC. This ADR selects the router architecture that best implements that stack under P0–P2.

Quantitative PPA and makespan estimates for all three candidates are documented in [architecture-candidates.md](../phase-2-architecture/architecture-candidates.md). Estimates are analytic, not synthesis results; baseline area and dynamic power are normalized to a five-port, 512-bit input-queued XY router at **1.00**.

---

## Decision

Select **Arch-A: CalSlot-Hybrid-ZB** as the Trial-1 router architecture.

### Arch-A definition (selected)

Per-router double-buffered calendar SRAM, hybrid TDM windows plus a dedicated BG XY VC, zero-buffer calendar forwarding, RTT-credit buffers only for BG, calendar `out_port_mask` fork, Tier-B two-input integer/bitwise combine with Tier-A fallback for FP, and watchdog demotion to the XY escape VC.

| Metric | Estimate | Notes |
|---|---:|---|
| Relative router area | **0.970** | 3.0% below normalized IQ-XY baseline |
| Relative dynamic power | **0.98** | Lowest among candidates |
| Calendar makespan overhead | **0–5%** | After recompiling calendars with hybrid windows; unmodified dense calendar sees conservative 6.25% slot tax |
| Reduction tier | Tier B (+2.7% area class); Tier A fallback | Aligns with ADR-001 |

### Rationale

1. **P0 satisfied:** Atomic calendar fork with credit gating; calendar flits never wait on BG buffers; BG and demoted traffic use one credit-controlled XY-DOR escape VC with an acyclic channel-dependency graph; watchdog demotion preserves flits with zero drop policy.
2. **P1 optimized:** Only candidate below the normalized baseline area estimate (0.970) and lowest-power estimate (0.98). Calendar zero-buffer forwarding removes general IQ VC buffering; Tier-B combine captures the +2.7% calibrated class without the +16.9% DCA area tax.
3. **P2 preserved for calendar path:** Zero-buffer calendar replay retains schedule fidelity close to theory. Hybrid isolation (1 protected BG opportunity per 16 slots) bounds BG progress without the rigid bandwidth waste of hard TDM or the non-deterministic replay of shared-buffer arbitration.
4. **ADR-001 alignment:** Arch-A is the direct microarchitectural instantiation of every ADR-001 algorithm choice; no algorithm/architecture mismatch.

---

## Alternatives Considered

### Arch-B: SrcRoute-VCPrio-Shared — not selected

**Definition.** Calendar fork/turn opcodes carried in the flit header; minimal epoch/control table; dedicated calendar VC with priority; shallow shared IQ buffers; header-mask multicast fork; Tier A reduction only; violations demote into the same credit-controlled XY BG VC.

| Metric | Estimate |
|---|---:|
| Relative router area | 1.008 (+0.8% above baseline) |
| Relative dynamic power | 1.08 |
| Calendar makespan overhead | 4–9% |

**Why not selected for Trial 1:**

- **Worse makespan fidelity:** Converts an offline conflict-free schedule into per-hop header decode and shared-buffer arbitration. Priority-aging force grants break strict calendar-only wait cycles, making calendar delivery non-deterministic.
- **Shared buffer interference:** Calendar traffic consumes shallow shared IQ storage alongside BG; data-dependent bubbles from buffer availability and arbitration cannot preserve zero-buffer schedule theory.
- **ADR-001 mismatch:** Source-routed header program and Tier-A-only reduction diverge from the ADR-001 calendar-table and Tier-B stack.

### Arch-C: CalSlot-HardTDM-DCA — not selected

**Definition.** Per-router calendar table with hard TDM slot ownership; zero-buffer calendar forwarding; deep BG/escape buffers; calendar-mask fork; Tier-C DCA interface pairing operands and offloading wide arithmetic to the tile FPU; watchdog demotion retained.

| Metric | Estimate |
|---|---:|
| Relative router area | 1.237 (+23.7% above baseline) |
| Relative dynamic power | 1.23 |
| Calendar makespan overhead | 0–2% for normal calendar traffic |
| Allreduce m=1..5 (Tier C) | 315, 318, 321, 324, 327 cycles |

**Why not selected for Trial 1:**

- **DCA area tax unjustified:** +16.9% wide-reduction/DCA router area class raises total to 1.237 — contradicts P1 for Trial-1 message sizes (m ≤ 5 flits).
- **Latency-bound, not bandwidth-bound:** Current 12-cycle DCA offload/return model yields allreduce cycles that lose to Tier B (101–228) for m=1..5; area and power spend cannot be amortized at this scale.
- **ADR-001 mismatch:** Tier-C DCA was explicitly deferred in ADR-001; hard TDM isolation is more rigid than the selected hybrid window model.

### Cross-candidate summary

| Dimension | Arch-A (selected) | Arch-B | Arch-C |
|---|---|---|---|
| Relative router area | **0.970** | 1.008 | 1.237 |
| Relative dynamic power | **0.98** | 1.08 | 1.23 |
| Calendar makespan fidelity | 0–5% (window-aware) | 4–9% | **0–2%** |
| BG QoS | Bounded: 1 protected slot / 16 | Priority-aging force grant | Deterministic BG slots |
| Reduction readiness | Tier B + A fallback | Tier A only | Tier C (FPU-dependent) |
| Primary risk | Window insertion in compiled schedules | Shared IQ interference | DCA latency + area |

Arch-C wins normal-calendar fidelity but fails P1 and P2 for Trial-1 workloads. Arch-B avoids calendar SRAM but cannot offer deterministic zero-buffer replay under shared-buffer contention.

---

## Consequences

### Positive

- **Direct ADR-001 instantiation:** Phase 3 μArch can proceed without reconciling algorithm/architecture gaps.
- **P0 by construction:** Isolated escape VC, atomic fork, bounded watchdog demotion, and finite BG service (hybrid window model).
- **P1 leader:** Lowest estimated router area and dynamic power among P0-capable candidates that preserve zero-buffer calendar fidelity.
- **Tier-B reduction in-network:** Captures +2.7% area class for integer/bitwise allreduce (101–228 cycles, m=1..5) without DCA hardware or FPU arbitration contracts.
- **Predictable datapath:** Two-stage calendar path (`SRAM/slot qualification → ST`); three-stage BG path (`RC → SA → ST`); three-cycle Tier-B combine merge contract calendar-reserved.

### Negative / trade-offs

- **Hybrid window overhead:** 6.25% reserved BG slots require offline calendar recompaction; unmodified dense calendars see conservative slot tax.
- **Tier B only in-network:** FP and non-associative operations route through Tier A gather+PE fallback with higher latency.
- **DCA deferred:** Wide FP/vector reduction throughput benefit unavailable until a future trial demonstrates amortization at larger message sizes.
- **Implementation medium complexity:** Window insertion, watchdog bounds, and demotion FSM details remain open per `open-requirements.json`.
- **Pending confirmation:** Decision source is `AGENT_ASSUMED`; user must ratify or override at Trial satisfaction check.

### Phase 3 follow-ups

- Resolve open numeric values: calendar load/epoch protocol, BG service and watchdog bounds, field encodings, Tier-B operand/order/identity contract.
- Instantiate μArch pipeline: double-buffered calendar SRAM, hybrid window counter, BG credit flow control, atomic fork/combine datapath, violation-demotion FSM.
- BFM must replay post-window calendars, inject early/late/wrong-port cases, and measure stated makespan and progress bounds — analytic estimates are not acceptance evidence.
- Keep Tier A fallback and Tier C opcode/tag reservation in interface definitions without instantiating DCA hardware.

---

## References

- [Architecture Candidates — DSE Trial 1](../phase-2-architecture/architecture-candidates.md) — Arch-A/B/C definitions, PPA estimates, trade-off matrix, recommendation
- [ADR-001: Algorithm Selection](ADR-001-algorithm-selection.md) — Trial-1 algorithm stack this architecture instantiates
- [DSE Input Spec](../dse-input-spec.md) — functional requirements (REQ-CAL, REQ-BG, REQ-MC, REQ-ROB), optimization priorities P0–P2
- [DCA Tier Analysis](../phase-1-research/dca-tier-analysis.md) — Tier A/B/C quantitative comparison
- Schedule baselines: `results/superpose_6x8.json`, `results/report_superpose_6x8.html`
