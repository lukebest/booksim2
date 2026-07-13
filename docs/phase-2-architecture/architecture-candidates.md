# DSE Trial 5 Architecture Candidates: CalFork + Aggressive SharedPool

## Scope and evaluation method

Trial 5 evaluates μArch area levers on the **Arch-A4** base under
**USER_CONFIRMED** constraints:

1. **Area-first:** beat A4 **0.822×**; target band **~0.75–0.79×** vs IQ-XY.
2. Keep SparseCal `2×128×23`, soft-prio, Tier A, zero-buffer calendar, demote→XY.
3. **Primary:** CalFork lean multicast (MC 0.058→~0.020–0.030).
4. **Secondary:** aggressive SharedPool (try 28+2 or 24+2) if P0-safe.
5. Physical params unchanged: 6×8, 512b @ 2 GHz, H=7, V=9, `ramp_bw=1`.

## Comparison matrix

| Candidate | Area | Power | MC | BG buffers | Verdict |
|---|---:|---:|---|---|---|
| **Arch-A5 SparseCal-SharedPool-CalFork-ZB-NoCombine** | **0.746** | **0.90** | CalFork 0.025 | Shared 28+res2=38 | **Selected** |
| Arch-A5b CalFork + pool 24+2 | 0.731 | ~0.90 | CalFork 0.025 | Shared 24+res2=34 | Sensitivity (RefC PASS; not default) |
| Arch-A5c CalFork-only (pool 40) | 0.789 | ~0.91 | CalFork 0.025 | Shared 40+res2=50 | CalFork alone (~0.79) |
| Arch-A4 (Trial 4) | 0.822 | 0.92 | FlooNoC 0.058 | Shared 40+res2=50 | Superseded |
| Arch-A3 (Trial 3) | 1.000 | 0.95 | FlooNoC 0.058 | Dedicated 100 | Superseded |
| Restore stream_fork / combine | ≥0.779 | — | FlooNoC / Tier B | — | Rejected (P0 Tier A + area) |

### Arch-A5 area breakdown

| Component | Rel. area | Notes |
|---|---:|---|
| Crossbar | 0.380 | Unchanged; dominates remaining mass |
| VC buffers | **0.139** | **38 flits SharedPool** |
| Calendar SRAM | 0.009 | Sparse 2×128×23 |
| CalFork MC | **0.025** | Lean mask fork (−0.033 vs FlooNoC) |
| Combine / DCA | 0.000 | Tier A |
| Control | 0.193 | Match + pool accounting |
| **Total** | **0.746** | **−0.076 vs Trial 4** |

## Deadlock / progress (selected 28+2)

- XY-DOR acyclic; per-port reserve=2; calendar never takes pool credits.
- Soft ~160 (reserve-covered); soft+pool ~188; hard 328.
- Pool 24+2 also RefC PASS — documented sensitivity only.

## Why not stop at CalFork-only (0.789)?

Both levers land inside the 0.75–0.79 target with margin. Pool shrink is
P0-safe under the same deadlock argument as Trial 4; CalFork is independent
of buffer organization.

---

# Appendix: Prior trial lineage (historical)

| Trial | Arch | Area | Key change |
|---|---|---:|---|
| 1 | Arch-A | 1.065 | Dense cal + combine |
| 2 | Arch-A2 | 1.028 | Drop combine (Tier A) |
| 3 | Arch-A3 | 1.000 | SparseCal |
| 4 | Arch-A4 | 0.822 | SharedPool 40+2 |
| **5** | **Arch-A5** | **0.746** | **CalFork + pool 28+2** |
