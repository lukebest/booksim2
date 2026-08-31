#!/usr/bin/env python3
"""What S22 actually needs from the hardware, priced one knob at a time.

The overhead table claims two costs over S1: the bus has to deliver in one
cycle instead of thirty, and the inject Q has to be deeper. Both are claims
about hardware, so both get measured here rather than asserted.

`dfc_bus_lat` is the load-bearing one. S22's deficit is a *current* progress
gap; if the table it is computed from is `lat` cycles stale the controller is
steering on history, and the 50-cycle index cannot see the correction. The
prediction is that the index degrades monotonically with latency and is back
near S0 by the time the bus costs a full S1 window.

`dir_inj_depth` prices the look-ahead: without candidates to overtake with, a
yield is an idle slot on a hop that is already ~91% loaded, so throughput
should fall while the index barely moves.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

_UTILS = Path(__file__).resolve().parent
if str(_UTILS) not in sys.path:
    sys.path.insert(0, str(_UTILS))

from dse_ring2_write_fair import (
    S22_CFG, W_FLITS, binned_jain, build_pattern, fairness_stats, run_scheme,
)
from rg_ring2_topo import CHI_VCS_WRITE, Ring2Topology

OUT = Path(__file__).resolve().parents[1] / "results" / "ring2_s22_cost.json"
BIN_W = 50

# Written before the runs. Do not edit after seeing results.
FORECAST = {
    "hypothesis": (
        "S22 的赤字是「当前」进度差，总线时延就是这个测量的年龄。"
        "bus_lat 从 1 拍拉到 30 拍（S1 的用量）后，让路依据变成一个窗口前的"
        "旧账，50 拍分箱看不到修正，Jbin 应单调退回 S0 附近（<0.97）；"
        "带宽不会明显变差，因为让路本身不扣槽。"
        "dir_inj_depth 从 32 减到 8 则相反：Jbin 基本不动，带宽掉 >1%，"
        "因为没有候选可超越的让路就是白扔一个槽。"
    ),
    "predicted": {
        "buslat30_jbin": [0.960, 0.975],
        "buslat_monotone": True,
        "buslat30_thr_delta_pct": [-1.0, 1.0],
        "dirq8_jbin": [0.988, 0.994],
        "dirq8_thr_delta_pct": [-2.5, -1.0],
    },
    "confidence": 0.7,
    "falsify": (
        "bus_lat=30 仍能保住 Jbin>0.99（那说明赤字其实是长期量，"
        "1 拍总线这笔开销可以省掉），"
        "或 dirq=8 的带宽损失不到 1%（那 32 深队列也可以省掉）"
    ),
}

# Written after the run, against the forecast above.
BELIEF = {
    "held": (
        "两笔开销都是真的。总线时延单调地吃掉 Jbin（1 拍 0.9916 → 30 拍 "
        "0.7536，比 S0 的 0.9572 还差），dir_q 从 32 减到 8 让带宽掉 1.49% "
        "而 Jbin 几乎不动（0.9916 → 0.9923），两者都落在预测区间内或方向一致。"
    ),
    "wrong": (
        "预测「让路不扣槽，所以总线变慢只伤公平、不伤带宽」——"
        "bus_lat=30 实测带宽掉 13.78%，远超预测的 ±1%。"
    ),
    "why": (
        "漏掉了一条反馈：赤字表变旧之后，已经追上的节点在表里仍然显示落后，"
        "于是请求一直挂着（让路判定活动量 75564 → 165539，翻了一倍多）。"
        "让路本身不扣槽，但『让给一个其实不落后的节点』就等于白扔一个 hop —— "
        "前瞻只在队列里真有非穿越候选时才救得回来。"
        "所以让路的代价不是常数，它随反馈年龄增长。"
    ),
    "revised": (
        "S22 的机制正确性依赖『赤字是当前值』这一条，"
        "不只是公平性依赖它。1 拍总线是硬需求，不是调优选择。"
    ),
}


def point(scheme: str, topo, txns, *, k: int, cfg: dict) -> dict:
    r = run_scheme(scheme, topo, txns, seed=0, cfg=cfg, quiet=True)
    inj = {int(c): v for c, v in (r.get("wr_inject_by_core") or {}).items()}
    f = fairness_stats(inj, r["makespan"] or 1, k * W_FLITS)
    jb = binned_jain(inj, BIN_W, f.get("t_fair") or 0)
    return {"cfg": cfg, "makespan": r["makespan"],
            "completed": r["completed"],
            "throughput": f["throughput"], "max_min": f["max_min"],
            "jain_bin": jb.get("jain_bin_mean"),
            "jain_bin_ideal": jb.get("jain_bin_ideal"),
            "n_dfc_yield": (r.get("fc") or {}).get("n_dfc_yield"),
            "n_dfc_dodge": (r.get("fc") or {}).get("n_dfc_dodge")}


def main() -> None:
    k = int(sys.argv[1]) if len(sys.argv) > 1 else 3000
    topo = Ring2Topology(vcs=CHI_VCS_WRITE, n_planes=1, route="latency")
    txns = build_pattern("uniform", k=k, W=W_FLITS, seed=0)
    t0 = time.perf_counter()
    print(f"K={k}  forecast frozen in source", flush=True)

    base = point("S0", topo, txns, k=k, cfg={})
    ref = base["throughput"]
    out = {"forecast": FORECAST, "belief_update": BELIEF, "k": k, "s0": base,
           "s22_cfg": dict(S22_CFG), "bus_lat": [], "dir_q": []}

    def say(tag: str, row: dict) -> None:
        d = 100.0 * (row["throughput"] - ref) / ref
        print(f"  {tag:20s} Jbin={row['jain_bin']:<9} "
              f"thr={row['throughput']:<8} ({d:+.2f}%)  "
              f"mm={row['max_min']:<8} yield={row['n_dfc_yield']} "
              f"dodge={row['n_dfc_dodge']}", flush=True)
        row["thr_delta_pct"] = round(d, 3)

    print(f"  {'S0':20s} Jbin={base['jain_bin']} thr={ref}", flush=True)
    for lat in (1, 2, 4, 8, 16, 30):
        row = point("S22", topo, txns, k=k, cfg={"dfc_bus_lat": lat})
        say(f"bus_lat={lat}", row)
        out["bus_lat"].append(row)
    for depth in (8, 16, 32):
        row = point("S22", topo, txns, k=k,
                    cfg={"dir_inj_depth": depth, "dfc_dodge": depth})
        say(f"dir_q={depth}", row)
        out["dir_q"].append(row)

    out["wall_secs"] = round(time.perf_counter() - t0, 1)
    OUT.write_text(json.dumps(out, indent=1))
    print(f"\nwrote {OUT}  {out['wall_secs']}s")


if __name__ == "__main__":
    main()
