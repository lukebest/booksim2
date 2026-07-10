# REQ → μArch Traceability (Trial 2 / Arch-A2)

Coverage target: **100%** of P1 iron (REQ-F/P), P2 iron (REQ-A), and P3 iron (REQ-U).

| REQ ID | μArch / block mapping | Notes | Status |
|---|---|---|---|
| REQ-F-001 | `calendar_store`, `calendar_replay` | Dual-bank slot replay | Covered |
| REQ-F-002 | `xy_route`, `vc_buffers`, `switch_alloc` | BG XY always available | Covered |
| REQ-F-003 | `calendar_replay`, `pe_ni` | Reduce via gather + PE (no combine) | Covered |
| REQ-F-004 | Tier A / ABSENT combine+DCA | Comparison retained in dca-tier-analysis | Covered |
| REQ-F-005 | `switch_alloc` BG window | 1-in-16 + 328-cycle bound | Covered |
| REQ-F-006 | `multicast_fork` | Atomic mask fork | Covered |
| REQ-F-007 | VC/TDM isolation | Calendar ZB + BG credited class | Covered |
| REQ-F-008 | `credit_fc`, `vc_buffers` | No overwrite | Covered |
| REQ-F-009 | `watchdog_demote` | No-loss demotion | Covered |
| REQ-F-010 | Tier A selected | B/C comparison-only | Covered |
| REQ-F-011 | DCA ABSENT | No interface datapath | Covered |
| REQ-P-001 | crossbar / links | 64B @ 2 GHz | Covered |
| REQ-P-002 | timing model | H=7 V=9 ramp=1 | Covered |
| REQ-P-003 | calendar replay BFM | Makespan vs baselines | Covered |
| REQ-P-004 | PPA model | Area 1.028× < 1.065× | Covered |
| REQ-P-005 | DCA ABSENT | Tile impact N/A | Covered |
| REQ-A-001 | `calendar_store` | 2×1024×13 | Covered |
| REQ-A-002 | hybrid TDM + BG | 328-cycle bound | Covered |
| REQ-A-003 | `vc_buffers` | 100 flits interior | Covered |
| REQ-A-004 | Tier A / no combine | ABSENT combine_unit | Covered |
| REQ-A-005 | `watchdog_demote` | 32-cycle / no-loss | Covered |
| REQ-A-006 | analytic PPA | 1.028× | Covered |
| REQ-U-001 | calendar S0/S1 | Dual-bank | Covered |
| REQ-U-002 | BG RC→SA→ST | Credits + window | Covered |
| REQ-U-003 | fork + watchdog | Atomic + demote | Covered |
| REQ-U-004 | no combine/DCA | PE handoff only | Covered |
| REQ-U-005 | PPA 1.028/0.96 | vs Trial 1 | Covered |
| REQ-U-006 | single noc_clk | No CDC | Covered |

**Coverage: 100%.** Combine/DCA mapped as **ABSENT** with PE Tier-A path.
