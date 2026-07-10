# Algorithm Selection — DSE Trial 2

**Date:** 2026-07-10  
**Status:** Accepted for Trial 2
**Decision source:** `USER_CONFIRMED`
**Formal record:** [ADR-001-algorithm-selection.md](../decisions/ADR-001-algorithm-selection.md)

---

## Binding priorities

| Priority | Goal |
|---|---|
| P0 | Functional correctness and robustness (REQ-CAL, REQ-BG, REQ-MC, REQ-ROB) |
| P1 | Area-first: relative router area below 1.065× baseline |
| P2 | Minimize makespan overhead vs zero-buffer schedule baselines |

---

## Selected stack

| Block | Selection |
|---|---|
| **Calendar** | Double-buffered per-router timeslot table (1,024 slots × 13 bits; 3.25 KiB/router) |
| **Isolation** | Hybrid TDM windows + buffered XY BG VC (1 BG slot per 16 cycles, 6.25%) |
| **Buffering** | Zero-buffer calendar + RTT-sized BG buffers (≤4.6 KiB interior) |
| **Multicast** | Calendar `out_port_mask` atomic fork (+5.8% area class) |
| **Reduction** | Tier A: gather + PE-local compute (+ broadcast for allreduce); no router combine or DCA |
| **Violations** | Watchdog demotion to buffered XY escape VC; never drop |

---

## Rationale summary

Selections follow [domain-analysis.md](domain-analysis.md) recommendations evaluated under P0 → P1 → P2. The stack preserves exact zero-buffer calendar replay, guarantees finite BG progress, and removes Trial 1's Tier B arithmetic datapath so the selected router targets area below 1.065× baseline. The A/B/C comparison remains in [dca-tier-analysis.md](dca-tier-analysis.md); Tier A is USER_CONFIRMED for Trial 2 despite its higher modeled allreduce makespan.

---

## Alternatives rejected (brief)

- Per-flow tag match and source-routed headers (calendar storage)
- Strict calendar priority and hard TDM (isolation)
- Shallow shared and full input-queued buffers (buffering)
- XY address-mask fork and software multi-unicast (multicast)
- Trial 1's Tier B router-local combine and Tier C DCA (reduction)
- NACK/retry and indefinite calendar stall (violations)

---

## Quality-gate note

This document mirrors ADR-001 for Phase 1→2 traceability. Tier A and the area-first direction are binding Trial 2 decisions; architecture diagrams remain a Phase 2 responsibility. No Phase 4 work is authorized.
