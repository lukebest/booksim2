# REQ → μArch Traceability (Trial 5 / Arch-A5)

Coverage target: **100%** of P1 iron (REQ-F/P), P2 iron (REQ-A), and P3 iron (REQ-U).

| REQ ID | μArch / block mapping | Notes | Status |
|---|---|---|---|
| REQ-F-001 | `calendar_store`, `next_event_match` | Sparse dual-bank event replay | Covered |
| REQ-F-002 | `cal_fork`, `xy_route`, SharedPool `vc_buffers`, `switch_alloc` | Calendar multicast via CalFork; BG XY soft-prio on non-match | Covered |
| REQ-F-003 | `next_event_match`, `pe_ni` | Reduce via gather + PE (no combine) | Covered |
| REQ-F-004 | Tier A / ABSENT combine+DCA | Comparison retained | Covered |
| REQ-F-005 | `switch_alloc` soft-prio BG | 328 / ~160 / ~188 bounds | Covered |
| REQ-F-006 | `cal_fork` | Atomic calendar-native mask fork via `cal_fork_expand()` | Covered |
| REQ-F-007 | VC/soft-prio isolation | Calendar ZB + BG SharedPool class | Covered |
| REQ-F-008 | `credit_fc`, SharedPool | No overwrite; reserves | Covered |
| REQ-F-009 | `watchdog_demote` → pool | No-loss demotion | Covered |
| REQ-F-010 | Tier A selected | B/C comparison-only | Covered |
| REQ-F-011 | DCA ABSENT | No interface datapath | Covered |
| REQ-F-012 | `bfm-portability.md`, `calendar-export-schema.md`, BFM replay | Portable C BFM replays 6×8 calendars and reports makespan | Covered |
| REQ-P-001 | crossbar / links | 64B @ 2 GHz | Covered |
| REQ-P-002 | timing model | H=7 V=9 ramp=1 | Covered |
| REQ-P-003 | sparse calendar replay BFM | Makespan vs baselines | Covered |
| REQ-P-004 | PPA model | 0.746× area; FlooNoC 0.058 remains a calibration anchor, CalFork implementation is 0.025 | Covered |
| REQ-P-005 | DCA ABSENT | Tile impact N/A | Covered |
| REQ-A-001 | `calendar_store` | 2×128×23 sparse list | Covered |
| REQ-A-002 | soft-prio + BG bounds | 328 / ~160 / ~188 | Covered |
| REQ-A-003 | SharedPool `vc_buffers` | Trial-5 28+5×2=38 flits; calendar never uses pool | Covered |
| REQ-A-004 | Tier A / no combine | ABSENT combine_unit | Covered |
| REQ-A-005 | `watchdog_demote` | 32-cycle / no-loss via pool | Covered |
| REQ-A-006 | analytic PPA | Trial-5 0.746× / 0.90×, better than A4 0.822× / 0.92× | Covered |
| REQ-U-001 | calendar S0/S1 + match | Sparse dual-bank | Covered |
| REQ-U-002 | SharedPool BG RC→SA→ST | 28 shared + 5×2 reserve = 38; soft-prio + credits + test_shared_pool | Covered |
| REQ-U-003 | CalFork + watchdog | Atomic `cal_fork_expand()` + lossless demote | Covered |
| REQ-U-004 | no combine/DCA | PE handoff only | Covered |
| REQ-U-005 | PPA 0.746/0.90 | vs Arch-A4 0.822/0.92 | Covered |
| REQ-U-006 | single noc_clk | Slot wrap 1024 | Covered |

**Coverage: 100% (12 REQ-F + 5 REQ-P + 6 REQ-A + 6 REQ-U = 29/29).**
Trial-5 SharedPool-BG is 28 shared flits plus five reserves of 2 (38 total);
calendar remains zero-buffer; CalFork replaces the general FlooNoC stream-fork
class; combine/DCA remain **ABSENT**.
