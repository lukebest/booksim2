# Analytic PPA — Arch-A5 SparseCal-SharedPool-CalFork-ZB-NoCombine (Trial 5)

## Method and scope

Normalized to five-port 512-bit IQ XY router = **1.00**. Analytic only (no synthesis).
Equations in [`ppa-workbook.md`](ppa-workbook.md) and `utils/ppa_analytic_model.py`.

| Trial | Architecture | Area | Power |
|---|---|---:|---:|
| Trial 1 | Arch-A (dense + combine) | 1.065× | 0.98× |
| Trial 2 | Arch-A2 (dense, no combine) | 1.028× | 0.96× |
| Trial 3 | Arch-A3 (sparse, dedicated 100) | 1.000× | 0.95× |
| Trial 4 | Arch-A4 (sparse + SharedPool 50) | 0.822× | 0.92× |
| **Trial 5** | **Arch-A5 (CalFork + SharedPool 38)** | **0.746×** | **0.90×** |

| Assumption | Trial-5 value |
|---|---|
| SRAM / calendar | **2 × 128 × 23-bit = 5,888 bits → 0.009** |
| Crossbar | 0.380 |
| VC-buffer flits | **28 shared + 5×2 reserve = 38 → 0.139** |
| Multicast | **CalFork lean → 0.025** (was FlooNoC 0.058) |
| Combine / DCA | **0.000** (Tier A) |
| Control | **0.193** (match + pool accounting) |

## Relative area and dynamic power

| Candidate | XB | Buf | Cal | MC | Comb | Ctrl | **Area** | **Power** | vs IQ-XY | vs Trial 4 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| Baseline IQ XY | 0.380 | 0.450 | 0 | 0 | 0 | 0.170 | 1.000 | 1.00 | — | — |
| Arch-A4 (Trial 4) | 0.380 | 0.182 | 0.009 | 0.058 | 0 | 0.193 | 0.822 | 0.92 | −17.8% / −8% | — |
| **Arch-A5 (Trial 5)** | 0.380 | **0.139** | 0.009 | **0.025** | 0 | 0.193 | **0.746** | **0.90** | **−25.4% / −10%** | **−0.076 / −0.02** |
| CalFork-only (pool 40) | 0.380 | 0.182 | 0.009 | 0.025 | 0 | 0.193 | 0.789 | ~0.91 | — | CalFork alone |
| Pool 24+2 sensitivity | 0.380 | 0.124 | 0.009 | 0.025 | 0 | 0.193 | 0.731 | ~0.90 | — | Documented |

**Selected:** Arch-A5 at **0.746× area**, **0.90× power**.

## Lever deltas vs Trial 4

| Lever | Δ area | Notes |
|---|---:|---|
| CalFork (MC 0.058→0.025) | **−0.033** | Primary; calendar-native mask fork |
| SharedPool 50→38 flits | **−0.043** | Secondary; 28+2 default |
| **Net** | **−0.076** | 0.822 → **0.746** |

线性缓冲标定：`0.365 × (38/100) = 0.1387 → 0.139`。

## Pool sensitivity (CalFork fixed)

| Pool + reserve | Flits | Buf | Total |
|---|---:|---:|---:|
| 24+2 | 34 | 0.124 | **0.731** (RefC PASS; more aggressive) |
| **28+2 (default)** | **38** | **0.139** | **0.746** |
| 40+2 (A4 buffers) | 50 | 0.182 | 0.789 (CalFork-only) |

## BG latency bounds (12-hop)

| Policy | Cycles |
|---|---:|
| Hard 1-in-16 (conservative) | **328** |
| Soft-prio reserve-covered | **~160** |
| Soft + shared-pool contention | **~188** |

## Risk notes

- Remaining area mass dominated by **crossbar 0.380** (~51% of 0.746).
- Further pool shrink to 24 is RefC-safe but yields only −0.015 more area.
- Crossbar / datapath width cuts would be the next material lever (out of this trial’s scope).
- Synthesis remains out of scope.
