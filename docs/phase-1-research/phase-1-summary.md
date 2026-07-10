# Phase 1 Summary — DSE Trial 1 (6×8 Mesh Calendar-Collective Router)

- **Date:** 2026-07-10
- **Trial:** DSE Trial 1 — power/area-optimal calendar-collective NoC router
- **Phase 1 gate:** **PASS** (ambiguity 0.30 ≤ 0.50; research and feasibility reviews PASS)
- **Artifacts:** `iron-requirements.json`, `open-requirements.json`, `domain-analysis.md`, `dca-tier-analysis.md`, `ADR-001-algorithm-selection.md`, `reviews/phase-1-research/`

---

## Problem Statement

Design a **calendar-collective router** for a **6×8 mesh (48 tiles)** that replays **offline, conflict-free, zero-buffer collective schedules** while sharing a **single 512-bit physical network** with **XY dimension-order background unicast**. The router must support broadcast, allgather, gather, reduce, and allreduce via per-collective calendars; preserve **deadlock/hang freedom and zero packet loss** under mixed traffic; and degrade gracefully when collective traffic violates its schedule (late/early/wrong port).

Optimization priorities: **P0** correctness and robustness (REQ-CAL, REQ-BG, REQ-MC, REQ-ROB); **P1** minimize router power/area (analytic model, FlooNoC calibration anchors); **P2** minimize makespan overhead vs published 6×8 schedule baselines. PPA is **analytic only** — no RTL synthesis in this trial.

---

## Physical Parameters

| Parameter | Value |
|-----------|-------|
| Topology | 6×8 2D mesh, 48 nodes |
| Flit | 64 B (512 bit), 1 flit/cycle when granted |
| Clock | 2 GHz (`noc_clk`, 0.5 ns/cycle) |
| Link delay | **H = 7** cycles (X), **V = 9** cycles (Y) |
| PE↔router ramp | 1 cycle, `ramp_bw = 1` |
| Throughput | 128 GB/s decimal per granted direction |
| Schedule source | `results/superpose_6x8.json` (zero-buffer baselines) |

Reference makespan endpoints (single-collective, from domain analysis): broadcast 91–95 cycles, gather 91–245 cycles (m=1..5); superposed **ag_bcast** 167–708 and **ag_gather** 170–898 cycles for REQ-P-003 comparison.

---

## Iron Requirements

| Category | Count | Must | Should | May |
|----------|------:|-----:|-------:|----:|
| Functional (F) | **12** | 10 | 2 | 0 |
| Performance (P) | **5** | 2 | 2 | 1 |
| **Total** | **17** | 12 | 4 | 1 |

### Key P0 (must) requirements

| Group | IDs | Essence |
|-------|-----|---------|
| **REQ-CAL** | F-001–F-004 | Load/replay offline calendar; support all five collectives; tier A/B/C reduction comparison mandated |
| **REQ-BG** | F-005 | Always-available XY unicast on same network; Phase 2 must define numeric progress bound |
| **REQ-MC** | F-006 | Atomic calendar fork via `out_port_mask`; release source only after all copies accepted |
| **REQ-ROB** | F-007–F-009 | Deadlock-free mixed traffic; credit/ready flow control, no overwrite/drop; watchdog demotion on schedule violation |
| **REQ-PERF** | P-001, P-002 | 1 flit/cycle at 2 GHz; analytic model uses H=7, V=9, ramp=1 |

Should/may items (F-011 DCA interface, F-012 SystemC BFM, P-003 baseline overhead, P-004 analytic PPA model, P-005 DCA tile-area reporting) bound Phase 2/3 deliverables without blocking the Phase 1→2 gate.

---

## Algorithm Selection — ADR-001 Stack

**Status:** Accepted as Trial-1 default (`AGENT_ASSUMED`; pending user confirmation at trial satisfaction check).

| Dimension | Selection | Trial-1 parameters |
|-----------|-----------|-------------------|
| **Calendar** | Double-buffered per-router slot table | 1,024 slots × 13 bits × 2 banks ≈ 3.25 KiB/router |
| **Isolation** | Hybrid TDM windows + buffered XY BG VC | 1 BG slot / 16 calendar slots (6.25%); acyclic XY escape VC |
| **Buffering** | Zero-buffer calendar + RTT-sized BG buffers | Calendar cut-through; BG VC up to 74 flits (≈4.6 KiB interior) |
| **Multicast** | Calendar `out_port_mask` atomic fork | 5-bit mask, 3-bit `in_port`; all-or-nothing per slot |
| **Reduction** | **Tier B** primary; Tier A fallback; **Tier C deferred** | 3-cycle 2-input integer/bitwise combine (~+2.7% area class) |
| **Violations** | Watchdog → demote to buffered XY escape VC | Never drop; multicast leaf expansion on demotion |

Rejected alternatives (tag-match calendar, strict calendar priority, shallow shared buffers, DCA for m=1..5, NACK/retry) are documented in `ADR-001-algorithm-selection.md` with P0/P1/P2 rationale.

---

## DCA Tier A / B / C — Recommendation Snapshot

| Tier | Mechanism | Allreduce m=1..5 (cycles) | Router area class | Trial-1 role |
|------|-----------|----------------------------:|-------------------|--------------|
| **A** | Gather + PE local compute + broadcast | 229–575 | +0% arithmetic | Mandatory fallback (FP, non-associative ops) |
| **B** | Router 2-input int/bitwise merge | **101–228** | **~+2.7%** | **Selected** — best P0/P1/P2 balance |
| **C** | DCA tile-FPU offload (12-cycle model) | 315–327 | ~+16.9% collective-wide | **Deferred** — latency-bound at m≤5 |

**Recommendation:** Implement Tier B in-network; route IEEE FP and unsupported opcodes through Tier A; reserve opcode/tag space for future Tier C without instantiating DCA hardware.

---

## Open Items for Phase 2

Five architecture decisions remain open (`open-requirements.json`); Phase 2 must resolve and document numeric/protocol details before Phase 3 interface freeze:

| ID | Topic | Candidates (summary) |
|----|-------|---------------------|
| OPEN-1-001 | Calendar storage/dispatch encoding | Slot table vs tag match vs source-routed header |
| OPEN-1-002 | Calendar/BG isolation | Dedicated VC vs hard TDM vs **hybrid (ADR-001 default)** |
| OPEN-1-003 | Buffering / flow control | Zero-cal + BG VC vs shallow shared vs full input-queued |
| OPEN-1-004 | Reduction tier / semantics | Tier A vs **B (ADR-001 default)** vs C; opcode/format/order |
| OPEN-1-005 | Violation detection / recovery | Watchdog demotion vs slot validity vs retry-then-demote |

Additional Phase 2 closures from iron ambiguities: calendar load protocol and epoch handoff, credit/ready widths and buffer depths, numeric BG progress bound, watchdog timeout and recovery latency, Tier-B opcode/lane/overflow contract. `io_definition.json` retains null widths — not an RTL port contract until Phase 2.

---

## Ambiguity Score and Gate Verdict

| Axis | Score | Weight |
|------|------:|-------:|
| Goal clarity | 0.18 | 40% |
| Constraint clarity | 0.42 | 30% |
| Acceptance-criteria clarity | 0.34 | 30% |
| **Weighted total** | **0.30** | (threshold **≤ 0.50**) |

**Gate: PASS.** Unresolved implementation choices are explicitly isolated in open items rather than silently assumed in the iron contract.

| Review | Verdict |
|--------|---------|
| `reviews/phase-1-research/research-review.md` | **PASS** — 17/17 iron entries, 12/12 feature groups, 5 open items correctly classified |
| `reviews/phase-1-research/feasibility-review.md` | **PASS (analytic)** — selected stack implementable; conditional on Phase 2 numeric/protocol closure and post-synthesis timing |

**Phase 2 authorization:** Proceed to architecture design. Ratify ADR-001 (or override) and close OPEN-1-001..005 before Phase 3 μArch/RTL freeze.

---

## References

- Input spec: `docs/dse-input-spec.md`
- Decision record: `docs/decisions/ADR-001-algorithm-selection.md`
- Analysis: `docs/phase-1-research/domain-analysis.md`, `dca-tier-analysis.md`
- Baselines: `results/superpose_6x8.json`, `results/report_superpose_6x8.html`
