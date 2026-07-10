# ADR-003: DCA Reduction Tier Selection for DSE Trial 1

| Field | Value |
|---|---|
| **Status** | Accepted (Trial-1 default; pending user confirmation) |
| **Date** | 2026-07-10 |
| **Decision source** | `AGENT_ASSUMED` — recorded per DSE policy when AskUserQuestion is unavailable; subject to user confirmation at Trial satisfaction check |
| **Related analysis** | [dca-tier-analysis.md](../phase-1-research/dca-tier-analysis.md) |
| **Prior decisions** | [ADR-001](ADR-001-algorithm-selection.md), [ADR-002](ADR-002-architecture-selection.md) |
| **Input spec** | [dse-input-spec.md](../dse-input-spec.md) |

---

## Context

REQ-F-004 and REQ-F-010 require comparing reduction tiers A (PE-local compute), B
(router-local two-input integer/bitwise combine), and C (DCA tile-FPU offload) for
reduce and allreduce on the 6×8 mesh at m=1..5 flits. Phase 2 frozen REQ-A-004
selects Tier B for Trial 1; this ADR records that choice and the rejected
alternatives pending explicit user confirmation.

---

## Decision

Select **Tier B** — router-local two-input lane-wise combine (AND, OR, XOR,
modulo-2⁶⁴ ADD, unsigned MIN, unsigned MAX) with a calendar-reserved 3-cycle
merge pipeline. IEEE floating-point and unsupported operations use Tier-A PE
compute. **Tier C DCA is disabled**; only an inactive stub is retained.

| Tier | Role in Trial 1 | Outcome |
|---|---|---|
| **A** | Fallback for FP / unsupported opcodes | Retained; higher reduce/allreduce latency |
| **B** | **Selected** primary in-network reduction | +2.7% area class; lowest allreduce cycles m=1..5 |
| **C** | Rejected for Trial 1 | +16.9% router class; 12-cycle visible DCA latency |

---

## Rationale

- Tier B meets the frozen opcode, operand-order, and 3-cycle latency contract in
  `docs/phase-2-architecture/iron-requirements.json` REQ-A-004.
- `refc/test_combine_ops.c` validates all six supported opcodes across eight
  64-bit lanes; calendar replay reports Tier-B combine activity on reduce and
  allreduce vectors.
- Tier C requires unresolved FPU arbitration, format, and SoC contracts; its
  latency and area class are not amortized for 1–5 flit messages.

---

## Consequences

- `combine_unit` is in the Trial-1 RTL scope; DCA interface remains tied inactive.
- Phase 4/5 must close REQ-U-004.AC-2 (full pipeline tagging and lane-wise
  numerical closure beyond calendar-replay opcode counting).
- User confirmation is required to promote this ADR from `AGENT_ASSUMED` to
  `USER_CONFIRMED` or to override with Tier A or Tier C.
