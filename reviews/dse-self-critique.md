# DSE Self-Critique — Trial 2

Harsh review of Trial 2 Phase 1→3 package after Tier A / area-first redesign.

## HIGH

| ID | Finding | Disposition |
|---|---|---|
| H1 | Trial 1 combine path must not remain in RefC/BFM/μArch | **RESOLVED** — `combine_unit` deleted; builds pass without it |
| H2 | Area must be < 1.065× | **RESOLVED** — Arch-A2 analytic **1.028×** |
| H3 | Diagrams must show no combine/DCA | **RESOLVED** — `architecture-diagram.md`, `uarch-diagram.md` |
| H4 | ADR-003 must be Tier A USER_CONFIRMED | **RESOLVED** |
| H5 | REQ-A-004 / REQ-U-004 must forbid router arithmetic | **RESOLVED** |
| H6 | Do not start Phase 4 | **RESOLVED** — pipeline stops at Phase 3 |

## MEDIUM

| ID | Finding | Disposition |
|---|---|---|
| M1 | Tier A increases reduce/allreduce latency vs Trial 1 | **ACCEPTED** — area-first trade-off documented |
| M2 | Calendar depth not reduced despite area pressure | **JUSTIFIED** — max_slot 951; 512 unsafe |
| M3 | Arch-B lower area temptation | **REJECTED** — P0 deterministic replay |

## LOW

| ID | Finding | Disposition |
|---|---|---|
| L1 | Analytic PPA ≠ synthesis | Noted; out of DSE scope |
| L2 | PE compute latency model is conservative stub | Noted for future SoC integration |

## Cross-phase consistency

P1 iron (Tier A, area <1.065) → P2 Arch-A2 → P3 μArch/BFM without combine: **consistent**.
No ADR-001/002 invalidation beyond intentional Trial 2 override of Tier B → Tier A.

## Verdict

Self-critique closure: all HIGH findings RESOLVED or JUSTIFIED. Ready for trial
comparison/promotion. **Not** proceeding to Phase 4.
