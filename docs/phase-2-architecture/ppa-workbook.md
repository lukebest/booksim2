# Analytic PPA Workbook — Trial 3 Arch-A3

## Component equations

| Component | Formula / value |
|---|---|
| Crossbar | 0.380 (common 5×512b) |
| Buffers | 0.365 for 100 flits BG/escape (audited Trial 1/2 model) |
| Calendar (sparse) | `K_ctrl × 2 × 128 × 23` where `K_ctrl = 0.040 / (2 × 1024 × 13) ≈ 1.50×10⁻⁶` → **0.009** |
| Calendar (dense ref) | `2 × 1024 × 13 / 26624 × 0.040 = 0.040` (Trial 2) |
| Multicast | FlooNoC +5.8% → 0.058 |
| Combine | **0.000** (Tier A) |
| Control | **0.188** = Trial2 0.185 + 0.003 (next-event match) |
| **Total area** | **1.000** |
| **Power** | **0.95×** |

### Sparse calendar derivation

```
K_ctrl = DENSE_CAL_AREA / (DENSE_BANKS × DENSE_SLOTS × DENSE_ENTRY_BITS)
       = 0.040 / (2 × 1024 × 13)
       ≈ 1.502×10⁻⁶ per bit

sparse_cal = K_ctrl × SPARSE_BANKS × SPARSE_DEPTH × SPARSE_EVENT_BITS
           = K_ctrl × 2 × 128 × 23
           ≈ 0.009

delta_cal  = sparse_cal − dense_cal = 0.009 − 0.040 = −0.031
delta_ctrl = +0.003 (next-event / CAM-like compare)
net vs T2  = −0.031 + 0.003 = −0.028 → 1.028 − 0.028 = 1.000
```

### Event entry width (23 bits)

| Field | Width | Notes |
|---|---:|---|
| `slot` | 10 | Global cycle index; counter wraps 1024 |
| `valid` | 1 | Entry active |
| `in_port` | 3 | N/E/S/W/local |
| `out_port_mask` | 5 | Atomic multicast fork |
| `opcode` | 4 | FORWARD / PE_HANDOFF / reserved |
| **Total** | **23** | Packed per sparse entry (slot explicit, not index) |

Dense Trial 2 entry was 13 bits (slot implicit as SRAM address).

## BG delivery bounds

| Policy | Formula (12-hop, \|dx\|=5, \|dy\|=7) | Cycles |
|---|---|---:|
| Hard 1-in-16 (conservative) | `2×RAMP + dx×(16+3+7) + dy×(16+3+9)` | **328** |
| Soft-prio occupancy-aware | `wait ≈ ceil(952/(952−49)) = 2`; hop = wait+3+link | **~160** |

Reproduce:

```bash
python3 utils/ppa_analytic_model.py
python3 utils/ppa_analytic_model.py --sensitivity
```

## Trial comparison

| | Area | Power | Calendar bits |
|---|---:|---:|---:|
| IQ-XY | 1.000 | 1.00 | 0 |
| Trial 1 Arch-A | 1.065 | 0.98 | 26,624 (dense) |
| Trial 2 Arch-A2 | 1.028 | 0.96 | 26,624 (dense) |
| **Trial 3 Arch-A3** | **1.000** | **0.95** | **5,888 (sparse)** |
| Δ T3−T2 | −0.028 | −0.01 | −20,736 |

## Non-cuts (P0)

- Sparse depth 128: max observed 49 entries/router (allreduce m=1)
- Global slot counter wrap 1024: max_slot≈951 in exports
- BG FIFO 20 flits: vertical credit RTT

## Depth sensitivity

| Depth | Calendar area | Total area |
|---:|---:|---:|
| 64 | 0.004 | 0.995 |
| **128** | **0.009** | **1.000** |
| 256 | 0.018 | 1.009 |
| 512 | 0.036 | 1.027 |

128 is the selected depth: >2× margin over max 49, still well below dense area.
