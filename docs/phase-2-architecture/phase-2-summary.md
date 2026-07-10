# Phase 2 Summary — DSE Trial 1 (6×8 Mesh Calendar-Collective Router)

- **Date:** 2026-07-10
- **Trial:** DSE Trial 1 — power/area-optimal calendar-collective NoC router
- **Phase 2 gate:** **PASS** (compliance report `.rat/state/compliance-report-p2.json`)
- **Selected architecture:** **Arch-A CalSlot-Hybrid-ZB**
- **Artifacts:** `iron-requirements.json`, `architecture.md`, `architecture-candidates.md`, `ppa-analytic.md`, `ADR-001`, `ADR-002`, `refc/`, reviews in `reviews/phase-2-architecture/`

---

## Outcome

Phase 2 finalized a concrete router microarchitecture that instantiates the ADR-001
algorithm stack.  All five Phase-1 open items (OPEN-1-001..005) are resolved as
REQ-A-001..005.  Feature coverage is **22/22 (100%)**.  Architecture self-review
verdict is **PASS**.  Reference C smoke model builds and runs (`make -C refc test`).

---

## Selected Stack (Arch-A)

| Dimension | Trial-1 choice |
|---|---|
| Calendar | Double-buffered 1,024×13-bit per-router table; 2-bit calendar ID + epoch |
| Isolation | Hybrid: 1 protected BG slot / 16 + dedicated credited XY-DOR escape VC |
| Buffering | Zero-buffer calendar; 16 H / 20 V RTT BG credits (74-flit interior bound) |
| Multicast | Atomic 5-bit `out_port_mask` fork |
| Reduction | Tier B (integer/bitwise); Tier A PE fallback; Tier C DCA disabled |
| Violations | 32-cycle watchdog; lossless demotion to XY escape (328-cycle BG bound) |

**Analytic PPA:** relative area **1.065**, dynamic power **0.98** (+6.5% vs normalized
IQ-XY baseline).  Calendar makespan overhead **0–5%** after window-aware recompilation.

---

## Candidates Compared

Three P0-capable architectures were evaluated:

| Candidate | Area | Power | Calendar overhead | Selected |
|---|---:|---:|---|:---:|
| **Arch-A CalSlot-Hybrid-ZB** | 1.065 | 0.98 | 0–5% (recompiled) | **Yes** |
| Arch-B SrcRoute-VCPrio-Shared | 1.008 | 1.08 | 4–9% | No |
| Arch-C CalSlot-HardTDM-DCA | 1.237 | 1.23 | 0–2% (normal calendar) | No |

---

## Phase 3 Handoff

Phase 3 must instantiate RTL/μArch and provide SystemC BFM evidence for REQ-A
acceptance criteria (replay, mixed traffic, combine, demotion).  Analytic PPA and
makespan estimates are not acceptance proof.  ADR-001/002 remain `AGENT_ASSUMED`
pending user confirmation at Trial satisfaction check.
