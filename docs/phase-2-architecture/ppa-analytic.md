# Analytic PPA — Arch-A2 CalSlot-Hybrid-ZB-NoCombine (Trial 2)

## Method and scope

Normalized to five-port 512-bit IQ XY router = **1.00**. Analytic only (no synthesis).
Full equations in [`ppa-workbook.md`](ppa-workbook.md) and `utils/ppa_analytic_model.py`.

Trial 1 audited Arch-A baseline for comparison: **area 1.065×, power 0.98×**.

| Assumption | Trial-2 value |
|---|---|
| SRAM / calendar | 2 × 1024 × 13-bit = 26,624 bits → 0.040 |
| Crossbar | 0.380 |
| VC-buffer flits | 5 × 20 = 100 flits → 0.365 |
| Multicast | +5.8% → 0.058 |
| Combine / DCA | **0.000** (Tier A) |
| Control | 0.185 (lean vs Trial 1 0.195) |

## Relative area and dynamic power

| Candidate | XB | Buf | Cal | MC | Comb/DCA | Ctrl | **Area** | **Power** | vs IQ-XY | vs Trial 1 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| Baseline IQ XY | 0.380 | 0.450 | 0 | 0 | 0 | 0.170 | 1.000 | 1.00 | — | — |
| Arch-A (Trial 1) | 0.380 | 0.365 | 0.040 | 0.058 | 0.027 | 0.195 | 1.065 | 0.98 | +6.5% / −2% | — |
| **Arch-A2 (Trial 2)** | 0.380 | 0.365 | 0.040 | 0.058 | **0.000** | **0.185** | **1.028** | **0.96** | **+2.8% / −4%** | **−3.5% area / −2% power** |
| Arch-B | 0.405 | 0.340 | 0.005 | 0.058 | 0 | 0.200 | 1.008 | 1.08 | +0.8% / +8% | Rejected (P0) |
| Arch-C | 0.380 | 0.400 | 0.040 | 0.058 | 0.169 | 0.190 | 1.237 | 1.23 | +23.7% / +23% | Rejected |

**Selected:** Arch-A2 at **1.028× area**, **0.96× power**.

## Makespan context (Tier A)

Tier A reduce ≈ gather; allreduce ≈ gather + PE compute + bcast (higher latency than
Trial 1 Tier B). Calendar overhead vs zero-buffer theory remains 0–5% after
window-aware recompilation (6.25% dense tax without recompilation). No combine
latency slots are reserved.

## Risk notes

- Area win vs Trial 1 is primarily −0.027 combine removal plus −0.010 control lean.
- Calendar depth/banks and BG FIFO depths intentionally **not** cut (P0 evidence).
- Arch-B’s lower area is not accepted because it sacrifices deterministic replay.
