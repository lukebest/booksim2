#!/usr/bin/env python3
"""DSE: collectives on the AIC reticle fabric (the reference-document setup).

Reads nothing but `rg_aic_reticle` / `rg_aic_collectives`. Writes
`results/aic_reticle_collectives_8x6.json` for the report and the verifier.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from rg_aic_collectives import (ALGOS, CORE_LANES, CYC_TURN, N_LANES, PATTERNS,
                                RAMP, T1_PATTERNS, BaseResult, Calendar,
                                build_calendar, cut_lanes, lower_bounds,
                                run_base)
from rg_aic_reticle import (CYC, N_COLS, N_CORES, N_HRAIL, N_ROWS, N_VRAIL,
                            PITCH_X, PITCH_Y, UM_PER_CYCLE, W, H, Fabric,
                            all_routes, ring_order, route)

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results" / "aic_reticle_collectives_8x6.json"
M_LIST = (1, 13)
ROUNDS = (1, 4)
FIFO_SWEEP = (1, 2, 4, 8)
ROOT_CORE = 0


def cal_row(c: Calendar) -> dict:
    return {
        "pattern": c.pattern, "algo": c.algo, "tier": c.tier, "m": c.m,
        "makespan": c.makespan, "n_boardings": c.n_boardings,
        "depth": c.depth, "lane_cycles": c.lane_cycles,
        "useful_lane_cycles": c.useful_lane_cycles,
        "lane_util": round(c.lane_util, 6),
        "useful_util": round(c.useful_util, 6),
        "hop_tax": round(c.hop_tax, 4),
        "turn_peak": c.turn_peak, "turn_mean": c.turn_mean,
        "n_bridges": len(c.per_bridge),
        "hot_bridge": max(c.per_bridge, key=lambda k: c.per_bridge[k]["peak"])
        if c.per_bridge else None,
    }


def base_row(b: BaseResult) -> dict:
    return {
        "pattern": b.pattern, "m": b.m, "makespan": b.makespan,
        "n_messages": b.n_messages, "deflections": b.deflections,
        "lane_cycles": b.lane_cycles,
        "useful_lane_cycles": b.useful_lane_cycles,
        "lane_util": round(b.lane_util, 6),
        "turn_peak": b.turn_peak, "turn_peak_node": b.turn_peak_node,
        "turn_mean": b.turn_mean, "turn_full_cycles": b.turn_full_cycles,
        "n_bridges": len(b.per_bridge),
        "per_bridge": b.per_bridge,
    }


def t_avg(t1: int, t_r: int, r: int) -> float:
    """T_avg = T1 + (R-1)/2 * II_eff, with II_eff = (T_R - T1)/(R-1)."""
    if r <= 1:
        return float(t1)
    ii = (t_r - t1) / (r - 1)
    return t1 + (r - 1) / 2 * ii


def main() -> None:
    t0 = time.time()
    fab = Fabric().build()
    print("building routes...", flush=True)
    rs = all_routes(fab)
    tots = [r.total for r in rs.values()]
    same = [r.total for (s, d), r in rs.items() if s // N_COLS == d // N_COLS]
    cross = [r.total for (s, d), r in rs.items() if s // N_COLS != d // N_COLS]
    au = {
        "diameter_cy": max(tots), "min_cy": min(tots),
        "avg_cy": round(sum(tots) / len(tots), 2),
        "same_row": {"n": len(same), "min": min(same), "max": max(same),
                     "avg": round(sum(same) / len(same), 2), "turns": [0]},
        "cross_row": {"n": len(cross), "min": min(cross), "max": max(cross),
                      "avg": round(sum(cross) / len(cross), 2), "turns": [2]},
    }
    cuts = cut_lanes(fab)
    print(f"  {len(rs)} pairs  diameter={au['diameter_cy']}  "
          f"avg={au['avg_cy']}  [{time.time()-t0:.1f}s]", flush=True)

    bounds = []
    for pat in PATTERNS:
        for m in M_LIST:
            tiers = ("T0", "T1") if pat in T1_PATTERNS else ("T0",)
            for tier in tiers:
                b = lower_bounds(fab, pat, m, tier=tier, rs=rs)
                bounds.append(b)
                print(f"  bound {pat:10s} m={m:2d} {tier}  "
                      f"floor={b['floor']:5d} ({b['binding']})", flush=True)

    calendars = []
    for pat in PATTERNS:
        for algo in ALGOS[pat]:
            tiers = ("T0", "T1") if pat in T1_PATTERNS else ("T0",)
            for tier in tiers:
                for m in M_LIST:
                    t1 = time.time()
                    c = build_calendar(fab, rs, pat, algo, m, tier=tier,
                                       root=ROOT_CORE)
                    row = cal_row(c)
                    calendars.append(row)
                    print(f"  cal {pat:10s} {algo:12s} {tier} m={m:2d}  "
                          f"{c.makespan:5d} cy  "
                          f"util={c.lane_util*100:5.2f}%  "
                          f"[{time.time()-t1:.1f}s]", flush=True)

    # II: four serialized rounds of the best T0 calendar per pattern at m=1/13
    throughput = []
    for pat in PATTERNS:
        for m in M_LIST:
            cand = [r for r in calendars
                    if r["pattern"] == pat and r["m"] == m and r["tier"] == "T0"]
            if not cand:
                continue
            best = min(cand, key=lambda r: r["makespan"])
            t1 = time.time()
            c4 = build_calendar(fab, rs, pat, best["algo"], m, tier="T0",
                                root=ROOT_CORE, rounds=4)
            per = c4.makespan / 4
            ii_eff = (c4.makespan - best["makespan"]) / 3
            b = next(x for x in bounds
                     if x["pattern"] == pat and x["m"] == m and x["tier"] == "T0")
            cap = max(b["cut"], b["inject"], b["eject"], b["turn"])
            throughput.append({
                "pattern": pat, "m": m, "algo": best["algo"], "tier": "T0",
                "T1": best["makespan"], "T4": c4.makespan,
                "per_round": round(per, 2),
                "II_eff": round(ii_eff, 2),
                "II_lb": cap,
                "T_avg_R4": round(t_avg(best["makespan"], c4.makespan, 4), 2),
                "T_avg_R13": round(t_avg(best["makespan"],
                                         best["makespan"] + 12 * ii_eff, 13), 2),
            })
            print(f"  II  {pat:10s} m={m:2d}  T1={best['makespan']}  "
                  f"per_round={per:.1f}  II_eff={ii_eff:.1f}  "
                  f"II_lb={cap}  [{time.time()-t1:.1f}s]", flush=True)

    baselines = []
    for pat in PATTERNS:
        for m in M_LIST:
            t1 = time.time()
            b = run_base(fab, rs, pat, m, root=ROOT_CORE, fifo_depth=4)
            baselines.append(base_row(b))
            print(f"  base {pat:10s} m={m:2d}  {b.makespan:5d} cy  "
                  f"defl={b.deflections}  peakFIFO={b.turn_peak}  "
                  f"[{time.time()-t1:.1f}s]", flush=True)

    fifo_sweep = []
    for depth in FIFO_SWEEP:
        t1 = time.time()
        b = run_base(fab, rs, "alltoall", 1, root=ROOT_CORE, fifo_depth=depth)
        fifo_sweep.append({**base_row(b), "fifo_depth": depth})
        print(f"  fifo {depth:2d}  alltoall m=1  {b.makespan:5d} cy  "
              f"defl={b.deflections}  [{time.time()-t1:.1f}s]", flush=True)

    # Corner-to-corner ledger, so the report can quote the reference route.
    r047 = route(fab, 0, 47)
    example = {
        "src": 0, "dst": 47, "total": r047.total, "um": r047.um,
        "turns": r047.turns, "steps": r047.steps, "folds": r047.folds,
        "counts": r047.counts, "kind_cycles": r047.kind_cycles(),
    }

    # Best calendar vs base vs floor, one row per (pattern, m, tier)
    compare = []
    for pat in PATTERNS:
        for m in M_LIST:
            tiers = ("T0", "T1") if pat in T1_PATTERNS else ("T0",)
            for tier in tiers:
                cands = [r for r in calendars
                         if r["pattern"] == pat and r["m"] == m
                         and r["tier"] == tier]
                if not cands:
                    continue
                best = min(cands, key=lambda r: r["makespan"])
                fl = next(x for x in bounds if x["pattern"] == pat
                          and x["m"] == m and x["tier"] == tier)
                base = next((x for x in baselines
                             if x["pattern"] == pat and x["m"] == m), None)
                compare.append({
                    "pattern": pat, "m": m, "tier": tier,
                    "floor": fl["floor"], "binding": fl["binding"],
                    "cal_algo": best["algo"],
                    "cal_makespan": best["makespan"],
                    "cal_util": best["lane_util"],
                    "cal_tax": best["hop_tax"],
                    "cal_turn_peak": best["turn_peak"],
                    "base_makespan": None if tier != "T0" or base is None
                    else base["makespan"],
                    "base_defl": None if base is None else base["deflections"],
                    "base_turn_peak": None if base is None
                    else base["turn_peak"],
                    "gap_cal_floor": round(best["makespan"] / fl["floor"], 3),
                    "gap_base_cal": (None if tier != "T0" or base is None
                                     else round(base["makespan"]
                                                / best["makespan"], 3)),
                })

    payload = {
        "source": "aic-reticle-shortest-path (transcribed)",
        "elapsed_s": round(time.time() - t0, 1),
        "wire": {
            "reticle_um": [W, H], "um_per_cycle": UM_PER_CYCLE,
            "pitch_um": [PITCH_X, PITCH_Y],
            "n_rows": N_ROWS, "n_cols": N_COLS, "n_cores": N_CORES,
            "n_hrails": N_HRAIL, "n_vrails": N_VRAIL,
            "n_rbrg": N_HRAIL * N_VRAIL, "n_lanes": N_LANES,
            "segment_cycles": dict(CYC), "t_turn": CYC_TURN,
            "ramp": RAMP, "core_lanes": CORE_LANES,
            "ring_tour": ring_order(),
            "row_tour_cy": 216, "col_adjacent_cy": 43, "col_wrap_cy": 111,
            "diameter_cy": au["diameter_cy"], "avg_cy": au["avg_cy"],
            "same_row": au["same_row"], "cross_row": au["cross_row"],
            "cuts": cuts,
        },
        "example_0_to_47": example,
        "bounds": bounds,
        "calendars": calendars,
        "baselines": baselines,
        "throughput": throughput,
        "fifo_sweep": fifo_sweep,
        "compare": compare,
        "audit": au,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, indent=1, ensure_ascii=False),
                   encoding="utf-8")
    print(f"wrote {OUT}  [{time.time()-t0:.1f}s]", flush=True)


if __name__ == "__main__":
    main()
