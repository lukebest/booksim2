# DSE Self-Critique — Trial 4 (Arch-A4 SharedPool)

Date: 2026-07-10  
Scope: Phase 1→3 artifacts after SharedPool-BG integration.

## Findings

### HIGH
_None._ User decisions (SparseCal, soft-prio, Tier A, SharedPool 40+2) are consistent
with iron requirements and PPA targets. No ADR invalidation.

### MEDIUM

1. **M1 — Downstream credit vs local pool depth**  
   Vertical credit depth remains 20 while per-router total BG storage is 50 shared
   across ports. Full-rate multi-port bursts may backpressure earlier than Trial 3.
   **Mitigation:** reserves + soft-prio bounds documented; `test_shared_pool` covers
   exhaustion/reserve. Acceptable for analytic DSE; revisit in Phase 4 RTL sizing.

2. **M2 — Soft+pool bound (~200) is conservative, not measured**  
   The +40 pool-turnover addend is an adversarial analytic assumption, not a mesh
   measurement under calendar load.  
   **Mitigation:** hard 328 retained; reserve-covered ~160 unchanged for typical
   single-flit BG; document assumptions in architecture.md / ppa-workbook.

3. **M3 — Allocator μArch is behavioral (count-based), not free-list RTL**  
   RefC uses per-port queues + `shared_used` accounting equivalent to DAMQ reserves.
   RTL may prefer linked-list SRAM.  
   **Mitigation:** DPI/BFM semantics match iron; Phase 4 may refine structure without
   changing 40+2 capacity contract.

### LOW

1. **L1 — Area 0.822 is below the stated 0.85–0.92 band** (better). Call out in
   comparison tables so reviewers do not treat the band as a floor.
2. **L2 — Phase 1 docs still say Trial 3 in places**; light-touch inheritance is OK
   but a one-line Trial 4 banner would reduce confusion.

## Cross-phase consistency

| Check | Result |
|---|---|
| ADR-001 algorithm | Preserved |
| ADR-002 → Arch-A4 | Updated |
| ADR-003 Tier A | Reaffirmed |
| Calendar never uses pool | Enforced in router_step |
| PPA model vs docs | 0.822 / 0.92 aligned |
| REQ coverage | 100% in req-uarch-traceability.md |

## Critique closure actions

| ID | Action | Status |
|---|---|---|
| M1 | Documented in architecture.md + report | RESOLVED (doc) |
| M2 | Bounds table + assumptions | RESOLVED (doc) |
| M3 | Noted for Phase 4; BFM/RefC PASS | JUSTIFIED |
| L1/L2 | Noted | NOTE ONLY |

**Verdict:** Ready to present. No Phase 4. No HIGH carry-forward.
