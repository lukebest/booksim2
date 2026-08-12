#!/usr/bin/env python3
"""Multi-flit (5-round pipelined) allgather: makespan x implementation-area.

A 5-flit allgather is 5 rounds of the 1-flit schedule, but round k+1's flits
can overlap round k as soon as shared resources free.  Two makespans matter:

  * T1  -- pipeline FILL: first fully-gathered flit ready (tile compute start).
  * T5  -- measured 5-round makespan under the same W/E/B eject model as the
    rigid packer (NOT T1+(R-1)*link_reuse).

II is reported two ways (they differ!):

  * delta2     : earliest per-source second-flit inject gap after a feasible
    1-flit pack.  For axis+CCW at E=2 this is typically 1–31 (avg ~13–17),
    **much less than link_reuse=42**.
  * II_eff     : (T5-T1)/(R-1) from a free multi-round pack of all (source,
    round) jobs.  This is the throughput-relevant spacing of round completions.

The cyclic-replay lower bound II >= max(link_reuse, ceil((N-1)/E)) still
applies to *periodic* schedule shifting, but one-shot / per-source overlap
is not forced to that bound — link_reuse serializes one directed link inside
a single round, yet the next flit of a given source can often reuse other
links / slack slots much earlier.

Combined metric (compute-agnostic mean ready time):

    T_avg = (T1 + T5) / 2   =   T1 + (R-1)/2 * II_eff
"""

from __future__ import annotations

import argparse
import json
import math
import os
import time
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

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
# R=1 is the fill-only case (T_avg == T1 by definition); R=13 is the deep-pipeline
# end of the range. Both are needed because the two ends do not have to rank
# schemes the same way: T1 dominates T_avg at small R and II_eff dominates at
# large R, so a scheme can win one and lose the other.
ROUNDS_LIST = [1, 5, 13]
CAP = 1000
W_RANGE = [1, 2, 3, 4]
E_RANGE = [1, 2, 3, 4]
B_RANGE = [0, 1, 2, 4, 8, 11]


def coherent():
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


def _try_place(slots, o, link_used, up_arr, down_arr, b):
    for kind, key, rel in slots:
        c = o + rel
        if c >= CAP:
            return False
        if kind == "L" and c in link_used[key]:
            return False
        if kind == "U" and up_arr[key][c] + 1 > RAMP_BW:
            return False
    touched = {}
    for kind, key, rel in slots:
        if kind == "D":
            touched.setdefault(key, down_arr[key][:])
            touched[key][o + rel] += 1
    return all(BSW.fifo_ok(arr, b) for arr in touched.values())


def _commit(slots, o, link_used, up_arr, down_arr):
    for kind, key, rel in slots:
        c = o + rel
        if kind == "L":
            link_used[key].add(c)
        elif kind == "U":
            up_arr[key][c] += 1
        else:
            down_arr[key][c] += 1


def pack_one_with_offs(fps, b, order):
    link_used = defaultdict(set)
    up_arr = [[0] * CAP for _ in range(N)]
    down_arr = [[0] * CAP for _ in range(N)]
    offs = {}
    for s in order:
        slots = fps[s]
        chosen = None
        for o in range(CAP):
            if _try_place(slots, o, link_used, up_arr, down_arr, b):
                chosen = o
                break
        if chosen is None:
            return None
        offs[s] = chosen
        _commit(slots, chosen, link_used, up_arr, down_arr)
    mk = max(BSW.node_completion(down_arr[n]) for n in range(N))
    return {"makespan": mk, "offs": offs, "link_used": link_used,
            "up_arr": up_arr, "down_arr": down_arr}


def delta2_stats(fps, b, pack):
    """Earliest per-source second-flit inject gap (others fixed)."""
    offs = pack["offs"]
    deltas = []
    for s in range(N):
        slots = fps[s]
        found = None
        for d in range(1, 250):
            if _try_place(slots, offs[s] + d, pack["link_used"],
                          pack["up_arr"], pack["down_arr"], b):
                found = d
                break
        if found is None:
            return None
        deltas.append(found)
    return {"min": min(deltas), "avg": round(sum(deltas) / len(deltas), 2),
            "max": max(deltas)}


def pack_rounds(fps, rounds, b, base_order, mode: str):
    """Free multi-round pack of all (source, round) jobs."""
    if mode == "round_major":
        order = [(s, r) for r in range(rounds) for s in base_order]
    else:
        order = [(s, r) for s in base_order for r in range(rounds)]
    link_used = defaultdict(set)
    up_arr = [[0] * CAP for _ in range(N)]
    down_arr = [[0] * CAP for _ in range(N)]
    offs = {}
    for s, r in order:
        slots = fps[s]
        chosen = None
        for o in range(CAP):
            if _try_place(slots, o, link_used, up_arr, down_arr, b):
                chosen = o
                break
        if chosen is None:
            return None
        offs[(s, r)] = chosen
        _commit(slots, chosen, link_used, up_arr, down_arr)
    mk = max(BSW.node_completion(down_arr[n]) for n in range(N))
    deltas = [offs[(s, r + 1)] - offs[(s, r)]
              for s in range(N) for r in range(rounds - 1)]
    return {"makespan": mk,
            "min_delta": min(deltas), "avg_delta": round(sum(deltas) / len(deltas), 2),
            "max_delta": max(deltas)}


def best_rounds(fps, rounds, b, orders):
    best = None
    for base in orders:
        for mode in ("round_major", "source_major"):
            rec = pack_rounds(fps, rounds, b, base, mode)
            if rec and (best is None or rec["makespan"] < best["makespan"]):
                best = {**rec, "mode": mode}
    return best


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
    for k in keys:
        pp = [p for p in points if p["scheme"] == k and p["t_avg"] is not None]
        a0.scatter([p["area_total"] for p in pp], [p["t_avg"] for p in pp],
                   s=24, color=cmap(kidx[k]), alpha=0.4, edgecolor="none",
                   label=SCHEMES[k][0])
        a1.scatter([p["t1"] for p in pp], [p["t5"] for p in pp],
                   s=24, color=cmap(kidx[k]), alpha=0.4, edgecolor="none",
                   label=SCHEMES[k][0])
    a0.plot([p["area_total"] for p in front_avg],
            [p["t_avg"] for p in front_avg],
            "-o", color="#111827", lw=2.3, ms=5, zorder=5, label="global Pareto")
    for p in front_avg:
        a0.annotate(
            f"{SCHEMES[p['scheme']][0]}\nW{p['W']}/E{p['E']}/B{p['B']}",
            (p["area_total"], p["t_avg"]),
            textcoords="offset points", xytext=(6, 4), fontsize=6)
    a0.set_xlabel("chip implementation area (IQ-XY=1.0)")
    a0.set_ylabel("T_avg = (T1+T5)/2  (cycles)")
    a0.set_title("Pareto: area vs combined pipeline-ready latency (R=5, measured)")
    a0.grid(True, ls=":", alpha=0.5)
    a0.legend(loc="upper right", fontsize=7, ncol=2)

    a1.plot([p["t1"] for p in front_t5], [p["t5"] for p in front_t5],
            "-o", color="#111827", lw=2.0, ms=5, zorder=5, label="T1-T5 Pareto")
    a1.axvline(lb1, color="#2563eb", ls="--", lw=1)
    a1.set_xlabel("T1  1-flit makespan = pipeline fill (cycles)")
    a1.set_ylabel("T5  measured 5-flit makespan (cycles)")
    a1.set_title("fill (T1) vs throughput (T5), measured multi-round pack")
    a1.grid(True, ls=":", alpha=0.5)
    a1.legend(loc="upper left", fontsize=7, ncol=2)
    fig.tight_layout()
    fig.savefig(OUT_PNG, dpi=130)
    plt.close(fig)


# Adding R=13 made this sweep roughly 400 CPU-minutes: a 13-round pack costs
# ~40 s at the widest scheme and there are 7 schemes x 96 coherent design
# points. The work is embarrassingly parallel over (scheme, W, E, B) and the
# packers are pure functions of (fps, B, order) once the two module globals are
# set, so each worker sets them itself and results are bit-identical to the
# serial run.

_W: dict[str, Any] = {}


def _worker_init() -> None:
    S.cfg(MX, MY, H, V)
    S.init_ring()
    S.init_quadrants()
    orders = [BSW.corner_order()]
    for _n, gen in S.SRC_ORDERS.items():
        try:
            orders.append(list(gen()))
        except TypeError:
            continue
    _W["orders"] = orders[:4]
    _W["fps"] = {}


def _fps_for(key: str):
    if key not in _W["fps"]:
        _label, builder = SCHEMES[key]
        _W["fps"][key] = BSW.build(builder)[0]
    return _W["fps"][key]


def _one_point(task: tuple[str, int, int, int]) -> dict[str, Any]:
    key, W, E, B = task
    fps = _fps_for(key)
    orders = _W["orders"]
    BSW.XBAR_WRITE = W
    BSW.DRAIN = E
    pack1 = None
    for order in orders:
        rec = pack_one_with_offs(fps, B, order)
        if rec and (pack1 is None or rec["makespan"] < pack1["makespan"]):
            pack1 = rec
    t1 = pack1["makespan"] if pack1 else None
    d2 = delta2_stats(fps, B, pack1) if pack1 else None
    by_rounds: dict[str, Any] = {}
    for R in ROUNDS_LIST:
        if t1 is None:
            by_rounds[str(R)] = None
            continue
        if R == 1:
            by_rounds["1"] = {"T_R": t1, "II_eff": None, "T_avg": t1}
            continue
        rr = best_rounds(fps, R, B, orders)
        tr = rr["makespan"] if rr else None
        by_rounds[str(R)] = None if tr is None else {
            "T_R": tr,
            "II_eff": round((tr - t1) / (R - 1), 2),
            "T_avg": round((t1 + tr) / 2, 1)}
    r5 = by_rounds.get(str(ROUNDS))
    return {
        "by_rounds": by_rounds, "scheme": key, "W": W, "E": E, "B": B,
        "t1": t1,
        "t5": r5["T_R"] if r5 else None,
        "t_avg": r5["T_avg"] if r5 else None,
        "ii_eff": r5["II_eff"] if r5 else None,
        "delta2_min": d2["min"] if d2 else None,
        "delta2_avg": d2["avg"] if d2 else None,
        "delta2_max": d2["max"] if d2 else None,
    }


def main(jobs: int = 0) -> None:
    S.cfg(MX, MY, H, V)
    S.init_ring()
    S.init_quadrants()
    jobs = jobs or max(1, (os.cpu_count() or 2) - 1)

    scheme_meta = {}
    for key, (label, builder) in SCHEMES.items():
        pmax, issue = scheme_pmax(key, builder)
        fps, _stretch, dil = BSW.build(builder)
        lreuse = link_reuse(fps)
        scheme_meta[key] = {"label": label, "pmax": pmax, "issue": issue,
                            "dilation": dil, "link_reuse": lreuse,
                            "cyclic_ii_lb": lreuse}

    tasks = [(key, W, E, B) for key in SCHEMES for W, E, B in coherent()]
    t0 = time.time()
    print(f"{len(tasks)} design points x R in {ROUNDS_LIST} on {jobs} workers",
          flush=True)
    raw: list[dict[str, Any]] = []
    with ProcessPoolExecutor(max_workers=jobs,
                             initializer=_worker_init) as ex:
        for i, p in enumerate(ex.map(_one_point, tasks, chunksize=1), 1):
            raw.append(p)
            if i % 24 == 0 or i == len(tasks):
                print(f"  {i}/{len(tasks)} points  {time.time() - t0:.0f}s",
                      flush=True)

    points = []
    for p in raw:
        meta = scheme_meta[p["scheme"]]
        down_lb = math.ceil((N - 1) / p["E"])
        points.append({**p,
                       "label": meta["label"], "pmax": meta["pmax"],
                       "issue": meta["issue"],
                       "ii": p["ii_eff"],          # throughput II_eff
                       "cyclic_ii_lb": max(meta["link_reuse"], down_lb),
                       "down_ii_lb": down_lb,
                       "link_reuse": meta["link_reuse"],
                       "area_total": total_area(meta["pmax"], meta["issue"],
                                                p["W"], p["E"], p["B"])})
    for key, (label, _b) in SCHEMES.items():
        sp = [p for p in points if p["scheme"] == key and p["t5"]]
        if not sp:
            continue
        print(f"{label:16s} reuse={scheme_meta[key]['link_reuse']:3d} "
              f"T1={min(p['t1'] for p in sp)} "
              f"T5={min(p['t5'] for p in sp)} "
              f"T13={min((p['by_rounds']['13']['T_R'] for p in sp
                          if p['by_rounds'].get('13')), default=None)} "
              f"II_eff={min(p['ii_eff'] for p in sp)} "
              f"delta2_min={min(p['delta2_min'] for p in sp)} "
              f"delta2_avg@bestT5="
              f"{min(sp, key=lambda p: p['t5'])['delta2_avg']}")

    front_avg = pareto(points, "area_total", "t_avg")
    front_t5 = pareto(points, "t1", "t5")
    front_area_t5 = pareto(points, "area_total", "t5")
    lb1 = min(p["t1"] for p in points if p["t1"])

    make_plot(points, front_avg, front_t5, lb1)

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "model": {
            "mesh": [MX, MY], "H": H, "V": V, "rounds": ROUNDS,
            "rounds_list": ROUNDS_LIST,
            "design_vars": {"W": W_RANGE, "E": E_RANGE, "B": B_RANGE},
            "t1_lb": lb1,
            "combined_metric": "T_avg = (T1+T5)/2  [= T1 + (R-1)/2 * II_eff]",
            "ii_definition": "II_eff=(T5-T1)/(R-1) from free multi-round pack; "
                             "delta2=earliest per-source 2nd-flit gap. "
                             "cyclic_ii_lb=max(link_reuse,ceil((N-1)/E)) is "
                             "ONLY a periodic-replay bound — delta2 can be "
                             "much smaller (axis+CCW @ E=2: delta2 << 42).",
            "t5_method": "greedy free pack of (source,round) under link/up/"
                         "FIFO(W,E,B); best of round_major/source_major",
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
    # highlight axis E=2
    ax_e2 = [p for p in points if p["scheme"] == "axis_ccw" and p["E"] == 2
             and p["t5"]]
    if ax_e2:
        b = min(ax_e2, key=lambda p: p["t5"])
        print(f"axis+CCW best E=2: T1={b['t1']} T5={b['t5']} "
              f"II_eff={b['ii_eff']} delta2={b['delta2_min']}/{b['delta2_avg']}/"
              f"{b['delta2_max']} (cyclic_lb={b['cyclic_ii_lb']})")
    print(f"Wrote {OUT_JSON}\nWrote {OUT_PNG}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--jobs", type=int, default=0,
                    help="worker processes; default is cpu_count-1")
    main(jobs=ap.parse_args().jobs)
