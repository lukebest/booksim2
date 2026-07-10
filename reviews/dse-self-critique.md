# DSE Self-Critique — Trial 3 (light pass)

Scope: Arch-A3 SparseCal rewrite from Trial 2 baseline. Harsh but focused.

## Findings

### HIGH
None remaining after SparseCal implementation.

| ID | Finding | Resolution |
|---|---|---|
| H1 | Dense 2×1024×13 over-provisions vs ≪1% occupancy | **RESOLVED** — SparseCal 2×128×23 |
| H2 | Area 1.028× above 0.97–1.00 target band | **RESOLVED** — analytic 1.000× |
| H3 | Hard 1-in-16 tax unnecessary under sparsity | **RESOLVED** — soft-prio selected; 328 retained as conservative ref |

### MEDIUM
| ID | Finding | Action |
|---|---|---|
| M1 | Shared BG buffer pool could further cut area | **Deferred** — Trial 3b / out of scope |
| M2 | Soft-prio bound ~160 is occupancy-model dependent | Documented; keep 328 as compliance ceiling |
| M3 | Phase 1 iron still Trial-2-flavored in places | Light-touch notes added; IDs preserved |

### LOW
| ID | Finding | Action |
|---|---|---|
| L1 | CAM vs sorted-list micro-choice for next-event | Note only; both fit 128 depth |
| L2 | Chinese summaries cover P2/P3; some EN iron JSON | Acceptable per trial brief |

## Cross-phase consistency
- ADR-002 → Arch-A3; ADR-003 Tier A reaffirmed; ADR-004 SparseCal PPA
- P2/P3 iron area 1.000× / power 0.95× aligned with `ppa_analytic_model.py`
- RefC/BFM implement sparse match + soft-prio

## Critique closure
All HIGH findings RESOLVED. MEDIUM deferred or documented. Ready for user comparison/promotion. **No Phase 4.**
