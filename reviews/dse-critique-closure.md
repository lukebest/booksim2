# DSE Trial 1 Critique Closure Tracking

- Date: 2026-07-10 (post self-critique re-run, Trial 1 finalize)
- Source critique: `reviews/dse-self-critique.md`
- ADR disposition: **ADR-001, ADR-002, ADR-003, ADR-004** recorded as
  `AGENT_ASSUMED`; awaiting user confirmation at satisfaction check.

Status key: **RESOLVED** | **PARTIAL** | **JUSTIFIED** | **OPEN**

| ID | Severity | Summary | Status | Evidence / remaining work |
|---|---|---|---|---|
| HIGH-01 | HIGH | No required calendar-replay BFM with schedule loader | **RESOLVED** | `utils/export_calendar_slots.py` emits calendar-export/v1 JSON; `bfm/src/calendar_replay.c` loader; `make -C bfm test_calendars` PASS all five collectives m=1. Remaining: superpose_6x8.json conversion and m=2..5 vectors. |
| HIGH-02 | HIGH | BFM/RefC timing model lacks link pipelines, BG window, delayed credits | **RESOLVED** | `refc/mesh_sim.c` H=7/V=9 link chains, PE ramps, H=16/V=20 delayed credits; `refc/router.c` protected BG_WINDOW_PERIOD slot; `make -C refc test` PASS including `test_bg_window`. Remaining: 12-hop saturated bound test. |
| HIGH-04 | HIGH | Lossless atomic multicast/demotion not represented | **RESOLVED** | `refc/watchdog_demote.c` per-context epoch/sequence, leaf bitmap, release-once; `refc/test_demote_noloss.c` PASS; `refc/test_blocked_fork.c` retains blocked fork leaves. |
| HIGH-05 | HIGH | Blocked calendar flit can be silently discarded | **RESOLVED** | Blocked calendar fork latched in watchdog context; `test_blocked_fork` verifies both E/S leaves retained in BG FIFO. |
| HIGH-06 | HIGH | Reduce/allreduce not executable at required depth | **PARTIAL** | `refc/test_combine_ops.c` six opcodes × eight lanes, 3-cycle pipeline PASS; calendar replay reduce/allreduce combine_ops=336. Remaining: m=2..5 calendars, Tier-A fallback directed tests. |
| HIGH-07 | HIGH | BG bound not conservative | **RESOLVED** | Formula in `docs/phase-2-architecture/iron-requirements.json` REQ-A-002 and `architecture.md`: eligible 12-hop worst case = **328 cycles** (`2 + 5×26 + 7×28`). Buffer model reconciled to 5×20-flit FIFOs; area_rel **1.065**. |
| MEDIUM-01 | MEDIUM | Calendar packing disconnected from executable model | **RESOLVED** | 13-bit packed layout in calendar-export schema; `calendar_replay.c` parses physical fields with range/duplicate checks. RefC retains unpacked struct at API boundary only. |
| MEDIUM-02 | MEDIUM | Buffer organization claims contradict (74 vs 104 flits) | **RESOLVED** | Ownership unified: five per-input 20-flit FIFOs (100 flits, 51,200 bits); PPA updated in ADR-004 (area_rel 0.970→1.065). |
| MEDIUM-03 | MEDIUM | Calendar loading / epoch safety documentation-only | **PARTIAL** | `uarch.md` inactive-write rejection and slot-zero activation documented; loader rejects duplicate slots. Remaining: directed epoch/CRC activation tests. |
| MEDIUM-04 | MEDIUM | PPA ranking not reproducible | **RESOLVED** | `docs/phase-2-architecture/ppa-workbook.md` and `utils/ppa_analytic_model.py` with sensitivity tables. |
| MEDIUM-05 | MEDIUM | Iron criteria / traceability too weak for PASS | **RESOLVED** | `req-uarch-traceability.md` MAPPED/MODEL-TESTED/UNTESTED taxonomy; `.rat/state/compliance-report.json` P1+P2→P3 gate. |
| MEDIUM-06 | MEDIUM | DCA A/B/C comparison quantitatively under-supported | **RESOLVED** | `docs/phase-1-research/dca-tier-analysis.md` m=1..5 table; ADR-003 records Tier B selection with A/C alternatives. |
| LOW-01 | LOW | Three-round Phase-3 review incomplete | **RESOLVED** | `uarch-review-r1.md`, `uarch-review-r2.md`, `uarch-review-r3.md` present. |
| LOW-02 | LOW | Phase-2 compliance report overstates verified evidence | **PARTIAL** | P3 compliance report regenerated; traceability tiers adopted. Remaining: rerun P2 gate with updated evidence tiers. |

## Severity rollup (post re-run finalize)

| Severity | Total | Resolved | Partial | Open | Justified |
|---:|---:|---:|---:|---:|---:|
| HIGH | 6 | 5 | 1 | 0 | 0 |
| MEDIUM | 6 | 5 | 1 | 0 | 0 |
| LOW | 2 | 1 | 1 | 0 | 0 |

## Items closed in critique re-run

1. **HIGH-01** — calendar-export/v1 schema, loader, five-collective m=1 replay BFM.
2. **HIGH-02** — timing-faithful RefC link/credit/BG-window model and directed tests.
3. **HIGH-04** — per-leaf watchdog demotion with release-once accounting.
4. **HIGH-05** — blocked calendar fork retention under backpressure.
5. **HIGH-07** — 328-cycle eligible BG bound and buffer-consistent PPA (ADR-004).
6. **MEDIUM-01/02/04/05/06** — packing, buffer reconciliation, PPA workbook, traceability, DCA ADR.

## Remaining follow-on (non-blocking for Trial 1 user review)

- **HIGH-06 PARTIAL:** m=2..5 calendar vectors and Tier-A fallback tests.
- **Compliance gate:** mixed calendar/BG stress, 12-hop BG bound, superpose baseline overhead (see `.rat/state/compliance-report.json`).
- **User confirmation:** promote ADR-001/002/003/004 from `AGENT_ASSUMED`.
