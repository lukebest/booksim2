# ADR-004: PPA Post-Critique / Trial 2 Area Reduction

| Field | Value |
|---|---|
| **Status** | Accepted (Trial 2) |
| **Date** | 2026-07-10 |
| **Decision source** | `USER_CONFIRMED` area-first + `SPEC_DERIVED` analytic model |
| **Related** | [ppa-analytic.md](../phase-2-architecture/ppa-analytic.md), ADR-002, ADR-003 |

---

## Context

Trial 1 critique locked the audited Arch-A area at **1.065×** (100-flit BG model +
combine). Trial 2 must beat that number without breaking P0.

---

## Decision

1. Use Trial 1 audited **1.065×** as the comparison baseline (not the older 0.970 figure).
2. Arch-A2 area = `0.380 + 0.365 + 0.040 + 0.058 + 0.000 + 0.185 = 1.028`.
3. Do **not** cut calendar depth/banks or BG FIFO RTT depth without new evidence.
4. Power estimate **0.96×** (remove combine switching; slight control lean).

| Delta vs Trial 1 | Value |
|---|---|
| Remove combine | −0.027 |
| Lean control | −0.010 |
| **Net area** | **−0.037 (−3.5%)** → **1.028×** |

---

## Consequences

PPA workbook and `utils/ppa_analytic_model.py` must print Arch-A2 defaults
(`combine_delta=0`, `control=0.185`). Synthesis remains out of scope for this DSE trial.
