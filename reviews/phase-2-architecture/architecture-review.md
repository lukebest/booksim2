# Phase 2 Architecture Review — Trial 5

**Architecture:** Arch-A5 SparseCal-SharedPool-CalFork-ZB-NoCombine  
**Date:** 2026-07-13  
**Verdict:** **PASS**

## Checks

| Gate item | Result |
|---|---|
| architecture.md + diagrams present | PASS |
| architecture-candidates.md quantitative matrix | PASS |
| ADR-002 / ADR-004 / ADR-005 recorded | PASS |
| iron-requirements.json REQ-A-* (incl. CalFork REQ-A-007) | PASS |
| P0 preserved (ZB cal, XY BG, demote, Tier A) | PASS |
| Analytic area 0.746 < A4 0.822 and in ~0.75–0.79 band | PASS |
| No Phase 4 RTL | PASS |

## Findings

- None blocking. Pool 24 sensitivity documented; default 28 retained for margin.
- Remaining area dominated by crossbar (0.380).

## Verdict

**PASS** — ready for Phase 3 μArch/BFM gate.
