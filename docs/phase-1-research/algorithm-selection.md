# Algorithm Selection — DSE Trial 1

**Date:** 2026-07-10  
**Status:** Trial-1 default (pending user confirmation)  
**Decision source:** `AGENT_ASSUMED`  
**Formal record:** [ADR-001-algorithm-selection.md](../decisions/ADR-001-algorithm-selection.md)

---

## Binding priorities

| Priority | Goal |
|---|---|
| P0 | Functional correctness and robustness (REQ-CAL, REQ-BG, REQ-MC, REQ-ROB) |
| P1 | Minimize router power and area |
| P2 | Minimize makespan overhead vs zero-buffer schedule baselines |

---

## Selected stack

| Block | Selection |
|---|---|
| **Calendar** | Double-buffered per-router timeslot table (1,024 slots × 13 bits; 3.25 KiB/router) |
| **Isolation** | Hybrid TDM windows + buffered XY BG VC (1 BG slot per 16 cycles, 6.25%) |
| **Buffering** | Zero-buffer calendar + RTT-sized BG buffers (≤4.6 KiB interior) |
| **Multicast** | Calendar `out_port_mask` atomic fork (+5.8% area class) |
| **Reduction** | Tier B: router-local 2-input integer/bitwise combine (+2.7%); Tier A fallback for FP/unsupported ops; Tier C DCA deferred |
| **Violations** | Watchdog demotion to buffered XY escape VC; never drop |

---

## Rationale summary

Selections follow [domain-analysis.md](domain-analysis.md) recommendations evaluated under P0 → P1 → P2. The stack preserves exact zero-buffer calendar replay, guarantees finite BG progress, minimizes router area vs full DCA (+16.9%), and retains lowest tested allreduce makespan for m=1..5 via Tier B reduction. DCA analysis in [dca-tier-analysis.md](dca-tier-analysis.md) confirms Tier C is architecturally valuable but not justified for Trial-1 message sizes.

---

## Alternatives rejected (brief)

- Per-flow tag match and source-routed headers (calendar storage)
- Strict calendar priority and hard TDM (isolation)
- Shallow shared and full input-queued buffers (buffering)
- XY address-mask fork and software multi-unicast (multicast)
- DCA as primary reduction path (reduction)
- NACK/retry and indefinite calendar stall (violations)

---

## Quality-gate note

This document mirrors ADR-001 for Phase 1→2 traceability. User confirmation at Trial satisfaction check is required to elevate `AGENT_ASSUMED` to a binding project decision.
