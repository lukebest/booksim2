# Phase 3 Summary — DSE Trial 2

**Architecture:** Arch-A2 CalSlot-Hybrid-ZB-NoCombine  
**Tier:** DCA Tier A (no in-router combine/DCA)  
**PPA:** area **1.028×**, power **0.96×** vs IQ-XY (−3.5% / −2% vs Trial 1)

## Deliverables

| Artifact | Path |
|---|---|
| μArch spec | `docs/phase-3-uarch/uarch.md` |
| μArch diagrams | `docs/phase-3-uarch/uarch-diagram.md` |
| Iron (REQ-U-*) | `docs/phase-3-uarch/iron-requirements.json` (6 REQs) |
| Traceability | `docs/phase-3-uarch/req-uarch-traceability.md` (100%) |
| BFM | `bfm/` — compiles; matches RefC; calendars PASS |
| RefC | `refc/` — no `combine_unit` |

## μArch highlights

- Calendar S0/S1 zero-buffer path; BG RC→SA→ST credited XY path
- Hybrid TDM 1-in-16; watchdog demote → escape VC
- Multicast atomic fork
- **combine_unit / DCA explicitly absent**; PE-local Tier-A compute

## Gate

- uarch-review: PASS
- BFM ↔ RefC: PASS
- Compliance P1+P2→P3: PASS (see `.rat/state/`)
- Phase 4 RTL: **not started** (DSE stop)
