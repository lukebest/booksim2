#!/usr/bin/env python3
"""Re-tune S22 for per-direction up-ring ports, where the baseline is *less* fair.

Giving each direction its own port group (the real full-ring structure: six
inject ports per node, REQ / RSP / DAT per direction) moves the baseline the
opposite way from the previous two re-tunes. Throughput goes up, but so does
the spread: a node can now board on both directions in the same cycle, which
compounds the position advantage of cores sitting next to a memory node. The
short probe put S0's per-bin Jain near 0.89 against 0.955 on the shared port,
with whole-window max/min near 1.94 against 1.36.

So the tuning direction reverses. The last two rounds wanted the controller
*gentler* (margin 2 -> 4) because the baseline had become fair enough that most
deficits were noise. Here the deficits are real and large, so the controller
needs to intervene *harder*: the grid is extended down to margin 0 and 1, which
previous rounds had ruled out as too aggressive.

The two acceptance lines are unchanged -- per-bin Jain > 0.99 and total write
bandwidth within 1% of S0 -- but they are now a much longer reach, because the
gap to close is ~0.10 of Jain rather than ~0.02.

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
        "按方向拆端口之后基线反而更不齐（Jbin ≈ 0.89、max/min ≈ 1.94），"
        "因为一个节点现在能在同一拍向两个方向各上环一个 flit，"
        "邻 mem 多的核的位置优势被放大。S22 的机制本身与不齐的<b>来源</b>无关 —— "
        "它只看总线上播的进度差 —— 所以它应该仍然能收敛，"
        "但需要比上一轮<b>更激进</b>的工作点（margin 0~2 而不是 4），"
        "且带宽代价会更高，因为要让的路更多。"
        "关键不确定性在于：这次要补的 Jain 缺口是 0.10 而不是 0.02，"
        "让路次数可能大到把带宽拖出 1% 以外。"
    ),
    "predicted": {
        "exists_point_passing_both": True,
        "best_margin_le": 2.0,
        "best_thr_delta_pct": [-1.0, 0.0],
        "best_jbin": [0.990, 0.995],
        # More aggression should now cost more, not less.
        "yield_rises_vs_prev_fabric": True,
    },
    "confidence": 0.45,
    "falsify": (
        "所有点都过不了双线 —— 那说明在按方向拆端口的 fabric 上，"
        "「不扣槽、只换仲裁赢家」这条路的收敛能力不足以补 0.10 的 Jain 缺口，"
        "必须回到扣槽类机制（并接受带宽损失），或者承认这条 fabric 上"
        "两条线不可同时满足"
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
    grid = list(product((2, 3, 4), (0.5, 1.0, 2.0), (0.0, 1.0, 2.0, 4.0)))
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
