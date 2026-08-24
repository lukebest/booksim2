#!/usr/bin/env python3
"""Where exactly is the concurrency cliff, and is it the same on every seed?

The recommendation in the report is a single number -- the per-core
outstanding limit to configure -- so it has to be the largest value that
drains the batch on *every* seed, not the one with the best throughput on the
seed that happened to be measured first. This scan produces that evidence and
appends it to the study blob.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import sys

_UTILS = Path(__file__).resolve().parent
if str(_UTILS) not in sys.path:
    sys.path.insert(0, str(_UTILS))

from dse_ring2_write_fair import fairness_stats
from rg_stack_base import StackBaseParams, StackBaseSim, run_batch
from rg_stack_topo import StackTopology, build_uniform_write

M_WDATA = 4


def scan(ks: int, seeds, ocs, h_assign: str = "split") -> list[dict]:
    out = []
    for oc in ocs:
        rows = []
        for sd in seeds:
            topo = StackTopology(route_mode="bound", h_assign=h_assign)
            txns = build_uniform_write(topo, k=ks, seed=sd)
            bd = topo.write_bounds(txns, m_req=1, m_rsp=2,
                                   m_wdata=M_WDATA)["bound"]
            r = run_batch(topo, txns,
                          params=StackBaseParams(turn_depth=64, d2d_depth=128,
                                                 core_outstanding=oc),
                          sim_cls=StackBaseSim, seed=sd, stall_after=20_000)
            f = fairness_stats(r["wr_inject_by_core"], r["makespan"],
                               ks * M_WDATA)
            rows.append({
                "seed": sd, "completed": r["completed"],
                "makespan": r["makespan"],
                "thr": round(r["n_txn_done"] / max(1, r["makespan"]), 4),
                "eff": round(bd / max(1, r["makespan"]), 4),
                "jain": f["jain"], "max_min": f["max_min"],
            })
        ok = [r for r in rows if r["completed"]]
        rec = {
            "h_assign": h_assign, "outstanding": oc, "runs": rows,
            "n_completed": len(ok), "n_runs": len(rows),
            "thr_mean_ok": (round(sum(r["thr"] for r in ok) / len(ok), 4)
                            if ok else 0.0),
            "eff_mean_ok": (round(sum(r["eff"] for r in ok) / len(ok), 4)
                            if ok else 0.0),
            "jain_mean": round(sum(r["jain"] for r in rows) / len(rows), 5),
            "jain_min": round(min(r["jain"] for r in rows), 5),
            "mm_worst": round(max(r["max_min"] for r in rows
                                  if r["max_min"] != float("inf")), 4),
        }
        out.append(rec)
        print("  %-5s oc=%-3d %d/%d seeds drained  thr=%.3f  jain=%.5f  "
              "mm_worst=%.2f" % (h_assign, oc, rec["n_completed"],
                                 rec["n_runs"], rec["thr_mean_ok"],
                                 rec["jain_mean"], rec["mm_worst"]),
              flush=True)
    return out


def depth_scan(ks: int, seeds, ocs, depths) -> list[dict]:
    """How deep the crossing FIFOs have to be, as a function of concurrency.

    The turn FIFO is the one place this fabric is allowed to buffer, so its
    depth is a real area cost. It turns out to be coupled to the concurrency
    limit: running at a lower limit needs a shallower FIFO, so the two
    recommendations reinforce each other instead of trading off.
    """
    out = []
    for oc in ocs:
        for d in depths:
            rows = []
            for sd in seeds:
                topo = StackTopology(route_mode="bound")
                txns = build_uniform_write(topo, k=ks, seed=sd)
                r = run_batch(topo, txns,
                              params=StackBaseParams(turn_depth=d,
                                                     d2d_depth=2 * d,
                                                     core_outstanding=oc),
                              sim_cls=StackBaseSim, seed=sd,
                              stall_after=20_000)
                f = fairness_stats(r["wr_inject_by_core"], r["makespan"],
                                   ks * M_WDATA)
                rows.append({
                    "seed": sd, "completed": r["completed"],
                    "makespan": r["makespan"], "jain": f["jain"],
                    "turn_peak": r["fifo"]["turn_peak"],
                    "thr": round(r["n_txn_done"] / max(1, r["makespan"]), 4),
                })
            ok = [r for r in rows if r["completed"]]
            rec = {
                "outstanding": oc, "turn_depth": d, "d2d_depth": 2 * d,
                "n_completed": len(ok), "n_runs": len(rows),
                "thr_mean_ok": (round(sum(r["thr"] for r in ok) / len(ok), 4)
                                if ok else 0.0),
                "jain_mean": round(sum(r["jain"] for r in rows) / len(rows), 5),
                "turn_peak": max(r["turn_peak"] for r in rows),
            }
            out.append(rec)
            print("  oc=%-3d turn=%-4d %d/%d drained  thr=%.3f  jain=%.5f  "
                  "peak=%d" % (oc, d, rec["n_completed"], rec["n_runs"],
                               rec["thr_mean_ok"], rec["jain_mean"],
                               rec["turn_peak"]), flush=True)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--k", type=int, default=50)
    ap.add_argument("--seeds", type=int, nargs="*", default=[0, 1, 2])
    ap.add_argument("--ocs", type=int, nargs="*",
                    default=[2, 3, 4, 5, 6, 7, 8])
    ap.add_argument("--depth-ocs", type=int, nargs="*", default=[3, 5])
    ap.add_argument("--depths", type=int, nargs="*",
                    default=[8, 16, 24, 32, 48, 64])
    ap.add_argument("--blob", default="results/dse_stack_write_fair.json")
    args = ap.parse_args()

    print("concurrency cliff, per seed:")
    rows = scan(args.k, args.seeds, args.ocs, "split")
    rows += scan(args.k, args.seeds, args.ocs, "stack")

    safe = {}
    for ha in ("split", "stack"):
        ok = [r["outstanding"] for r in rows
              if r["h_assign"] == ha and r["n_completed"] == r["n_runs"]]
        safe[ha] = max(ok) if ok else 0
    print(f"\nlargest limit that drains every seed: {safe}")

    print("\nturn-FIFO depth requirement vs concurrency:")
    depth = depth_scan(args.k, args.seeds, args.depth_ocs, args.depths)
    need = {}
    for oc in args.depth_ocs:
        ok = [r["turn_depth"] for r in depth
              if r["outstanding"] == oc and r["n_completed"] == r["n_runs"]]
        need[str(oc)] = min(ok) if ok else None
    print(f"\nshallowest depth that drains every seed: {need}")

    p = Path(args.blob)
    blob = json.loads(p.read_text())
    blob["stability"] = {"rows": rows, "safe_oc": safe,
                         "seeds": args.seeds, "k": args.k}
    blob["depth"] = {"rows": depth, "need": need, "seeds": args.seeds,
                     "k": args.k}
    p.write_text(json.dumps(blob, indent=1))
    print(f"appended to {p}")


if __name__ == "__main__":
    main()
