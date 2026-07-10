# Arch-A CalSlot-Hybrid-ZB Microarchitecture

## Fixed implementation parameters

`MESH_X=6`, `MESH_Y=8`, `FLIT_W=512`, `PAYLOAD_W=496`, `LANES=8`,
`LANE_W=64`, `CAL_SLOTS=1024`, `CAL_ENTRY_W=13`, `BG_WINDOW=16`,
`H_LINK=7`, `V_LINK=9`, `RAMP=1`, and `noc_clk=2 GHz`.  All widths derive
from these parameters; no datapath width is literal.  A physical direction
accepts one `FLIT_W` flit/cycle when granted (`1 * 2 GHz = 128 GB/s` decimal).

Calendar traffic is a compiled, zero-payload-buffer path.  BG and escape
traffic use the single credited XY-DOR class.  The model uses one clock domain.

```mermaid
flowchart LR
  C0[S0: calendar SRAM read] -->|entry[12:0]| C1[S1: qualify and masked switch]
  B0[B0: route compute] -->|request metadata| B1[B1: switch allocate]
  B1 -->|flit[511:0]| B2[B2: switch traverse]
  C1 --> FORK[multicast fork]
  FORK --> COMB[combine pipeline: 3 cycles]
  B2 --> XBAR[5x5 crossbar]
  COMB --> XBAR
  XBAR --> LINK[H=7 / V=9 pipeline registers]
  C1 -.->|mismatch or timeout| WD[watchdog demote]
  WD --> B0
```

## Module specifications

### `calendar_store`
- **Decomposition/clock:** `inactive_loader`, `bank_header`, and `active_reader`
  are all in `noc_clk`; no CDC exists.
- **Storage:** two single-clock, two-port SRAM wrappers, each
  `DEPTH=1024`, `WIDTH=13`, `ADDR_W=$clog2(CAL_SLOTS)`, 1-cycle registered
  read. One inactive-bank write and one active-bank read are permitted per
  cycle. Total is 26,624 bits, so SRAM is mandatory. Headers are flip-flops:
  `calendar_id[1:0]`, epoch, CRC-good, and load-complete.
- **Pipeline/hazards:** S0 reads `slot=cycle % CAL_SLOTS`; S1 qualifies valid,
  port, mask, and opcode. Activation only at slot zero after old-epoch
  retirement and a complete, CRC-good inactive header; active writes are
  rejected. No read-after-write ambiguity exists because banks are distinct.

### `calendar_replay`
- **Decomposition/clock:** combinational legality decoder plus S1 registered
  request in `noc_clk`. A valid entry requires a legal input port and nonzero
  mask for forwarding opcodes.
- **Protocol/partitioning:** consumes a registered calendar entry and
  calendar-class input; presents an unbackpressured compiled request to the
  fork/combine selector. A missing, early, or wrong-port flit is sent to
  `watchdog_demote`; it never enters `vc_buffers`.
- **Registers:** entry and qualification result; no SRAM/FSM.

### `xy_route`
- **Decomposition/clock:** combinational X-before-Y decoder followed by one
  registered RC request in `noc_clk`.
- **Protocol:** BG/escape FIFO head -> valid/ready RC request. Route is
  `E/W` while `dst_x != x`, then `N/S` while `dst_y != y`, else local.
- **Allocation:** metadata width is
  `FLIT_W + $clog2(MESH_X) + $clog2(MESH_Y) + CLASS_W`; route decision is
  registered to break the RC-to-SA path.

### `multicast_fork`
- **Decomposition/clock:** availability reduction and atomic commit register,
  both in `noc_clk`.
- **Protocol:** consumes one legal replay request with `out_port_mask[4:0]`;
  it asserts all selected credit-valid requests only if every selected egress
  is available at the slot boundary. Otherwise the flit and complete leaf mask
  remain registered. After any externally observed partial acceptance, accepted
  bits clear and only `remaining_leaf_mask[4:0]` is demoted.
- **Storage/FSM:** flit context register (`FLIT_W+5` bits) and two-state
  `IDLE/HELD` FSM. This is below 256 bits of control plus one flit datapath;
  it is flip-flops.

### `combine_unit`
- **Decomposition/clock:** input-pair latch, lane operation stage, and output
  register are all `noc_clk`; latency is exactly three reserved calendar cycles.
- **Datapath:** eight independent `LANE_W` operations: AND, OR, XOR,
  modulo-`2^LANE_W` ADD, unsigned MIN, or unsigned MAX. Operand ordering is
  compiler-emitted left-to-right. Unsupported/FP opcodes are handed to `pe_ni`
  Tier-A; DCA is not used.
- **Storage/FSM:** two `FLIT_W` operand/result pipeline registers and a valid
  shift `[2:0]`; no SRAM. A new pair is accepted only when its reserved slots
  are valid, avoiding overwrite.

### `vc_buffers`
- **Decomposition/clock:** five ingress FIFO banks and dequeue arbiters in
  `noc_clk`. Calendar has no FIFO.
- **Storage:** BG and escape share per-ingress partitioned queues. The
  interior provision is 74 flits (`74 * FLIT_W = 37,888` bits), implemented as
  banked synchronous SRAM wrappers: four horizontal banks of 16 flits and two
  vertical banks of 20 flits, with output-class admission accounting. Each bank
  is `DEPTH={16|20}`, `WIDTH=FLIT_W`, one write and one registered read;
  `ADDR_W=$clog2(DEPTH)`. The BFM uses equivalent bounded C queues.
- **Hazards:** a full queue deasserts ready; dequeue returns exactly one credit.
  Escape retains its class bit for observability but has no second dependency
  class.

### `switch_alloc`
- **Decomposition/clock:** protected-slot detector, five-output request matrix,
  and round-robin grant registers in `noc_clk`.
- **Protocol:** RC valid/ready requests are eligible only with positive egress
  credit. Calendar owns legal compiled slots; BG receives one non-borrowable
  opportunity each `BG_WINDOW` slots. SA grants at most one source per output
  and one output per unicast head.
- **Storage/FSM:** five 3-bit round-robin pointers plus grant registers. The
  5x5 request matrix is control flip-flops. Eligible BG obtains a hop grant in
  at most 16 slots.

### `crossbar`
- **Decomposition/clock:** registered 5x5 select then `FLIT_W` traverse
  register in `noc_clk`; no storage ownership.
- **Timing:** ST emits one granted flit/cycle after fill. Links are explicit
  pipeline-register chains of `H_LINK` or `V_LINK` cycles; they are analytic
  model latency, not a second clock.

### `credit_fc`
- **Decomposition/clock:** per-egress credit counters and delayed-return
  scheduler in `noc_clk`.
- **Storage/protocol:** horizontal counters are 5 bits for 0..16; vertical
  counters are 5 bits for 0..20. `credit_valid` increments exactly once after
  downstream release, saturates at allocation, and a sender only transfers with
  count nonzero. Calendar fork availability samples the same egress condition
  but does not consume payload VC credit.

### `watchdog_demote`
- **Decomposition/clock:** slot comparator, 32-cycle timer, release-once latch,
  and escape constructor are in `noc_clk`.
- **FSM:** `IDLE -> ARMED -> DEMOTE_WAIT -> EMIT -> IDLE`. Early or wrong-port
  enters `DEMOTE_WAIT` immediately; absent/blocked expected arrival expires at
  32 cycles. `released` prevents a duplicate reservation release. `EMIT`
  creates one escape flit per remaining leaf only when `vc_buffers` is ready.
- **Storage:** preserved flit plus `remaining_leaf_mask[4:0]`, fault code, and
  timer are registers; no loss is permitted under normal backpressure.

### `pe_ni`
- **Decomposition/clock:** local ready/valid adapter, injection/ejection
  staging, and Tier-A dispatch in `noc_clk`.
- **Protocol:** PE traffic uses ready/valid; a handshake enqueues BG or presents
  a calendar input at its compiled local slot. Ejection holds `o_valid` until
  PE `i_ready`. The one-flit staging registers are flip-flops. The DCA stub
  exposes `dca_req_valid=0`, `dca_rsp_ready=0`; no Trial-1 transaction is
  accepted.

## Signal and throughput conventions

Future RTL uses `i_`, `o_`, and `io_` port prefixes, `noc_clk`/`noc_rst_n`,
`u_` instances, `UPPER_SNAKE_CASE` parameters, and upper-snake FSM states.
Calendar S0/S1 starts one legal transfer per compiled slot, while BG RC/SA/ST
starts one eligible flit/cycle after fill at a granted opportunity. Therefore
both paths meet `rate_per_cycle * 2 GHz >= 2 GHz` per granted direction.
