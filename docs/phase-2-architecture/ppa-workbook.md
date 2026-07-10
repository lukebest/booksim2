# PPA Workbook — Arch-A CalSlot-Hybrid-ZB

Reproducible analytic derivation of the **0.970** relative router area and supporting
assumptions for DSE Trial 1. Values are normalized to a five-port 512-bit input-queued
(IQ) XY router at area and dynamic power **1.00**. This workbook closes **MEDIUM-04**
from `reviews/dse-self-critique.md`.

Companion script: `utils/ppa_analytic_model.py` (run with `--sensitivity` for sweeps).


## Post-critique errata (Trial-1 re-run)

1. **BG bound**: **328 cycles** for 12-hop worst case under eligibility premise
   `T ≤ 2·RAMP + |dx|·(16+3+7) + |dy|·(16+3+9)` (REQ-A-002). Credit RTT sizes FIFOs; it is not added to the delivery bound.
2. **Buffer / area**: per-input BG FIFOs are **5 × 20 flits**. Authoritative Arch-A relative area is **1.065** in `ppa-analytic.md` (earlier 0.970 assumed a 74-flit aggregate). Ranking vs Arch-B/C unchanged.

The historical 0.970 derivation below is retained for audit.

---

## 1. Baseline IQ XY decomposition

| Component | Relative area | Role |
|---:|---:|---|
| Crossbar / decode | **0.380** | Five-port 512-bit switch and port decode |
| VC payload buffers | **0.450** | Multi-VC input-queued payload SRAM (interior reference) |
| Credit / control | **0.170** | Credit counters, allocator control, NI glue |
| **Total** | **1.000** | Normalized reference |

The baseline interior payload reference is **123 flits** (62,976 bits). This is the
conservative aggregate used to scale buffer area when Arch-A removes general calendar
IQ buffering. The 123-flit reference is not an edge-router allocation; it matches the
same “interior aggregate upper bound” style as the Arch-A 74-flit provision.

## 2. Arch-A buffer reduction

Arch-A stores payload only for BG and escape traffic (REQ-A-003):

```
bits_arch = 74 flits × 512 bits/flit = 37,888 bits
bits_base = 123 flits × 512 bits/flit = 62,976 bits

A_buf(Arch-A) = A_buf(base) × (bits_arch / bits_base)
              = 0.450 × (37,888 / 62,976)
              = 0.450 × 0.6017
              ≈ 0.270  (exact ratio 0.2707; table uses 3-decimal rounding)
```

Calendar traffic uses zero queued payload storage; the 0.450 baseline VC term is not
carried forward for calendar replay.

## 3. Calendar SRAM area

Each router owns two banks of 1,024 entries × 13 bits (REQ-A-001):

```
bits_cal = 2 × 1,024 × 13 = 26,624 bits (3.25 KiB)

K_ctrl = 0.040 / 26,624 = 1.502 × 10⁻⁶  (relative area per control bit)

A_calendar = bits_cal × K_ctrl = 0.040
```

The 13-bit entry packs `{valid[0], in_port[2:0], out_port_mask[4:0], opcode[3:0]}`.
Bank headers (calendar ID, epoch, CRC) are flip-flop state and are included in the
**0.195** control term, not in the SRAM bit count above.

Compare to payload SRAM density: `K_payload = 0.450 / 62,976 = 7.145 × 10⁻⁶` per bit.
Control table SRAM is modeled ~4.7× denser per bit than wide payload FIFO storage.

## 4. FlooNoC calibration anchors (additive)

FlooNoC router-class deltas from `docs/dse-input-spec.md` are inserted as **additive**
components on the normalized baseline router, not as multipliers on total area:

| Feature class | FlooNoC anchor | Trial-1 additive | Uncertainty (±30% on delta) |
|---|---:|---:|---|
| Multicast fork | +5.8% | **0.058** | [0.041, 0.075] |
| Tier-B combine | +2.7% | **0.027** | [0.019, 0.035] |
| Tier-C wide+DCA | +16.9% | 0.169 (comparison only) | [0.118, 0.220] |

Uncertainty applies to the **delta**, not the entire router. Example Arch-A total range
with both anchors at −30% / +30%: **[0.941, 0.999]**. The selected point **0.970**
remains inside this band.

## 5. Control / isolation increment

```
A_control(Arch-A) = A_control(base) + Δ_BG_window + Δ_watchdog + Δ_demotion + Δ_credit
                  = 0.170 + 0.025
                  = 0.195
```

The +0.025 increment covers the BG window counter, extended 16/20 credit state,
watchdog FSM, and demotion leaf context beyond the baseline IQ credit/control block.

## 6. Arch-A total area

| Component | Value | Derivation |
|---:|---:|---|
| Crossbar | 0.380 | Shared with baseline |
| VC buffers | 0.270 | §2 buffer reduction |
| Calendar SRAM | 0.040 | §3 bit accounting |
| Multicast | 0.058 | §4 FlooNoC +5.8% additive |
| Combine | 0.027 | §4 FlooNoC +2.7% additive |
| Control / isolation | 0.195 | §5 |
| **Total** | **0.970** | **−3.0% vs baseline** |

```python
# utils/ppa_analytic_model.py — canonical check
0.380 + 0.270 + 0.040 + 0.058 + 0.027 + 0.195  # == 0.970
```

## 7. Dynamic power (0.98)

Dynamic power is analytic, not synthesized:

| Effect | Direction | Rationale |
|---|---|---|
| No calendar payload SRAM R/W | − | Zero-buffer calendar bypasses VC read/write |
| Reduced BG buffer depth (74 vs 123 flit ref) | − | Fewer stored bits and lower fill activity |
| Calendar SRAM read + fork toggle | + | One registered 13-bit read per active slot |
| 1-in-16 BG window counter | + | Small control switching on protected slots |

Net estimate: **0.98** (−2% vs baseline). Synthesis is required to replace this
placeholder.

## 8. Sensitivity tables

### Calendar depth (1,024 default)

| Depth (slots/bank) | Calendar bits | A_calendar | Total area | Δ vs 1.00 |
|---:|---:|---:|---:|---:|
| 512 | 13,312 | 0.020 | 0.950 | −5.0% |
| **1,024** | **26,624** | **0.040** | **0.970** | **−3.0%** |
| 2,048 | 53,248 | 0.080 | 1.010 | +1.0% |

Depth scales calendar SRAM linearly; crossbar, buffers, and FlooNoC anchors are
unchanged.

### BG service period (performance bound; minimal area effect)

| BG period | Control est. | 12-hop bound (5H+7V) | Notes |
|---:|---:|---:|---|
| 8 | 0.175 | 244 cycles | Tighter progress; finer window counter |
| **16** | **0.195** | **328 cycles** | **Trial-1 selection** |
| 32 | 0.193 | 524 cycles | Looser progress; simpler window |

Area sensitivity to BG period is ±0.003 on control (not material to ranking). The
dominant effect is the end-to-end BG bound (see §9 and `architecture.md`).

Run `python3 utils/ppa_analytic_model.py --sensitivity` to regenerate these tables.

## 9. BG delivery bound (REQ-A-002, HIGH-07 closure)

Observation point: source `pe_ni` enqueue → destination `pe_ni` eject.

```
T ≤ 2×RAMP + |dx|×(BG_WINDOW + T_router + H_LINK)
      + |dy|×(BG_WINDOW + T_router + V_LINK) + T_credit
```

| Symbol | Trial-1 value | Role |
|---|---:|---|
| RAMP | 1 | PE-router inject/eject staging |
| BG_WINDOW | 16 | Non-borrowable protected slot period |
| T_router | 3 | RC→SA→ST pipeline |
| H_LINK | 7 | Horizontal link register chain |
| V_LINK | 9 | Vertical link register chain |
| T_credit | 20 | Worst vertical credit round-trip class |

Longest 6×8 Manhattan route: `|dx|=5`, `|dy|=7`, 12 hops.

```
T = 2×1 + 5×(16+3+7) + 7×(16+3+9) = 328 cycles
```

The prior `16×(|dx|+|dy|)+20` formula (212 cycles) omitted per-hop link and router
latency and is retired. Per-hop grant bound (≤16 slots) is unchanged.

## 10. Cross-reference

- Summary table: `docs/phase-2-architecture/ppa-analytic.md`
- Candidate comparison: `docs/phase-2-architecture/architecture-candidates.md`
- Selection record: `docs/decisions/ADR-002-architecture-selection.md` (unchanged decision;
  analytic inputs are now reproducible)
