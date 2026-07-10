# ADR-002: Architecture Selection for DSE Trial 2

| Field | Value |
|---|---|
| **Status** | Accepted (Trial 2, USER_CONFIRMED) |
| **Date** | 2026-07-10 |
| **Decision source** | `USER_CONFIRMED` — area-first + Tier A binding feedback |
| **Related analysis** | [architecture-candidates.md](../phase-2-architecture/architecture-candidates.md), [ppa-analytic.md](../phase-2-architecture/ppa-analytic.md) |
| **Prior decision** | [ADR-001](ADR-001-algorithm-selection.md), [ADR-003](ADR-003-dca-tier.md) |
| **Input spec** | [dse-input-spec.md](../dse-input-spec.md) |

---

## Context

Trial 1 selected Arch-A CalSlot-Hybrid-ZB at analytic **1.065×** area with Tier-B
combine. Trial 2 binding feedback requires (1) area below 1.065× and (2) no in-router
reduction. Candidates were re-scored under those constraints.

---

## Decision

Select **Arch-A2: CalSlot-Hybrid-ZB-NoCombine**.

| Metric | Trial 2 Arch-A2 | Trial 1 Arch-A | IQ-XY |
|---|---:|---:|---:|
| Relative area | **1.028** | 1.065 | 1.000 |
| Relative power | **0.96** | 0.98 | 1.00 |
| Combine / DCA | **Absent** | Tier B | — |

Rejected: Arch-B (area ~1.008 but breaks deterministic ZB calendar replay); Arch-C
(DCA + area).

---

## Consequences

- `combine_unit` removed from architecture, RefC, and BFM.
- Diagrams in `architecture.md` / `architecture-diagram.md` must show absence of combine/DCA.
- Phase 3 μArch mirrors Arch-A2; no Phase 4 in this trial.
