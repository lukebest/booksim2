# Phase 1 Ambiguity Assessment — DSE Trial 1

- Date: 2026-07-10
- Scope: 6×8 calendar-collective NoC router
- Gate criterion: weighted ambiguity score must be <= 0.50 for Phase 1 -> 2 passage
- Result: **PASS — 0.30**

## Method

Scores measure ambiguity (0.00 = explicit and testable; 1.00 = missing or contradictory).
The weighted score is `0.40 * goal + 0.30 * constraints + 0.30 * acceptance criteria`.
Unknown implementation details do not make an iron requirement ambiguous when its acceptance
criterion explicitly requires Phase 2 to select and document the missing numeric or protocol
parameter.

| Axis | Score | Weight | Evidence |
|---|---:|---:|---|
| Goal clarity | 0.18 | 40% | Topology, collective set, 512-bit links, and single-network scope are explicit in the input spec. |
| Constraint clarity | 0.42 | 30% | Link/ramp timing and bandwidth are explicit; header layout, VC count, buffers, watchdog, and reduction formats remain Phase 2 work. |
| Acceptance-criteria clarity | 0.34 | 30% | All iron entries now have measurable criteria; selected-architecture documentation is required where the source omits a numeric/protocol choice. |
| **Overall** | **0.30** | **100%** | `0.40*0.18 + 0.30*0.42 + 0.30*0.34 = 0.30` |

## Per-requirement assessment

| Requirement | Score | Gate note |
|---|---:|---|
| REQ-F-001 | 0.42 | Calendar content is defined; storage/loading/epoch format stays in OPEN-1-001. |
| REQ-F-002 | 0.24 | Legal-schedule fork delivery and output exclusivity are observable. |
| REQ-F-003 | 0.31 | Gather delivery is measurable; combine behavior is conditional on the selected tier. |
| REQ-F-004 | 0.47 | Reduction semantics are deliberately deferred, but a selected architecture must define them before RTL acceptance. |
| REQ-F-005 | 0.44 | XY order is explicit; Phase 2 must select a numeric background-service bound. |
| REQ-F-006 | 0.36 | Atomic replication is testable; mask encoding remains OPEN-1-001. |
| REQ-F-007 | 0.43 | Deadlock proof and stress completion are measurable; isolation implementation remains OPEN-1-002. |
| REQ-F-008 | 0.35 | No-loss/occupancy behavior is measurable; credit versus ready/valid stays open. |
| REQ-F-009 | 0.48 | Recovery behavior is defined; Phase 2 must select numeric watchdog and recovery bounds. |
| REQ-F-010 | 0.25 | Required A/B/C comparison scope and reporting fields are explicit. |
| REQ-F-011 | 0.46 | Conditional DCA interface remains optional and protocol widths are not frozen. |
| REQ-F-012 | 0.33 | BFM outcome is measurable; concrete API/file encoding is Phase 3 work. |
| REQ-P-001 | 0.12 | 512-bit, 1 flit/cycle, 2 GHz, and 128 GB/s are explicit. |
| REQ-P-002 | 0.19 | Clock and analytic link/ramp values are explicit; independent router latency is intentionally not asserted. |
| REQ-P-003 | 0.27 | Baseline vectors and overhead formula are explicit; no pass/fail overhead ceiling was supplied. |
| REQ-P-004 | 0.36 | Required analytic assumptions and calibration anchors are explicit; absolute PPA target is intentionally absent. |
| REQ-P-005 | 0.42 | DCA reporting is measurable, but literature deltas are calibration references rather than binding process targets. |

## Required Phase 2 closures

The following remain architecture choices, not Phase 1 blockers: calendar encoding (OPEN-1-001),
mixed-traffic isolation (OPEN-1-002), buffering/flow-control parameters (OPEN-1-003),
reduction tier/semantics (OPEN-1-004), and watchdog recovery details (OPEN-1-005).
Before a Phase 2 architecture is frozen, it must turn each relevant selection into exact interface
widths, numeric bounds, and testable Phase 3 criteria.

## Gate decision

**PASS.** The settled requirements have ambiguity scores at or below 0.50. The unresolved
implementation choices are explicitly isolated in the open-requirements handoff rather than being
silently assumed by the iron contract.
