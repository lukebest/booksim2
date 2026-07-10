# Phase 1 Summary — DSE Trial 2 (6×8 Mesh Calendar-Collective Router)

> **Trial 4 note (2026-07-10):** Phase 2/3 Trial 4 selects **Arch-A4 SharedPool-BG**
> on the Trial 3 SparseCal base (pool 40 + reserve 5×2). Phase 1 algorithm/Tier A
> decisions are unchanged; see Trial 4 `architecture-candidates.md` and
> `.rat/scratch/trial4-report-zh.md`.

> **Trial 3 note (2026-07-10):** Phase 2/3 Trial 3 selects **SparseCal** for
> **OPEN-1-001** — sparse ordered event list (`2×128×23` per router) with
> next-event match, replacing the Trial 2 dense `2×1024×13` slot table. Phase 1
> iron JSON structure is unchanged; see `domain-analysis.md` §1 update and
> Trial 3 `architecture-candidates.md`.

- **Date:** 2026-07-10
- **Trial:** DSE Trial 2 — area-first calendar-collective NoC router
- **Phase 1 gate:** **PASS** (ambiguity 0.25 ≤ 0.50; research and feasibility reviews PASS)
- **Artifacts:** `iron-requirements.json`, `open-requirements.json`, `domain-analysis.md`, `dca-tier-analysis.md`, `ADR-001-algorithm-selection.md`, `reviews/phase-1-research/`

---

## Problem Statement

Design a **calendar-collective router** for a **6×8 mesh (48 tiles)** that replays **offline, conflict-free, zero-buffer collective schedules** while sharing a **single 512-bit physical network** with **XY dimension-order background unicast**. The router must support broadcast, allgather, gather, reduce, and allreduce via per-collective calendars; preserve **deadlock/hang freedom and zero packet loss** under mixed traffic; and degrade gracefully when collective traffic violates its schedule (late/early/wrong port).

Optimization priorities: **P0** correctness and robustness (REQ-CAL, REQ-BG, REQ-MC, REQ-ROB); **P1** area-first, with selected relative router area **<1.065×** baseline; **P2** minimize makespan overhead vs published 6×8 schedule baselines. PPA is **analytic only** — no RTL synthesis in this trial. The trial ends at Phase 3; no Phase 4 RTL work is authorized.

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

**Status:** Accepted for Trial 2 (`USER_CONFIRMED`).

| Dimension | Selection | Trial-1 parameters |
|-----------|-----------|-------------------|
| **Calendar** | Double-buffered per-router slot table | 1,024 slots × 13 bits × 2 banks ≈ 3.25 KiB/router |
| **Isolation** | Hybrid TDM windows + buffered XY BG VC | 1 BG slot / 16 calendar slots (6.25%); acyclic XY escape VC |
| **Buffering** | Zero-buffer calendar + RTT-sized BG buffers | Calendar cut-through; BG VC up to 74 flits (≈4.6 KiB interior) |
| **Multicast** | Calendar `out_port_mask` atomic fork | 5-bit mask, 3-bit `in_port`; all-or-nothing per slot |
| **Reduction** | **Tier A** | Gather + PE-local compute; allreduce adds broadcast; no router combine or DCA |
| **Violations** | Watchdog → demote to buffered XY escape VC | Never drop; multicast leaf expansion on demotion |

Trial 1's Tier B router-local combine and Tier C DCA are comparison-only alternatives. Architecture diagrams are required from the architecture team; this requirements package does not prescribe RTL.

---

## DCA Tier A / B / C — Recommendation Snapshot

| Tier | Mechanism | Allreduce m=1..5 (cycles) | Router area class | Trial-1 role |
|------|-----------|----------------------------:|-------------------|--------------|
| **A** | Gather + PE local compute + broadcast | 229–575 | +0% arithmetic | **Selected** — USER_CONFIRMED, area-first |
| **B** | Router 2-input int/bitwise merge | **101–228** | **~+2.7%** | Trial 1 selection; removed from Trial 2 router |
| **C** | DCA tile-FPU offload (12-cycle model) | 315–327 | ~+16.9% collective-wide | Comparison-only; no Trial 2 interface |

**Recommendation:** Implement Tier A only: gather operands to PE-local compute and broadcast the PE result for allreduce. Do not instantiate router combine, reduction tags/opcodes, or DCA hardware.

---

## Open Items for Phase 2

Five architecture decisions remain open (`open-requirements.json`); Phase 2 must resolve and document numeric/protocol details before Phase 3 interface freeze:

| ID | Topic | Candidates (summary) |
|----|-------|---------------------|
| OPEN-1-001 | Calendar storage/dispatch encoding | Slot table vs tag match vs source-routed header |
| OPEN-1-002 | Calendar/BG isolation | Dedicated VC vs hard TDM vs **hybrid (ADR-001 default)** |
| OPEN-1-003 | Buffering / flow control | Zero-cal + BG VC vs shallow shared vs full input-queued |
| OPEN-1-004 | Reduction tier / semantics | **Resolved toward Tier A**; close PE handoff/operation semantics only |
| OPEN-1-005 | Violation detection / recovery | Watchdog demotion vs slot validity vs retry-then-demote |

Additional Phase 2 closures from iron ambiguities: calendar load protocol and epoch handoff, credit/ready widths and buffer depths, numeric BG progress bound, watchdog timeout and recovery latency, and PE-compute handoff semantics. `io_definition.json` retains null widths — not an RTL port contract until Phase 2.

---

## Ambiguity Score and Gate Verdict

| Axis | Score | Weight |
|------|------:|-------:|
| Goal clarity | 0.14 | 40% |
| Constraint clarity | 0.36 | 30% |
| Acceptance-criteria clarity | 0.30 | 30% |
| **Weighted total** | **0.25** | (threshold **≤ 0.50**) |

**Gate: PASS.** Unresolved implementation choices are explicitly isolated in open items rather than silently assumed in the iron contract.

| Review | Verdict |
|--------|---------|
| `reviews/phase-1-research/research-review.md` | **PASS** — Trial 2 Tier A direction, 17/17 iron entries, and 5 open items correctly classified |
| `reviews/phase-1-research/feasibility-review.md` | **PASS (analytic)** — Tier A removes router reduction hardware; conditional on Phase 2 protocol closure and analytic area check |

**Phase 2 authorization:** Proceed to architecture design, including required diagrams. ADR-001 is ratified for Trial 2; close the remaining protocol details before Phase 3 μArch/BFM work. No Phase 4 authorization is implied.

---

## References

- Input spec: `docs/dse-input-spec.md`
- Decision record: `docs/decisions/ADR-001-algorithm-selection.md`
- Analysis: `docs/phase-1-research/domain-analysis.md`, `dca-tier-analysis.md`
- Baselines: `results/superpose_6x8.json`, `results/report_superpose_6x8.html`
