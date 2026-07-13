# ADR-004: PPA Post-Critique / Trial 5 CalFork + Aggressive SharedPool

| Field | Value |
|---|---|
| **Status** | Accepted (Trial 5) |
| **Date** | 2026-07-13 |
| **Decision source** | `USER_CONFIRMED` CalFork + SharedPool + `SPEC_DERIVED` analytic model |
| **Related** | [ppa-analytic.md](../phase-2-architecture/ppa-analytic.md), ADR-002, ADR-003, ADR-005 |

---

## Context

Trial 4 locked Arch-A4 at **0.822×** / **0.92×**. Trial 5 applies CalFork and
pool shrink while ADR-003 (Tier A) substance remains unchanged.

---

## Decision

1. Use Trial 4 **0.822×** as the immediate comparison baseline.
2. Arch-A5 area = `0.380 + 0.139 + 0.009 + 0.025 + 0.000 + 0.193 = 0.746`.
3. Buffer flits: **28 shared + 5×2 reserve = 38**; area =
   `0.365 × (38/100) = 0.139` (rounded).
4. Multicast: **CalFork 0.025** (was FlooNoC 0.058); Δ = **−0.033**.
5. Control remains **0.193**.
6. Power estimate **0.90×**.
7. Sensitivity: pool 24+2 → total **0.731**; CalFork-only (pool 40) → **0.789**.

| Delta vs Trial 4 | Value |
|---|---|
| CalFork MC | −0.033 |
| Shared 50 → 38 flits | −0.043 |
| **Net area** | **−0.076** → **0.746×** |
| **Net power** | **−0.02** → **0.90×** |

### Target check

| Metric | Target | Achieved |
|---|---|---|
| Beat A4 area | < 0.822 | **0.746** |
| Band | ~0.75–0.79 | **0.746** (meets / slightly better) |

---

## Consequences

`utils/ppa_analytic_model.py` prints Arch-A5 defaults. Synthesis out of scope.
