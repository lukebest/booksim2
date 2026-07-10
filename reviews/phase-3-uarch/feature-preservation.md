# Phase 3 Feature Preservation — Trial 2

| Feature | Mapping | Evidence | Status |
|---|---|---|---|
| Calendar replay | calendar_store/replay | calendar_replay PASS | PASS |
| BG XY | vc_buffers, xy_route | test_bg_* PASS | PASS |
| Multicast fork | multicast_fork | smoke + blocked_fork | PASS |
| Watchdog demote | watchdog_demote | test_demote_noloss PASS | PASS |
| Tier-A PE handoff | pe_ni / CAL_OP_PE_HANDOFF | reduce/allreduce pe_handoffs | PASS |
| Combine unit | **ABSENT** | no combine_unit in build | PASS |
| DCA | **ABSENT** | no interface datapath | PASS |

**Verdict: PASS** — Trial 1 combine path intentionally not preserved.
