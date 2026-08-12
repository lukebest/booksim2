#!/usr/bin/env python3
"""Fault tolerance and jitter tolerance of the ring collective calendars.

Faults
------
Three outcomes per scenario, because they cost different things:

    immune      the healthy calendar never touches the faulty resource, so it
                keeps running bit-identically. No recompile, no inflation.
    recompile   a new calendar exists. Report the inflation, since that is what
                a run-time recompile buys you.
    infeasible  this algorithm cannot be scheduled at all under this fault.

The `bypass` sweep prices one specific piece of hardware. A bufferless ring
station sits IN the ring, so a dead node without a bypass mux breaks the two
segments on each of its two rings -- one dead node becomes four dead segments
and both of its rings degrade to paths. With a bypass mux the wire keeps
conducting and only the node's ramp is lost. Running every node-fault scenario
both ways turns "you probably want a bypass mux" into a number.

Wrap-segment faults are added on top of the repo's link/node/quadrant set
because they are the ring's own failure mode: connectivity survives (every ring
is still a path) but the *cycle* does not, and any schedule that assumed a cycle
has to be rebuilt.

Jitter
------
A rigid calendar can only be replayed late in two conflict-free ways: shift
everything by one constant, or shift each phase by its own constant. Anything
finer moves transfers relative to their neighbours and breaks R1/R2/R3. So
`global_shift` and `phase_shift` are not two design points among many, they are
the complete set, and the gap between them is exactly what per-phase
resynchronization hardware buys.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Sequence

from rg_ring_calendar import (
    FaultModel, build_calendar, fault_sweep, jitter_sweep, release_offsets,
    replay_jitter, repo_fault_scenarios, scattered_node_scenarios,
    wrap_link_scenarios,
)
from rg_ring_collectives import build_ring_collective, replay
from rg_ring_topo import RingTopology, verify_dr

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results" / "ring_robust_8x6.json"
ROOT_NODE = 27
M = 13
JITTER_GRID = (0, 2, 4, 8, 16, 32, 64, 128, 256, 512)

# One entry per structural lever, so a robustness difference points at a lever.
SCHEMES: tuple[tuple[str, str, str, str], ...] = (
    ("broadcast", "dim_2phase", "T1", "arc multicast + bidirectional half-arc"),
    ("broadcast", "flat", "T0", "root unicasts to everyone"),
    ("allgather", "dim_2phase", "T1", "arc multicast, both dimensions"),
    ("allgather", "flat", "T0", "flat unicast, planner routed"),
    ("allgather", "ring_rotate", "T0", "full-cycle rotation"),
    ("reduce", "dim_2phase", "T0", "L1 accumulate chain, two dimensions"),
    ("reduce", "flat", "T0", "everyone unicasts to root"),
    ("allreduce", "dim_2phase", "T1", "L1 chain then arc multicast"),
    ("gather", "flat", "T0", "no folding: root ejects everything"),
    ("alltoall", "flat", "T0", "no structure to exploit"),
)


def bypass_price(topo: RingTopology) -> list[dict[str, Any]]:
    """Node faults with and without a ring-station bypass mux.

    Both the repo's contiguous holes and the scattered scenarios are included,
    because only the scattered ones can show a difference: a contiguous hole
    leaves the survivors as one path, which the other direction covers with no
    bypass hardware at all.
    """
    node_scen = [s for s in repo_fault_scenarios(topo)
                 if s.fault_class in ("node", "quadrant")]
    node_scen += scattered_node_scenarios(topo)
    out: list[dict[str, Any]] = []
    for pattern, algo, tier, _ in SCHEMES:
        rec: dict[str, Any] = {"pattern": pattern, "algo": algo, "tier": tier}
        for bp in (True, False):
            scen = [FaultModel(s.name, s.dead_nodes, s.dead_links, bp,
                               s.fault_class, s.desc) for s in node_scen]
            r = fault_sweep(topo, pattern, algo, tier, M, root=ROOT_NODE,
                            scenarios=scen)
            rec["bypass" if bp else "no_bypass"] = {
                "n_scenarios": r["n_scenarios"],
                "n_immune": r["n_immune"],
                "n_recompile": r["n_recompile"],
                "n_infeasible": r["n_infeasible"],
                "worst_inflation": r["worst_inflation"],
                "median_inflation": r["median_inflation"],
            }
        a, b = rec["bypass"], rec["no_bypass"]
        rec["extra_infeasible_without_bypass"] = (b["n_infeasible"]
                                                  - a["n_infeasible"])
        rec["inflation_penalty"] = (
            round(b["worst_inflation"] / a["worst_inflation"], 3)
            if (a["worst_inflation"] and b["worst_inflation"]) else None)
        out.append(rec)
    return out


def jitter_for_scheme(topo: RingTopology, pattern: str, algo: str, tier: str
                      ) -> dict[str, Any]:
    col = build_ring_collective(topo, pattern, m=M, tier=tier, algo=algo,
                                root=ROOT_NODE)
    cal = build_calendar(topo, col)
    js = jitter_sweep(cal, topo, col, grid=JITTER_GRID)
    # Delivery must be unchanged: a shifted calendar is a translated calendar,
    # so the item sets cannot move. Assert it rather than assume it.
    rel = release_offsets(topo, 256, "burst", seed=1)
    shifted = replay_jitter(cal, rel, "phase_shift")
    repacked = build_calendar(topo, col, release=rel)
    return {
        "pattern": pattern, "algo": algo, "tier": tier, "m": M,
        "makespan": cal.makespan,
        "n_phases": len(cal.phase_window),
        "slack": cal.slack(),
        "jitter": js,
        "delivery_unchanged_under_jitter": replay(col)["ok"],
        "repack_still_conflict_free": verify_dr(
            topo, repacked.items)["conflict_free"],
        "at_J256_burst": {
            "phase_shift_makespan": cal.makespan + shifted["shift_total"],
            "repack_makespan": repacked.makespan,
            "slack_absorbed_cycles": (cal.makespan + shifted["shift_total"]
                                      - repacked.makespan)},
    }


def main() -> None:
    topo = RingTopology()
    t_start = time.perf_counter()

    print(f"=== fault sweep (m={M}, root={ROOT_NODE}) ===")
    n_wrap = len(wrap_link_scenarios(topo))
    n_scat = len(scattered_node_scenarios(topo))
    n_repo = len(repo_fault_scenarios(topo))
    print(f"scenarios: {n_wrap + n_scat + n_repo} "
          f"({n_wrap} ring-specific wrap + {n_scat} scattered node + "
          f"{n_repo} repo link/node/quadrant)")
    print(f"root = {ROOT_NODE}; the one scenario that stays infeasible "
          f"everywhere is the hole that contains the root, which no rooted "
          f"collective can survive\n")
    print("inflation = recompiled/healthy makespan; a node fault removes work, "
          "so <1.0 means a smaller array, not a faster fabric -- read "
          "work-norm alongside it")
    print(f"{'pattern':10} {'algo':17} {'tier':4} {'healthy':>8} "
          f"{'immune':>7} {'recomp':>7} {'infeas':>7} {'worst':>7} {'med':>7} "
          f"{'worstWN':>8}")
    faults: list[dict[str, Any]] = []
    for pattern, algo, tier, note in SCHEMES:
        r = fault_sweep(topo, pattern, algo, tier, M, root=ROOT_NODE)
        r["note"] = note
        faults.append(r)
        print(f"{pattern:10} {algo:17} {tier:4} {r['healthy_makespan']:>8} "
              f"{r['n_immune']:>7} {r['n_recompile']:>7} "
              f"{r['n_infeasible']:>7} "
              f"{str(r['worst_inflation']):>7} "
              f"{str(r['median_inflation']):>7} "
              f"{str(r['worst_work_normalized_inflation']):>8}", flush=True)

    print("\n=== does a dead node need a ring-station bypass mux? ===")
    print(f"{'pattern':10} {'algo':17} {'tier':4} "
          f"{'bypass:inf':>11} {'none:inf':>9} {'extra':>6} "
          f"{'bypass:worst':>13} {'none:worst':>11}")
    bp = bypass_price(topo)
    for r in bp:
        print(f"{r['pattern']:10} {r['algo']:17} {r['tier']:4} "
              f"{r['bypass']['n_infeasible']:>11} "
              f"{r['no_bypass']['n_infeasible']:>9} "
              f"{r['extra_infeasible_without_bypass']:>6} "
              f"{str(r['bypass']['worst_inflation']):>13} "
              f"{str(r['no_bypass']['worst_inflation']):>11}", flush=True)

    print("\n=== jitter: J* for <=5% makespan inflation ===")
    print("policies: gs=global_shift  ps=phase_shift (both rigid replays), "
          "rp=repack (needs a recompile; measures what the slack is worth)")
    print(f"{'pattern':10} {'algo':17} {'tier':4} {'mk':>6} {'slkp50':>7} "
          + " ".join(f"{mo[:4] + ':' + po:>9}"
                     for mo in ("uniform", "distance", "burst")
                     for po in ("gs", "ps", "rp"))
          + f" {'absorbed@256':>13}")
    jit: list[dict[str, Any]] = []
    for pattern, algo, tier, _ in SCHEMES:
        r = jitter_for_scheme(topo, pattern, algo, tier)
        jit.append(r)
        cells = []
        for mo in ("uniform_jitter", "distance_skew", "burst"):
            for po in ("global_shift", "phase_shift", "repack"):
                cells.append(str(r["jitter"]["models"][mo][po]["J_star"]))
        print(f"{pattern:10} {algo:17} {tier:4} {r['makespan']:>6} "
              f"{r['slack']['p50']:>7} "
              + " ".join(f"{c:>9}" for c in cells)
              + f" {r['at_J256_burst']['slack_absorbed_cycles']:>13}",
              flush=True)

    payload = {
        "m": M, "root": ROOT_NODE, "jitter_grid": list(JITTER_GRID),
        "audit": topo.audit(),
        "schemes": [{"pattern": p, "algo": a, "tier": t, "note": n}
                    for p, a, t, n in SCHEMES],
        "faults": faults,
        "bypass_price": bp,
        "jitter": jit,
        "wall_s": round(time.perf_counter() - t_start, 1),
    }
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(payload, indent=1))
    print(f"\nwrote {OUT} ({payload['wall_s']}s)")


if __name__ == "__main__":
    main()
