#!/usr/bin/env python3
"""Sweep the mesh scheduler family: algo x pattern x m x topo x plane.

Emits results/mesh_sched_pareto.json with, per row, the DES makespan (with the
scheduler's own T_sched charged back onto it), the LDPS round count against its
max_e load(e) lower bound, the analytic scheduler area, and the verification
flags (conflict-free, zero residency, rounds >= lower bound).

    python3 utils/dse_mesh_sched_pareto.py [--quick]
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from rg_topo import RAMP, Topology
from rg_collectives import build_collective
from rg_arbiter import Grant
from rg_mesh_sched import (
    ALGO_CLASS, ALL_ALGOS, INCREMENTAL_ALGOS, SLOT_ALGOS,
    schedule_mesh, verify_rounds_disjoint,
)
from rg_sched_cost import lam_winner, pareto_front, sched_cost
from dse_rg_noc_8x6 import (
    DEFAULT_Q, simulate_bufferable, simulate_bufferable_fast,
    simulate_bufferless, simulate_fifo_baseline,
)

PATTERNS = ("alltoall", "allgather", "allreduce", "broadcast", "reduce")
MS = (1, 4, 16)
TOPOS = ("mesh", "torus")
PLANES = ("bufferless", "bufferable")
ITER_SWEEP = (1, 2, 4)
LAMBDAS = (0.0, 0.25, 0.5, 1.0, 2.0, 4.0, 8.0, 16.0)

# This study fixes the request discipline at one request = one VOQ (see `meta`
# below and the `voq_request_discipline` check), so it cannot host algorithms
# that redefine the request. `islip2d_mesh` sends one residual bitmap per
# source instead, and lives in dse_islip2d_8x6.py with its own baselines.
SWEEP_ALGOS = tuple(a for a in ALL_ALGOS if a != "islip2d_mesh")

OUT = Path(__file__).resolve().parent.parent / "results" / "mesh_sched_pareto.json"


def _des(topo: Topology, col, grants: list[Grant], plane: str,
         Q: int = DEFAULT_Q) -> dict[str, Any]:
    if plane == "bufferless":
        return simulate_bufferless(topo, col, grants)
    n_trees = sum(1 for f in col.flows if f.kind == "tree")
    if n_trees > 4 or len(col.flows) > 500:
        return simulate_bufferable_fast(topo, col, grants)
    return simulate_bufferable(topo, col, grants, Q=Q)


def fifo_baseline(topo: Topology, col) -> int:
    """Plain packet-switched FIFO NoC, no request-grant: the comparison point."""
    n_trees = sum(1 for f in col.flows if f.kind == "tree")
    if n_trees > 4:
        fake = [Grant(f.flow_id, f.src, 0, RAMP, {}) for f in col.flows]
        return simulate_bufferable_fast(topo, col, fake)["makespan"]
    return simulate_fifo_baseline(topo, col)["makespan"]


def run_one(topo_kind: str, plane: str, algo: str, pattern: str, m: int, *,
            iters: int = 1, window: int = 64, t_sched_ctrl: int = 8,
            seed: int = 0, fifo_mk: int | None = None) -> dict[str, Any]:
    topo = Topology(topo_kind)
    sync = pattern in ("allgather", "allreduce")
    col = build_collective(topo, pattern, m=m, sync=sync)

    t0 = time.time()
    # Strict VOQ discipline: one request per VOQ (N−1 per source on alltoall).
    res = schedule_mesh(topo, col, algo, iters=iters, window=window,
                        t_sched=t_sched_ctrl, aggregate=False, seed=seed)
    wall = time.time() - t0
    sim = _des(topo, col, res["grants"], plane)

    mean_hops = (sum(len(mf) for mf in
                     [res["grants"][i].reservations for i in
                      range(len(res["grants"]))]) / max(1, len(res["grants"])))
    cost = sched_cost(algo, topo, res["n_flows"], iters=iters,
                      n_rounds=res["n_rounds"], mean_hops=max(1.0, mean_hops))

    mk_des = sim["makespan"]
    row: dict[str, Any] = {
        "topo": topo_kind, "plane": plane, "algo": algo,
        "algo_class": ALGO_CLASS[algo], "select": res["select"],
        "pattern": pattern, "m": m, "iters": res["iters"], "sync": sync,
        "n_flows": res["n_flows"], "sigma": topo.sigma,

        # --- VOQ request discipline (one request = one VOQ; N−1 per source)
        "aggregate": res["ctrl"]["aggregate"],
        "request_unit": res["ctrl"]["request_unit"],
        "n_voqs": res["ctrl"]["n_voqs"],
        "n_voq_per_src_max": res["ctrl"]["n_voq_per_src_max"],
        "n_request_units": res["ctrl"]["n_request_units"],

        # --- timing: DES makespan, then the scheduler's own latency added back
        "makespan_des": mk_des,
        "t_sched_cycles": cost["t_sched_cycles"],
        "makespan": mk_des + cost["t_sched_cycles"],
        "data_span": res["data_span"],
        "t_first_data_start": res["t_first_data_start"],
        "R_rg": res["ctrl"]["R_rg"],
        "fifo_baseline": fifo_mk,
        "speedup_vs_fifo": (round(fifo_mk / (mk_des + cost["t_sched_cycles"]),
                                  3) if fifo_mk else None),

        # --- LDPS structure
        "n_rounds": res["n_rounds"],
        "round_lb": res["round_lb"],
        "round_ratio": res["round_ratio"],
        "mean_flows_per_round": res["mean_flows_per_round"],
        "convoy_span": res["convoy_span"],
        "convoy_ratio": res["convoy_ratio"],
        "n_unanimous": res["n_unanimous"],
        "unanimous_frac": res["unanimous_frac"],

        # --- analytic cost
        "area_norm": cost["area_norm"],
        "area_total_norm": cost["area_total_norm"],
        "bits": cost["bits"],
        "bits_breakdown": cost["bits_breakdown"],
        "comparator_bits": cost["comparator_bits"],
        "gate_levels": cost["gate_levels"],
        "dependent_steps": cost["dependent_steps"],

        # --- verification
        "conflict_free": res["verify"]["conflict_free"],
        "n_conflicts": res["verify"]["n_violations"],
        "max_residency": sim.get("max_residency"),
        "reservation_violations": sim.get("reservation_violations"),
        "ordered_ok": sim.get("ordered_ok"),
        "rounds_ge_lb": (res["n_rounds"] >= res["round_lb"]
                         if res["n_rounds"] is not None else None),
        "des_kind": sim.get("plane"),
        "wall_s": round(wall, 3),
    }
    if algo in SLOT_ALGOS:
        rd = verify_rounds_disjoint(topo, col, res)
        row["round_links_disjoint"] = rd["disjoint"]
        row["round_overlaps"] = rd["overlaps"]
        row["round_ramp_violations"] = rd["ramp_violations"]
    else:
        row["round_links_disjoint"] = None
        row["round_overlaps"] = None
        row["round_ramp_violations"] = None
    return row


def run_sweep(quick: bool = False) -> dict[str, Any]:
    patterns = PATTERNS if not quick else ("alltoall", "reduce")
    ms = MS if not quick else (1, 4)
    topos = TOPOS if not quick else ("mesh",)
    planes = PLANES if not quick else ("bufferless",)

    rows: list[dict[str, Any]] = []
    fifo_cache: dict[tuple[str, str, int], int] = {}
    for topo_kind in topos:
        topo = Topology(topo_kind)
        for pattern in patterns:
            for m in ms:
                key = (topo_kind, pattern, m)
                col = build_collective(topo, pattern, m=m,
                                       sync=pattern in ("allgather",
                                                        "allreduce"))
                fifo_cache[key] = fifo_baseline(topo, col)
                print(f"[fifo] {topo_kind:5} {pattern:10} m={m:<3} "
                      f"mk={fifo_cache[key]}", flush=True)

    for topo_kind in topos:
        for plane in planes:
            for pattern in patterns:
                for m in ms:
                    fifo_mk = fifo_cache[(topo_kind, pattern, m)]
                    for algo in SWEEP_ALGOS:
                        it_list = (ITER_SWEEP
                                   if algo in ("islip_mesh", "pim_mesh")
                                   else (1,))
                        for it in it_list:
                            r = run_one(topo_kind, plane, algo, pattern, m,
                                        iters=it, fifo_mk=fifo_mk)
                            rows.append(r)
                            print(f"  {topo_kind:5} {plane:11} {pattern:10} "
                                  f"m={m:<3} {algo:11} I={it} "
                                  f"mk={r['makespan']:<7} "
                                  f"(des={r['makespan_des']}+"
                                  f"{r['t_sched_cycles']}) "
                                  f"area={r['area_norm']:.4f} "
                                  f"cf={int(bool(r['conflict_free']))} "
                                  f"{r['wall_s']}s", flush=True)

    verifications = verify(rows)
    pareto = build_pareto(rows)
    lam = build_lambda(rows)
    return {
        "meta": {
            "generated": time.strftime("%Y-%m-%d %H:%M:%S"),
            "patterns": list(patterns), "ms": list(ms),
            "topos": list(topos), "planes": list(planes),
            "algos": list(SWEEP_ALGOS), "iter_sweep": list(ITER_SWEEP),
            "lambdas": list(LAMBDAS),
            "theory": {
                "request_unit": "one request = one VOQ; each source holds "
                                "N-1 VOQs for unicast alltoall "
                                "(48×47=2256 on 8×6); aggregate=False",
                "unit": "LDPS (link-disjoint path set) replaces the "
                        "permutation matrix of a crossbar",
                "round_lb": "max_e (#VOQs whose XY path uses directed link e) "
                            "— the Birkhoff permutation-count analog",
                "accept_rule": "a VOQ accepts only on UNANIMOUS grants from "
                               "every link on its route (a crossbar needs 1)",
                "t_sched": "dependent arbitration steps x cycles/step, added "
                           "back onto the DES makespan",
                "area_calibration": "state bits + comparators, normalized so "
                                    "greedy_ff = ARB_AREA['ca'] = 0.05",
            },
        },
        "verifications": verifications,
        "pareto": pareto,
        "lambda_sensitivity": lam,
        "n_rows": len(rows),
        "rows": rows,
    }


def build_pareto(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Non-dominated (area, makespan) points per (topo, plane, pattern, m)."""
    out: dict[str, Any] = {}
    keys = sorted({(r["topo"], r["plane"], r["pattern"], r["m"])
                   for r in rows})
    for topo_kind, plane, pattern, m in keys:
        sel = [r for r in rows
               if (r["topo"], r["plane"], r["pattern"], r["m"])
               == (topo_kind, plane, pattern, m)]
        pts = [(r["area_norm"], r["makespan"],
                f"{r['algo']}" + (f"/I{r['iters']}"
                                  if r["algo"] in INCREMENTAL_ALGOS
                                  and r["algo"] != "greedy_ff" else ""))
               for r in sel]
        front = pareto_front(pts)
        best_mk = min(sel, key=lambda r: r["makespan"])
        best_area = min(sel, key=lambda r: r["area_norm"])
        out[f"{topo_kind}|{plane}|{pattern}|m{m}"] = {
            "front": [{"area_norm": a, "makespan": mk, "tag": t}
                      for a, mk, t in front],
            "best_makespan": {"algo": best_mk["algo"],
                              "iters": best_mk["iters"],
                              "makespan": best_mk["makespan"],
                              "area_norm": best_mk["area_norm"]},
            "smallest_area": {"algo": best_area["algo"],
                              "makespan": best_area["makespan"],
                              "area_norm": best_area["area_norm"]},
            "fifo_baseline": sel[0]["fifo_baseline"],
        }
    return out


def build_lambda(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Who wins min(makespan_norm + lam * area_norm) as lam sweeps."""
    out: dict[str, Any] = {}
    keys = sorted({(r["topo"], r["plane"], r["pattern"], r["m"])
                   for r in rows})
    for topo_kind, plane, pattern, m in keys:
        sel = [r for r in rows
               if (r["topo"], r["plane"], r["pattern"], r["m"])
               == (topo_kind, plane, pattern, m)]
        rec = {}
        for lam in LAMBDAS:
            w = lam_winner(sel, lam)
            rec[str(lam)] = {"algo": w["algo"], "iters": w["iters"],
                             "makespan": w["makespan"],
                             "area_norm": w["area_norm"]}
        out[f"{topo_kind}|{plane}|{pattern}|m{m}"] = rec
    return out


def verify(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    v: list[dict[str, Any]] = []

    bad = [r for r in rows if not r["conflict_free"]]
    v.append({
        "name": "all_schedules_conflict_free",
        "ok": not bad,
        "detail": f"{len(rows) - len(bad)}/{len(rows)} 行 reservation 无重叠"
                  + (f"；违例 {len(bad)}" if bad else ""),
    })

    bl = [r for r in rows if r["plane"] == "bufferless"]
    bad = [r for r in bl if r["max_residency"] not in (0, None)
           or r["reservation_violations"]]
    v.append({
        "name": "bufferless_zero_residency",
        "ok": not bad,
        "detail": f"{len(bl)} 个 bufferless 回放全部 max_residency=0 且预约窗口自洽"
                  + (f"；违例 {len(bad)}" if bad else ""),
    })

    rr = [r for r in rows if r["n_rounds"] is not None]
    bad = [r for r in rr if not r["rounds_ge_lb"]]
    v.append({
        "name": "rounds_ge_max_link_load",
        "ok": not bad,
        "detail": "LDPS 轮次数 ≥ max_e load(e)（Birkhoff 置换数界类比）："
                  f"{len(rr) - len(bad)}/{len(rr)}",
    })

    dj = [r for r in rows if r["round_links_disjoint"] is not None]
    bad = [r for r in dj if not r["round_links_disjoint"]]
    v.append({
        "name": "phase_rounds_link_disjoint",
        "ok": not bad,
        "detail": "相位型算法同轮流的链路集独立复核两两不相交："
                  f"{len(dj) - len(bad)}/{len(dj)}",
    })

    # calibration is defined at the reference point (the 2256-flow alltoall)
    cal = [r for r in rows if r["algo"] == "greedy_ff"
           and r["pattern"] == "alltoall"]
    ok = bool(cal) and all(abs(r["area_norm"] - 0.05) < 1e-3 for r in cal)
    v.append({
        "name": "area_model_calibrated",
        "ok": ok,
        "detail": "greedy_ff 在标定点（alltoall, 2256 流）归一面积 = "
                  f"ARB_AREA['ca']=0.05（实测 "
                  f"{sorted({r['area_norm'] for r in cal}) if cal else 'n/a'}）",
    })

    # compare within one workload: the class difference is Θ(rounds) vs Θ(flows)
    pairs = 0
    bad_pairs = 0
    for key in {(r["topo"], r["plane"], r["pattern"], r["m"]) for r in rows}:
        sel = [r for r in rows if (r["topo"], r["plane"], r["pattern"],
                                   r["m"]) == key and r["iters"] == 1]
        slot = [r["t_sched_cycles"] for r in sel if r["algo"] in SLOT_ALGOS]
        pipe = [r["t_sched_cycles"] for r in sel
                if r["algo"] in ("bcfs", "greedy_ff")]
        if not slot or not pipe:
            continue
        pairs += 1
        if max(slot) >= min(pipe):
            bad_pairs += 1
    v.append({
        "name": "slot_class_cheaper_in_time",
        "ok": pairs > 0 and bad_pairs == 0,
        "detail": "同一工作负载下（I=1），相位型的相关仲裁步数（≈轮次数）严格少于"
                  f"流水型（≈流数）：{pairs - bad_pairs}/{pairs}",
    })

    uni = [r for r in rows if r["algo"] == "islip_mesh"
           and r["pattern"] == "alltoall"]
    fr = max((r["unanimous_frac"] or 0) for r in uni) if uni else None
    v.append({
        "name": "path_unanimity_collapses_on_alltoall",
        "ok": fr is not None and fr < 0.5,
        "detail": "alltoall 下逐链路 RR 的「全路径一致 accept」成功率 "
                  f"最高仅 {fr}，故 mesh 必须补一个路径级顺序分配步（否则活锁）",
    })

    a2a = [r for r in rows if r["pattern"] == "alltoall"]
    ok = (a2a
          and all(not r["aggregate"]
                  and r["request_unit"] == "voq"
                  and r["n_request_units"] == r["n_voqs"]
                  and r["n_voqs"] == 48 * 47
                  and r["n_voq_per_src_max"] == 47
                  for r in a2a))
    v.append({
        "name": "voq_request_discipline",
        "ok": bool(ok),
        "detail": "alltoall 严格一 request = 一 VOQ：每源 N−1=47 条、全网 "
                  f"N·(N−1)=2256 条控制消息"
                  + (f"（实测 n_req={a2a[0]['n_request_units']}）" if a2a else ""),
    })

    return v


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--out", default=str(OUT))
    a = ap.parse_args()
    data = run_sweep(quick=a.quick)
    p = Path(a.out)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    print(f"\nwrote {p}  ({data['n_rows']} rows)")
    for v in data["verifications"]:
        print(f"  [{'ok' if v['ok'] else 'FAIL'}] {v['name']}: {v['detail']}")


if __name__ == "__main__":
    main()
