#!/usr/bin/env python3
"""Measured multi-round (R=5) makespan / delta2 at E≡2 for all schemes.

Replaces the overly pessimistic II=link_reuse model for the E=2 report section.
Also patches multiflit_area_makespan.json E=2 points and regenerates E2 plots.
"""

from __future__ import annotations

import json
import math
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import sched_zerobuf_compare as S
import dse_burst_sweep_8x6 as BSW
from dse_tree_allgather_6x8 import MX, MY, H, V, N, RAMP_BW
from dse_multi_area_makespan import SCHEMES, total_area, scheme_pmax
from dse_axis_area_makespan import pareto
from dse_multiflit_area_makespan import (
    pack_one_with_offs, delta2_stats, pack_rounds, link_reuse, ROUNDS, CAP,
)

ROOT = Path(__file__).resolve().parents[1]
MF_JSON = ROOT / "results" / "multiflit_area_makespan.json"
E2_JSON = ROOT / "results" / "e2_pareto_views.json"

# E=2 coherent configs (W>=2)
CONFIGS = [
    (2, 2, 0), (2, 2, 1), (2, 2, 2), (2, 2, 4), (2, 2, 8), (2, 2, 11),
    (3, 2, 1), (3, 2, 2), (3, 2, 4), (3, 2, 8), (3, 2, 11),
    (4, 2, 2), (4, 2, 4), (4, 2, 8), (4, 2, 11),
]


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
    orders = orders[:2]   # corner + one SRC order — enough for E=2 check

    points = []
    floors = {}
    for key, (label, builder) in SCHEMES.items():
        pmax, issue = scheme_pmax(key, builder)
        fps, _, dil = BSW.build(builder)
        lreuse = link_reuse(fps)
        best = None
        for W, E, B in CONFIGS:
            BSW.XBAR_WRITE = W
            BSW.DRAIN = E
            pack1 = None
            for order in orders:
                rec = pack_one_with_offs(fps, B, order)
                if rec and (pack1 is None or rec["makespan"] < pack1["makespan"]):
                    pack1 = rec
            if not pack1:
                continue
            d2 = delta2_stats(fps, B, pack1)
            # multi-round: corner order only, both modes
            r5 = None
            for mode in ("round_major", "source_major"):
                rec = pack_rounds(fps, ROUNDS, B, orders[0], mode)
                if rec and (r5 is None or rec["makespan"] < r5["makespan"]):
                    r5 = {**rec, "mode": mode}
            t1, t5 = pack1["makespan"], r5["makespan"] if r5 else None
            ii_eff = round((t5 - t1) / (ROUNDS - 1), 2) if t5 else None
            t_avg = round((t1 + t5) / 2, 1) if t5 else None
            pt = {
                "scheme": key, "label": label, "pmax": pmax, "issue": issue,
                "W": W, "E": E, "B": B,
                "t1": t1, "t5": t5, "t_avg": t_avg,
                "ii": ii_eff, "ii_eff": ii_eff,
                "delta2_min": d2["min"] if d2 else None,
                "delta2_avg": d2["avg"] if d2 else None,
                "delta2_max": d2["max"] if d2 else None,
                "cyclic_ii_lb": max(lreuse, math.ceil((N - 1) / E)),
                "link_reuse": lreuse,
                "area_total": total_area(pmax, issue, W, E, B),
            }
            points.append(pt)
            if t5 and (best is None or t5 < best["t5"]
                       or (t5 == best["t5"] and t1 < best["t1"])):
                best = pt
        floors[key] = {
            "label": label, "link_reuse": lreuse,
            "t1": best["t1"], "ii": best["ii_eff"], "t5": best["t5"],
            "t_avg": best["t_avg"],
            "delta2_min": best["delta2_min"], "delta2_avg": best["delta2_avg"],
            "delta2_max": best["delta2_max"],
            "cyclic_ii_lb": best["cyclic_ii_lb"],
            "best_cfg": f"W{best['W']}/E{best['E']}/B{best['B']}",
        }
        print(f"{label:16s} reuse={lreuse:3d} cyclic_lb={floors[key]['cyclic_ii_lb']} "
              f"T1={best['t1']} T5={best['t5']} II_eff={best['ii_eff']} "
              f"delta2={best['delta2_min']}/{best['delta2_avg']}/{best['delta2_max']} "
              f"@ {floors[key]['best_cfg']}")

    # patch multiflit JSON: replace E=2 points with measured ones
    mf = json.loads(MF_JSON.read_text(encoding="utf-8"))
    kept = [p for p in mf["points"] if p["E"] != 2]
    # mark measured
    for p in points:
        p["ii_model"] = "measured_multiround"
    mf["points"] = kept + points
    mf["model"]["ii_definition"] = (
        "For E!=2 points may still use legacy analytic II; E=2 points use "
        "measured II_eff=(T5-T1)/(R-1) and delta2 (per-source 2nd-flit gap). "
        "cyclic_ii_lb=max(link_reuse,ceil((N-1)/E)) is periodic-replay only; "
        "axis+CCW delta2 << 42 at E=2."
    )
    mf["model"]["e2_measured_at"] = datetime.now(timezone.utc).isoformat()
    # recompute global fronts over all points that have t_avg
    valid = [p for p in mf["points"] if p.get("t_avg") is not None]
    mf["pareto_area_tavg"] = pareto(valid, "area_total", "t_avg")
    mf["pareto_t1_t5"] = pareto(valid, "t1", "t5")
    mf["pareto_area_t5"] = pareto(valid, "area_total", "t5")
    MF_JSON.write_text(json.dumps(mf, indent=2), encoding="utf-8")

    # update E2 views JSON multiflit section + regenerate note
    e2 = json.loads(E2_JSON.read_text(encoding="utf-8"))
    front_avg = pareto(points, "area_total", "t_avg")
    front_avg.sort(key=lambda p: p["area_total"])
    front_t15 = pareto(points, "t1", "t5")
    front_t15.sort(key=lambda p: p["t1"])
    e2["multiflit"] = {
        "n_points": len(points),
        "pareto_area_tavg": front_avg,
        "pareto_t1_t5": front_t15,
        "scheme_floors": floors,
        "plot": e2["multiflit"]["plot"],
        "ii_model": "measured: II_eff=(T5-T1)/4; delta2=per-source 2nd-flit; "
                    "cyclic_ii_lb=link_reuse bound (NOT binding for one-shot overlap)",
    }
    e2["generated_at"] = datetime.now(timezone.utc).isoformat()
    e2["note"] = (
        e2.get("note", "") + " Multiflit E=2 uses measured multi-round pack; "
        "axis+CCW delta2 << cyclic link_reuse=42."
    )
    E2_JSON.write_text(json.dumps(e2, indent=2), encoding="utf-8")

    # regenerate E2 multiflit plot
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, (a0, a1) = plt.subplots(1, 2, figsize=(13.8, 6.0))
    cmap = plt.get_cmap("tab10")
    keys = list(SCHEMES)
    for i, k in enumerate(keys):
        pp = [p for p in points if p["scheme"] == k]
        a0.scatter([p["area_total"] for p in pp], [p["t_avg"] for p in pp],
                   s=28, color=cmap(i), alpha=0.55, edgecolor="none",
                   label=SCHEMES[k][0])
        a1.scatter([p["t1"] for p in pp], [p["t5"] for p in pp],
                   s=28, color=cmap(i), alpha=0.55, edgecolor="none",
                   label=SCHEMES[k][0])
    a0.plot([p["area_total"] for p in front_avg],
            [p["t_avg"] for p in front_avg],
            "-o", color="#111827", lw=2.3, ms=5, zorder=5)
    for p in front_avg:
        a0.annotate(f"{p['label']}\nW{p['W']}/B{p['B']}",
                    (p["area_total"], p["t_avg"]),
                    textcoords="offset points", xytext=(6, 4), fontsize=6)
    a0.set_xlabel("area (IQ-XY=1.0)")
    a0.set_ylabel("T_avg=(T1+T5)/2")
    a0.set_title("E≡2 measured: area vs T_avg")
    a0.grid(True, ls=":", alpha=0.5)
    a0.legend(fontsize=7, ncol=2)
    a1.plot([p["t1"] for p in front_t15], [p["t5"] for p in front_t15],
            "-o", color="#111827", lw=2.0, ms=5, zorder=5)
    a1.set_xlabel("T1 (fill)")
    a1.set_ylabel("T5 measured (5-flit)")
    a1.set_title("E≡2 measured: fill vs throughput")
    a1.grid(True, ls=":", alpha=0.5)
    a1.legend(fontsize=7, ncol=2)
    fig.tight_layout()
    fig.savefig(ROOT / "results" / "e2_multiflit_area_makespan.png", dpi=130)
    plt.close(fig)
    print(f"Wrote {MF_JSON}\nWrote {E2_JSON}\nWrote e2_multiflit plot")


if __name__ == "__main__":
    main()
