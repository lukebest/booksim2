# Analytic PPA — Arch-A4 SparseCal-SharedPool-ZB-NoCombine (Trial 4)

## Method and scope

Normalized to five-port 512-bit IQ XY router = **1.00**. Analytic only (no synthesis).
Equations in [`ppa-workbook.md`](ppa-workbook.md) and `utils/ppa_analytic_model.py`.

| Trial | Architecture | Area | Power |
|---|---|---:|---:|
| Trial 1 | Arch-A (dense + combine) | 1.065× | 0.98× |
| Trial 2 | Arch-A2 (dense, no combine) | 1.028× | 0.96× |
| Trial 3 | Arch-A3 (sparse, dedicated 100) | 1.000× | 0.95× |
| **Trial 4** | **Arch-A4 (sparse + SharedPool 50)** | **0.822×** | **0.92×** |

| Assumption | Trial-4 value |
|---|---|
| SRAM / calendar | **2 × 128 × 23-bit = 5,888 bits → 0.009** |
| Crossbar | 0.380 |
| VC-buffer flits | **40 shared + 5×2 reserve = 50 → 0.182** |
| Multicast | +5.8% → 0.058 |
| Combine / DCA | **0.000** (Tier A) |
| Control | **0.193** (0.188 + 0.005 pool accounting) |

## Relative area and dynamic power

| Candidate | XB | Buf | Cal | MC | Comb | Ctrl | **Area** | **Power** | vs IQ-XY | vs Trial 3 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| Baseline IQ XY | 0.380 | 0.450 | 0 | 0 | 0 | 0.170 | 1.000 | 1.00 | — | — |
| Arch-A3 (Trial 3) | 0.380 | 0.365 | 0.009 | 0.058 | 0 | 0.188 | 1.000 | 0.95 | 0% / −5% | — |
| **Arch-A4 (Trial 4)** | 0.380 | **0.182** | 0.009 | 0.058 | 0 | **0.193** | **0.822** | **0.92** | **−17.8% / −8%** | **−0.178 / −0.03** |

**Selected:** Arch-A4 at **0.822× area**, **0.92× power**.

## Buffer area delta (dedicated → shared)

| Organization | Flits | Rel. area |
|---|---:|---:|
| Dedicated 5×20 (Trial 3) | 100 | 0.365 |
| **Shared 40 + reserve 5×2 (Trial 4)** | **50** | **0.182** |
| Reduction | −50 flits (−50%) | **−0.183** |

线性标定：`0.365 × (50/100) = 0.1825 → 0.182`。控制 +0.005 用于共享池空闲表/预留记账。
净面积相对 Trial 3：**−0.178**。

目标核对：buffer **0.182 ∈ [0.15, 0.22]**；total **0.822** 优于目标带 **[0.85, 0.92]**（面积更低）。

## Why not 48+2?

Alternative pool 48 + reserve 2 → 58 flits ≈ buffer 0.212 / total ~0.852。
Default **40+2** already satisfies deadlock freedom (reserve + XY-DOR) and
progress bounds; larger pool reserved only if future stress evidence requires it.

## BG latency bounds (12-hop)

| Policy | Cycles |
|---|---:|
| Hard 1-in-16 (conservative) | **328** |
| Soft-prio reserve-covered | **~160** |
| Soft + shared-pool contention | **~200** |

## Risk notes

- Shared pool reduces worst-case per-port depth; reserves prevent starvation.
- Calendar path remains zero-buffer and pool-independent.
- Demote→XY still lossless via pool/reserves.
- Synthesis remains out of scope.
