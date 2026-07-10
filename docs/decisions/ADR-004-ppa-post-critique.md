# ADR-004: PPA Recalibration After Self-Critique Buffer Fix

| Field | Value |
|---|---|
| **Status** | Accepted (Trial-1 default; pending user confirmation) |
| **Date** | 2026-07-10 |
| **Decision source** | `AGENT_ASSUMED` — critique-driven correction; subject to user confirmation |
| **Related analysis** | [ppa-workbook.md](../phase-2-architecture/ppa-workbook.md), `utils/ppa_analytic_model.py` |
| **Prior decision** | [ADR-002](ADR-002-architecture-selection.md) |

---

## Context

Self-critique finding **MEDIUM-02** identified inconsistent buffer accounting:
documentation alternated between 74-flit interior bounds and a 100-flit
five-FIFO implementation (5 × 20 flits = 51,200 bits per interior router).
The PPA model used the lower figure while μArch specified the higher
implementation depth.

---

## Change

| Metric | Pre-critique (ADR-002 gate) | Post-critique (Trial 1 final) |
|---|---:|---:|
| **area_rel** | 0.970 | **1.065** |
| **power_rel** | — | **0.98** (unchanged ranking driver) |
| Buffer model | 74-flit interior bound | 5 × 20-flit per-input FIFOs (100 flits) |
| BG bound | 212 cycles (under-counted) | **328 cycles** (eligible 12-hop formula) |

The area delta (+9.8% relative to the pre-fix estimate, +6.5% vs baseline 1.00)
comes from reconciling buffer bitcell contribution with the implementation
described in `docs/phase-3-uarch/uarch.md` and `docs/phase-2-architecture/iron-requirements.json` REQ-A-003.

---

## Recommendation

**Arch-A CalSlot-Hybrid-ZB remains the recommended Trial-1 architecture.**

The buffer-consistency fix moves area from 0.970 to 1.065 but does not change
the P0–P2 ranking: Arch-A still leads on combined makespan, robustness, and
power/area among P0-capable candidates. Tier-B selection and the 328-cycle BG
service bound are unchanged in substance.

---

## User action

Confirm or override the recalibrated PPA figures at the Trial-1 satisfaction
check. If absolute area budget is binding below 1.065× baseline, revisit buffer
depth policy (REQ-A-003) before RTL freeze.
