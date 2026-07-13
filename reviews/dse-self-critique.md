# DSE Self-Critique — Trial 5 (Arch-A5)

**Date:** 2026-07-13  
**Scope:** P1 inherit + P2/P3 Arch-A5 CalFork + SharedPool 28+2

## Findings

### HIGH
None.

### MEDIUM
1. **CalFork area 0.025 is analytic mid-band** — synthesis may land 0.020–0.030.  
   *Mitigation:* sensitivity table documents band; ADR-005 records assumption.
2. **Pool 28 burst margin** — smaller than Trial 4’s 40 under adversarial multi-port BG.  
   *Mitigation:* reserve=2 + RefC PASS; soft+pool bound ~188; 24 sensitivity documented.
3. **Crossbar still 0.380** — ~51% of total; neither lever addresses it.  
   *Note:* expected; convergence section recommends stop vs XB lever.

### LOW
1. Chinese summaries present; English ADRs retained (consistent with prior trials).
2. BFM is RefC-linked smoke; no separate cycle-accurate SystemC elaboration this trial.

## ADR invalidation
- ADR-001 algorithm: **not invalidated**
- ADR-002 architecture: **updated** to A5 (user-directed), not invalidated
- ADR-003 Tier A: **not invalidated**

## Critique closure
All HIGH = none. MEDIUM documented. Ready for user comparison / satisfaction.
