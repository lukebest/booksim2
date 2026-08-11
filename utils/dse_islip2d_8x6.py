#!/usr/bin/env python3
"""Batch makespan sweep for the two iSLIP-2D variants on the 8x6 fabrics.

This is the "drain a known workload in as few rounds as possible" half of the
study; `dse_load_sweep_8x6.py` is the steady-state half. Both are needed and
they do not rank the configurations the same way, which is itself a result.

Axes
----
    fabric          mesh (D-M) / ring (D-R)
    grants_per_src  1 (one request, one grant) / 2 (= RAMP_BW)
    path_mode       mesh: xy / romm_static / romm_dyn
                    ring: fixed / balanced / dyn
    conflict_domain free_at (per-resource frontier) / interval (full table)
    board/leave     ring only, 1 / 2 insertion and extraction points per ring
    spatial_reuse   ring only, arc / whole_ring
    pattern         alltoall plus the any-to-any family
    m               1 / 4 / 16 flits per packet
    t_rtt           control loop round trip
    pipeline_depth  1 (hard barrier) / 2 / unbounded
    sigma           1 / 2 cycles per flit per link (metal-constant comparison)

Every row records the achieved round count against that configuration's own
lower bound, so a row is only interesting relative to its bound -- comparing raw
round counts across patterns or across m is meaningless.

Reference points come from the pre-existing schedulers (`islip_mesh`,
`greedy_ff`, `latin_mesh`) plus the FIFO baseline, so the new variants are not
graded against themselves.

Writes results/islip2d_8x6.json.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from rg_topo import Topology
from rg_collectives import build_collective
from rg_mesh_sched import schedule_mesh, verify_rounds_disjoint
from rg_ring_topo import RingTopology, greedy_max_set, misuse_stats
from rg_ring_sched import schedule_ring

OUT = Path(__file__).resolve().parents[1] / "results" / "islip2d_8x6.json"
N = 48
A2A = [(s, d) for s in range(N) for d in range(N) if s != d]
INF = 1 << 20

MESH_PATTERNS = ("alltoall", "permutation", "k_permutation", "transpose",
                 "cluster", "hotspot_any", "halfxhalf", "cornerAtoB")


def _mesh_row(pattern: str, m: int, sigma: int, **kw) -> dict[str, Any]:
    topo = Topology("mesh")
    topo.sigma = sigma
    col = build_collective(topo, pattern, m=m)
    t0 = time.perf_counter()
    r = schedule_mesh(topo, col, kw.pop("algo", "islip2d_mesh"), **kw)
    dt = time.perf_counter() - t0
    v = verify_rounds_disjoint(topo, col, r)
    return {
        "fabric": "mesh", "pattern": pattern, "m": m, "sigma": sigma,
        "algo": r["algo"], "path_mode": kw.get("path_mode", "xy"),
        "grants_per_src": kw.get("grants_per_src"),
        "conflict_domain": kw.get("conflict_domain"),
        "fill": kw.get("fill"), "iters": kw.get("iters"),
        "t_rtt": kw.get("t_rtt"), "pipeline_depth": kw.get("pipeline_depth"),
        "n_flows": r["n_flows"], "n_rounds": r["n_rounds"],
        "round_lb": r["round_lb"], "round_ratio": r["round_ratio"],
        "makespan": r["makespan_sched"], "data_span": r["data_span"],
        "convoy_span": r["convoy_span"], "convoy_ratio": r["convoy_ratio"],
        "mean_flows_per_round": r["mean_flows_per_round"],
        "unanimous_frac": r["unanimous_frac"],
        "max_load": r["path"].get("max_load"),
        "cut_bound": r["path"].get("cut_bound"),
        "conflicts": r["verify"]["n_violations"],
        "recheck_overlaps": v["overlaps"],
        "recheck_ramp_violations": v["ramp_violations"],
        "residual_bitmap_ok": r["residual_bitmap_ok"],
        "mean_grant_wait": r["mean_grant_wait"],
        "ctrl_msgs_total": r["ctrl_msgs_total"],
        "secs": round(dt, 2),
    }


def _ring_row(m: int, sigma: int, *, board: int = 1, leave: int = 1,
              reuse: str = "arc", **kw) -> dict[str, Any]:
    topo = RingTopology(sigma=sigma, board_ports=board, leave_ports=leave,
                        spatial_reuse=reuse)
    t0 = time.perf_counter()
    r = schedule_ring(topo, A2A, m=m, **kw)
    dt = time.perf_counter() - t0
    return {
        "fabric": "ring", "pattern": "alltoall", "m": m, "sigma": sigma,
        "algo": "islip2d_ring", "path_mode": kw.get("ring_path_mode"),
        "grants_per_src": kw.get("grants_per_src"),
        "conflict_domain": kw.get("conflict_domain"),
        "fill": kw.get("fill"), "iters": kw.get("iters"),
        "t_rtt": kw.get("t_rtt"), "pipeline_depth": kw.get("pipeline_depth"),
        "board_ports": board, "leave_ports": leave, "spatial_reuse": reuse,
        "n_flows": len(A2A), "n_rounds": r["n_rounds"],
        "round_lb": r["round_lb"], "round_ratio": r["round_ratio"],
        "makespan": r["makespan_sched"], "data_span": r["data_span"],
        "convoy_span": r["convoy_span"],
        "convoy_ratio": (round(r["convoy_span"] / r["data_span"], 3)
                         if r["data_span"] else None),
        "mean_flows_per_round": r["mean_flows_per_round"],
        "unanimous_frac": r["unanimous_frac"],
        "port_lb": r["plan"].get("port_lb"),
        "max_link_load": r["plan"]["max_link_load"],
        "conflicts": sum(r["verify"][k] for k in (
            "R1_link_violations", "R2_board_violations",
            "R3_leave_violations", "R4_turn_violations",
            "R5_voq_violations")),
        "verify_by_clause": {k: r["verify"][k] for k in (
            "R1_link_violations", "R2_board_violations",
            "R3_leave_violations", "R4_turn_violations",
            "R5_voq_violations")},
        "turn_residency": r["verify"]["max_turn_residency"],
        "residual_bitmap_ok": r["residual_bitmap_ok"],
        "ctrl_msgs_per_round": r["ctrl_msgs_per_round"],
        "ctrl_msgs_total": r["ctrl_msgs_total"],
        "secs": round(dt, 2),
    }


def sweep() -> dict[str, Any]:
    rows: list[dict[str, Any]] = []

    # -- Part A: mesh ----------------------------------------------------
    for g in (1, 2):
        for dom in ("free_at", "interval"):
            rows.append(_mesh_row("alltoall", 1, 1, grants_per_src=g,
                                  conflict_domain=dom, pipeline_depth=INF))
    for pm in ("xy", "romm_static", "romm_dyn"):
        for pat in MESH_PATTERNS:
            rows.append(_mesh_row(pat, 1, 1, path_mode=pm, grants_per_src=2,
                                  pipeline_depth=INF))
    for fill in ("hops_desc", "hops_asc", "pressure", "random", "flowid"):
        rows.append(_mesh_row("alltoall", 1, 1, fill=fill, grants_per_src=2,
                              pipeline_depth=INF))
    for it in (0, 1, 2, 4):
        rows.append(_mesh_row("alltoall", 1, 1, iters=it, grants_per_src=2,
                              pipeline_depth=INF))
    for m in (1, 4, 16):
        for sigma in (1, 2):
            rows.append(_mesh_row("alltoall", m, sigma, grants_per_src=2,
                                  pipeline_depth=INF))
    for algo in ("islip_mesh", "greedy_ff", "latin_mesh", "bcfs"):
        try:
            rows.append(_mesh_row("alltoall", 1, 1, algo=algo))
        except Exception as exc:                      # reference point only
            rows.append({"fabric": "mesh", "algo": algo, "error": str(exc)})

    # -- Part B: ring ----------------------------------------------------
    for pm in ("fixed", "balanced", "dyn"):
        for g in (1, 2):
            rows.append(_ring_row(1, 1, ring_path_mode=pm, grants_per_src=g,
                                  pipeline_depth=INF))
    for board in (1, 2):
        for leave in (1, 2):
            rows.append(_ring_row(1, 1, board=board, leave=leave,
                                  grants_per_src=2, pipeline_depth=INF))
    for reuse in ("arc", "whole_ring"):
        rows.append(_ring_row(1, 1, reuse=reuse, grants_per_src=2,
                              pipeline_depth=INF))
    for dom in ("free_at", "interval"):
        rows.append(_ring_row(1, 1, conflict_domain=dom, grants_per_src=2,
                              pipeline_depth=INF))
    for fill in ("arc_desc", "arc_asc", "pressure", "random", "flowid"):
        rows.append(_ring_row(1, 1, fill=fill, grants_per_src=2,
                              pipeline_depth=INF))
    for m in (1, 4, 16):
        for sigma in (1, 2):
            rows.append(_ring_row(m, sigma, grants_per_src=2,
                                  pipeline_depth=INF))

    # -- pipelining and RTT sensitivity ----------------------------------
    # `convoy_span` is what a hard per-round barrier would cost, so
    # convoy_ratio = convoy_span / data_span crossing 1.0 marks the RTT at which
    # round-level pipelining stops paying for itself.
    pipe: list[dict[str, Any]] = []
    for depth in (1, 2, INF):
        for rtt in (8, 16, 32, 40, 48, 56, 64, 96):
            pipe.append(_mesh_row("alltoall", 1, 1, grants_per_src=2,
                                  t_rtt=rtt, pipeline_depth=depth))
            pipe.append(_ring_row(1, 1, grants_per_src=2, t_rtt=rtt,
                                  pipeline_depth=depth))
    cross: dict[str, Any] = {}
    for fab in ("mesh", "ring"):
        for depth in (1, 2, INF):
            pts = sorted(((r["t_rtt"], r["convoy_ratio"]) for r in pipe
                          if r["fabric"] == fab
                          and r["pipeline_depth"] == depth),
                         key=lambda x: x[0])
            xr = None
            for (r0, v0), (r1, v1) in zip(pts, pts[1:]):
                if v0 is not None and v1 is not None and v0 >= 1.0 > v1:
                    xr = round(r0 + (v0 - 1.0) / (v0 - v1) * (r1 - r0), 1)
                    break
            key = f"{fab}_depth{'inf' if depth > 1000 else depth}"
            cross[key] = {"crossover_t_rtt": xr, "points": pts}

    # -- cross-fabric predicate misuse ------------------------------------
    topo = RingTopology()
    from rg_ring_topo import fixed_plan
    plan = fixed_plan(topo, A2A)
    misuse = misuse_stats(topo, plan.paths, n_samples=40_000, seed=0)
    gms = {
        "r1_only": greedy_max_set(topo, plan.paths, clauses="R1", trials=20),
        "r1_r2_r3": greedy_max_set(topo, plan.paths, clauses="R1+R2+R3",
                                   trials=20),
    }
    return {"rows": rows, "pipeline": pipe, "rtt_crossover": cross,
            "ring_misuse": misuse, "ring_greedy_max_set": gms,
            "audit": topo.audit()}


if __name__ == "__main__":
    t0 = time.perf_counter()
    res = sweep()
    res["wall_secs"] = round(time.perf_counter() - t0, 1)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(res, indent=1, default=str))
    print(f"wrote {OUT} ({len(res['rows'])} rows, "
          f"{len(res['pipeline'])} pipeline rows, {res['wall_secs']}s)")

    print("\n=== mesh alltoall: grants_per_src x conflict_domain ===")
    for r in res["rows"][:4]:
        print(f"  g={r['grants_per_src']} {r['conflict_domain']:9} "
              f"rounds={r['n_rounds']:>4} lb={r['round_lb']:>4} "
              f"ratio={r['round_ratio']} span={r['data_span']}")

    print("\n=== RTT at which round pipelining stops paying (convoy = 1.0) ===")
    for k, v in res["rtt_crossover"].items():
        print(f"  {k:16} T_rtt = {v['crossover_t_rtt']}")

    print("\n=== ring alltoall: path mode x grants_per_src ===")
    for r in res["rows"]:
        if r.get("fabric") == "ring" and r.get("path_mode") in (
                "fixed", "balanced", "dyn") and r.get("board_ports") == 1 \
                and r.get("spatial_reuse") == "arc" and r["m"] == 1 \
                and r.get("fill") is None:
            print(f"  {r['path_mode']:9} g={r['grants_per_src']} "
                  f"rounds={r['n_rounds']:>4} lb={r['round_lb']:>4} "
                  f"ratio={r['round_ratio']} conflicts={r['conflicts']}")
