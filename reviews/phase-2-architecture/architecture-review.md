# Phase 2 Architecture Review — DSE Trial 3 (Arch-A3 SparseCal)

**Verdict: PASS**

## Artifact gate
- [x] architecture.md (Arch-A3 SparseCal-Hybrid-ZB-NoCombine)
- [x] architecture-diagram.md (sparse list + next-event match + soft-prio)
- [x] architecture-candidates.md (A3 selected; A2/B/C compared)
- [x] iron-requirements.json (REQ-A-*, trial=3)
- [x] ppa-analytic.md / ppa-workbook.md (area 1.000×, power 0.95×)
- [x] ADR-002 updated for Arch-A3; ADR-003 Tier A reaffirmed; ADR-004 SparseCal PPA
- [x] RefC sparse calendar_store + soft-prio router; tests PASS
- [x] BFM sparse replay; all `*_m1.json` PASS

## Quality gate
- Feature coverage: calendar/BG/multicast/watchdog/Tier-A/PPA mapped
- OPEN-1-001 resolved as SparseCal depth 128 dual-bank
- Compliance vs P1 iron: PASS (Tier A, ZB calendar, single network, BG progress)
- Area target 0.97–1.00× met at **1.000×**
- No Phase 4 / no combine restore

## Findings
None blocking. Future Trial 3b may explore shared BG buffer pool (out of scope).
