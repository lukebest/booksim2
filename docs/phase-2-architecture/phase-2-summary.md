# Phase 2 Summary — DSE Trial 2

**Selected:** Arch-A2 CalSlot-Hybrid-ZB-NoCombine  
**ADRs:** ADR-002 (Arch-A2), ADR-003 (Tier A), ADR-004 (PPA 1.028)

## Candidates compared

| Candidate | Area | Decision |
|---|---:|---|
| Arch-A2 NoCombine | **1.028** | **Selected** |
| Arch-A Trial 1 | 1.065 | Superseded |
| Arch-B SrcRoute | ~1.008 | Rejected (P0 replay) |
| Arch-C HardTDM-DCA | ~1.237 | Rejected |

## Key artifacts

- `architecture.md` + `architecture-diagram.md`
- `architecture-candidates.md`, `ppa-analytic.md`
- `iron-requirements.json` (REQ-A-001..006)
- RefC without combine (`refc/`)

## Gate

Architecture review PASS; feature coverage 100%; OPEN items closed toward Tier A;
compliance vs P1 iron PASS. No Phase 4.
