# Phase 1 Assumptions and Explicit Unknowns

Date: 2026-07-10  
Scope: DSE Trial 1, 6x8 mesh calendar-collective router

This document records only derived values and unresolved specification gaps. It does not add implementation requirements.

## Derived analytic values

- `noc_clk` is 2 GHz, therefore the analytic cycle period is 0.5 ns.
- Horizontal link delay is 7 cycles = 3.5 ns.
- Vertical link delay is 9 cycles = 4.5 ns.
- PE-to-router ramp latency is 1 cycle = 0.5 ns.
- A 512-bit flit is 64 B. At one flit/cycle on a granted direction, throughput is 64 B/cycle = 128 GB/s decimal at 2 GHz.
- The five router ports are North, East, South, West, and Local. The DCA interface is optional and exists only if Phase 2 selects tier C.

## Non-assumptions / Phase 2 resolution required

- Calendar loading protocol, calendar-table depth, slot count, calendar ID width, and epoch/reset behavior.
- Header packing and bit widths for destination, mask/fork, opcode, calendar ID/slot, VC, and credits. The only specified flit width is 512 bits.
- Flow-control implementation: the source permits credit-based flow control or equivalent ready/valid; the IO definition presents a ready/valid candidate without choosing it.
- Buffer depth, credit return latency, VC count, and arbitration policy.
- Calendar/background isolation mechanism and any quantitative background progress bound.
- Router pipeline latency separate from the stated wire-delay model.
- Watchdog timeout, error reporting, credit-reclaim mechanics, and how a malformed multicast is expanded or translated for XY demotion.
- Reduction operation semantics, numerical formats, element ordering, DCA FPU availability/latency, and core/DCA sharing policy.
- Absolute PPA goals, process/library parameters, and a numerical maximum makespan-overhead target.

## Scope boundaries retained from the input specification

- This trial uses one 64 B physical network; narrow/wide dual networks are out of scope.
- Multi-die/AFIFO CDC is out of scope; no CDC crossings are assumed for the single `noc_clk` domain.
- Full RTL and synthesis PPA measurements are out of scope; PPA is analytic.
