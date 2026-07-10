# ADR-003: DCA Reduction Tier Selection for DSE Trial 2

| Field | Value |
|---|---|
| **Status** | Accepted (Trial 2, USER_CONFIRMED) |
| **Date** | 2026-07-10 |
| **Decision source** | `USER_CONFIRMED` — binding Trial 2 feedback: no in-router reduction |
| **Related analysis** | [dca-tier-analysis.md](../phase-1-research/dca-tier-analysis.md) |
| **Prior decisions** | [ADR-001](ADR-001-algorithm-selection.md), [ADR-002](ADR-002-architecture-selection.md) |
| **Supersedes** | Trial 1 ADR-003 Tier B selection |

---

## Context

REQ-F-004 / REQ-F-010 require A/B/C comparison. Trial 1 selected Tier B (+2.7% area,
best small-m allreduce). Trial 2 user feedback prioritizes area and explicitly forbids
in-router reduction.

---

## Decision

Select **Tier A** — no in-network arithmetic.

| Collective | Mechanism |
|---|---|
| Reduce | Calendar gather + **PE-local compute** |
| Allreduce | Gather + PE-local compute + calendar broadcast |

| Tier | Trial 2 role |
|---|---|
| **A** | **Selected** — lowest router area; PE owns arithmetic |
| **B** | Rejected — would reintroduce combine (+2.7% class) |
| **C** | Rejected — DCA (+16.9% class) out of scope |

---

## Consequences

- Router datapath has **no** `combine_unit` and **no** DCA interface/datapath.
- Makespan for reduce/allreduce is higher than Trial 1 Tier B; accepted under P1 area-first.
- RefC/BFM must not implement router arithmetic; `CAL_OP_PE_HANDOFF` is a forward/tag only.
