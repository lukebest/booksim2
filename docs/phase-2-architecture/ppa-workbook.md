# Analytic PPA Workbook — Trial 4 Arch-A4

## Component equations

| Component | Formula / value |
|---|---|
| Crossbar | 0.380 (common 5×512b) |
| Buffers (Trial 3) | 0.365 for 100 flits dedicated |
| Buffers (Trial 4) | `0.365 × (50/100) = 0.182` for SharedPool 40+5×2 |
| Calendar (sparse) | `K_ctrl × 2 × 128 × 23` → **0.009** |
| Multicast | FlooNoC +5.8% → 0.058 |
| Combine | **0.000** (Tier A) |
| Control | **0.193** = 0.188 + 0.005 (shared-pool accounting) |
| **Total area** | **0.822** |
| **Power** | **0.92×** |

### SharedPool derivation

```
flits_T3 = 100
flits_T4 = 40 + 5×2 = 50
buffers_T4 = 0.365 × (50/100) = 0.1825 → 0.182
control_T4 = 0.188 + 0.005 = 0.193
area_T4 = 0.380 + 0.182 + 0.009 + 0.058 + 0.000 + 0.193 = 0.822
delta vs T3 = 0.822 − 1.000 = −0.178
```

### Alternative (not selected)

```
pool 48 + reserve 5×2 = 58 flits
buffers ≈ 0.212
area ≈ 0.380+0.212+0.009+0.058+0.193 = 0.852
```

### BG bounds

| Bound | Cycles | Formula notes |
|---|---:|---|
| Hard TDM | 328 | `2+5×(16+3+7)+7×(16+3+9)` |
| Soft reserve-covered | 160 | occupancy-aware wait≈2 |
| Soft+pool | 200 | soft + ≤40 pool turnover |

Reproduce: `python3 utils/ppa_analytic_model.py [--sensitivity]`
