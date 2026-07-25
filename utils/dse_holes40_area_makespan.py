#!/usr/bin/env python3
"""Makespan × implementation-area Pareto for 40-compute / 8-hole allgather.

Mirrors dse_multi_area_makespan.py on the hole topology:
  * 8×6 mesh, holes at 1-indexed cols 4–5 × rows 1–4 (transit-only)
  * allgather among 40 alive compute nodes
  * rigid pack under (W, E, B) eject path; area = IQ-XY + calendar + CalFork + eject

Also records measured 5-flit T5 / delta2 at each coherent point (E drain).
"""

from __future__ import annotations

import json
import math
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

import ppa_analytic_model as PPA
import sched_zerobuf_compare as S
import dse_burst_sweep_8x6 as BSW
from dse_axis_area_makespan import area_parts, pareto
from dse_multi_area_makespan import total_area
from dse_tree_allgather_6x8 import (
    MX, MY, H, V, RAMP,
    dim_tree, col_comb_tree, edge_comb_tree, hamilton_tree, coord, nid,
)
from dse_holes_40_allgather import (
    HOLES, ALIVE, NA, steiner_arborescence, dim_order_arborescence,
    axis_ccw_alive, dual_wing_bridge, dual_wing_comb,
    validate_alive_tree, footprint_alive, build_fps, formal_bounds_alive,
    pack_rounds_alive, delta2_alive, ascii_map, svg_tree,
)
from dse_multiflit_area_makespan import pack_one_with_offs, ROUNDS

ROOT = Path(__file__).resolve().parents[1]
OUT_JSON = ROOT / "results" / "holes40_area_makespan.json"
OUT_PNG = ROOT / "results" / "holes40_area_makespan.png"
OUT_PNG_MF = ROOT / "results" / "holes40_multiflit_area_makespan.png"

MC = PPA.CALFORK_MC_DELTA
W_RANGE = [1, 2, 3, 4]
E_RANGE = [2]  # eject lanes fixed to 2
B_RANGE = [0, 1, 2, 4, 8, 11]


def prune_full(builder):
    """Prune a full-mesh tree builder to an alive-covering arborescence."""
    def b(source: int):
        full = builder(source)
        parent = {c: p for p, c in full}
        needed = set()
        for d in ALIVE:
            if d == source:
                continue
            cur = d
            seen = set()
            while cur != source and cur in parent and cur not in seen:
                seen.add(cur)
                p = parent[cur]
                needed.add((p, cur))
                cur = p
        if len({c for _, c in needed}) < NA - 1:
            return steiner_arborescence(source)
        return sorted(needed)
    return b


SCHEMES = {
    "axis_ccw": ("axis+CCW pruned", axis_ccw_alive),
    "steiner_sp": ("shortest-path Steiner", steiner_arborescence),
    "dim_xy": ("dim-XY pruned", prune_full(lambda s: dim_tree(s, "xy"))),
    "dim_yx": ("dim-YX pruned", prune_full(lambda s: dim_tree(s, "yx"))),
    "col_comb3": ("col-comb3 pruned", prune_full(col_comb_tree)),
    "nec3": ("NEC-3 pruned", prune_full(lambda s: edge_comb_tree(s))),
    "nec2": ("NEC-2 pruned", prune_full(lambda s: edge_comb_tree(s, fanout_two=True))),
    "hamilton_bi_tree": ("Hamilton bi-tree pruned",
                         prune_full(lambda s: hamilton_tree(s, True))),
    "wing_bridge_y4": ("dual-wing bridge y=4", lambda s: dual_wing_bridge(s, 4)),
    "wing_comb_y5": ("dual-wing comb y=5", dual_wing_comb),
}


def coherent():
    for E in E_RANGE:
        for W in W_RANGE:
            if W < E:
                continue
            for B in B_RANGE:
                if B == 0 and W != E:
                    continue
                yield W, E, B


def uarch_from_trees(builder):
    """Estimate Pmax / issue_width from alive trees (offset-0 superposition)."""
    trees = {}
    for s in ALIVE:
        e = builder(s)
        chk = validate_alive_tree(s, e)
        assert chk["ok"], (s, chk["errors"])
        trees[s] = chk
    # issue: max concurrent mesh fanouts at a (node, cycle)
    peak = defaultdict(int)
    busy_cycles = defaultdict(set)
    fan_max = 0
    for s, tree in trees.items():
        fan_max = max(fan_max, tree["max_fanout"])
        for node, dist in tree["distance"].items():
            fan = len(tree["children"].get(node, []))
            if fan == 0:
                continue
            cy = RAMP + dist
            peak[(node, cy)] += 1
            busy_cycles[node].add(cy)
    issue = max(peak.values()) if peak else 1
    # topology period ≈ #distinct busy relative cycles at busiest router
    pmax = max((len(v) for v in busy_cycles.values()), default=1)
    dil = max(2 * RAMP + max(t["distance"][d] for d in ALIVE)
              for t in trees.values())
    return pmax, max(issue, 1), fan_max, dil


def alive_orders():
    hx, hy = 3.5, 1.5
    return [
        sorted(ALIVE, key=lambda s: (coord(s)[0] - hx) ** 2 + (coord(s)[1] - hy) ** 2,
               reverse=True),
        sorted(ALIVE, key=lambda s: min(
            coord(s)[0] * H + coord(s)[1] * V,
            (MX - 1 - coord(s)[0]) * H + coord(s)[1] * V,
            coord(s)[0] * H + (MY - 1 - coord(s)[1]) * V,
            (MX - 1 - coord(s)[0]) * H + (MY - 1 - coord(s)[1]) * V),
               reverse=True),
        [s for s in ALIVE if coord(s)[0] <= 2]
        + [s for s in ALIVE if coord(s)[0] >= 5]
        + [s for s in ALIVE if 2 < coord(s)[0] < 5],
    ]


def make_plot(points, front, per_scheme_front, lb):
    fig, ax = plt.subplots(figsize=(10.2, 6.4))
    cmap = plt.get_cmap("tab10")
    for i, (key, (label, _)) in enumerate(SCHEMES.items()):
        pts = [p for p in points if p["scheme"] == key and p["makespan"]]
        ax.scatter([p["area_total"] for p in pts],
                   [p["makespan"] for p in pts],
                   s=26, color=cmap(i % 10), alpha=0.45, edgecolor="none")
        fr = per_scheme_front[key]
        if fr:
            ax.plot([p["area_total"] for p in fr], [p["makespan"] for p in fr],
                    "-", color=cmap(i % 10), lw=1.1, alpha=0.85, label=label)
    ax.plot([p["area_total"] for p in front], [p["makespan"] for p in front],
            "-o", color="#111827", lw=2.3, ms=5, zorder=5, label="global Pareto")
    for p in front:
        ax.annotate(f"{p['label'][:10]}\nW{p['W']}/E{p['E']}/B{p['B']}",
                    (p["area_total"], p["makespan"]),
                    textcoords="offset points", xytext=(5, 3), fontsize=6)
    ax.axhline(lb, color="#2563eb", ls="--", lw=1)
    ax.text(max(p["area_total"] for p in points if p["makespan"]), lb + 0.8,
            f"LB={lb}", color="#2563eb", fontsize=8, ha="right")
    ax.set_xlabel("chip implementation area (IQ-XY=1.0)")
    ax.set_ylabel("1-flit makespan (cycles)")
    ax.set_title("40-compute / 8-hole allgather: makespan vs area (E≡2, sweep W/B)")
    ax.grid(True, ls=":", alpha=0.5)
    ax.legend(loc="upper right", fontsize=7, ncol=2)
    fig.tight_layout()
    fig.savefig(OUT_PNG, dpi=130)
    plt.close(fig)


def make_mf_plot(points, front_avg, front_t15):
    fig, (a0, a1) = plt.subplots(1, 2, figsize=(13.6, 5.8))
    cmap = plt.get_cmap("tab10")
    keys = list(SCHEMES)
    for i, k in enumerate(keys):
        pp = [p for p in points if p["scheme"] == k and p.get("t_avg")]
        a0.scatter([p["area_total"] for p in pp], [p["t_avg"] for p in pp],
                   s=22, color=cmap(i % 10), alpha=0.45, edgecolor="none",
                   label=SCHEMES[k][0])
        a1.scatter([p["t1"] for p in pp], [p["t5"] for p in pp],
                   s=22, color=cmap(i % 10), alpha=0.45, edgecolor="none",
                   label=SCHEMES[k][0])
    if front_avg:
        a0.plot([p["area_total"] for p in front_avg],
                [p["t_avg"] for p in front_avg],
                "-o", color="#111827", lw=2.2, ms=5, zorder=5)
    if front_t15:
        a1.plot([p["t1"] for p in front_t15], [p["t5"] for p in front_t15],
                "-o", color="#111827", lw=2.0, ms=5, zorder=5)
    a0.set_xlabel("area"); a0.set_ylabel("T_avg=(T1+T5)/2")
    a0.set_title("E≡2: area vs T_avg (R=5 measured)")
    a0.grid(True, ls=":", alpha=0.5); a0.legend(fontsize=6, ncol=2)
    a1.set_xlabel("T1"); a1.set_ylabel("T5")
    a1.set_title("fill vs throughput")
    a1.grid(True, ls=":", alpha=0.5); a1.legend(fontsize=6, ncol=2)
    fig.tight_layout()
    fig.savefig(OUT_PNG_MF, dpi=130)
    plt.close(fig)


def main() -> None:
    S.cfg(MX, MY, H, V)
    S.init_ring()
    S.init_quadrants()
    orders = alive_orders()
    bounds = formal_bounds_alive(1)
    lb = bounds["T_lb"]

    points = []
    scheme_meta = {}
    for key, (label, builder) in SCHEMES.items():
        pmax, issue, fan, dil = uarch_from_trees(builder)
        fps, _ = build_fps(builder)
        scheme_meta[key] = {
            "label": label, "pmax": pmax, "issue": issue,
            "fanout_max": fan, "dilation": dil,
        }
        print(f"{label:26s} Pmax={pmax} issue={issue} dil={dil}", flush=True)
        for W, E, B in coherent():
            BSW.XBAR_WRITE = W
            BSW.DRAIN = E
            pack1 = None
            for order in orders:
                rec = pack_one_with_offs(fps, B, order)
                if rec and (pack1 is None or rec["makespan"] < pack1["makespan"]):
                    pack1 = rec
            t1 = pack1["makespan"] if pack1 else None
            d2 = delta2_alive(fps, B, pack1) if pack1 else None
            # T5: only try primary order × 2 modes (cost control)
            r5 = None
            if pack1:
                for mode in ("round_major", "source_major"):
                    rec = pack_rounds_alive(fps, ROUNDS, B, orders[0], mode)
                    if rec and (r5 is None or rec["makespan"] < r5["makespan"]):
                        r5 = rec
            t5 = r5["makespan"] if r5 else None
            ii_eff = (round((t5 - t1) / (ROUNDS - 1), 2)
                      if (t1 is not None and t5 is not None) else None)
            t_avg = (round((t1 + t5) / 2, 1)
                     if (t1 is not None and t5 is not None) else None)
            points.append({
                "scheme": key, "label": label, "pmax": pmax, "issue": issue,
                "W": W, "E": E, "B": B,
                "makespan": t1, "t1": t1, "t5": t5, "ii_eff": ii_eff,
                "t_avg": t_avg,
                "delta2_min": d2["min"] if d2 else None,
                "delta2_avg": d2["avg"] if d2 else None,
                "delta2_max": d2["max"] if d2 else None,
                "area_total": total_area(pmax, issue, W, E, B),
            })
        floor = min(p["makespan"] for p in points
                    if p["scheme"] == key and p["makespan"])
        print(f"  T1_floor={floor}  "
              f"T5_floor={min(p['t5'] for p in points if p['scheme']==key and p['t5'])}")

    front = pareto(points, "area_total", "makespan")
    front.sort(key=lambda p: p["area_total"])
    per_scheme_front = {
        k: pareto([p for p in points if p["scheme"] == k],
                  "area_total", "makespan")
        for k in SCHEMES
    }
    make_plot(points, front, per_scheme_front, lb)

    mf_pts = [p for p in points if p.get("t_avg") is not None]
    front_avg = pareto(mf_pts, "area_total", "t_avg")
    front_avg.sort(key=lambda p: p["area_total"])
    front_t15 = pareto(mf_pts, "t1", "t5")
    front_t15.sort(key=lambda p: p["t1"])
    make_mf_plot(mf_pts, front_avg, front_t15)

    demo_src = nid(6, 1)
    demo_svg = svg_tree(demo_src, axis_ccw_alive(demo_src))

    floors = {}
    for k in SCHEMES:
        sp = [p for p in points if p["scheme"] == k and p["makespan"]]
        fl = min(p["makespan"] for p in sp)
        area_fl = min(p["area_total"] for p in sp if p["makespan"] == fl)
        t5f = min(p["t5"] for p in sp if p["t5"])
        floors[k] = {"t1": fl, "area_at_t1": area_fl, "t5": t5f}

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "model": {
            "mesh": [MX, MY], "H": H, "V": V,
            "holes_1indexed": {"cols": [4, 5], "rows": [1, 2, 3, 4]},
            "N_alive": NA, "N_holes": len(HOLES),
            "ascii_map": ascii_map(),
            "lower_bound_m1": lb,
            "bounds": bounds,
            "design_vars": {"W": W_RANGE, "E": E_RANGE, "B": B_RANGE},
            "area": "1.0 + calendar(Pmax,issue) + CalFork + eject(W,E,B)",
            "multicast_delta": MC,
            "semantics": ("H=non-compute but live routers/links; "
                          "transit-only (no inject/eject); allgather among 40 C"),
        },
        "scheme_meta": scheme_meta,
        "scheme_floors": floors,
        "points": points,
        "global_pareto": front,
        "per_scheme_pareto": {k: per_scheme_front[k] for k in SCHEMES},
        "pareto_area_tavg": front_avg,
        "pareto_t1_t5": front_t15,
        "demo_svg": demo_svg,
        "demo_svg_source": list(coord(demo_src)),
        "plot": str(OUT_PNG.relative_to(ROOT)),
        "plot_multiflit": str(OUT_PNG_MF.relative_to(ROOT)),
    }
    OUT_JSON.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"\npoints={len(points)} global_pareto={len(front)}")
    print(f"schemes_on_pareto={sorted({p['scheme'] for p in front})}")
    print(f"Wrote {OUT_JSON}\nWrote {OUT_PNG}\nWrote {OUT_PNG_MF}")


if __name__ == "__main__":
    main()
