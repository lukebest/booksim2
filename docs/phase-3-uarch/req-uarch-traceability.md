# REQ → μArch Traceability (Trial 4 / Arch-A4)

Coverage target: **100%** of P1 iron (REQ-F/P), P2 iron (REQ-A), and P3 iron (REQ-U).

| REQ ID | μArch / block mapping | Notes | Status |
|---|---|---|---|
| REQ-F-001 | `calendar_store`, `next_event_match` | Sparse dual-bank event replay | Covered |
| REQ-F-002 | `xy_route`, SharedPool `vc_buffers`, `switch_alloc` | BG XY soft-prio on non-match | Covered |
| REQ-F-003 | `next_event_match`, `pe_ni` | Reduce via gather + PE (no combine) | Covered |
| REQ-F-004 | Tier A / ABSENT combine+DCA | Comparison retained | Covered |
| REQ-F-005 | `switch_alloc` soft-prio BG | 328 / ~160 / ~200 bounds | Covered |
| REQ-F-006 | `multicast_fork` | Atomic mask fork | Covered |
| REQ-F-007 | VC/soft-prio isolation | Calendar ZB + BG SharedPool class | Covered |
| REQ-F-008 | `credit_fc`, SharedPool | No overwrite; reserves | Covered |
| REQ-F-009 | `watchdog_demote` → pool | No-loss demotion | Covered |
| REQ-F-010 | Tier A selected | B/C comparison-only | Covered |
| REQ-F-011 | DCA ABSENT | No interface datapath | Covered |
| REQ-P-001 | crossbar / links | 64B @ 2 GHz | Covered |
| REQ-P-002 | timing model | H=7 V=9 ramp=1 | Covered |
| REQ-P-003 | sparse calendar replay BFM | Makespan vs baselines | Covered |
| REQ-P-004 | PPA model | Area 0.822× ≤ 0.92× | Covered |
| REQ-P-005 | DCA ABSENT | Tile impact N/A | Covered |
| REQ-A-001 | `calendar_store` | 2×128×23 sparse list | Covered |
| REQ-A-002 | soft-prio + BG bounds | 328 / ~160 / ~200 | Covered |
| REQ-A-003 | SharedPool `vc_buffers` | 40+5×2=50 flits; cal never uses pool | Covered |
| REQ-A-004 | Tier A / no combine | ABSENT combine_unit | Covered |
| REQ-A-005 | `watchdog_demote` | 32-cycle / no-loss via pool | Covered |
| REQ-A-006 | analytic PPA | 0.822× | Covered |
| REQ-U-001 | calendar S0/S1 + match | Sparse dual-bank | Covered |
| REQ-U-002 | SharedPool BG RC→SA→ST | Soft-prio + credits + test_shared_pool | Covered |
| REQ-U-003 | fork + watchdog | Atomic + demote | Covered |
| REQ-U-004 | no combine/DCA | PE handoff only | Covered |
| REQ-U-005 | PPA 0.822/0.92 | vs Trial 3 1.000/0.95 | Covered |
| REQ-U-006 | single noc_clk | Slot wrap 1024 | Covered |

**Coverage: 100%.** SharedPool-BG replaces dedicated FIFOs; calendar remains zero-buffer; combine/DCA remain **ABSENT**.
