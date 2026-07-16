#!/usr/bin/env python3
"""Multi-flit (5-round pipelined) allgather: makespan x implementation-area.

Motivation
----------
A 5-flit allgather is, in principle, 5 rounds of the 1-flit schedule, but each
round's flit can start overlapping the previous round as early as the busiest
shared resource allows (the *initiation interval*, II).  Two makespans matter,
and for different reasons:

  * T1  (1-flit makespan)  -- pipeline FILL latency: how soon the first fully
    gathered flit is ready.  In tile programming, compute on a tile can start
    the moment its 1-flit allgather completes, so T1 gates when the compute
    pipeline can begin.  For compute-heavy tiles this is the ONLY exposed comm
    term (steady rounds are hidden behind compute).
  * T5  (5-flit makespan)  -- pipeline THROUGHPUT: T5 = T1 + (R-1)*II.  For
    compute-light tiles this dominates, because II (not latency) sets the rate.

Because a fine-grained comm/compute pipeline exposes T1 once and II every
round, we combine them into a single **mean per-round ready time**:

    T_avg = (1/R) * sum_{k=0..R-1} (T1 + k*II) = T1 + (R-1)/2 * II

which for R=5 is T1 + 2*II.  It rewards BOTH low fill latency (T1) and low
steady throughput (II) -- exactly the two quantities fine-grained tiling cares
about -- and is compute-agnostic (no assumed compute cost).  We also emit T1
and T5 individually and an area vs T5 view.

Key structural fact (why the 1-flit winner need not win at 5 flits): the
sustained II is bounded below by

    II >= max( max-directed-link-reuse ,  ceil((N-1)/E) )

where E is the eject-FIFO -> gather-SRAM write bandwidth (each PE must write
N-1 gathered flits per round).  axis+CCW minimizes T1 but concentrates traffic
(high link reuse), so its II floor is high; flatter trees trade T1 for a lower
II floor and can win the throughput / T_avg race once E is generous.
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

import sched_zerobuf_compare as S
import dse_burst_sweep_8x6 as BSW
from dse_tree_allgather_6x8 import MX, MY, H, V, N, RAMP, RAMP_BW, coord
from dse_multi_area_makespan import SCHEMES, total_area, scheme_pmax

ROOT = Path(__file__).resolve().parents[1]
OUT_JSON = ROOT / "results" / "multiflit_area_makespan.json"
OUT_PNG = ROOT / "results" / "multiflit_area_makespan.png"

ROUNDS = 5
W_RANGE = [1, 2, 3, 4]
E_RANGE = [1, 2, 3, 4]          # E is the throughput knob for multi-flit
B_RANGE = [0, 1, 2, 4, 8, 11]


def coherent():
    """Coherent (W,E,B): W>=E (cannot drain faster than the crossbar feeds),
    and B=0 only when W==E (no burst to absorb)."""
    for E in E_RANGE:
        for W in W_RANGE:
            if W < E:
                continue
            for B in B_RANGE:
                if B == 0 and W != E:
                    continue
                yield W, E, B


def link_reuse(fps) -> int:
    cnt = defaultdict(int)
    for s in fps:
        for kind, key, _rel in fps[s]:
            if kind == "L":
                cnt[key] += 1
    return max(cnt.values()) if cnt else 0


def min_ii(lreuse: int, E: int):
    """Resource lower bound on the sustainable initiation interval.

    II >= max(link_reuse, ceil((N-1)/E)):
      * a directed link traversed r times per round carries <=1 flit/cy, so
        successive rounds must be >= r cycles apart on that link;
      * every PE must write N-1 gathered flits per round into its gather SRAM
        at E flits/cy, so rounds cannot start closer than ceil((N-1)/E).
    This bound is independent of W and B; the burst buffer B and crossbar
    write W are what let a schedule APPROACH this rate (and set T1), but they
    cannot beat it.  T5 = T1 + (R-1)*II is therefore an ideal-overlap bound.
    """
    down_lb = math.ceil((N - 1) / E)
    return max(lreuse, down_lb), down_lb


def pareto(points, xkey, ykey):
    pts = [p for p in points if p[ykey] is not None]
    pts.sort(key=lambda p: (p[xkey], p[ykey]))
    front, best = [], math.inf
    for p in pts:
        if p[ykey] < best:
            front.append(p)
            best = p[ykey]
    return front


def make_plot(points, front_avg, front_t5, lb1):
    fig, (a0, a1) = plt.subplots(1, 2, figsize=(13.8, 6.0))
    cmap = plt.get_cmap("tab10")
    keys = list(SCHEMES)
    kidx = {k: i for i, k in enumerate(keys)}

    # left: area vs combined T_avg
    for k in keys:
        pp = [p for p in points if p["scheme"] == k and p["t_avg"] is not None]
        a0.scatter([p["area_total"] for p in pp], [p["t_avg"] for p in pp],
                   s=24, color=cmap(kidx[k]), alpha=0.4, edgecolor="none",
                   label=SCHEMES[k][0])
    a0.plot([p["area_total"] for p in front_avg],
            [p["t_avg"] for p in front_avg], "-o", color="#111827",
            lw=2.3, ms=5, zorder=5, label="global Pareto")
    for p in front_avg:
        a0.annotate(f"{SCHEMES[p['scheme']][0]}\nW{p['W']}/E{p['E']}/B{p['B']}",
                    (p["area_total"], p["t_avg"]), textcoords="offset points",
                    xytext=(6, 4), fontsize=6, color="#111827")
    a0.set_xlabel("chip implementation area (IQ-XY=1.0)")
    a0.set_ylabel("combined T_avg = T1 + 2*II  (cycles)")
    a0.set_title("Pareto: area vs combined pipeline-ready latency (R=5)")
    a0.grid(True, ls=":", alpha=0.5)
    a0.legend(loc="upper right", fontsize=7, ncol=2)

    # right: T1 (fill) vs T5 (throughput) tradeoff
    for k in keys:
        pp = [p for p in points if p["scheme"] == k and p["t5"] is not None]
        a1.scatter([p["t1"] for p in pp], [p["t5"] for p in pp],
                   s=24, color=cmap(kidx[k]), alpha=0.4, edgecolor="none",
                   label=SCHEMES[k][0])
    a1.plot([p["t1"] for p in front_t5], [p["t5"] for p in front_t5],
            "-o", color="#111827", lw=2.0, ms=5, zorder=5,
            label="T1-T5 Pareto")
    a1.axvline(lb1, color="#2563eb", ls="--", lw=1)
    a1.text(lb1 + 0.5, a1.get_ylim()[1], f"T1 LB={lb1}", color="#2563eb",
            fontsize=7, va="top")
    a1.set_xlabel("T1  1-flit makespan = pipeline fill (cycles)")
    a1.set_ylabel("T5  5-flit makespan = T1 + 4*II (cycles)")
    a1.set_title("fill (T1) vs throughput (T5): the winner flips")
    a1.grid(True, ls=":", alpha=0.5)
    a1.legend(loc="upper left", fontsize=7, ncol=2)

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

    points = []
    scheme_meta = {}
    for key, (label, builder) in SCHEMES.items():
        pmax, issue = scheme_pmax(key, builder)
        fps, _stretch, dil = BSW.build(builder)
        lreuse = link_reuse(fps)
        scheme_meta[key] = {"label": label, "pmax": pmax, "issue": issue,
                            "dilation": dil, "link_reuse": lreuse}
        for W, E, B in coherent():
            BSW.XBAR_WRITE = W
            BSW.DRAIN = E
            rec = BSW.pack_with_buffer(fps, B, orders)
            t1 = rec["makespan"] if rec else None
            ii, down_lb = min_ii(lreuse, E)
            t5 = (t1 + (ROUNDS - 1) * ii) if (t1 and ii) else None
            t_avg = (t1 + (ROUNDS - 1) / 2 * ii) if (t1 and ii) else None
            points.append({
                "scheme": key, "label": label, "pmax": pmax, "issue": issue,
                "W": W, "E": E, "B": B,
                "t1": t1, "ii": ii, "down_ii_lb": down_lb,
                "t5": t5, "t_avg": round(t_avg, 1) if t_avg else None,
                "area_total": total_area(pmax, issue, W, E, B),
            })
        floor_ii = min((p["ii"] for p in points
                        if p["scheme"] == key and p["ii"]), default=None)
        floor_t5 = min((p["t5"] for p in points
                        if p["scheme"] == key and p["t5"]), default=None)
        print(f"{label:16s} reuse={lreuse:3d} II_floor={floor_ii} "
              f"T5_floor={floor_t5}")

    front_avg = pareto(points, "area_total", "t_avg")
    front_t5 = pareto(points, "t1", "t5")
    front_area_t5 = pareto(points, "area_total", "t5")
    lb1 = min(p["t1"] for p in points if p["t1"])

    make_plot(points, front_avg, front_t5, lb1)

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "model": {
            "mesh": [MX, MY], "H": H, "V": V, "rounds": ROUNDS,
            "design_vars": {"W": W_RANGE, "E": E_RANGE, "B": B_RANGE},
            "t1_lb": lb1,
            "combined_metric": "T_avg = T1 + (R-1)/2 * II  (R=5 -> T1 + 2*II)",
            "ii_lower_bound": "max(link_reuse, ceil((N-1)/E))",
        },
        "scheme_meta": scheme_meta,
        "points": points,
        "pareto_area_tavg": front_avg,
        "pareto_t1_t5": front_t5,
        "pareto_area_t5": front_area_t5,
        "plot": str(OUT_PNG.relative_to(ROOT)),
    }
    OUT_JSON.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"\npoints={len(points)}")
    print(f"area/T_avg Pareto schemes: {sorted({p['scheme'] for p in front_avg})}")
    print(f"T1/T5 Pareto schemes: {sorted({p['scheme'] for p in front_t5})}")
    print(f"Wrote {OUT_JSON}\nWrote {OUT_PNG}")


if __name__ == "__main__":
    main()
