---
name: Calendar collective BookSim2
overview: Extend BookSim2 with a new sim_type=calendar that models contention-free TDM ("calendar") scheduling of 7 collectives on a 12x16 heterogeneous mesh (H=4/V=8 cycle links, 1 flit/cycle ramps and NoC), with in-network reduce/combine and multicast fork, configurable message size M, and offline rescheduling under node/link faults. Combine theoretical makespan/period bounds with simulation, then emit a self-contained HTML report.
todos:
  - id: config
    content: Add calendar config params in src/booksim_config.cpp and register sim_type=calendar in TrafficManager::New (src/trafficmanager.cpp)
    status: completed
  - id: graph
    content: Implement MeshGraph (src/mesh_graph.*) with 12x16 H=4/V=8 latencies, ramps, fault application, and Dijkstra routing; add utils/gen_mesh_anynet.py
    status: completed
  - id: collective
    content: Implement collective dataflow generation (src/collective.*) for the 7 collectives with combine/fork and message size M
    status: completed
  - id: scheduler
    content: "Implement CalendarScheduler (src/calendar_scheduler.*): time-slot link reservation, combine/fork, makespan + period + feasibility"
    status: completed
  - id: tm
    content: "Implement CalendarTrafficManager (src/calendartrafficmanager.*): _SingleSim execution, validation, CSV result output; add runfiles/calendarconfig"
    status: completed
  - id: experiments
    content: "Implement utils/run_calendar_experiments.py: build + healthy M-sweep + representative fault sweep, aggregate results.csv"
    status: completed
  - id: report
    content: "Implement utils/gen_report.py: self-contained HTML report with topology SVG, theory bounds, charts, Q1/Q2 conclusions"
    status: completed
  - id: verify
    content: Build, smoke-test, run full sweeps, verify periods match M/191M/768M scaling and fault degradation trends; produce report.html
    status: in_progress
isProject: false
---

# Calendar-preconfigured collective communication on a 12x16 heterogeneous mesh (BookSim2 extension)

## Goal and answers we must produce
- Q1: For each collective (broadcast, reduce, gather, allgather, allreduce, alltoall, anytoany), can a static calendar (contention-free TDM, all patterns known ahead) reach the minimum makespan? What is the most efficient calendar's cycle period (initiation interval)?
- Q2: Under representative node/link faults (corner/edge/center; 1/2/4 each), can an offline-recomputed calendar still complete each collective, and by how much does makespan/period degrade vs the healthy topology?
- Deliver: simulation + theory, packaged as an HTML report.

## Model (fixed parameters)
- Mesh 12 (x) x 16 (y) = 192 nodes; node id `x + 12*y`, x in [0,11], y in [0,15].
- Link latency: horizontal 4 cycles, vertical 8 cycles. Ramp PE->router (up) and router->PE (down): 1 cycle, bandwidth 1 flit/cycle. Every NoC link: 1 flit/cycle.
- Message size `M` flits per source per collective (default M=1, swept over {1,4,16,64}).
- In-network compute allowed: routers may combine (reduce/allreduce) and fork/copy (broadcast/multicast).

## Why a new TDM layer (not the stock router)
Per the [topology exploration](a8bb9b9c-df0f-410a-b520-a47d33d1cb42), stock `mesh` (KNCube) only supports a single radix `k` with uniform 1-cycle links, so 12x16 with H=4/V=8 must use `anynet` (per-link latency from a `network_file`). Per the [traffic-manager exploration](dd984cb7-5ebc-4f96-b890-7b4539576ec1), the stock VC/wormhole `IQRouter` cannot combine or fork flits and does dynamic (non-calendar) allocation. Therefore we reuse BookSim2's config/build/run harness and `anynet` topology+latency model, but implement calendar scheduling and TDM switching (with combine/fork) in a new `sim_type=calendar` traffic manager, following `BatchTrafficManager` as the structural template and registering it in `TrafficManager::New()` (`src/trafficmanager.cpp:43`).

## Architecture
```mermaid
flowchart TD
  cfg["calendar config + params"] --> tmnew["TrafficManager::New (sim_type=calendar)"]
  tmnew --> ctm["CalendarTrafficManager"]
  gen["gen_mesh_anynet.py"] --> nf["mesh 12x16 network_file"]
  nf --> graph["MeshGraph (latency + adjacency + faults)"]
  ctm --> graph
  graph --> coll["Collective dataflow (7 types, M, combine/fork)"]
  coll --> sched["CalendarScheduler (time-slot link reservation)"]
  sched --> exec["TDM cycle-accurate execution -> makespan + period"]
  exec --> csv["results CSV"]
  csv --> report["gen_report.py -> report.html"]
```

## Theory (to be derived fully in the report; headline bounds, time in cycles)
- Latency-weighted Manhattan distance = `4*|dx| + 8*|dy|`; diameter (corner-to-corner) = `4*11 + 8*15 = 164`, +2 ramps = 166.
- Latency-bound collectives with combine/fork -- broadcast, reduce, allreduce: calendar reaches `tree_depth_latency + (M-1)` makespan; minimal period (initiation interval) = `M` (ramp-bound at root/each node). These CAN hit the latency lower bound.
- Ramp-bound collectives -- gather, allgather: a single down-ramp must absorb `(N-1)*M = 191*M` flits at 1 flit/cycle, so min makespan/period ~= `191*M`; the "minimum" is the bandwidth bound, achievable by keeping the bottleneck ramp busy.
- Bisection-bound collectives -- alltoall (and worst-case anytoany): horizontal cut (12 vertical links) gives `96*96*M/12 = 768*M`; vertical cut (16 horizontal links) gives `576*M`; dominant lower bound `~768*M`. Calendar can approach this with a phased schedule; the simulation reports the achieved efficiency vs the bound.
- Net answer to Q1: not every collective can reach the unconstrained latency minimum -- gather/allgather/alltoall are fundamentally bandwidth-bound, and for those the achievable "minimum makespan" equals the bandwidth bound, which a well-designed calendar (nearly) achieves. The most-efficient calendar period equals the bottleneck-link/ramp occupancy: `M` (bcast/reduce/allreduce), `~191*M` (gather/allgather), `~768*M` (alltoall/anytoany).

## Implementation steps

### 1. Config + factory wiring
- In `src/booksim_config.cpp` (constructor, near the traffic section ~line 158) register: `AddStrField("collective_type","allreduce")`, `_int_map["msg_size"]=1`, `_int_map["mesh_x"]=12`, `_int_map["mesh_y"]=16`, `_int_map["h_latency"]=4`, `_int_map["v_latency"]=8`, `_int_map["ramp_latency"]=1`, `_int_map["allow_combine"]=1`, `_int_map["allow_fork"]=1`, and fault vectors using the dual-registration pattern (`_int_map["fault_nodes"]=...; AddStrField("fault_nodes","")`, same for `fault_links`), plus `AddStrField("result_csv","")`.
- In `src/trafficmanager.cpp:43` `TrafficManager::New`, add `else if(sim_type=="calendar") result = new CalendarTrafficManager(config, net);` and `#include "calendartrafficmanager.hpp"`.
- New source files go in `src/` top-level so the Makefile wildcard (`src/Makefile:39`) and existing `-I.` include path pick them up with no Makefile edit.

### 2. Topology + graph + faults
- `utils/gen_mesh_anynet.py`: emit a 12x16 `anynet` `network_file` (router `x+12*y` -> its node, +x/-x links latency 4, +y/-y links latency 8, ramps latency 1), so the BookSim-native topology matches the model. Used both to drive runs and as cross-validation.
- `src/mesh_graph.{hpp,cpp}` (`MeshGraph`): build adjacency + per-link latency + ramp latency from config; apply `fault_nodes` (remove node + its ramps/links) and `fault_links` (remove a directed/undirected edge); expose neighbors, link latency, and shortest-path (Dijkstra on latency weights) for routing around faults.

### 3. Collective dataflow
- `src/collective.{hpp,cpp}`: given `collective_type`, `M`, set of live nodes, and `MeshGraph`, produce the logical dataflow DAG:
  - broadcast/reduce/allreduce: latency-aware spanning tree (min-latency tree; allreduce = reduce-tree then broadcast-tree or recursive-halving) with combine at reduce-internal nodes and fork at broadcast-internal nodes.
  - gather/allgather: all-to-root / all-pairs transfers (no combine) saturating bottleneck ramps.
  - alltoall: phased (row-dimension then column-dimension) decomposition to approach the bisection bound.
  - anytoany: arbitrary predefined traffic matrix (default: worst-case permutation + one random fixed permutation instance) handled by the generic scheduler.

### 4. Calendar scheduler (the core)
- `src/calendar_scheduler.{hpp,cpp}`: a time-expanded greedy list scheduler that reserves per-directed-link time slots (the "calendar") honoring per-link latency and the 1-flit/cycle bandwidth, supporting fork (one input -> multiple outputs, no extra cost on the shared link) and combine (multiple inputs at a node -> single output). Outputs:
  - `makespan` (one-shot completion time, cycles),
  - `period` = initiation interval = max link/ramp slot-occupancy (steady-state cycle period of the calendar),
  - feasibility flag, and per-link occupancy for validation (no oversubscription).

### 5. CalendarTrafficManager
- `src/calendartrafficmanager.{hpp,cpp}` (subclass of `TrafficManager`, template from `src/batchtrafficmanager.cpp`): override `_SingleSim()` to build graph -> dataflow -> schedule -> execute the calendar cycle-accurately and record makespan/period; write a CSV row (`collective,M,fault_desc,makespan,period,theo_bound,efficiency,feasible`) to `result_csv`. Validate the schedule against the channel model.

### 6. Theory + comparison
- Encode closed-form lower bounds (Section "Theory" above) in the report generator and as a `theo_bound` per run so each simulated result reports efficiency = bound/achieved.

### 7. Experiment driver
- `utils/run_calendar_experiments.py`: build (`make -C src`), then invoke `./src/booksim runfiles/calendarconfig collective_type=... msg_size=... fault_nodes=... fault_links=...` across:
  - Healthy sweep: 7 collectives x M in {1,4,16,64}.
  - Fault sweep (M=16): per collective, node faults and link (H and V) faults at corner/edge/center with counts 1/2/4 (representative set), offline rescheduled.
  - Aggregate all CSV rows into `results/results.csv`.
- Add `runfiles/calendarconfig` example config.

### 8. HTML report
- `utils/gen_report.py`: read `results/results.csv` and emit a self-contained `results/report.html` with: problem statement + model, an inline-SVG 12x16 topology diagram, theory tables as bullet/HTML tables, charts (inline SVG bars/lines: makespan vs M per collective, efficiency vs bound, degradation % under each fault scenario), and the Q1/Q2 conclusions. Self-contained (no external JS dependency) for offline viewing.

### 9. Build, run, verify
- `make -C src`, run a smoke config (broadcast, M=1, healthy) to verify makespan ~= diameter+ramps, run the full driver, open `results/report.html`, sanity-check that simulated periods match the theoretical `M / 191M / 768M` scaling and that fault degradations are monotonic in fault count and worse for low-path-diversity (corner) faults.

## Key files to create/modify
- Modify: `src/booksim_config.cpp` (params), `src/trafficmanager.cpp` (factory + include).
- Create (src/, top-level so no Makefile/include edits): `mesh_graph.{hpp,cpp}`, `collective.{hpp,cpp}`, `calendar_scheduler.{hpp,cpp}`, `calendartrafficmanager.{hpp,cpp}`.
- Create: `utils/gen_mesh_anynet.py`, `utils/run_calendar_experiments.py`, `utils/gen_report.py`, `runfiles/calendarconfig`.
- Output: `results/results.csv`, `results/report.html`.

## Assumptions (chosen defaults; tell me to change any)
- A failed node leaves the collective (collective defined over surviving nodes); a failed link is routed around using mesh path diversity.
- `anytoany` = arbitrary predefined traffic matrix; default instances are worst-case permutation + one fixed random permutation.
- Fault sweep uses M=16; healthy sweep uses M in {1,4,16,64}.
- Report is self-contained HTML with inline SVG charts (offline-safe).