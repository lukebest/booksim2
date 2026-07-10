# DSE Trial Comparison — Trial 2 vs Trial 3 (SparseCal)

- Date: 2026-07-10
- Reviewer: rtl-architect
- Trial A path: `/home/luke/workspace/booksim2` (current best / **Trial 2** Arch-A2)
- Trial B path: `/home/luke/workspace/booksim2-dse-trial3` (**Trial 3** Arch-A3 SparseCal)
- Chinese Trial-3 report (already present): `booksim2-dse-trial3/.rat/scratch/trial3-report-zh.md`

[RAT: THOUGHT] Comparing Phase-2/3 summaries, ADRs 002–004, `ppa-analytic.md`, iron/traceability, and diagrams. Analytic PPA only (no synthesis). No Phase 4 on either side.

---

## Verdict table (promote decision)

| Dimension | Trial A (Trial 2 / master) | Trial B (Trial 3 / SparseCal) | Winner |
|---|---|---|---|
| **Area / power** (vs IQ-XY) | **1.028× / 0.96×** | **1.000× / 0.95×** (−0.028 / −0.01 vs T2) | **Trial 3** |
| **Calendar storage** | Dense `2×1024×13` = 26,624 bit (area class **0.040**) | Sparse `2×128×23` = 5,888 bit (area class **0.009**, −78% bits) | **Trial 3** |
| **BG policy** | Hard **1-in-16** TDM; 12-hop bound **328** cy | **Soft-prio** (calendar on match; BG on idle); hard 328 retained as conservative; soft ~**160** cy | **Trial 3** |
| **Diagrams** | `architecture-diagram.md` + `uarch-diagram.md` (dense / hard TDM) | Same paths, **Trial-3-specific** Mermaid (sparse store, `next_event_match`, soft-prio) | **Trial 3** (updated) |
| **Promote?** | Current best on master | User-requested SparseCal; IQ-XY area parity | **YES — promote Trial 3** |

[RAT: DECISION | USER_CONFIRMED] **Promote Trial 3 (Arch-A3 SparseCal-Hybrid-ZB-NoCombine) as the new current-best DSE result.**

---

## Side-by-side summary

| Dimension | Trial 2 (A) | Trial 3 (B) | Note |
|---|---|---|---|
| **Architecture name** | Arch-A2 CalSlot-Hybrid-ZB-NoCombine | **Arch-A3 SparseCal-Hybrid-ZB-NoCombine** | Sparse evolution of A2 |
| **ADR-002** | Select Arch-A2 | Select Arch-A3 (supersedes A2) | Both `USER_CONFIRMED` |
| **ADR-003** | Tier A (no combine/DCA) | Tier A **reaffirmed** | Unchanged substance |
| **ADR-004** | PPA lock **1.028×** / 0.96× | PPA lock **1.000×** / 0.95× | Sparse calendar is the delta |
| **Calendar dispatch** | Slot-indexed dense read | **next-event match** (`entry.slot == counter`) | Counter wrap still 1024 |
| **Sparsity evidence** | N/A (dense retained for P0) | allreduce max **49**/router, max_slot **951**; depth **128** (>2× margin) | From `results/calendars/*_m1.json` |
| **Combine / DCA** | Absent (Tier A) | Absent (Tier A) | Same |
| **Control area class** | 0.185 | **0.188** (+0.003 match) | Net still −0.028 vs T2 |
| **Phase 4** | No | No | DSE stop both |
| **Decision source** | `USER_CONFIRMED` | `USER_CONFIRMED` (SparseCal + soft-prio) | Aligns with user request |

---

## Analytic PPA vs IQ-XY

Normalized to five-port 512-bit IQ-XY = 1.00 (`ppa-analytic.md` / `utils/ppa_analytic_model.py`).

| Candidate | XB | Buf | Cal | MC | Comb | Ctrl | **Area** | **Power** |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| IQ-XY baseline | 0.380 | 0.450 | 0 | 0 | 0 | 0.170 | 1.000 | 1.00 |
| Trial 1 Arch-A | 0.380 | 0.365 | 0.040 | 0.058 | 0.027 | 0.195 | 1.065 | 0.98 |
| **Trial 2 Arch-A2** | 0.380 | 0.365 | **0.040** | 0.058 | 0.000 | 0.185 | **1.028** | **0.96** |
| **Trial 3 Arch-A3** | 0.380 | 0.365 | **0.009** | 0.058 | 0.000 | **0.188** | **1.000** | **0.95** |

**Delta Trial 2 → Trial 3:** calendar −0.031, control +0.003 → **net −0.028 area**, **−0.01 power**.

[RAT: INSIGHT] Trial 3 reaches IQ-XY area parity without cutting BG FIFO RTT depth (P0 credit) and without restoring combine/DCA — the win is almost entirely dense→sparse calendar SRAM.

---

## Calendar storage & BG policy detail

### Trial 2 (dense + hard TDM)

- Store: double-buffered **2 × 1024 × 13-bit** packed `{valid, in_port, out_port_mask, opcode}` (`architecture.md` calendar_store).
- Replay: slot-index read each cycle; legal calendar owns compiled slot.
- BG: hard **1-in-16** non-borrowable window; 12-hop bound **328** cycles.
- Evidence: `docs/phase-2-architecture/architecture.md`, `architecture-diagram.md`, ADR-002/004.

### Trial 3 (sparse + soft-prio)

- Store: double-buffered **2 × 128 × 23-bit** ordered events `{slot[9:0], valid, in_port, out_port_mask, opcode}` = **5,888 bits/router**.
- Replay: `next_event_match` fires when `entry.slot ==` global counter (wrap 1024).
- BG: **soft priority** — calendar wins on match; BG uses non-matching cycles; never displaces a firing calendar event. Hard 1-in-16 retained as conservative reference (328 cy); occupancy-aware soft bound ~**160** cy.
- Depth rationale: measured max busy-router entries = **49** (allreduce m=1) → 128 gives >2× margin; max_slot ≈951 < 1024 wrap.
- Evidence: Trial-3 `architecture.md` / `architecture-diagram.md`, ADR-002/004, `ppa-analytic.md`, Chinese report `.rat/scratch/trial3-report-zh.md`.

---

## Diagrams

| Artifact | Trial 2 | Trial 3 |
|---|---|---|
| `docs/phase-2-architecture/architecture-diagram.md` | Dense `2×1024×13`, hard TDM | Sparse `2×128×23`, `next_event_match`, soft-prio section |
| `docs/phase-3-uarch/uarch-diagram.md` | Present (dense / hybrid TDM) | Present (SparseCal / soft-prio) |
| Combine/DCA in diagrams | Explicitly absent | Explicitly absent |

Both trials meet the “dedicated arch + μArch diagrams” bar; Trial 3 diagrams are the ones that match the SparseCal decision.

---

## Iron / traceability / compliance coherence (quick check)

### Docs look coherent for SparseCal

| Check | Trial 3 status |
|---|---|
| P2 iron `trial: 3`, Arch-A3 | Yes — `iron-requirements.json` header + REQ-A-001 sparse 128×23, REQ-A-002 soft-prio, REQ-A-006 area **1.000×** |
| P3 iron + traceability | `req-uarch-traceability.md` claims **100%** coverage; SparseCal/`next_event_match`/soft-prio mapped for REQ-F/A/U |
| ADR chain | ADR-002 Arch-A3, ADR-003 Tier A reaffirmed, ADR-004 SparseCal PPA — consistent with summaries |
| OPEN-1-001 | Phase-2 summary: closed via SparseCal |
| Unchanged P0 guards | BG FIFO depths not cut; dual-bank hot-swap retained; ZB calendar path retained |

[RAT: INSIGHT] Hierarchical Spec Compliance for SparseCal is **internally consistent** across architecture.md ↔ iron REQ-A-001/002/006 ↔ ADR-002/004 ↔ μArch traceability (100% claimed).

### Compliance JSON caveat (stale / not SparseCal-fresh)

| Report | Path | Observation |
|---|---|---|
| Trial 3 `compliance-report-p2.json` | `.rat/state/` | Verdict **PASS**, but summary still names **Arch-A2** — stale label vs Arch-A3 docs |
| Trial 3 `compliance-report-new.json` | `.rat/state/` | Same shape as Trial 2: **FAIL** 23/20/3 — violations `REQ-F-002`, `REQ-F-012`, `REQ-P-003` |
| Trial 3 `compliance-report-current.json` | `.rat/state/` | Looks like older Trial-1-era FAIL (5 violations incl. Tier-B-ish `REQ-F-004`/`REQ-A-004`) — **not** aligned with Tier-A SparseCal iron |

[RAT: WARNING] Promote Trial 3 on **document + ADR + PPA + user SparseCal intent**. Do **not** treat `.rat/state/compliance-report-*.json` in the Trial-3 worktree as a fresh SparseCal gate until re-run; iron/traceability markdown is the coherent source of truth today.

Shared residual compliance themes (same as Trial 2 new report): SystemC BFM mandate (`REQ-F-012`) and makespan baseline table (`REQ-P-003`) — promoting SparseCal does not auto-clear those unless waived or closed in a follow-up compliance pass.

---

## Alignment with user Trial-3 goals

| User goal | Trial 3 status | Evidence |
|---|---|---|
| **SparseCal** (replace dense calendar) | **Met** | 2×128×23; ADR-002/004; architecture + diagrams |
| **Depth 128** with evidence | **Met** | max 49/router; >2× margin; calendar JSON |
| **Soft-prio BG** | **Met** | REQ-A-002; soft ~160 / hard 328 |
| **Area ~1.000×** (IQ-XY parity) | **Met** | ppa-analytic / ADR-004 |
| **Keep Tier A** | **Met** | ADR-003 reaffirmed; no combine/DCA |
| **No Phase 4** | **Met** | Phase-2/3 summaries |
| **Diagrams updated** | **Met** | Trial-3 Mermaid sets |

Trial 2 does **not** meet SparseCal / soft-prio / 1.000× goals (still dense 1.028× + hard TDM).

---

## Recommendation

[RAT: DECISION | USER_CONFIRMED] **Promote Trial 3 → new current best on master.**

Rationale:

1. User explicitly requested SparseCal; Trial 3 ADRs are `USER_CONFIRMED`.
2. Analytic area reaches **IQ-XY parity (1.000×)** with better power (0.95×) without sacrificing P0 replay or Tier A.
3. Soft-prio improves BG progress bound (~160 vs 328) while preserving calendar determinism on match cycles.
4. Arch/μArch diagrams, iron IDs, and 100% traceability markdown are updated for SparseCal.
5. Residual work is compliance-JSON refresh + known BFM/makespan items — not a reason to keep dense Arch-A2 as best.

**Promote action (suggested):** merge Trial-3 worktree docs/ADRs/PPA/iron/μArch (and RefC/BFM if changed) into main `booksim2` as the new baseline; retain Trial-2 artifacts under prior-trial archive/tag for A/B history; re-run compliance checker so `.rat/state/` matches Arch-A3.

**Chinese companion report:** see worktree `.rat/scratch/trial3-report-zh.md` (full Chinese narrative; this file is the English structured comparison).
