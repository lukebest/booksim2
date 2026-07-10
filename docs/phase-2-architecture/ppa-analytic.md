# Analytic PPA — Arch-A3 SparseCal-Hybrid-ZB-NoCombine (Trial 3)

## Method and scope

Normalized to five-port 512-bit IQ XY router = **1.00**. Analytic only (no synthesis).
Full equations in [`ppa-workbook.md`](ppa-workbook.md) and `utils/ppa_analytic_model.py`.

| Trial | Architecture | Area | Power |
|---|---|---:|---:|
| Trial 1 | Arch-A (dense + combine) | 1.065× | 0.98× |
| Trial 2 | Arch-A2 (dense, no combine) | 1.028× | 0.96× |
| **Trial 3** | **Arch-A3 (sparse, no combine)** | **1.000×** | **0.95×** |

| Assumption | Trial-3 value |
|---|---|
| SRAM / calendar | **2 × 128 × 23-bit = 5,888 bits → 0.009** |
| Crossbar | 0.380 |
| VC-buffer flits | 5 × 20 = 100 flits → 0.365 |
| Multicast | +5.8% → 0.058 |
| Combine / DCA | **0.000** (Tier A) |
| Control | **0.188** (Trial 2 0.185 + 0.003 next-event match) |

## Relative area and dynamic power

| Candidate | XB | Buf | Cal | MC | Comb/DCA | Ctrl | **Area** | **Power** | vs IQ-XY | vs Trial 2 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| Baseline IQ XY | 0.380 | 0.450 | 0 | 0 | 0 | 0.170 | 1.000 | 1.00 | — | — |
| Arch-A (Trial 1) | 0.380 | 0.365 | 0.040 | 0.058 | 0.027 | 0.195 | 1.065 | 0.98 | +6.5% / −2% | — |
| Arch-A2 (Trial 2) | 0.380 | 0.365 | 0.040 | 0.058 | 0.000 | 0.185 | 1.028 | 0.96 | +2.8% / −4% | — |
| **Arch-A3 (Trial 3)** | 0.380 | 0.365 | **0.009** | 0.058 | **0.000** | **0.188** | **1.000** | **0.95** | **0.0% / −5%** | **−0.028 / −0.01** |
| Arch-B | 0.405 | 0.340 | 0.005 | 0.058 | 0 | 0.200 | 1.008 | 1.08 | +0.8% / +8% | Rejected (P0) |
| Arch-C | 0.380 | 0.400 | 0.040 | 0.058 | 0.169 | 0.190 | 1.237 | 1.23 | +23.7% / +23% | Rejected |

**Selected:** Arch-A3 at **1.000× area**, **0.95× power**.

## Calendar area delta (dense → sparse)

| Organization | Bits/router | Rel. area |
|---|---:|---:|
| Dense 2×1024×13 (Trial 2) | 26,624 | 0.040 |
| **Sparse 2×128×23 (Trial 3)** | **5,888** | **0.009** |
| Reduction | −20,736 bits (−78%) | **−0.031** |

稀疏日历存储从 26,624 bit 降至 5,888 bit，面积类从 0.040 降至 0.009；控制增加 0.003
用于 next-event 匹配逻辑，净面积收益 −0.028 vs Trial 2。

## Makespan context (Tier A)

Tier A reduce ≈ gather; allreduce ≈ gather + PE compute + bcast. Calendar overhead vs
zero-buffer theory remains 0–5% after window-aware recompilation. Soft-priority BG
improves progress bound from conservative 328 cycles to ~160 cycles occupancy-aware.

## Risk notes

- Area win vs Trial 2 is primarily −0.031 calendar SRAM plus +0.003 match control.
- Depth 128 validated against max 49 entries/router (allreduce m=1); margin >2×.
- BG FIFO depths intentionally **not** cut (P0 credit RTT).
- Shared BG buffer pool deferred to Trial 3b (out of scope).
