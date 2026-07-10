# ADR-002: Architecture Selection for DSE Trial 3

| Field | Value |
|---|---|
| **Status** | Accepted (Trial 3, USER_CONFIRMED) |
| **Date** | 2026-07-10 |
| **Decision source** | `USER_CONFIRMED` — SparseCal + soft-prio + Tier A binding feedback |
| **Related analysis** | [architecture-candidates.md](../phase-2-architecture/architecture-candidates.md), [ppa-analytic.md](../phase-2-architecture/ppa-analytic.md) |
| **Prior decisions** | [ADR-001](ADR-001-algorithm-selection.md), [ADR-003](ADR-003-dca-tier.md), Trial 2 ADR-002 (superseded) |
| **Input spec** | [dse-input-spec.md](../dse-input-spec.md) |

---

## Context

Trial 2 selected Arch-A2 CalSlot-Hybrid-ZB-NoCombine at analytic **1.028×** area with
dense `2×1024×13` calendar SRAM and hard 1-in-16 BG TDM. Sparsity evidence from
`results/calendars/*_m1.json` shows calendar occupancy ≪1% of dense address space
(allreduce max 49 entries/router, max_slot 951). Trial 3 binding feedback requires
(1) exploit sparsity for IQ-XY area parity, (2) soft-priority BG, (3) retain Tier A.

---

## Decision

Select **Arch-A3: SparseCal-Hybrid-ZB-NoCombine**.

| Metric | Trial 3 Arch-A3 | Trial 2 Arch-A2 | Trial 1 Arch-A | IQ-XY |
|---|---:|---:|---:|---:|
| Relative area | **1.000** | 1.028 | 1.065 | 1.000 |
| Relative power | **0.95** | 0.96 | 0.98 | 1.00 |
| Calendar store | **Sparse 2×128×23** | Dense 2×1024×13 | Dense 2×1024×13 | — |
| BG policy | **Soft priority** | Hard 1-in-16 | Hard 1-in-16 | — |
| Combine / DCA | **Absent** | Absent | Tier B | — |

Rejected: Arch-B (area ~1.008 but breaks deterministic ZB calendar replay); Arch-C
(DCA + area). Trial 2 Arch-A2 superseded as immediate prior.

### Key deltas from Trial 2

1. Replace dense per-router SRAM with **sparse ordered event list** (`slot` explicit in 23-bit entry).
2. Depth **128** per bank (covers max 49 observed with >2× margin).
3. **next-event match** dispatch on global slot counter (wrap 1024).
4. **Soft priority:** calendar on match; BG on idle cycles; conservative hard bound 328 cy retained; soft bound ~160 cy.

---

## Consequences

- `calendar_store` μArch changes from slot-indexed dense read to sparse next-event match.
- Diagrams in `architecture-diagram.md` / `uarch-diagram.md` updated for Trial 3.
- `iron-requirements.json` REQ-A/U updated; trial field = 3.
- Phase 3 μArch mirrors Arch-A3; no Phase 4 in this trial.
- Shared BG buffer pool deferred to Trial 3b (out of scope).
