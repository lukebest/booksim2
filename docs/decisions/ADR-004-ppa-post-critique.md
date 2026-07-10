# ADR-004: PPA Post-Critique / Trial 4 SharedPool Area Reduction

| Field | Value |
|---|---|
| **Status** | Accepted (Trial 4) |
| **Date** | 2026-07-10 |
| **Decision source** | `USER_CONFIRMED` SharedPool-BG + `SPEC_DERIVED` analytic model |
| **Related** | [ppa-analytic.md](../phase-2-architecture/ppa-analytic.md), ADR-002, ADR-003 |

---

## Context

Trial 3 locked Arch-A3 at **1.000×** with SparseCal calendar 0.009 and dedicated
BG buffers 0.365. The remaining area lever identified in Trial 3 was SharedPool-BG
(deferred as Trial 3b). Trial 4 executes that lever.

ADR-003 (Tier A) substance is **unchanged** — no combine, no DCA.

---

## Decision

1. Use Trial 3 **1.000×** as the immediate comparison baseline.
2. Arch-A4 area = `0.380 + 0.182 + 0.009 + 0.058 + 0.000 + 0.193 = 0.822`.
3. Buffer flits: **40 shared + 5×2 reserve = 50**; area =
   `0.365 × (50/100) = 0.182` (rounded).
4. Control **+0.005** for shared-pool free-list / reserve accounting
   (0.188 → 0.193).
5. Power estimate **0.92×** (fewer buffer bits switching).
6. Alternative 48+2 (~58 flits, ~0.852 total) documented but not selected.

| Delta vs Trial 3 | Value |
|---|---|
| Dedicated 100 → shared 50 flits | −0.183 |
| Shared-pool control | +0.005 |
| **Net area** | **−0.178** → **0.822×** |
| **Net power** | **−0.03** → **0.92×** |

### Target check

| Metric | Target | Achieved |
|---|---|---|
| Buffer area | 0.15–0.22 | **0.182** |
| Total area vs IQ-XY | ~0.85–0.92 | **0.822** (better) |

---

## Consequences

`utils/ppa_analytic_model.py` prints Arch-A4 defaults (SharedPool 40+2,
buffers=0.182, control=0.193, power=0.92). Synthesis remains out of scope.
