# μArch Review Round 3 — Critique-Driven Final Pass

- Date: 2026-07-10
- Scope: DSE Trial 1 Phase-3 μArch after `reviews/dse-self-critique.md`
- Prior rounds: `uarch-review-r1.md`, `uarch-review-r2.md`, consolidated `uarch-review.md`

## Critique items addressed in this round

| Critique ID | Fix applied | Artifact |
|---|---|---|
| HIGH-07 | BG bound now includes per-hop link delay, RC→SA→ST, ramps, and credit margin; 12-hop bound **212 → 348 cycles** | `architecture.md`, `iron-requirements.json` REQ-A-002 |
| MEDIUM-04 | Reproducible PPA derivation for Arch-A **0.970** relative area | `ppa-workbook.md`, `utils/ppa_analytic_model.py` |
| MEDIUM-05 | Traceability distinguishes MAPPED / MODEL-TESTED / UNTESTED | `req-uarch-traceability.md` |
| LOW-01 | Mandatory third review round recorded | this document |

ADR-001 and ADR-002 are **not invalidated**. Arch-A CalSlot-Hybrid-ZB remains selected;
changes correct analytic contracts and evidence labeling only.

## Re-verification against R1/R2 fixes

| R1/R2 item | R3 check | Result |
|---|---|---|
| Calendar S0/S1 two-stage path | Still documented in `uarch.md`; not timing-validated in BFM | **Hold** — documentation intact, model gap noted in closure tracker |
| Calendar zero-buffer / BG isolation | Unchanged μArch partition | **Pass** (design intent) |
| Remaining-leaf demotion mask | Specified; RefC model incomplete per HIGH-04 | **Hold** |
| Portable C BFM | Smoke PASS (45 cycles); no schedule replay | **Hold** |

## Open findings carried forward

The self-critique HIGH-01/02/04/05 items remain **OPEN**. Round 3 does not upgrade the
Phase-3 gate to implementation-ready. Specific gaps:

- No 6×8 schedule loader or five-collective replay (HIGH-01).
- No H=7/V=9 link pipelines or protected BG slot enforcement in RefC (HIGH-02).
- Multicast demotion and blocked-calendar retention not model-tested (HIGH-04/05).
- REQ-P-001..003 marked **UNTESTED** in updated traceability.

## Verdict

**CONDITIONAL PASS (documentation and contract quality).**

The μArch document set, PPA workbook, BG bound derivation, and traceability taxonomy
now meet the critique-driven documentation bar for Trial 1. Behavioral closure of
HIGH-01 through HIGH-05 requires the timing-faithful schedule-replay BFM planned in
`reviews/dse-critique-closure.md`. Arch-A block boundaries and ADR alignment are
preserved; no architecture re-selection is recommended.

## Sign-off chain

| Round | Focus | Outcome |
|---|---|---|
| R1 | Timing, isolation, demotion, BFM portability | 4 findings accepted and documented |
| R2 | Build/run smoke, module I/O logs | Zero new findings |
| R3 | Critique closure, BG bound, PPA reproducibility, traceability | 4 critique items resolved; 5 HIGH behavioral items remain open |
