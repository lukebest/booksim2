#!/usr/bin/env python3
"""Re-tune S22 on the corrected fabric, where the baseline is already fairer.

Implementing I-tag as specified moved the baseline: per-bin Jain went from
0.957 to 0.968 and whole-window max/min from 1.12 to 1.03. S22 was tuned
against the old, less fair baseline, so at its old operating point it now
intervenes harder than it needs to and costs 1.88% -- over the acceptance line.

Every knob here makes the controller *gentler*, which is the direction the new
baseline calls for: a longer control window averages the deficit over more
cycles, a higher threshold ignores smaller gaps, and a higher margin refuses
more near-level swaps. The question is whether any combination clears
Jain > 0.99 while staying inside 1% of S0.

Usage:
    PYTHONHASHSEED=0 python3 probe_ring2_s22_retune.py [K]
"""
from __future__ import annotations

import json
import sys
import time
from itertools import product
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from dse_ring2_write_fair import (BIN_W, FABRIC, W_FLITS, binned_jain,
                                  build_pattern, fairness_stats, run_scheme)
from rg_ring2_topo import CHI_VCS_WRITE, Ring2Topology

OUT = (Path(__file__).resolve().parents[1] / "results"
       / "probe_ring2_s22_retune.json")

DEEP = {"dfc_dodge": 32, "dir_inj_depth": 32, "inj_depth": 32}

# Written before the runs. Do not edit after seeing results.
FORECAST = {
    "hypothesis": (
        "S22 的代价来自「让路」，而让路次数由「有多少节点被判定落后」驱动。"
        "修正 I-tag 之后基线本身已经把 max/min 从 1.12 压到 1.03，"
        "落后判定应当大幅减少，所以同一组参数下让路次数会下降、"
        "但旧工作点 (w=2, thresh=0.5, margin=2) 仍然过于激进 —— "
        "它是针对更不公平的基线调的。放宽任一项（更长窗口 / 更高门限 / "
        "更大余量）应该都能把带宽损失拉回 1% 以内，"
        "而 Jbin 因为起点更高（0.968 而非 0.957）不需要那么多干预就能过 0.99。"
    ),
    "predicted": {
        "exists_point_passing_both": True,
        "best_thr_delta_pct": [-1.0, 0.2],
        "best_jbin": [0.990, 0.996],
        # Gentler settings should trade monotonically: less yield, less cost.
        "yield_falls_with_margin": True,
    },
    "confidence": 0.65,
    "falsify": (
        "所有 48 个点都过不了双线（要么 Jbin<0.99，要么带宽差>1%）——"
        "那说明在更公平的基线上 S22 的边际收益已经不足以抵消它的边际代价，"
        "必须换机制而不是换参数"
    ),
}


def main() -> None:
    k = int(sys.argv[1]) if len(sys.argv) > 1 else 3000
    topo = Ring2Topology(n_planes=1, vcs=CHI_VCS_WRITE, route="latency")
    tx = build_pattern("uniform", k=k, W=W_FLITS, seed=0)

    def run(scheme: str, over: dict[str, Any]) -> dict[str, Any]:
        r = run_scheme(scheme, topo, tx, seed=0, cfg={**FABRIC, **over},
                       quiet=True)
        inj = r.get("wr_inject_by_core") or {}
        f = fairness_stats(inj, r["makespan"], k * W_FLITS)
        jb = binned_jain(inj, BIN_W, f["t_fair"])
        fc = r.get("fc") or {}
        return {"thr": f["throughput"], "jain_bin": jb["jain_bin_mean"],
                "max_min": f["max_min"], "bw_min": f["bw_min"],
                "bw_max": f["bw_max"],
                "n_yield": fc.get("n_dfc_yield"), "n_dodge": fc.get("n_dfc_dodge")}

    s0 = run("S0", {})
    print(f"K={k}  S0 thr={s0['thr']} Jbin={s0['jain_bin']} "
          f"mm={s0['max_min']}", flush=True)

    rows = []
    t0 = time.perf_counter()
    grid = list(product((2, 3, 4, 6), (0.5, 1.0, 2.0), (2.0, 3.0, 4.0, 6.0)))
    for w, thresh, margin in grid:
        cfg = {"dfc_window": w, "dfc_bus_lat": 1, "dfc_thresh": thresh,
               "dfc_hold": 16, "dfc_margin": margin, **DEEP}
        r = run("S22", cfg)
        d = 100.0 * (r["thr"] - s0["thr"]) / s0["thr"]
        ok = r["jain_bin"] > 0.99 and abs(d) < 1.0
        rows.append({"cfg": {"dfc_window": w, "dfc_thresh": thresh,
                             "dfc_margin": margin},
                     **r, "thr_delta_pct": round(d, 2), "pass": ok})
        print(f"  w={w} thresh={thresh} margin={margin}  "
              f"Jbin={r['jain_bin']:.5f} thr={r['thr']:.4f} ({d:+.2f}%) "
              f"mm={r['max_min']}  yield={r['n_yield']} dodge={r['n_dodge']}"
              f"{'   <== PASS' if ok else ''}", flush=True)

    passing = [r for r in rows if r["pass"]]
    passing.sort(key=lambda r: -r["jain_bin"])
    out = {"k": k, "s0": s0, "forecast": FORECAST, "rows": rows,
           "passing": passing, "wall_secs": round(time.perf_counter() - t0, 1)}
    OUT.write_text(json.dumps(out, indent=1))
    print(f"\n{len(passing)}/{len(rows)} pass both lines")
    for r in passing[:5]:
        print(f"  {r['cfg']}  Jbin={r['jain_bin']} thr={r['thr']} "
              f"({r['thr_delta_pct']:+.2f}%)")
    print(f"wrote {OUT}  {out['wall_secs']}s")


if __name__ == "__main__":
    main()
