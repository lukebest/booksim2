# Phase 3 Summary — DSE Trial 1

Arch-A CalSlot-Hybrid-ZB is refined into a single-`noc_clk` microarchitecture:
two-stage calendar replay, credited BG/escape `RC→SA→ST`, atomic multicast,
three-cycle Tier-B combine, and 32-cycle lossless watchdog demotion. The BFM
instantiates the full 6×8 (48-router) RefC-compatible mesh and logs all 11
specified module boundaries.

The portable C implementation is the Trial-1 approved substitute when SystemC
is unavailable; its scope and remaining SystemC/Tier-B limitations are stated
in `bfm-portability.md`. The independent calendar replayer loads
`calendar-export/v1` JSON schedules, applies the H=7/V=9 link and one-cycle
PE-ramp timing model, scores ejections, and reports makespan.

Gate inputs: 22/22 upstream requirements mapped; five REQ-U requirements have
measurable acceptance criteria; Phase-2 supplied no open items; no Phase-3
open file exists. `make -C bfm test_calendars` generates and replays m=1
broadcast, gather, reduce, allreduce, and simplified multi-source allgather
vectors. Their schema and synthetic-versus-research baseline distinction are
documented in `calendar-export-schema.md`; the existing smoke remains in
`bfm/logs/smoke_summary.log`.
