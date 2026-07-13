# ADR-005: CalFork Lean Multicast (Trial 5)

| Field | Value |
|---|---|
| **Status** | Accepted (Trial 5, USER_CONFIRMED) |
| **Date** | 2026-07-13 |
| **Decision source** | `USER_CONFIRMED` — primary area lever LeanMulticast / CalFork |
| **Related** | ADR-002, ADR-004, [architecture.md](../phase-2-architecture/architecture.md) |

---

## Context

Trials 1–4 charged multicast as a general **FlooNoC-class stream_fork** at
**+5.8% (0.058)** relative area. The calendar already stores
`out_port_mask[4:0]` per sparse event; RefC performs a pure mask→port expand
with all-or-nothing credit commit. A general multi-stream fork engine is not
required for P0 calendar replay.

---

## Decision

Adopt **CalFork / LeanMulticast**:

1. Fork is **calendar-native**: driven only by sparse-event `out_port_mask`.
2. Hardware model: wire fanout + single all-or-nothing credit AND — **no**
   independent stream FSMs, **no** FlooNoC stream_fork pipeline, **no** combine.
3. Analytic MC area **0.025** (mid of requested **0.020–0.030** band).
4. RefC API: `cal_fork_expand()` alias over `multicast_expand()`.
5. Demote leaf context remains register-only via `watchdog_demote`.

| Model | MC area | Notes |
|---|---:|---|
| FlooNoC stream_fork (T1–T4) | 0.058 | Rejected for A5 |
| **CalFork lean** | **0.025** | **Selected** |
| Aggressive CalFork floor | 0.020 | Sensitivity (−0.005 more) |

---

## Consequences

- Area Δ vs A4 from CalFork alone: **−0.033** → ~0.789 with A4 buffers.
- Combined with pool 28: total **0.746**.
- P3 μArch names the block `CalFork`; BFM continues to link RefC.
- Does **not** restore combine/DCA (ADR-003).
