# ADR-002: Architecture Selection for DSE Trial 5

| Field | Value |
|---|---|
| **Status** | Accepted (Trial 5, USER_CONFIRMED) |
| **Date** | 2026-07-13 |
| **Decision source** | `USER_CONFIRMED` — CalFork + aggressive SharedPool on Arch-A4 |
| **Related analysis** | [architecture-candidates.md](../phase-2-architecture/architecture-candidates.md), [ppa-analytic.md](../phase-2-architecture/ppa-analytic.md), [ADR-005](ADR-005-calfork-lean-multicast.md) |
| **Prior decisions** | [ADR-001](ADR-001-algorithm-selection.md), [ADR-003](ADR-003-dca-tier.md), Trial 4 ADR-002 (superseded for MC + pool size) |
| **Input spec** | [dse-input-spec.md](../dse-input-spec.md) |

---

## Context

Trial 4 locked Arch-A4 at analytic **0.822×** area (SharedPool 40+2=50,
FlooNoC-class MC 0.058). Remaining area: XB 0.380 | Buf 0.182 | Cal 0.009 |
MC 0.058 | Ctrl 0.193. Trial 5 binding feedback requires further cut via
**CalFork** (primary) and **aggressive SharedPool** (secondary), keeping P0
(calendar ZB, XY BG, demote lossless, Tier A).

---

## Decision

Select **Arch-A5: SparseCal-SharedPool-CalFork-ZB-NoCombine**.

| Metric | Trial 5 Arch-A5 | Trial 4 Arch-A4 | IQ-XY |
|---|---:|---:|---:|
| Relative area | **0.746** | 0.822 | 1.000 |
| Relative power | **0.90** | 0.92 | 1.00 |
| Calendar store | Sparse 2×128×23 | Sparse 2×128×23 | — |
| Multicast | **CalFork 0.025** | FlooNoC 0.058 | — |
| BG buffers | **Shared 28 + reserve 5×2 = 38** | Shared 40+2=50 | — |
| BG policy | Soft priority | Soft priority | — |
| Combine / DCA | Absent | Absent | — |

### Key deltas from Trial 4

1. Replace FlooNoC-class stream_fork area model with **CalFork** lean mask fork.
2. Shrink shared pool **40 → 28** (reserve 2 unchanged); total flits **50 → 38**.
3. Update BG pool-contention bound: soft+pool **~188** (was ~200).
4. Calendar path, Tier A, soft-prio, demote lossless unchanged.

Rejected: restoring combine/DCA; zero reserve; Phase 4 RTL in this trial.
Pool **24+2** documented as sensitivity (also RefC PASS) but not default.

---

## Consequences

- ADR-004 / ADR-005 record PPA and CalFork rationale.
- RefC macros `BG_SHARED_POOL_SIZE=28`; `cal_fork_expand()` used on calendar path.
- Diagrams and iron REQ-A / REQ-U updated; trial field = 5.
- No Phase 4 in this trial.
