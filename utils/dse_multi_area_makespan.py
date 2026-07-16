#!/usr/bin/env python3
"""Multi-scheme makespan vs implementation-area Pareto on 8x6.

Extends the axis+CCW-only study to NEC-3/NEC-2/Hamilton-bi/dim-XY/dim-YX/
col-comb3.  For every scheme we sweep the eject-path design variables
(W = crossbar down-ramp bandwidth, E = buffer->SRAM drain bandwidth,
B = burst-buffer depth) and compute:

  makespan(scheme,W,E,B)  : rigid wide-eject FIFO pack (dse_burst_sweep model),
  area(scheme,W,E,B)      : 1.0 (IQ-XY core)
                            + calendar(Pmax_scheme, issue)   (slot table)
                            + CalFork multicast (fixed)
                            + eject-path delta (crossbar ports, buffer, SRAM).

Reuses the area primitives and Pareto helper from dse_axis_area_makespan.
Also reports each scheme's own frontier and a calendar-cost crossover: the
multiplier on slot-table area at which a lower-Pmax scheme would enter the
global Pareto (nominal calendar area is negligible, so axis+CCW wins).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

import ppa_analytic_model as PPA
import sched_zerobuf_compare as S
import dse_burst_sweep_8x6 as BSW
from dse_axis_area_makespan import area_parts, pareto
from dse_tree_allgather_6x8 import (
    MX, MY, H, V, formal_bounds, pack_scheme, next_power_of_two,
    axis_ccw_tree, dim_tree, col_comb_tree, edge_comb_tree, hamilton_tree,
)

ROOT = Path(__file__).resolve().parents[1]
OUT_JSON = ROOT / "results" / "multi_area_makespan.json"
OUT_PNG = ROOT / "results" / "multi_area_makespan.png"

# Requested schemes (+ axis baseline).  Labels in plot order.
SCHEMES = {
    "axis_ccw": ("axis+CCW", axis_ccw_tree),
    "dim_yx": ("dim-YX", lambda s: dim_tree(s, "yx")),
    "dim_xy": ("dim-XY", lambda s: dim_tree(s, "xy")),
    "col_comb3": ("col-comb3", col_comb_tree),
    "nec3": ("NEC-3", lambda s: edge_comb_tree(s)),
    "nec2": ("NEC-2", lambda s: edge_comb_tree(s, fanout_two=True)),
    "hamilton_bi_tree": ("Hamilton bi-tree", lambda s: hamilton_tree(s, True)),
}

W_RANGE = [1, 2, 3, 4]
E_RANGE = [1, 2]
B_RANGE = [0, 1, 2, 4, 8, 11]
MC = PPA.CALFORK_MC_DELTA


def coherent():
    for E in E_RANGE:
        for W in W_RANGE:
            if W < E:
                continue
            for B in B_RANGE:
                if B == 0 and W != E:
                    continue
                yield W, E, B


def calendar_area(pmax: int, issue: int, mult: float = 1.0) -> float:
    return mult * PPA.sparse_calendar_area(next_power_of_two(pmax)) * issue


def scheme_pmax(name, builder):
    rec = pack_scheme(name, builder, 1)
    mi = rec["microarchitecture"]
    return mi["topology_period_max"], mi["calendar_issue_width"]


def total_area(pmax, issue, W, E, B, cal_mult=1.0):
    xbar, buf, sram = area_parts(W, E, B)
    return round(1.0 + calendar_area(pmax, issue, cal_mult) + MC
                 + xbar + buf + sram, 5)


LAMBDA_VIEWS = [1, 20, 50, 100]
LOW_MK_BAND = 118  # "low-makespan" threshold owned by axis+CCW at nominal cost


def pareto_at_lambda(points, lam):
    """Global Pareto when slot-table (calendar) area is scaled by lam."""
    pts = []
    for p in points:
        if p["makespan"] is None:
            continue
        a = total_area(p["pmax"], p["issue"], p["W"], p["E"], p["B"],
                       cal_mult=lam)
        pts.append({**p, "area_total": a})
    return pareto(pts, "area_total", "makespan")


def ownership(front):
    """Per-scheme makespan values owned on a Pareto front (sorted)."""
    own = {}
    for p in front:
        own.setdefault(p["scheme"], []).append(p["makespan"])
    return {k: sorted(v) for k, v in sorted(own.items())}


def lambda_views(points):
    views = []
    for lam in LAMBDA_VIEWS:
        fr = pareto_at_lambda(points, lam)
        own = ownership(fr)
        low_owners = sorted(
            {s for s in own if s != "axis_ccw"
             and any(mk <= LOW_MK_BAND for mk in own[s])})
        views.append({
            "lambda": lam,
            "front": fr,
            "ownership": own,
            "axis_owns": own.get("axis_ccw", []),
            "low_band_nonaxis_owners": low_owners,
        })
    return views


def smallest_lambda_low_pmax_owns(points, mk_thresh=LOW_MK_BAND):
    """Smallest calendar-area multiplier at which a non-axis (lower-Pmax)
    scheme owns a Pareto point with makespan <= mk_thresh."""
    lam = 1.0
    while lam <= 1000:
        fr = pareto_at_lambda(points, lam)
        for p in fr:
            if p["scheme"] != "axis_ccw" and p["makespan"] is not None \
                    and p["makespan"] <= mk_thresh:
                return {"lambda": round(lam, 1), "scheme": p["scheme"],
                        "makespan": p["makespan"],
                        "area_total": p["area_total"]}
        lam += 1.0 if lam < 60 else 5.0
    return {"lambda": None, "scheme": None, "makespan": None}


def crossover_calendar(points):
    """Smallest slot-table area multiplier at which a non-axis scheme enters
    the global Pareto (axis+CCW has the deepest calendar, Pmax=15)."""
    lam = 1.0
    while lam <= 400:
        fr = pareto_at_lambda(points, lam)
        winners = {q["scheme"] for q in fr}
        if winners - {"axis_ccw"}:
            return {"multiplier": round(lam, 1),
                    "entering_schemes": sorted(winners - {"axis_ccw"})}
        lam += 1.0 if lam < 40 else 10.0
    return {"multiplier": None, "entering_schemes": []}


def make_plot(points, front, per_scheme_front, lb, views=None):
    if views:
        fig, (ax, ax2) = plt.subplots(
            1, 2, figsize=(13.6, 6.0),
            gridspec_kw={"width_ratios": [2.05, 1.0]})
    else:
        fig, ax = plt.subplots(figsize=(9.2, 6.0))
        ax2 = None
    cmap = plt.get_cmap("tab10")
    for i, (key, (label, _)) in enumerate(SCHEMES.items()):
        pts = [p for p in points if p["scheme"] == key and p["makespan"]]
        ax.scatter([p["area_total"] for p in pts], [p["makespan"] for p in pts],
                   s=26, color=cmap(i), alpha=0.45, edgecolor="none", zorder=2)
        fr = per_scheme_front[key]
        ax.plot([p["area_total"] for p in fr], [p["makespan"] for p in fr],
                "-", color=cmap(i), lw=1.1, alpha=0.8, zorder=3, label=label)
    fx = [p["area_total"] for p in front]
    fy = [p["makespan"] for p in front]
    ax.plot(fx, fy, "-o", color="#111827", lw=2.4, ms=6, zorder=5,
            label="global Pareto")
    for p in front:
        ax.annotate(f"W{p['W']}/E{p['E']}/B{p['B']}",
                    (p["area_total"], p["makespan"]),
                    textcoords="offset points", xytext=(6, 5),
                    fontsize=6.5, color="#111827")
    ax.axhline(lb, color="#2563eb", ls="--", lw=1, zorder=1)
    ax.text(max(p["area_total"] for p in points if p["makespan"]), lb + 1,
            f"rb=2 lower bound LB={lb}", color="#2563eb", fontsize=8,
            ha="right", va="bottom")
    ax.set_xlabel("chip implementation area (normalized, IQ-XY=1.0)")
    ax.set_ylabel("makespan (cycles)")
    ax.set_title("allgather schemes: makespan vs implementation area (W/E/B swept)")
    ax.grid(True, ls=":", alpha=0.5)
    ax.legend(loc="upper right", fontsize=8, ncol=2)

    if ax2 is not None:
        key_index = {k: i for i, k in enumerate(SCHEMES)}
        for v in views:
            for p in v["front"]:
                ax2.scatter(v["lambda"], p["makespan"], s=30,
                            color=cmap(key_index[p["scheme"]]),
                            edgecolor="none", alpha=0.85, zorder=3)
        ax2.axhline(LOW_MK_BAND, color="#dc2626", ls="--", lw=1)
        ax2.text(LAMBDA_VIEWS[-1], LOW_MK_BAND + 0.5,
                 f"low band <= {LOW_MK_BAND}", color="#dc2626",
                 fontsize=7.5, ha="right", va="bottom")
        ax2.set_xscale("symlog")
        ax2.set_xticks(LAMBDA_VIEWS)
        ax2.set_xticklabels([str(x) for x in LAMBDA_VIEWS])
        ax2.set_xlabel("calendar-area weight lambda")
        ax2.set_ylabel("Pareto makespan (owned)")
        ax2.set_title("slot-table-cost sensitivity:\nwho owns the Pareto vs lambda")
        ax2.grid(True, ls=":", alpha=0.5)

    fig.tight_layout()
    fig.savefig(OUT_PNG, dpi=130)
    plt.close(fig)


def main() -> None:
    S.cfg(MX, MY, H, V)
    S.init_ring()
    S.init_quadrants()
    orders = [BSW.corner_order()]
    for _n, gen in S.SRC_ORDERS.items():
        try:
            orders.append(list(gen()))
        except TypeError:
            continue
    lb = formal_bounds(1)["T_lb"]

    points = []
    scheme_meta = {}
    for key, (label, builder) in SCHEMES.items():
        pmax, issue = scheme_pmax(key, builder)
        fps, _stretch, dil = BSW.build(builder)
        scheme_meta[key] = {"label": label, "pmax": pmax, "issue": issue,
                            "dilation": dil}
        for W, E, B in coherent():
            BSW.XBAR_WRITE = W
            BSW.DRAIN = E
            rec = BSW.pack_with_buffer(fps, B, orders)
            mk = rec["makespan"] if rec else None
            points.append({
                "scheme": key, "label": label, "pmax": pmax, "issue": issue,
                "W": W, "E": E, "B": B, "makespan": mk,
                "area_total": total_area(pmax, issue, W, E, B),
            })
        print(f"{label:16s} Pmax={pmax} issue={issue} dil={dil} "
              f"floor={min(p['makespan'] for p in points if p['scheme']==key and p['makespan'])}")

    front = pareto(points, "area_total", "makespan")
    per_scheme_front = {
        key: pareto([p for p in points if p["scheme"] == key],
                    "area_total", "makespan")
        for key in SCHEMES
    }
    crossover = crossover_calendar(points)
    views = lambda_views(points)
    smallest_lam = smallest_lambda_low_pmax_owns(points)

    # convergence: signature is the per-lambda ownership map across all views.
    signature = {str(v["lambda"]): v["ownership"] for v in views}
    prev_sig, prev_stable = None, 0
    if OUT_JSON.exists():
        try:
            prev = json.loads(OUT_JSON.read_text(encoding="utf-8"))
            prev_sig = (prev.get("lambda_signature")
                        or {str(v["lambda"]): v["ownership"]
                            for v in prev.get("lambda_views", [])})
            prev_stable = prev.get("convergence", {}).get("stable_ticks", 0)
        except Exception:
            prev_sig = None
    stable_ticks = (prev_stable + 1) if (prev_sig == signature and prev_sig) else 1
    converged = stable_ticks >= 2
    convergence = {"stable_ticks": stable_ticks, "converged": converged,
                   "changed_vs_prev": prev_sig != signature}

    make_plot(points, front, per_scheme_front, lb, views)

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "model": {
            "mesh": [MX, MY], "H": H, "V": V, "lower_bound_rb2": lb,
            "design_vars": {"W": W_RANGE, "E": E_RANGE, "B": B_RANGE},
            "area": "1.0 core + calendar(Pmax,issue) + CalFork MC + eject(W,E,B)",
            "multicast_delta": MC,
        },
        "scheme_meta": scheme_meta,
        "points": points,
        "global_pareto": front,
        "per_scheme_pareto": per_scheme_front,
        "calendar_cost_crossover": crossover,
        "lambda_views": views,
        "lambda_signature": signature,
        "smallest_lambda_low_pmax_le118": smallest_lam,
        "convergence": convergence,
        "plot": str(OUT_PNG.relative_to(ROOT)),
    }
    OUT_JSON.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"\npoints={len(points)} global_pareto={len(front)} "
          f"schemes_on_pareto={sorted({p['scheme'] for p in front})}")
    print(f"calendar crossover: {crossover}")
    for v in views:
        print(f"lambda={v['lambda']:>3}: axis owns {v['axis_owns']}; "
              f"low-band(<= {LOW_MK_BAND}) non-axis owners {v['low_band_nonaxis_owners']}")
    print(f"smallest lambda for non-axis to own <= {LOW_MK_BAND}: {smallest_lam}")
    print(f"convergence: {convergence}")
    print(f"Wrote {OUT_JSON}\nWrote {OUT_PNG}")


if __name__ == "__main__":
    main()
