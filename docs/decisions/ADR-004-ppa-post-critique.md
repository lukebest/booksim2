# ADR-004: PPA Post-Critique / Trial 3 SparseCal Area Reduction

| Field | Value |
|---|---|
| **Status** | Accepted (Trial 3) |
| **Date** | 2026-07-10 |
| **Decision source** | `USER_CONFIRMED` SparseCal + `SPEC_DERIVED` analytic model |
| **Related** | [ppa-analytic.md](../phase-2-architecture/ppa-analytic.md), ADR-002, ADR-003 |

---

## Context

Trial 2 locked Arch-A2 at **1.028×** with dense calendar SRAM (0.040 area class).
Sparsity measurements from `results/calendars/*_m1.json` justify replacing dense
`2×1024×13` storage with sparse `2×128×23` event lists without sacrificing P0 replay
fidelity. Trial 3 targets **IQ-XY parity (1.000×)**.

ADR-003 (Tier A) substance is **unchanged** — no combine, no DCA.

---

## Decision

1. Use Trial 2 **1.028×** as the immediate comparison baseline.
2. Arch-A3 area = `0.380 + 0.365 + 0.009 + 0.058 + 0.000 + 0.188 = 1.000`.
3. Sparse calendar area derived via calibrated `K_ctrl` from dense anchor:
   `K_ctrl = 0.040 / (2×1024×13)`; sparse = `K_ctrl × 2×128×23 = 0.009`.
4. Control increases **+0.003** for next-event match logic (net −0.028 vs Trial 2).
5. Power estimate **0.95×** (sparse SRAM leakage reduction + no combine switching).
6. Do **not** cut BG FIFO RTT depth without new evidence.

| Delta vs Trial 2 | Value |
|---|---|
| Dense → sparse calendar | −0.031 |
| Next-event match control | +0.003 |
| **Net area** | **−0.028** → **1.000×** |
| **Net power** | **−0.01** → **0.95×** |

### Sparsity evidence (binding)

| Collective | Total entries | Max/router | Max slot |
|---|---:|---:|---:|
| allreduce m=1 | 384 | **49** | **951** |
| gather/reduce m=1 | 336 | 48 | 851 |
| allgather m=1 | 192 | 4 | 699 |
| broadcast m=1 | 48 | 1 | 99 |

Depth 128 selected: >2× margin over max 49; counter wrap 1024 still safe.

---

## Consequences

PPA workbook and `utils/ppa_analytic_model.py` print Arch-A3 defaults
(`sparse 2×128×23`, `calendar=0.009`, `control=0.188`, `combine_delta=0`).
Synthesis remains out of scope for this DSE trial.
