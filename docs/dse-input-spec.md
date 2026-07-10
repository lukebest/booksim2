# DSE Input Spec: 6×8 Mesh Calendar-Collective Router

**Project**: booksim2 calendar-collective NoC router microarchitecture  
**Target**: power/area-optimal router for 6×8 2D mesh with calendar-scheduled collectives + XY background traffic  
**Process**: rat-dse Phase 1→3 (research → architecture → μArch + BFM); analytic PPA (no RTL synth in this trial)  
**Date**: 2026-07-10

---

## Trial 2 Binding Decision Note

- **Trial:** DSE Trial 2 (new trial; Trial 1 artifacts are retained only as comparison history).
- **Priority:** Area-first. The selected architecture must target relative router area **below 1.065×** the baseline five-port XY router, the Trial 1 reported area.
- **Reduction direction:** **Tier A is selected.** Reduce is calendar-scheduled gather followed by PE-local compute; allreduce is gather + PE-local compute + calendar-scheduled broadcast. No in-router arithmetic/combine datapath and no DCA interface are in scope for Trial 2.
- **Retained stack:** Calendar replay, XY background routing, multicast fork, and watchdog recovery remain in scope. Architecture diagrams are a Phase 2 deliverable.
- **Boundary:** This trial ends at Phase 3; no Phase 4 RTL work is authorized.

---

## 1. System Context

| Parameter | Value | Notes |
|-----------|-------|-------|
| Topology | 6×8 2D mesh (48 nodes) | Compute tiles; single physical NoC |
| Link bandwidth | 64 B/cycle/direction | 512-bit flit |
| Flit size | 64 B | One flit = one beat |
| Clock | 2 GHz | Analytic power uses this |
| Per-hop wire delay | H=7 (X), V=9 (Y) cycles | Align with `utils/allgather_6x8_rigid.py`, `results/superpose_6x8.json` |
| PE↔router ramp | 1 cycle latency; `ramp_bw=1` flit/cycle | Down-ramp can bind gather/reduce |
| Calendar source | Existing zero-buffer schedules | `results/superpose_6x8.json`, `utils/sched_zerobuf_compare.py` family |
| Background traffic | XY (DOR) unicast | Shares same physical network with calendar traffic |

Literature anchors:
- `/home/luke/wiki/concepts/collective-capable-noc.md` (FlooNoC collective + DCA area/perf)
- `/home/luke/wiki/concepts/noc-router-microarchitecture.md`
- `/home/luke/wiki/concepts/deterministic-routing-dor.md`

Prior schedule research (makespan baselines, not RTL):
- `results/report_superpose_6x8.html`, `results/superpose_6x8.json`
- `utils/sweep_allgather_scale.py`, `utils/allgather_6x8_rigid.py`

---

## 2. Functional Requirements

### 2.1 Calendar-scheduled collectives (REQ-CAL)

Router must load/replay per-collective calendars that minimize makespan for:

| Semantic | Hardware primitives needed |
|----------|----------------------------|
| **Broadcast** | Multicast fork (XY mask / calendar fork ports) |
| **Allgather** | Multicast fork + multi-source inject schedule |
| **Gather** | Tree converge; no intermediate combine |
| **Reduce** | Gather tree + PE-local compute (see §4 Tier A) |
| **Allreduce** | Gather + PE-local compute + broadcast |

Different semantics use **different calendars**. Calendar content is produced offline (existing Python packers); hardware stores/replays slot → (in_port, out_port_mask, opcode) mappings.

### 2.2 Guaranteed point-to-point (REQ-BG)

- Always-available XY (dimension-order) unicast for background / non-scheduled traffic.
- Shares the single 512b physical network with calendar traffic.
- Must not permanently starve under calendar load (progress guarantee).

### 2.3 Multicast (REQ-MC)

- In-network fork: one flit → multiple output ports per calendar slot or mask.
- Must support schedules used by broadcast / allgather on 6×8.

### 2.4 Robustness (REQ-ROB)

| Property | Requirement |
|----------|-------------|
| Deadlock freedom | Background: XY-DOR. Calendar: schedule is conflict-free by construction; mixed traffic needs isolation (VC and/or TDM) that preserves deadlock freedom |
| Hang freedom | No permanent stall: credit/ready-valid flow control; watchdog on calendar slots |
| No packet loss | Credit-based (or equivalent) flow control; buffers never overwrite live flits |
| Spec violation | If collective traffic arrives off-calendar (late/early/wrong port), system **still completes** delivery (graceful degradation: e.g. timeout → demote to XY unicast / buffered path; never drop) |

### 2.5 DCA boundary (REQ-DCA-OPT)

Direct Compute Access and router-local arithmetic are **not selected for Trial 2**. The DSE retains the Tier A/B/C comparison evidence, but Trial 2 implements reduce/allreduce as Tier A only (see §4).

---

## 3. Non-Functional / Optimization Goals

| Priority | Goal |
|----------|------|
| P0 | Satisfy REQ-CAL, REQ-BG, REQ-MC, REQ-ROB |
| P1 | **Area-first:** target relative router area **<1.065×** baseline using the analytic gate/bitwidth/port model; calibrate multicast against the FlooNoC ~+5.8% anchor and report B/C reduction deltas only as comparison evidence |
| P2 | Minimize makespan overhead vs existing zero-buffer schedule theory (superpose / allgather 6×8 baselines) |
| P3 | Prefer designs that keep tile area impact small if DCA is chosen (FlooNoC claim: full tile <1%) |

PPA method: **analytic** (no Yosys/DC in this DSE trial). Document assumptions (SRAM bitcell, crossbar mux, VC buffer flits, calendar table depth).

---

## 4. DCA Analysis Mandate (three tiers)

Compare for **Reduce** and **Allreduce** on 6×8 (H=7, V=9, ramp_bw=1, message sizes m=1…5 flits where applicable):

| Tier | Mechanism | Expected area impact | Makespan implication |
|------|-----------|----------------------|----------------------|
| **A. No in-network arithmetic** | Reduce = gather tree + local PE compute; Allreduce = gather+compute+bcast (or software ring) | Lowest router area | Highest latency (extra PE compute + possible re-inject) |
| **B. Router-local 2-input combine** | Lightweight combine in router (integer/bitwise; FP only if justified) | Medium | Mid; 2-input/hop limits fan-in like FlooNoC |
| **C. DCA** | Router sync + offload to tile FPU (FlooNoC-style); router stays control-light | Router +~17% class; tile <1% | Best arithmetic reduce throughput if FPU available |

Deliverable: quantitative comparison table (makespan estimate + relative area/power) and recommendation. **Trial 2 selection is Tier A, USER_CONFIRMED, because area is prioritized over Trial 1's Tier B makespan result.**

---

## 5. Architecture Dimensions to Explore (Phase 2)

Must evaluate at least:

1. **Calendar storage**: per-router timeslot table vs per-flow tag match vs source-routed flit (fork/turn opcodes in header)
2. **Isolation of calendar vs background**: dedicated VC + priority vs hard TDM slot reservation vs hybrid
3. **Buffering**: zero-buffer for calendar (schedule guarantees no conflict) + buffered VC for background vs shallow shared buffers for all
4. **Reduction**: retain A/B/C comparison; implement Tier A only
5. **Violation handling**: watchdog timeout + demote-to-XY path; credit reclaim; no drop

Each candidate: analytic power/area + makespan impact + robustness argument.

---

## 6. Phase 3 Expectations

- Selected μArch: pipeline stages, arbitration, credit flow control, calendar table organization, multicast fork, PE-local reduction handoff, violation-demotion FSM, and required architecture diagrams
- SystemC BFM under `bfm/` that can replay 6×8 calendars derived from `results/superpose_6x8.json` (or equivalent) and report makespan
- Iron requirements + traceability through Phase 3

---

## 7. Out of Scope (this trial)

- Full RTL + commercial/open synthesis PPA numbers
- Multi-die / AFIFO reticle CDC (existing `sc/` work is separate)
- Narrow+wide dual physical networks (user chose single 64B network)
- Collectives beyond: allgather, allreduce, broadcast, gather, reduce

---

## 8. Success Criteria

1. Iron requirements cover all P0 requirements with measurable acceptance criteria  
2. ≥3 architecture candidates compared on power/area/makespan/robustness  
3. Explicit DCA tier A/B/C analysis for reduce/allreduce  
4. One recommended μArch + BFM skeleton  
5. Trial 2 review records Tier A selection, relative-area target, and Phase 3-only boundary
