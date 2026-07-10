# Analytic PPA — Arch-A CalSlot-Hybrid-ZB

## Method and scope

All values are normalized to a five-port 512-bit input-queued (IQ) XY router of
area and dynamic power **1.00**.  They are analytic estimates, not synthesis or
post-layout measurements.  The common model uses crossbar 0.380, VC payload
buffering 0.450, and credit/control 0.170 for the baseline.  Full derivations,
equations, and sensitivity sweeps are in **`ppa-workbook.md`** and
**`utils/ppa_analytic_model.py`**.

| Assumption required by REQ-P-004 | Trial-1 analytic value |
|---|---|
| SRAM bitcell / calendar table | Two local 1,024×13-bit control SRAM banks = 26,624 bits/router; modeled as 0.040 relative area. |
| Crossbar mux | Five-port 512-bit crossbar is common to all candidates; baseline/Arch-A contribution = 0.380. |
| VC-buffer flits | Arch-A uses five per-input BG/escape FIFOs of 20 flits: 100 flits/51,200 bits per interior router. H/V egress credit counters remain 16/20. Scaling the prior 74-flit estimate linearly gives 0.365 relative buffer area; calendar needs no payload IQ. |
| Calendar-table depth | 1,024 slots per bank; active replay is one registered read per slot. |
| Collective calibration | Multicast fork +5.8%, Tier-B parallel reduce +2.7%, Tier-C wide+DCA +16.9% router classes. |

## Relative area and dynamic power

| Candidate | Crossbar / decode | VC buffers | Calendar storage | Multicast | Combine / DCA | Credit, isolation, violation control | Total area | Dynamic power | Delta versus baseline |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Baseline IQ XY | 0.380 | 0.450 | 0.000 | 0.000 | 0.000 | 0.170 | 1.000 | 1.00 | 0.0% |
| **Arch-A CalSlot-Hybrid-ZB** | 0.380 | 0.365 | 0.040 | 0.058 | 0.027 | 0.195 | **1.065** | **0.98** | **+6.5% area, -2% power** |
| Arch-B SrcRoute-VCPrio-Shared | 0.405 | 0.340 | 0.005 | 0.058 | 0.000 | 0.200 | 1.008 | 1.08 | +0.8% area, +8% power |
| Arch-C CalSlot-HardTDM-DCA | 0.380 | 0.400 | 0.040 | 0.058 | 0.169 | 0.190 | 1.237 | 1.23 | +23.7% area, +23% power |

Arch-A remains the selected architecture because it preserves calendar replay without
general calendar payload queues and retains a finite BG service guarantee. The
consistent 100-flit per-input queue organization moves its analytic area above the
normalized IQ-XY baseline; the result remains an analytic estimate pending synthesis.
Its dynamic estimate benefits from avoiding calendar payload SRAM read/write; the
1-in-16 BG window and calendar SRAM add control switching.

## Makespan baselines and Arch-A overhead (REQ-P-003)

Published zero-buffer baselines (unchanged schedule inputs):

| Profile | m=1 | m=2 | m=3 | m=4 | m=5 |
|---|---:|---:|---:|---:|---:|
| ag_bcast (cycles) | 167 | 267 | 310 | 524 | 708 |
| ag_gather (cycles) | 170 | 310 | 368 | 628 | 898 |

Arch-A estimated calendar overhead uses `(estimated_makespan - baseline_makespan) /
baseline_makespan`.  Window-aware recompilation targets **0–5%**; the conservative
unmodified dense-calendar tax is **6.25%** (one protected BG slot per 16).

| Profile | m | Baseline | Arch-A est. (recompiled, +2.5%) | Overhead | Arch-A est. (dense, +6.25%) | Overhead |
|---|---|---:|---:|---:|---:|---:|
| ag_bcast | 1 | 167 | 171 | +2.4% | 177 | +6.0% |
| ag_bcast | 2 | 267 | 274 | +2.6% | 284 | +6.4% |
| ag_bcast | 3 | 310 | 318 | +2.6% | 329 | +6.1% |
| ag_bcast | 4 | 524 | 537 | +2.5% | 557 | +6.3% |
| ag_bcast | 5 | 708 | 726 | +2.5% | 752 | +6.2% |
| ag_gather | 1 | 170 | 174 | +2.4% | 181 | +6.5% |
| ag_gather | 2 | 310 | 318 | +2.6% | 329 | +6.1% |
| ag_gather | 3 | 368 | 377 | +2.4% | 391 | +6.3% |
| ag_gather | 4 | 628 | 644 | +2.5% | 667 | +6.2% |
| ag_gather | 5 | 898 | 920 | +2.5% | 954 | +6.2% |

Recompiled estimates use the midpoint (+2.5%) of the stated 0–5% window; Phase 3 BFM
must replace these analytic placeholders with measured post-window calendars.

## Performance and risk context

| Candidate | Calendar makespan estimate | Key strength | Key limitation |
|---|---|---|---|
| **Arch-A** | 0–5% after window-aware recompilation; 6.25% dense-calendar tax without recompilation | Lowest PPA and finite BG service | Requires schedules to model non-borrowable BG windows |
| Arch-B | 4–9%, contention dependent | No calendar SRAM | Shared IQ arbitration breaks deterministic replay |
| Arch-C | 0–2% for normal hard-TDM replay | Best calendar fidelity | DCA area/latency and unused hard slots dominate Trial-1 workloads |

Tier-B has a +2.7% router-area calibration and is selected.  Tier-C's +16.9%
router-wide-path calibration is retained only for comparison; its cited less-than-1%
tile-area preference cannot be claimed because this Trial does not establish an
existing FPU, arbitration policy, or implementation library.
