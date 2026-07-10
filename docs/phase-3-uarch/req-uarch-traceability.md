# Requirement-to-μArch Traceability

All Phase-1 functional/performance requirements and Phase-2 architecture
requirements are traced below. **Status** distinguishes document mapping from
executable evidence (closes **MEDIUM-05** from `reviews/dse-self-critique.md`):

| Status | Meaning |
|---|---|
| **MAPPED** | μArch module and section assigned; no executed test evidence |
| **MODEL-TESTED** | Portable C BFM / RefC smoke or directed model test executed |
| **UNTESTED** | Required behavior not yet covered by any model test |

Phase-2-only analysis requirements remain mapped to the preserving μArch decision.
BFM evidence today is the three-entry smoke in `bfm/src/main_smoke.c` (45 cycles).

| REQ ID | uArch module(s) | Section / evidence | Status |
|---|---|---|---|
| REQ-F-001 | calendar_store, calendar_replay | `uarch.md` calendar sections; REQ-U-001 | MAPPED |
| REQ-F-002 | calendar_replay, multicast_fork, pe_ni | calendar/fork sections; REQ-U-001/003 | MODEL-TESTED |
| REQ-F-003 | calendar_replay, combine_unit, pe_ni | calendar and Tier-A sections; REQ-U-001/004 | MAPPED |
| REQ-F-004 | combine_unit, pe_ni | Tier-B three-cycle pipeline; REQ-U-004 | MODEL-TESTED |
| REQ-F-005 | xy_route, vc_buffers, switch_alloc | RC→SA→ST; REQ-U-002 | MODEL-TESTED |
| REQ-F-006 | multicast_fork, watchdog_demote | atomic mask and remaining leaves; REQ-U-003 | MODEL-TESTED |
| REQ-F-007 | xy_route, vc_buffers, credit_fc | single credited XY class; REQ-U-002 | MAPPED |
| REQ-F-008 | vc_buffers, credit_fc, pe_ni | admission and credit rules; REQ-U-002/003 | MAPPED |
| REQ-F-009 | watchdog_demote, vc_buffers | 32-cycle release-once FSM; REQ-U-005 | MODEL-TESTED |
| REQ-F-010 | combine_unit, pe_ni | Tier-B selected/Tier-A fallback; REQ-U-004 | MODEL-TESTED |
| REQ-F-011 | pe_ni | disabled DCA stub; REQ-U-004 | MAPPED |
| REQ-F-012 | BFM mesh/router harness | `bfm/src/main_smoke.c`; REQ-U-005 | MODEL-TESTED |
| REQ-P-001 | switch_alloc, crossbar, credit_fc | one granted flit/cycle; REQ-U-002 | UNTESTED |
| REQ-P-002 | clock-domain-map, mesh BFM | 2 GHz and H/V/RAMP parameters; REQ-U-005 | UNTESTED |
| REQ-P-003 | calendar_store, BFM | baseline documented in summary; REQ-U-005 | UNTESTED |
| REQ-P-004 | calendar_store, vc_buffers, crossbar | `ppa-workbook.md`; REQ-U-001/002 | MAPPED |
| REQ-P-005 | pe_ni | disabled DCA configuration; REQ-U-004 | MAPPED |
| REQ-A-001 | calendar_store, calendar_replay | double-bank S0/S1 implementation; REQ-U-001 | MAPPED |
| REQ-A-002 | xy_route, switch_alloc, credit_fc | protected BG service; 328-cycle bound in `architecture.md` | MAPPED |
| REQ-A-003 | vc_buffers, credit_fc, crossbar | bounded banks and credited egress; REQ-U-002 | MAPPED |
| REQ-A-004 | combine_unit, pe_ni | Tier-B and inactive DCA; REQ-U-004 | MODEL-TESTED |
| REQ-A-005 | multicast_fork, watchdog_demote | retained leaves and 32-cycle timeout; REQ-U-003/005 | MODEL-TESTED |

## Coverage summary

| Status | Count | Share |
|---|---:|---:|
| MAPPED | 10 | 45% |
| MODEL-TESTED | 9 | 41% |
| UNTESTED | 3 | 14% |

**22 of 22 requirements traced (100% mapped).** Only **9 of 22 (41%)** have
model-test evidence; **3 performance requirements (REQ-P-001..003)** remain
UNTESTED until a timing-faithful BFM validates rate, link latency, and makespan.

Prior “100% MAPPED = closure” wording is retired. Phase-3 gate PASS requires
advancing UNTESTED and smoke-only MODEL-TESTED items to schedule-replay evidence.
