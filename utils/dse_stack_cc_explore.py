#!/usr/bin/env python3
"""Explore hardware-cheap congestion control on the stacked write fabric.

Objective: E[Jain_t] → 1 and per-group write BW → ideal (bound/6).
Sweeps sender/receiver, window/rate, and trigger style on a 1-tile write
(same shape as the focus batch, 1/4 the length) and writes a cost/benefit
Pareto to results/stack_cc_pareto.json + .png.
"""

from __future__ import annotations

import json
import math
import time
from pathlib import Path
from typing import Any

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from dse_stack_write_fair import FABRIC, M_WDATA, run_scheme
from gen_stack_write_report import _cjk, inst_group_fairness
from rg_stack_topo import StackTopology, build_tiled_write

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "results" / "stack_cc_pareto.json"
PNG = ROOT / "results" / "stack_cc_pareto.png"

# Hardware cost in relative gate-equivalents. Integer, documented, not a
# synthesis number: bus > per-HA FSM > per-core regs > per-die counter.
COST = {
    "s0": 0,
    "s1": 80,
    "s1_nopath": 80,
    "s1_local": 18,
    "s16": 48,
    "s17": 12,
    "s18": 35,
    "s20": 8,
    "s20_leaky": 9,
    "s21": 4,
    "s21_leaky": 5,
}

LABEL = {
    "s0": "S0 无控",
    "s1": "S1 AIMD 窗+总线",
    "s1_nopath": "S1 无路径抵消",
    "s1_local": "S1 本地 AIMD",
    "s16": "S16 HA 授权",
    "s17": "S17 转向让行",
    "s18": "S18 RTT 窗口",
    "s20": "S20 每核 DAT 配额",
    "s20_leaky": "S20 漏桶",
    "s21": "S21 每 die DAT 配额",
    "s21_leaky": "S21 漏桶",
}

# (name, extra run_scheme kwargs)
SUITE = (
    ("s0", {}),
    ("s1", {}),
    ("s1_nopath", {"path_credit": False}),
    ("s1_local", {"path_credit": False, "use_bus": False}),
    ("s16", {}),
    ("s17", {}),
    ("s18", {}),
    ("s20", {"pace_window": 64, "pace_tokens": 10}),
    ("s20_leaky", {"pace_mode": "leaky", "pace_window": 64,
                   "pace_tokens": 10, "pace_burst": 8}),
    ("s21", {"pace_window": 64, "die_tokens": 98}),
    ("s21_leaky", {"pace_mode": "leaky", "pace_window": 64,
                   "die_tokens": 98, "die_burst": 40}),
)


def _sim_name(tag: str) -> str:
    if tag == "s1" or tag.startswith("s1_"):
        return "s1"
    if tag.startswith("s20"):
        return "s20"
    if tag.startswith("s21"):
        return "s21"
    return tag


def _cost_axes() -> dict[str, str]:
    return {
        "s0": "无",
        "s1": "发送端 / 窗口 / 拥塞等级",
        "s1_nopath": "发送端 / 窗口 / 本地失败",
        "s1_local": "发送端 / 窗口 / 本地失败无总线",
        "s16": "接收端 / 窗口 / DBID 授权",
        "s17": "织物侧 / 饥饿计数 / 转向",
        "s18": "发送端 / 窗口 / RTT",
        "s20": "发送端 / 速率 / 周期配额",
        "s20_leaky": "发送端 / 速率 / 漏桶",
        "s21": "发送端 / 速率 / die 配额",
        "s21_leaky": "发送端 / 速率 / die 漏桶",
    }


def pareto_min_x_max_y(pts: list[dict], xk: str, yk: str) -> list[dict]:
    """Non-dominated: smaller x (cost), larger y (benefit)."""
    ordered = sorted(pts, key=lambda p: (p[xk], -p[yk]))
    front, best_y = [], -1e30
    for p in ordered:
        if p[yk] > best_y + 1e-9:
            front.append(p)
            best_y = p[yk]
    return front


def run_one(topo: StackTopology, txns, tag: str, kw: dict,
            bound: dict, stall: int, seed: int) -> dict[str, Any]:
    name = _sim_name(tag)
    r = run_scheme(topo, txns, name, route="bound", seed=seed,
                   keep_trace=False, stall_after=stall, **kw)
    g = r["group"]
    inst = inst_group_fairness(r.get("bw_series") or {},
                               g.get("t_fair"))
    n_txn = len(txns)
    ideal_tot = (n_txn * M_WDATA / bound["bound"]) if bound["bound"] else 0
    ideal_g = ideal_tot / 6
    gp = g.get("goodput_total") or 0
    gp_g = gp / 6
    ej = inst.get("mean_jain") or 0
    bw_frac = (gp_g / ideal_g) if ideal_g else 0
    benefit = math.sqrt(max(0.0, ej) * max(0.0, bw_frac))
    return {
        "tag": tag, "scheme": name, "label": LABEL.get(tag, tag),
        "axis": _cost_axes().get(tag, ""),
        "completed": r["completed"],
        "makespan": r["makespan"],
        "n_txn_done": r["n_txn_done"],
        "goodput_total": round(gp, 5),
        "goodput_per_group": round(gp_g, 5),
        "ideal_per_group": round(ideal_g, 5),
        "bw_frac": round(bw_frac, 4),
        "mean_jain_t": ej,
        "jain_of_mean": inst.get("jain_of_mean"),
        "turn_index": inst.get("turn_index"),
        "frac_lt_090": inst.get("frac_lt_090"),
        "group_jain": g.get("jain"),
        "cost": COST.get(tag, 50),
        "benefit": round(benefit, 4),
        "wall_s": r.get("wall_s"),
        "kw": kw,
    }


def plot_pareto(blob: dict, path: Path) -> None:
    rows = blob["rows"]
    front = blob["pareto"]
    front_ids = {p["tag"] for p in front}
    _cjk()
    fig, axes = plt.subplots(1, 2, figsize=(12.4, 5.0))
    ax = axes[0]
    for r in rows:
        on = r["tag"] in front_ids
        ax.scatter(r["cost"], r["benefit"], s=90 if on else 60,
                   c="#16a34a" if on else "#2563eb", zorder=3)
        ax.annotate(r["label"], (r["cost"], r["benefit"]),
                    textcoords="offset points", xytext=(6, 4), fontsize=7)
    if len(front) >= 2:
        xs = [p["cost"] for p in front]
        ys = [p["benefit"] for p in front]
        ax.plot(xs, ys, "-", color="#16a34a", lw=1.1, alpha=0.7)
    ax.set_xlabel("实现代价（相对门数，越小越便宜）", fontsize=9)
    ax.set_ylabel("收益 √(E[Jain_t] × 写带宽/理想)", fontsize=9)
    ax.set_title("代价–收益 Pareto（绿 = 非支配）", fontsize=11)
    ax.grid(alpha=0.25)

    ax = axes[1]
    for r in rows:
        ax.scatter(r["mean_jain_t"], r["bw_frac"], s=20 + 3 * r["cost"],
                   c="#ea5800" if r["tag"] in front_ids else "#2563eb",
                   zorder=3)
        ax.annotate(r["label"], (r["mean_jain_t"], r["bw_frac"]),
                    textcoords="offset points", xytext=(6, 4), fontsize=7)
    ax.axvline(1.0, ls="--", color="#111827", lw=0.8)
    ax.axhline(1.0, ls="--", color="#111827", lw=0.8)
    ax.set_xlabel("E[Jain_t]（瞬时均衡，→1 更好）", fontsize=9)
    ax.set_ylabel("每组写带宽 / 理想（→1 更好）", fontsize=9)
    ax.set_title("目标平面（点大小 = 代价）", fontsize=11)
    ax.set_xlim(0.6, 1.02)
    ax.set_ylim(0.0, 1.15)
    ax.grid(alpha=0.25)
    fig.suptitle("堆叠 NoC 拥塞控制：硬件代价 vs E[Jain_t] / 写带宽",
                 fontsize=12)
    fig.tight_layout()
    fig.savefig(path, dpi=132)
    plt.close(fig)


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--n-tiles", type=int, default=1)
    ap.add_argument("--oc", type=int, default=FABRIC["core_outstanding"])
    ap.add_argument("--pos-depth", type=int, default=FABRIC["ha_pos_depth"])
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--only", nargs="*", default=None,
                    help="run only these tags (default: full SUITE)")
    ap.add_argument("--merge", action="store_true",
                    help="replace matching tags in an existing JSON")
    args = ap.parse_args()

    topo = StackTopology()
    txns = build_tiled_write(topo, n_tiles=args.n_tiles, seed=args.seed)
    bound = topo.write_bounds(txns, m_wdata=M_WDATA)
    stall = max(40_000, 80 * (len(txns) // max(1, len(topo.cores))))
    print(f"[cc] tiles={args.n_tiles} ntxn={len(txns)} bound={bound['bound']} "
          f"ideal_g={len(txns)*M_WDATA/bound['bound']/6:.4f} "
          f"oc={args.oc} pos={args.pos_depth}", flush=True)

    suite = SUITE
    if args.only:
        want = set(args.only)
        suite = tuple((t, k) for t, k in SUITE if t in want)
        extra_kw = {}
        for t in args.only:
            if t not in {x[0] for x in suite}:
                suite += ((t, extra_kw),)
    rows = []
    t0 = time.time()
    for tag, kw in suite:
        extra = dict(core_outstanding=args.oc, ha_pos_depth=args.pos_depth,
                     **kw)
        print(f"      {tag} ...", flush=True)
        rec = run_one(topo, txns, tag, extra, bound, stall, args.seed)
        rows.append(rec)
        print("      %-12s %-8s t=%6d ej=%.3f bw=%.3f× ideal  "
              "cost=%s benefit=%.3f"
              % (tag, "OK" if rec["completed"] else "FAIL",
                 rec["makespan"], rec["mean_jain_t"] or 0,
                 rec["bw_frac"], rec["cost"], rec["benefit"]),
              flush=True)

    if args.merge and OUT.exists():
        prev = json.loads(OUT.read_text())
        by = {r["tag"]: r for r in prev.get("rows") or []}
        for r in rows:
            by[r["tag"]] = r
        order = [t for t, _ in SUITE if t in by] + [t for t in by
                                                    if t not in dict(SUITE)]
        rows = [by[t] for t in order]
        wall = round((prev.get("meta") or {}).get("wall_s", 0) + time.time()
                     - t0, 1)
    else:
        wall = round(time.time() - t0, 1)
    front = pareto_min_x_max_y(rows, "cost", "benefit")
    best = max(rows, key=lambda r: (r["completed"], r["benefit"]))
    blob = {
        "meta": {
            "n_tiles": args.n_tiles, "n_txn": len(txns),
            "core_outstanding": args.oc, "pos_depth": args.pos_depth,
            "seed": args.seed, "bound": bound["bound"],
            "ideal_per_group": round(len(txns) * M_WDATA / bound["bound"] / 6,
                                     5),
            "wall_s": wall,
        },
        "rows": rows,
        "pareto": front,
        "best": best["tag"],
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(blob, indent=1))
    plot_pareto(blob, PNG)
    print(f"wrote {OUT}  {PNG}  best={best['tag']}  "
          f"front={[p['tag'] for p in front]}", flush=True)


if __name__ == "__main__":
    main()
