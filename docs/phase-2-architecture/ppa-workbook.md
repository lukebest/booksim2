# Analytic PPA Workbook — Trial 2 Arch-A2

## Component equations

| Component | Formula / value |
|---|---|
| Crossbar | 0.380 (common 5×512b) |
| Buffers | 0.365 for 100 flits BG/escape (audited Trial 1 model) |
| Calendar | `2 × 1024 × 13 / 26624 × 0.040 = 0.040` |
| Multicast | FlooNoC +5.8% → 0.058 |
| Combine | **0.000** (Tier A) |
| Control | 0.185 = Trial1 0.195 − 0.010 (drop combine control) |
| **Total area** | **1.028** |
| **Power** | **0.96×** |

## Trial comparison

| | Area | Power |
|---|---:|---:|
| IQ-XY | 1.000 | 1.00 |
| Trial 1 Arch-A | 1.065 | 0.98 |
| Trial 2 Arch-A2 | 1.028 | 0.96 |
| Δ Trial2−Trial1 | −0.037 (−3.5%) | −0.02 |

## Reproduce

```bash
python3 utils/ppa_analytic_model.py
python3 utils/ppa_analytic_model.py --sensitivity
```

## Non-cuts (P0)

- Calendar depth/banks: max_slot up to 951 in m=1 exports
- BG FIFO 20 flits: vertical credit RTT
