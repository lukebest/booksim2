#!/usr/bin/env python3
"""Phase 2: tune S1's AIMD so CW / CCW board failures come out even.

The default S1 does the opposite of what it should on a bufferless ring. A
board failure at a core is caused by *other* nodes' transit traffic, but
AIMD-on-loss makes the node that sees the failure shrink its own budget. So
the two cores with a hot outgoing hop (0 and 8, and their mirrors 10 and 18)
throttle themselves and hand the slots to the cores that were already doing
fine: bandwidth 0.23 vs 0.59, max/min 2.53.

`dir_split` gives each outgoing direction its own budget, which is the only
version of the knob that can move the CW/CCW ratio at all -- a node-level
budget scales both directions together and leaves the ratio alone.

Objective here is the direction balance (max over cores of
max(fail_cw, fail_ccw) / min(...)), with throughput and binned Jain tracked
alongside so a "balanced because nobody moves" answer is visible.

Usage:
    PYTHONHASHSEED=0 python3 probe_ring2_s1_dirbal.py [K] [grid ...]
"""
from __future__ import annotations

import itertools
import json
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from dse_ring2_write_fair import (BIN_W, FABRIC, W_FLITS, binned_jain,
                                  board_dir_from_inj, build_pattern,
                                  fairness_stats, run_scheme)
from rg_ring2_topo import CHI_VCS_WRITE, Ring2Topology

OUT = (Path(__file__).resolve().parents[1] / "results"
       / "probe_ring2_s1_dirbal.json")


def dir_balance(board_dir: dict[str, dict[str, int]]) -> dict[str, Any]:
    """Worst and mean CW/CCW board-failure ratio over the cores."""
    ratios = {}
    for c, r in board_dir.items():
        lo = min(r["fail_cw"], r["fail_ccw"])
        hi = max(r["fail_cw"], r["fail_ccw"])
        ratios[c] = (hi / lo) if lo else float("inf")
    vals = [v for v in ratios.values() if v != float("inf")]
    return {
        "fail_ratio_max": round(max(ratios.values()), 3) if ratios else None,
        "fail_ratio_mean": round(sum(vals) / len(vals), 3) if vals else None,
        "n_ge2": sum(1 for v in ratios.values() if v >= 2.0),
        "by_core": {c: round(v, 3) for c, v in sorted(ratios.items(),
                                                      key=lambda kv: int(kv[0]))},
    }


def _grids() -> dict[str, list[dict[str, Any]]]:
    def combos(**axes):
        keys = list(axes)
        return [dict(zip(keys, vals))
                for vals in itertools.product(*(axes[k] for k in keys))]

    return {
        # Can a per-direction budget move the ratio at all, and at what cost?
        "coarse": combos(dir_split=[False, True],
                         band=["spec", "harsh", "gentle"],
                         cap_scale=[1.0, 0.5, 0.25]),
        # `harsh` lost 20-29% throughput everywhere, so refine the two bands
        # that held it: how hard to squeeze, and how smoothly to meter.
        "refine": combos(dir_split=[True], band=["spec", "gentle"],
                         cap_scale=[0.25, 0.5],
                         window=[32, 64, 128],
                         pace_burst=[0, 1, 4]),
        # Does controlling the HAs too help? They are the binding injectors.
        "scope": combos(dir_split=[True], band=["spec", "gentle"],
                        cap_scale=[0.5], scope=["core_only", "both"],
                        window=[32, 64], pace_burst=[1]),
    }


def run_one(topo, txns, over: dict[str, Any], *, k: int, scheme: str = "S1"
            ) -> dict[str, Any]:
    cfg = dict(FABRIC)
    cfg.update(over)
    r = run_scheme(scheme, topo, txns, seed=0, cfg=cfg, quiet=True)
    inj = r.get("wr_inject_by_core") or {}
    f = fairness_stats(inj, r["makespan"], k * W_FLITS)
    jb = binned_jain(inj, BIN_W, f["t_fair"]) if f else {}
    db = dir_balance(board_dir_from_inj(r.get("inj_by_hop") or {},
                                        sorted(int(c) for c in inj)))
    return {
        "over": over, "scheme": scheme, "makespan": r["makespan"],
        "completed": r["completed"], "thr": f.get("throughput"),
        "max_min": f.get("max_min"), "bw_min": f.get("bw_min"),
        "jain_bin": jb.get("jain_bin_mean"),
        "jain_bin_ideal": jb.get("jain_bin_ideal"),
        "jain_vs_ideal": jb.get("jain_vs_ideal"),
        "fail_ratio_max": db["fail_ratio_max"],
        "fail_ratio_mean": db["fail_ratio_mean"],
        "n_ge2": db["n_ge2"], "fail_by_core": db["by_core"],
        "wall": r.get("wall_secs"),
    }


def main() -> None:
    k = int(sys.argv[1]) if len(sys.argv) > 1 else 2500
    want = sys.argv[2:]
    topo = Ring2Topology(n_planes=1, vcs=CHI_VCS_WRITE)
    txns = build_pattern("uniform", k=k, W=W_FLITS, seed=0)

    t0 = time.perf_counter()
    base = run_one(topo, txns, {}, k=k, scheme="S0")
    print(f"K={k}\n  S0 reference: thr={base['thr']} "
          f"fail_ratio_max={base['fail_ratio_max']} "
          f"Jbin={base['jain_bin']} maxmin={base['max_min']}", flush=True)
    out: dict[str, Any] = {"k": k, "s0": base, "grids": {}}
    for name, cases in _grids().items():
        if want and name not in want:
            continue
        print(f"\n[{name}]  {len(cases)} runs", flush=True)
        rows = []
        for over in cases:
            row = run_one(topo, txns, over, k=k)
            row["thr_delta_pct"] = round(
                100.0 * (row["thr"] - base["thr"]) / base["thr"], 2)
            rows.append(row)
            print(f"  {json.dumps(over, sort_keys=True):<74} "
                  f"failmax={row['fail_ratio_max']:<7} "
                  f"n>=2:{row['n_ge2']}  thr={row['thr']:<8}"
                  f"({row['thr_delta_pct']:+.1f}%)  "
                  f"Jbin={row['jain_bin']} mm={row['max_min']}", flush=True)
        rows.sort(key=lambda r: (r["fail_ratio_max"] or 1e9))
        out["grids"][name] = rows
        b = rows[0]
        print(f"  best balance: {b['over']}  failmax={b['fail_ratio_max']} "
              f"thr={b['thr']} ({b['thr_delta_pct']:+.1f}%)", flush=True)
    out["wall_secs"] = round(time.perf_counter() - t0, 1)
    OUT.write_text(json.dumps(out, indent=1))
    print(f"\nwrote {OUT}  {out['wall_secs']}s")


if __name__ == "__main__":
    main()
