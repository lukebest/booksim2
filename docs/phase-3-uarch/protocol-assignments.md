# Protocol Assignments

| Interface | Protocol | Width / rule | Rationale |
|---|---|---|---|
| calendar replay → fork/combine/switch | compiled slot ownership | `entry[12:0]`, `flit[511:0]`; no payload queue | Static schedule eliminates runtime payload backpressure. |
| BG/escape ingress → `vc_buffers` | ready/valid admission | `flit[511:0]`; ready only when selected bank has space | Lossless local admission and simple PE/mesh adaptation. |
| `vc_buffers` → RC → SA → ST | valid/ready request | flit plus route metadata | Separates one-cycle RC, SA, and ST stages; propagates stalls. |
| router egress → downstream BG/escape FIFO | credit-based flit transfer | `flit_valid`, `flit[511:0]`, `credit_valid` | Supports one flit/cycle under H=7/V=9 round-trip allocations. |
| calendar fork egress | atomic availability / commit | 5-bit mask, all selected outputs available | Preserves all-or-none multicast before commit. |
| PE NI inject/eject | ready/valid | `flit[511:0]`, class/header | Natural endpoint backpressure; one-flit staging. |
| optional DCA stub | ready/valid request/response, tied inactive | two operand flits + opcode + tag | Reserved naming/shape only; disabled in Trial 1. |

Credit counters never decrement below zero or increment above their horizontal
16-flit or vertical 20-flit allocation. BG is granted only in its non-borrowable
one-in-16 opportunity; calendar is conflict-free and owns every other compiled
slot. An escape releases calendar ownership before requesting the credited XY
class, preserving the acyclic dependency graph.
