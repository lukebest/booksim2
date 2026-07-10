# ADR-002: Architecture Selection for DSE Trial 4

| Field | Value |
|---|---|
| **Status** | Accepted (Trial 4, USER_CONFIRMED) |
| **Date** | 2026-07-10 |
| **Decision source** | `USER_CONFIRMED` — SharedPool-BG area reduction on Arch-A3 base |
| **Related analysis** | [architecture-candidates.md](../phase-2-architecture/architecture-candidates.md), [ppa-analytic.md](../phase-2-architecture/ppa-analytic.md) |
| **Prior decisions** | [ADR-001](ADR-001-algorithm-selection.md), [ADR-003](ADR-003-dca-tier.md), Trial 3 ADR-002 (superseded for buffer organization) |
| **Input spec** | [dse-input-spec.md](../dse-input-spec.md) |

---

## Context

Trial 3 selected Arch-A3 SparseCal-Hybrid-ZB-NoCombine at analytic **1.000×** area
with dedicated BG FIFOs (5×20=100 flits, buffer class 0.365). Shared BG buffer pool
was explicitly deferred as “Trial 3b”. Trial 4 binding feedback requires continuing
area reduction via SharedPool-BG while keeping SparseCal, soft-prio, Tier A, and
zero-buffer calendar.

---

## Decision

Select **Arch-A4: SparseCal-SharedPool-ZB-NoCombine**.

| Metric | Trial 4 Arch-A4 | Trial 3 Arch-A3 | IQ-XY |
|---|---:|---:|---:|
| Relative area | **0.822** | 1.000 | 1.000 |
| Relative power | **0.92** | 0.95 | 1.00 |
| Calendar store | Sparse 2×128×23 | Sparse 2×128×23 | — |
| BG buffers | **Shared 40 + reserve 5×2 = 50** | Dedicated 5×20 = 100 | — |
| BG policy | Soft priority | Soft priority | — |
| Combine / DCA | Absent | Absent | — |

### Key deltas from Trial 3

1. Replace dedicated per-ingress FIFOs with **shared pool 40** + **per-port reserve 2**.
2. Prove deadlock freedom (XY-DOR + reserves + calendar isolation).
3. Update BG progress bounds: soft ~160 (reserve-covered); soft+pool ~200; hard 328.
4. Calendar path remains zero-buffer and never consumes the pool.
5. Demote→XY remains lossless via pool/reserves.

Rejected for this trial: restoring combine/DCA; dual physical networks; cutting
reserves to zero (deadlock/progress risk).

---

## Consequences

- `vc_buffers` μArch becomes SharedPool-BG; RefC/BFM allocate from pool+reserves.
- ADR-004 updated for PPA 0.822×; ADR-003 Tier A unchanged in substance.
- Diagrams and iron REQ-A-003 / REQ-U-002 updated; trial field = 4.
- No Phase 4 in this trial.
