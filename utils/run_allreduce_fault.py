#!/usr/bin/env python3
"""Sweep allreduce over healthy + fault scenarios on 16x16 mesh.

Writes results/allreduce_results.csv with makespan vs golden for the best
conflict-free zero-buffer scheme (tree reduce+broadcast by default).
Fault scan uses M=1 (primary) and M=6 (reference).
"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

import hamilton_ring as hr
import sim_allreduce_16x16 as sa

ROOT = Path(__file__).resolve().parents[1]
MX, MY, H, V, RAMP = 16, 16, 4, 6, 1

FIELDS = [
    "scheme", "M", "fault_class", "region", "detail", "fault_desc",
    "feasible", "ring_is_cycle", "ring_len", "sacrificed", "makespan",
    "golden_makespan", "slowdown_pct", "ok", "theo_bound", "efficiency",
    "root", "phase_reduce_end", "reason",
]


_GOLDEN_CACHE = {}


def golden_makespan(M=1, r_lat=2):
    key = (M, r_lat)
    if key in _GOLDEN_CACHE:
        return _GOLDEN_CACHE[key]
    sa.sz.cfg(MX, MY, H, V)
    res = sa.scheme_tree(sa.DEFAULT_ROOT, flits=M, r_lat=r_lat)
    lb = __import__("allreduce_bound").allreduce_bounds(M, r_lat)["combined"]
    out = (res["makespan"], res["name"], lb, res)
    _GOLDEN_CACHE[key] = out
    return out


def simulate_scenario(sc, M=1, r_lat=2, golden=None):
    """Run tree allreduce under fault scenario."""
    if golden is None:
        golden = golden_makespan(M, r_lat)
    g_mk, g_name, lb, _ = golden
    dead_nodes = sc.get("dead_nodes", ())
    dead_links = sc.get("dead_links", ())
    res = sa.simulate_fault(dead_nodes, dead_links, flits=M, r_lat=r_lat, scheme="tree")
    ring = res.get("ring", {})
    base = {
        "fault_class": sc["fault_class"],
        "region": sc["region"],
        "detail": sc["detail"],
        "fault_desc": sc["desc"],
        "sacrificed": len(sc.get("sacrificed", [])) or len(ring.get("sacrificed", [])),
        "M": M,
    }
    if not res.get("feasible"):
        base.update(
            feasible="no",
            ring_is_cycle="",
            ring_len=len(ring.get("order") or []),
            makespan="",
            ok="",
            slowdown_pct="",
            root="",
            phase_reduce_end="",
            scheme="",
            theo_bound="",
            efficiency="",
            reason=res.get("reason", ring.get("reason", "infeasible")),
        )
        return base
    detail = res["best_detail"]
    mk = res["makespan"]
    slow = (mk / g_mk - 1.0) * 100.0 if g_mk else 0.0
    base.update(
        feasible="yes",
        ring_is_cycle=str(ring.get("is_cycle", "")),
        ring_len=len(ring.get("order") or []),
        makespan=mk,
        golden_makespan=g_mk,
        slowdown_pct=f"{slow:.1f}",
        ok=str(res.get("ok", False)),
        scheme=res["scheme"],
        theo_bound=lb,
        efficiency=f"{mk / lb:.4f}" if lb else "",
        root=res.get("root", ""),
        phase_reduce_end=detail.get("phase_reduce_end", ""),
        reason=ring.get("reason", "ok"),
    )
    return base


def all_fault_scenarios():
    scs = hr.all_scenarios(MX, MY)
    rebal = []
    for sc in hr.node_fault_scenarios(MX, MY):
        if "3x3" in sc.get("detail", ""):
            r = hr.find_ring_rebalanced(MX, MY, sc["dead_nodes"], sc["dead_links"])
            if r.get("feasible"):
                rebal.append({
                    **sc,
                    "name": sc["name"] + "_rebal",
                    "fault_class": "node_rebal",
                    "sacrificed": r.get("sacrificed", []),
                })
    return scs + rebal


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path, default=ROOT / "results" / "allreduce_results.csv")
    ap.add_argument("--r-lat", type=int, default=2)
    ap.add_argument("--msg-sizes", type=str, default="1,6")
    args = ap.parse_args()
    msg_sizes = [int(x) for x in args.msg_sizes.split(",")]

    rows = []
    goldens = {M: golden_makespan(M, args.r_lat) for M in msg_sizes}
    for M in msg_sizes:
        g_mk, g_scheme, lb, _ = goldens[M]
        rows.append({
            "scheme": g_scheme,
            "M": M,
            "fault_class": "healthy",
            "region": "-",
            "detail": "-",
            "fault_desc": "healthy mesh",
            "feasible": "yes",
            "ring_is_cycle": "True",
            "ring_len": MX * MY,
            "sacrificed": 0,
            "makespan": g_mk,
            "golden_makespan": g_mk,
            "slowdown_pct": "0.0",
            "ok": "True",
            "theo_bound": lb,
            "efficiency": f"{g_mk / lb:.4f}",
            "root": sa.DEFAULT_ROOT,
            "phase_reduce_end": "",
            "reason": "golden",
        })

    for sc in all_fault_scenarios():
        for M in msg_sizes:
            rows.append(simulate_scenario(sc, M, args.r_lat, goldens[M]))

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    print(f"Wrote {args.out} ({len(rows)} rows)")


if __name__ == "__main__":
    main()
