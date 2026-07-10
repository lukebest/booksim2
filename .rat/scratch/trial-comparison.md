# DSE Trial Comparison — Trial 1 vs Trial 2

- Date: 2026-07-10
- Reviewer: rtl-architect
- Trial A path: `/home/luke/workspace/booksim2` (current best / Trial 1)
- Trial B path: `/home/luke/workspace/booksim2-dse-trial2` (Trial 2)
- Compliance A: `.rat/state/compliance-report-current.json`
- Compliance B: `booksim2-dse-trial2/.rat/state/compliance-report-new.json`

[RAT: THOUGHT] Comparing audited Phase-2/3 artifacts and compliance JSON only; analytic PPA from `ppa-analytic.md` (not ADR-002's older 0.970 figure).

---

## Side-by-side summary

| Dimension | Trial A (Trial 1) | Trial B (Trial 2) | Winner / note |
|---|---|---|---|
| **Architecture name** | Arch-A CalSlot-Hybrid-ZB | Arch-A2 CalSlot-Hybrid-ZB-NoCombine | Trial 2 = area-first variant of Arch-A |
| **DCA / reduction tier** | **Tier B** (router combine; Tier A FP fallback; Tier C disabled stub) | **Tier A** (PE-local only; B/C comparison-only) | Trial 2 matches “no router reduce” |
| **Analytic area vs IQ-XY** | **1.065×** (+6.5%) | **1.028×** (+2.8%) | **Trial 2** (−3.5% vs Trial 1) |
| **Analytic power vs IQ-XY** | **0.98×** (−2%) | **0.96×** (−4%) | **Trial 2** (−2% vs Trial 1) |
| **Combine unit present?** | **Yes** — Tier-B 3-cycle lane combine (+0.027 area class) | **No** — `combine_unit` / DCA **ABSENT** (0.000) | Trial 2 |
| **Arch diagram present?** | **No** dedicated `architecture-diagram.md` | **Yes** — `docs/phase-2-architecture/architecture-diagram.md` | Trial 2 |
| **μArch diagram present?** | **No** dedicated `uarch-diagram.md` | **Yes** — `docs/phase-3-uarch/uarch-diagram.md` | Trial 2 |
| **Iron REQ count (P1 / P2 / P3)** | 17 / 5 / 5 | 17 / **6** / **6** | Trial 2 adds REQ-A-006, REQ-U-006 (area delta) |
| **Compliance (P3 vs P1+P2)** | **FAIL** — 22 checked, **17 PASS / 5 VIOLATION** | **FAIL** — 23 checked, **20 PASS / 3 VIOLATION** | Trial 2 fewer violations |
| **Phase 4 started?** | In scope for Trial-1 handoff (BFM/numerical closure deferred to P4/5) | **No** — explicit DSE stop (“No Phase 4”) | Trial 2 goal met |
| **Decision source** | `AGENT_ASSUMED` (ADR-002/003 pending user confirm) | `USER_CONFIRMED` (ADR-002/003) | Trial 2 |

---

## Architecture & tier detail

### Trial 1 (A)

- Selected stack: double-buffered 1024×13 calendar, hybrid 1-in-16 BG + credited XY escape, zero-buffer calendar, atomic 5-bit multicast fork, **Tier-B combine**, 32-cycle watchdog demotion.
- Evidence: `docs/phase-2-architecture/phase-2-summary.md`, `docs/decisions/ADR-002-architecture-selection.md`, `docs/decisions/ADR-003-dca-tier.md`, `docs/phase-1-research/dca-tier-analysis.md`.

### Trial 2 (B)

- Same calendar / hybrid / ZB / multicast / watchdog skeleton as Arch-A, with **combine and DCA removed** from router datapath.
- Reduce = calendar gather → PE; allreduce = gather → PE → calendar broadcast.
- Evidence: `docs/phase-2-architecture/phase-2-summary.md`, `docs/decisions/ADR-002-architecture-selection.md`, `docs/decisions/ADR-003-dca-tier.md`, diagrams below.

---

## Analytic PPA vs IQ-XY

Normalized to five-port 512-bit IQ-XY = 1.00 (`ppa-analytic.md`).

| Candidate | Area | Power | vs IQ-XY | vs Trial 1 |
|---|---:|---:|---|---|
| IQ-XY baseline | 1.000 | 1.00 | — | — |
| Trial 1 Arch-A | **1.065** | **0.98** | +6.5% / −2% | — |
| Trial 2 Arch-A2 | **1.028** | **0.96** | +2.8% / −4% | **−3.5% area / −2% power** |

Primary area delta: remove combine class **0.027** + lean control **0.010** (0.195 → 0.185). Calendar banks and BG FIFO depths intentionally unchanged (P0).

[RAT: INSIGHT] Trial 2 area win is almost entirely combine removal; router reduce latency regresses to Tier-A gather+PE (accepted under area-first USER_CONFIRMED).

---

## Combine unit & diagrams

| Artifact | Trial 1 | Trial 2 |
|---|---|---|
| `combine_unit` in μArch | Present (`uarch.md` combine section; REQ-U-004 Tier B) | Explicitly **ABSENT** (`uarch.md`, protocol-assignments, iron REQ-U-004/A-004) |
| DCA | Disabled stub retained | No stub / no datapath |
| `architecture-diagram.md` | Missing | Present (mesh, block, TDM, fork/demote, non-goals table) |
| `uarch-diagram.md` | Missing | Present (pipelines, TDM, demote FSM, absence of combine/DCA) |

---

## Iron requirements & compliance

### Iron counts

| Phase | Trial 1 | Trial 2 |
|---|---|---|
| Phase 1 (`REQ-F/P/A` upstream set) | 17 | 17 |
| Phase 2 (`REQ-A-*`) | 5 (A-001..005) | **6** (A-001..**006**) |
| Phase 3 (`REQ-U-*`) | 5 (U-001..005) | **6** (U-001..**006**) |

Trial 2 adds area-delta iron (REQ-A-006 / REQ-U-005–006 family) locking **≤1.028×** and negative delta vs Trial 1.

### Compliance reports (Phase 3 checked against Phase 1+2)

| | Trial 1 | Trial 2 |
|---|---|---|
| Verdict | **FAIL** | **FAIL** |
| Total / PASS / VIOLATION | 22 / **17** / **5** | 23 / **20** / **3** |
| Max violation authority | 1 | 1 |
| Infeasibility | false | false |

**Trial 1 VIOLATIONs:** REQ-F-004, REQ-F-012, REQ-P-003, REQ-P-004, REQ-A-004  
(mostly Tier-B numerical BFM closure deferred to P4/5, SystemC BFM mandate, missing makespan table, missing REQ-P-004 decomposition).

**Trial 2 VIOLATIONs:** REQ-F-002, REQ-F-012, REQ-P-003  
(missing REQ-U decomposition for broadcast/allgather; SystemC BFM; missing makespan baseline table).  
REQ-F-004 / REQ-A-004 **PASS** under Tier-A (no router arithmetic to prove). REQ-P-004 **PASS** via area iron.

[RAT: WARNING] Both trials still FAIL compliance on REQ-F-012 (SystemC BFM) and REQ-P-003 (makespan baselines). Promoting Trial 2 does not clear the gate; it improves alignment and reduces violation count.

---

## Alignment with user Trial-2 goals

| User goal | Trial 2 status | Evidence |
|---|---|---|
| **Area reduction** (below Trial 1 1.065×) | **Met** | 1.028× analytic; ADR-004 / REQ-A-006 / ppa-analytic |
| **No router reduce** | **Met** | Tier A USER_CONFIRMED; combine/DCA ABSENT |
| **Architecture / μArch diagrams** | **Met** | `architecture-diagram.md`, `uarch-diagram.md` |
| **No Phase 4** | **Met** | Phase-2/3 summaries: “No Phase 4” / “Phase 4 RTL: not started (DSE stop)” |

Trial 1 does **not** meet these goals (Tier B combine present, higher area, no dedicated diagrams, P4/5 numerical closure still in handoff narrative).

---

## Recommendation

[RAT: DECISION | USER_CONFIRMED] **Promote Trial 2 (Arch-A2) as the new current-best DSE result.**

Rationale:

1. User already requested the Trial-2 binding changes (area-first, no in-router reduction, diagrams, stop before Phase 4); Trial 2 ADRs are `USER_CONFIRMED`.
2. Analytic PPA improves vs Trial 1 without cutting calendar/BG resources that protect P0 replay.
3. Dedicated arch/μArch diagrams exist and explicitly document combine/DCA absence.
4. Compliance violation count drops 5 → 3; Tier-B numerical ACs that forced Trial-1 FAIL are no longer applicable.

**Caveats before treating compliance as green:** still resolve or waive REQ-F-012 (SystemC vs portable C BFM) and REQ-P-003 (publish the 10-point makespan/overhead table), and fix REQ-F-002 REQ-U decomposition in Trial 2 iron/traceability.

**Promote action (suggested):** copy/merge Trial-2 worktree docs+state into the main `booksim2` tree as the new baseline, retaining Trial-1 artifacts under an archive or prior-trial tag for A/B history.
