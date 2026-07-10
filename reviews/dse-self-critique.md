# DSE Trial 1 Self-Critique: Phase 1 -> 3
- Date: 2026-07-10
- Reviewer: rtl-architect
- Upper Spec: `docs/dse-input-spec.md`
- Scope: Trial 1 artifacts, RefC, portable-C BFM, and Phase-2 compliance report
- Verdict: FAIL

## Executive verdict

The architecture selection is directionally reasonable, but the Phase-3 evidence does
not demonstrate the selected architecture and several claimed frozen contracts are
internally inconsistent. The PASS gates and 22/22 traceability are document mappings,
not verified requirement closure. ADR-001 and ADR-002 are **not invalidated**: the
findings require contract correction, schedule-aware BFM work, and recalibration before
the decisions can be treated as implementation-ready.

## Feature coverage audit

| Input-spec obligation | Evidence reviewed | Assessment |
|---|---|---|
| Per-collective calendar replay for broadcast, allgather, gather, reduce, allreduce | `docs/dse-input-spec.md:36-48`, BFM smoke | **Not demonstrated.** The smoke loads three entries manually and has no schedule-file loader. |
| XY BG progress, no starvation, 1 flit/cycle | `docs/dse-input-spec.md:51-54`, `refc/router.c` | **Not demonstrated.** The executable does not model the declared BG window, link latency, or credit-return latency. |
| Atomic multicast and no-loss recovery | `docs/dse-input-spec.md:56-68`, `refc/router.c`, `refc/watchdog_demote.c` | **Not demonstrated; implementation model can lose state.** |
| Tier A/B/C comparison for reduce/allreduce, m=1..5 | `docs/phase-1-research/dca-tier-analysis.md` | **Analysis present, but numerical basis is insufficiently reproducible.** |
| SystemC BFM that replays a derived/equivalent 6x8 calendar and reports makespan | `docs/dse-input-spec.md:117-121`, `bfm/` | **Missing.** A portable-C smoke wrapper was substituted without an approved requirement change. |
| PPA calibration to FlooNoC deltas | `docs/phase-2-architecture/ppa-analytic.md` | **Partially addressed.** Anchors are quoted but not converted through a traceable model or sensitivity analysis. |

## Findings

### HIGH-01: No required calendar-replay BFM exists
- Category: Spec compliance / verification
- Locations: `docs/dse-input-spec.md:117-121`; `bfm/src/main_smoke.c:33-43`; `bfm/Makefile:4-7`
- Evidence:
```c
mesh_sim_load_calendar(&mesh, 0U, 0U, 0U, &entry);
...
mesh_sim_load_calendar(&mesh, 1U, 0U, 1U, &entry);
...
mesh_sim_load_calendar(&mesh, 0U, 1U, 1U, &entry);
```
- Analysis: The input requires a SystemC BFM under `bfm/` that replays 6x8 calendars
  derived from `results/superpose_6x8.json` (or an equivalent schedule) and reports
  makespan. The BFM links RefC and contains only a three-entry hand-authored smoke
  scenario. There is no C or SystemC schedule parser, no calendar compiler/adapter,
  no JSON reference in BFM sources, and no end-to-end replay for broadcast, allgather,
  gather, reduce, or allreduce. The `results/superpose_6x8.json` file records
  makespan outcomes, not router slot entries; a reproducible conversion artifact is
  absent. Calling the smoke log `baseline=superpose_6x8` is therefore unsupported.
- Concrete re-run fix: Preserve both ADRs. Add a schedule-export schema and generator
  from the existing scheduler output to `{router, slot, in_port, mask, opcode}` files.
  Implement a BFM loader and replay all five semantics for m=1..5, with destination
  scoreboards and per-profile makespan comparison. If SystemC remains unavailable,
  explicitly amend the input requirement before claiming conformance; portable C is
  not automatically equivalent to the specified SystemC deliverable.

### HIGH-02: BFM/RefC timing and flow-control model cannot validate the Phase-2 performance contracts
- Category: Performance / verification
- Locations: `docs/phase-2-architecture/architecture.md:119-128`; `refc/mesh_sim.c:87-113`; `refc/router.c:154-167`
- Evidence:
```c
router_step(&mesh->router[id], mesh->cycle, &mesh->ingress[id],
            &outputs[id]);
...
mesh->ingress[next_id].flit[(uint32_t)port_opposite(output_port)] =
    outputs[id].flit[port_index];
...
router_add_credit(&mesh->router[id], output_port);
```
- Analysis: A hop arrives in the adjacent router on the next `mesh_sim_advance()`;
  no H=7/V=9 link pipeline, PE ramp, RC/SA/ST latency, or delayed credit return
  exists. BG traffic is serviced every model cycle whenever an output is free; it is
  not restricted to the required one-in-16 protected opportunity. Immediate
  `router_add_credit()` also eliminates the stated 16/20-flit credit round trip.
  Consequently the passing 45-cycle run cannot substantiate REQ-P-001, REQ-P-002,
  REQ-A-002's 212-cycle bound, buffer sizing, or makespan overhead.
- Concrete re-run fix: Retain Arch-A. Implement explicit link pipelines, endpoint
  ramp stages, per-egress credit-return events, protected-slot arbitration, and
  bounded queues in one independent BFM model. Add saturated 12-hop BG, mixed
  calendar/BG, and backpressure tests that check the 16-slot and end-to-end bounds.

### MEDIUM-01: The calendar-entry packing and executable representation are disconnected
- Category: Interface compliance / area
- Locations: `docs/phase-2-architecture/iron-requirements.json:8-20`; `docs/phase-3-uarch/uarch.md:5-7`; `refc/include/calendar_store.h:21-26`
- Evidence:
```json
"statement": "Each router shall replay ... {valid[0], in_port[2:0],
out_port_mask[4:0], opcode[3:0]}."
```
```c
typedef struct {
    uint8_t valid;
    uint8_t in_port;
    uint8_t out_port_mask;
    uint8_t opcode;
} calendar_entry_t;
```
- Analysis: The fields can fit exactly in 13 bits (1 + 3 + 5 + 4), but no packed
  encoding, range constraint, or legality rule is specified. The reference model
  stores four unpacked bytes (`sizeof(calendar_entry_t)`), so it uses 32 bits/entry,
  not the 13-bit physical representation used by the PPA. Thus the claimed
  26,624-bit storage and 0.040 relative area are not connected to a checked
  executable interface. Opcode legality and bank-header storage are also omitted
  from the PPA accounting.
- Concrete re-run fix: Freeze a packed `calendar_entry_t` layout (including invalid,
  forward, and combine opcode encodings), assert its width in C and RTL, and account
  separately for bank headers/CRC. Recalculate calendar SRAM and PPA using the
  physically stored width. This does not require changing the selected calendar-table
  architecture.

### HIGH-04: Claimed lossless atomic multicast/demotion is not represented by the executable model
- Category: Functional correctness / robustness
- Locations: `docs/phase-2-architecture/architecture.md:83-90,103-110`; `refc/router.c:137-151`; `refc/watchdog_demote.c:12-32`
- Evidence:
```c
if (inputs->valid[port_index] &&
    (inputs->flit[port_index].flit_class == FLIT_CLASS_CALENDAR)) {
    watchdog_demote_arm(&router->watchdog, cycle,
                        &inputs->flit[port_index]);
    break;
}
```
```c
watchdog->retained_flit = *flit;
...
demoted_flit->flit_class = FLIT_CLASS_DEMOTED;
watchdog->armed = false;
```
- Analysis: The model has one watchdog record, no release-once state, no accepted
  versus remaining leaf accounting, and no per-leaf escape construction. Each newly
  observed illegal calendar flit overwrites `retained_flit`; only the first calendar
  input port scanned is armed. A demoted flit follows its existing single `dst_x/y`,
  while a calendar fork does not create destination records for its mask leaves.
  This cannot establish the exact-once/no-drop guarantee for early, late, wrong-port,
  blocked, or multicast violations.
- Concrete re-run fix: Add a per-transaction violation context keyed by epoch,
  calendar/sequence identity and ingress, with release-once state, pending-leaf
  bitmap or leaf list, accepted-leaf accounting, queue admission/backpressure, and
  a scoreboarding test for every violation type. No ADR change is needed.

### HIGH-05: The zero-buffer calendar path can silently discard a blocked flit in the model
- Category: Functional correctness / robustness
- Locations: `refc/router.c:112-137`; `refc/mesh_sim.c:92-108`
- Evidence:
```c
if (calendar_store_replay(&router->calendar, cycle, &entry) &&
    ... && calendar_outputs_available(router, outputs, entry.out_port_mask)) {
    ...
} else {
    ...
}
```
```c
(void)memset(mesh->ingress, 0, sizeof(mesh->ingress));
```
- Analysis: When a valid scheduled calendar flit cannot fire because an output lacks
  credit or is already occupied, it is neither retained nor reliably moved to a
  demotion context. `mesh_sim_advance()` clears all ingress state after every router
  step. This directly contradicts the no-loss requirement and the architecture's
  assertion that a pre-commit fork retains the flit and complete leaf mask. The
  smoke does not exercise this path.
- Concrete re-run fix: Define the slot contract for unavailable calendar egress:
  either forbid it in a schedule model that includes credit state, or latch the
  flit/context and trigger an explicit bounded recovery path. Add a directed test
  with one unavailable fork output and verify no accepted or unserved leaf disappears.

### HIGH-06: Reduce/allreduce timing and semantics are not executable despite being declared frozen
- Category: Functional correctness / performance
- Locations: `docs/phase-3-uarch/uarch.md:74-83`; `refc/combine_unit.c:20-53`; `bfm/src/main_smoke.c:69-75`
- Evidence:
```c
if (!unit->operand_valid) {
    unit->operand = *input;
    unit->operand_valid = true;
    return false;
}
...
unit->operand_valid = false;
return true;
```
- Analysis: The reference combine produces a result on the second function call and
  has no three-cycle pipeline, reservation enforcement, tag, operand-order identity,
  result calendar slot, or allreduce broadcast phase. The sole test checks ADD lane 0
  for 7 + 9. AND/OR/XOR/MIN/MAX, all eight lanes, overflow, identity handling,
  source ordering, gather fallback, reduce root delivery, and allreduce replay are
  untested. The DCA A/B/C analysis is present, but Phase 3 has not provided the
  evidence required to validate the selected Tier-B implementation.
- Concrete re-run fix: Model a three-cycle tagged pipeline, schedule result egress
  explicitly, and run generated reduce/allreduce calendars for all six Tier-B
  operations and m=1..5. Scoreboard every lane against the declared modulo/identity
  rules, then separately test Tier-A fallback behavior.

### HIGH-07: The 212-cycle BG bound is not a conservative end-to-end derivation
- Category: Performance / contract quality
- Locations: `docs/phase-2-architecture/architecture.md:119-125`; `docs/phase-2-architecture/iron-requirements.json:26-38`
- Evidence:
```markdown
`16 × (|dx| + |dy|) + 20` cycles after enqueue, where 20 covers the
worst H/V link-credit round-trip class and control margin.
```
- Analysis: The bound equals 212 for twelve hops, but it adds only a single 20-cycle
  term after twelve 16-slot service waits. It does not include the stated H=7/V=9
  forward latency per hop, RC/SA/ST latency, endpoint ramp, or contention
  serialization at intermediate protected slots. "Downstream credit available"
  excludes one cause of waiting but does not eliminate forward propagation. The
  bound is therefore not proven and the acceptance criterion is misleadingly
  measurable rather than valid.
- Concrete re-run fix: Define the bound at a precise observation point, derive it
  from all per-hop service, forwarding, link, and endpoint terms, and validate the
  worst source/destination orientations in the corrected BFM. Keep the one-in-16
  policy unless the measured budget makes a different service period necessary.

### MEDIUM-02: Buffer organization, capacity, and SRAM-port claims contradict each other
- Category: Memory feasibility
- Locations: `docs/phase-2-architecture/architecture.md:50`; `docs/phase-3-uarch/uarch.md:85-96`
- Evidence:
```markdown
five ingress FIFO banks
...
four horizontal banks of 16 flits and two vertical banks of 20 flits
```
- Analysis: Four 16-flit plus two 20-flit banks total 104 flits, not the inherited
  74-flit aggregate. The text calls them both five ingress banks and six directional
  banks. One read/one write SRAM per bank does not establish that simultaneous BG
  arrivals, demotion leaf expansion, and one-flit/cycle egress can be served without
  banking conflicts. Phase 3 nevertheless marks exact partitioning and port behavior
  closed.
- Fix for re-run: Select one ownership model (per-input or per-egress), give its
  exact depths and independent read/write ports, then rerun the area and throughput
  accounting. This is compatible with Arch-A.

### MEDIUM-03: Calendar loading, double-bank activation, and epoch safety are documentation-only
- Category: Functional correctness
- Locations: `docs/phase-3-uarch/uarch.md:33-41`; `refc/calendar_store.c:18-30,48-53`
- Evidence:
```c
void calendar_store_select_bank(calendar_store_t *store, uint16_t bank)
{
    if ((store != NULL) && (bank < 2U)) {
        store->active_bank = bank;
    }
}
```
- Analysis: The model permits arbitrary active-bank selection; it has no inactive-bank
  write protection, slot-zero gate, old-epoch retirement, CRC/load-complete status,
  calendar ID, or epoch. No BFM test exercises reloading. This is a material gap for
  REQ-F-001, although it does not undermine the decision to use double banking.
- Fix for re-run: Add the bank-header state and transaction rules to the model and
  test active-write rejection, failed CRC, deferred activation, and no mixed epoch.

### MEDIUM-04: PPA ranking is not reproducible from the stated FlooNoC calibration
- Category: Area / power methodology
- Locations: `docs/phase-2-architecture/ppa-analytic.md:5-18,22-27`; `docs/phase-2-architecture/architecture-candidates.md:65-80`
- Evidence:
```markdown
| Arch-A CalSlot-Hybrid-ZB | 0.380 | 0.270 | 0.040 | 0.058 | 0.027 | 0.195 | 0.970 | 0.98 |
```
- Analysis: The report provides normalized components but no equations, source
  sizing, activity factors, technology normalization, or uncertainty intervals that
  derive 0.270 buffer area, 0.040 calendar area, 0.195 control, or 0.98 power.
  FlooNoC percentage deltas are cited as anchors but are simply inserted as
  components. The baseline is an input-queued router while the winner removes much
  of its buffering, so the claimed 3% area win is highly sensitive to unshown SRAM
  and crossbar assumptions.
- Fix for re-run: Publish the calculation workbook/script, bit/cell and mux
  coefficients, traffic activities, and a sensitivity sweep for calendar width,
  buffer depth, BG period, and calibration deltas. Keep the result as analytic until
  synthesis calibrates it.

### MEDIUM-05: Iron criteria and traceability are too weak to support their PASS verdicts
- Category: Requirements quality
- Locations: `docs/phase-3-uarch/iron-requirements.json:16,28,40,52,64`;
  `docs/phase-3-uarch/req-uarch-traceability.md:7-33`
- Evidence:
```json
{"ac_id": "REQ-U-004.AC-1",
 "description": "The BFM combines 7 and 9 with ADD into 16.",
 "test_method": "cocotb", "verifiable": true}
```
- Analysis: Five Phase-3 requirements each carry only one smoke-scale acceptance
  criterion. Their named method is cocotb, but no cocotb test appears in `bfm/`; the
  implementation is C. "Mapped" is then treated as 100% coverage even where the
  direct requirement demands every legal calendar, all semantics, mixed-traffic
  stress, and every violation type. Resolution rationales are substantive in
  Phase 2, but Phase-3 closure is not.
- Fix for re-run: Split each requirement into measurable feature, negative, stress,
  and performance tests with a test path, vector source, oracle, threshold, and
  evidence log. Trace to executed tests/results rather than module names alone.

### MEDIUM-06: DCA A/B/C comparison is structurally adequate but quantitatively under-supported
- Category: Architecture / DCA analysis
- Locations: `docs/phase-1-research/dca-tier-analysis.md:11-25,55-67`;
  `docs/phase-2-architecture/architecture-candidates.md:239-255`
- Evidence:
```markdown
B and C values come from the existing conflict-free 6×8 model
(`utils/sim_allreduce_scale.py`) ... B sets `inc_lat=3` ... C uses
`node_red_lat=12`.
```
- Analysis: The analysis covers all mandatory tiers, reduce/allreduce, m=1..5,
  area/power classes, and recommendation, so it meets the required structure.
  However, it does not show the schedule inputs, model invocation, extracted data,
  effect of 1-in-16 windows, calendar load/epoch cost, or the resource model that
  makes a 3-cycle combine compatible with zero-buffer slot replay. The surprising
  case where Tier-B allreduce is faster than its root reduce is explained, but not
  independently replayed. It should be treated as a hypothesis, not calibration.
- Fix for re-run: Archive scripts/commands and generated slot traces for each tier,
  replay the same traces in the corrected BFM, and report post-window results and
  sensitivity to combine/DCA latency. ADR-001's Tier-B choice need not change unless
  that evidence reverses the ordering.

### LOW-01: The claimed three-round Phase-3 review is incomplete
- Category: Review process
- Locations: `reviews/CLAUDE.md:68-74`; `reviews/phase-3-uarch/uarch-review.md:3-18`
- Evidence:
```markdown
Phases 1, 2, and 3 use 3-round iterative reviews:
- Round 1 (`*-review-r1.md`)
- Round 2 (`*-review-r2.md`)
- Round 3 (`*-review-r3.md`): Final pass (mandatory)
```
- Analysis: The consolidated review has Round 1 and Round 2 prose only; no
  `uarch-review-r1.md`, `uarch-review-r2.md`, or mandatory Round 3 artifact is
  present. This is a governance gap, not a functional defect.
- Fix for re-run: Preserve the evidence and record the three independently scoped
  rounds, with the final round explicitly checking all prior fixes against executed
  schedule-replay tests.

### LOW-02: The Phase-2 compliance report overstates verified evidence
- Category: Audit integrity
- Locations: `.rat/state/compliance-report-p2.json:5-23,115-120`;
  `docs/phase-2-architecture/phase-2-summary.md:13-16`
- Evidence:
```json
"summary": {"verdict": "PASS", "total": 27, "pass": 27,
"violation": 0, "untested": 0}
```
- Analysis: The report correctly describes many document-level checks, but marks
  REQ-F-012 PASS before Phase 3 and marks no criteria untested even though it cites a
  future BFM boundary. This conflates artifact presence with behavioral proof.
- Fix for re-run: Separate "specified/mapped", "model-tested", and "RTL-tested"
  statuses; mark future-phase criteria untested at the Phase-2 gate.

## Cross-phase consistency summary

- Phase 1 captured the key uncertainty set well and its A/B/C structure is adequate.
  The principal weakness is that the ambiguity score treated required Phase-3 evidence
  as future closure without a gate preventing a documentation-only PASS.
- Phase 2 resolves choices consistently with ADR-001/002, but the 212-cycle guarantee,
  calendar packing/PPA, and queue organization were frozen without executable proof.
- Phase 3 does not contradict the selected high-level algorithm, but it contradicts
  Phase-2 claims of exact timing, lossless demotion, credit behavior, and 13-bit SRAM
  implementation. Its traceability converts these contradictions into "MAPPED."

## Priority re-run plan

1. Freeze and pack the calendar/header/epoch encoding; correct storage and PPA inputs.
2. Produce post-window slot calendars from the schedule toolchain for all five
   collective semantics and m=1..5.
3. Replace the smoke-only harness with a timing-faithful replay BFM, including H/V
   links, ramps, credits, protected BG arbitration, fork accounting, and demotion.
4. Add end-to-end scoreboards for gather/reduce/allreduce plus negative/stress tests;
   report measured makespans against the listed baselines.
5. Recompute the BG bound and analytic PPA with published assumptions/sensitivity,
   then rerun the Phase-2 and Phase-3 gates.

## Severity count and ADR disposition

- HIGH: 6
- MEDIUM: 6
- LOW: 2
- ADR-001 invalidated: No
- ADR-002 invalidated: No

The ADRs remain provisional (`AGENT_ASSUMED`) and must not be promoted to
implementation-ready decisions until the HIGH findings are closed.
