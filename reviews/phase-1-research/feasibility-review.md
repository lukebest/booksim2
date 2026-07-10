# Phase 1 Feasibility Review — DSE Trial 2

- Date: 2026-07-10
- Selected basis: USER_CONFIRMED Tier A stack in `docs/decisions/ADR-001-algorithm-selection.md`
- Verdict: **PASS (analytic feasibility only; Tier A area-first direction)**

## Assessment

The selected stack is implementable as synchronous RTL at the architectural level:

- A double-buffered 1,024-slot, 13-bit calendar is 26,624 bits (3.25 KiB) per router.
- The zero-buffer calendar path preserves slot ownership; the background XY VC absorbs the
  modeled credit round trip (74 flits / 37,888 bits at the stated interior upper bound).
- Atomic 512-bit output-mask fork requires per-output credit/ready qualification and is a
  bounded five-port control problem.
- Tier A sends gathered operands to PE-local compute and broadcasts the PE result for
  allreduce; it contains no router arithmetic, operand holding, reduction tag/opcode, or DCA path.
- Removing Trial 1's Tier B (~+2.7% class) and Tier C (~+16.9% class) hardware is consistent
  with the binding relative-router-area target below 1.065× baseline; the target remains analytic.

## Conditions before RTL freeze

1. Fix calendar/header/VC/credit widths and epoch handoff protocol from OPEN-1-001 and OPEN-1-003.
2. Fix the hybrid-window service period and prove the XY escape VC channel dependency graph
   is acyclic (OPEN-1-002).
3. Define PE-local reduction handoff, operation semantics, result reinjection, and PE-compute
   latency (OPEN-1-004); do not add router combine or DCA ports.
4. Define watchdog timeout, atomic credit transfer, multicast leaf tracking, and recovery
   latency bound (OPEN-1-005).
5. Run timing-aware implementation or synthesis analysis before claiming 2 GHz closure; the
   current DSE contains analytic latency/PPA estimates only.

## Verdict

**PASS.** No selected mechanism requires an unbounded structure, unsupported clock-domain
crossing, router arithmetic datapath, or DCA interface. Feasibility is conditional on the Phase 2
choices above and analytic area check; it does not constitute synthesized 2 GHz timing closure or
authorize Phase 4 RTL.
