#!/usr/bin/env python3
"""Scale x message-size x reduce-mode allreduce sweep.

Explores {INC vs node reduce} x {tree reduce+bcast vs RS+optimal AG}
over 4x4, 6x8, 8x8, 12x16, 16x16 meshes with m=1..5 flits.

Output: results/allreduce_scale_sweep.json
         results/allreduce_lb.json
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import allreduce_bound as ab
import sim_allreduce_scale as sa

ROOT = Path(__file__).resolve().parents[1]
LB_JSON = ROOT / "results" / "allreduce_lb.json"
OUT_JSON = ROOT / "results" / "allreduce_scale_sweep.json"

H, V = 4, 6
SIZES = [(4, 4), (6, 8), (8, 8), (12, 16), (16, 16)]
FLITS = [1, 2, 3, 4, 5]
RAMP_BW = 1
INC_LAT = 3
NODE_RED_LAT = 12


def sweep(inc_lat=INC_LAT, node_red_lat=NODE_RED_LAT, ramp_bw=RAMP_BW):
    ag_data = None
    if sa.AG_SWEEP_JSON.exists():
        ag_data = json.loads(sa.AG_SWEEP_JSON.read_text(encoding="utf-8"))

    lb = ab.sweep_lower_bounds(SIZES, FLITS, ramp_bw, inc_lat, node_red_lat)
    LB_JSON.write_text(json.dumps(lb, indent=2), encoding="utf-8")

    out = {
        "h": H, "v": V, "ramp": 1,
        "inc_lat": inc_lat, "node_red_lat": node_red_lat,
        "ramp_bw": ramp_bw,
        "sizes": [f"{mx}x{my}" for mx, my in SIZES],
        "flits": FLITS,
        "reduce_modes": ["inc", "node"],
        "data": {},
    }

    for mx, my in SIZES:
        key = f"{mx}x{my}"
        t0 = time.time()
        print(f"Sweep {key} ...", flush=True)
        block = {"mx": mx, "my": my, "n": mx * my, "flits": {}}
        for m in FLITS:
            quad = sa.compare_quadrants(mx, my, m, ramp_bw, inc_lat,
                                        node_red_lat, ag_data)
            cell = {}
            for mode in ("inc", "node"):
                res = quad[mode]
                lb_cell = lb["data"][key]["modes"][mode][str(m)]
                cell[mode] = {
                    "lower_bound": lb_cell["combined"],
                    "lower_bound_rsag": lb_cell["combined_rsag"],
                    "bounds": lb_cell,
                    "schemes": res["schemes"],
                    "best": res["best"],
                    "best_tree_bcast": res["best_tree_bcast"],
                    "best_rs_ag": res["best_rs_ag"],
                    "efficiency": res["efficiency"],
                }
                if res["best"]:
                    cell[mode]["ratio"] = res["best"]["makespan"] / lb_cell["combined"]
            cell["best_overall"] = quad["best_overall"]
            block["flits"][str(m)] = cell
        out["data"][key] = block
        print(f"  done in {time.time()-t0:.1f}s", flush=True)

    OUT_JSON.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"Wrote {OUT_JSON}")
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--inc-lat", type=int, default=INC_LAT)
    ap.add_argument("--node-red-lat", type=int, default=NODE_RED_LAT)
    ap.add_argument("--ramp-bw", type=int, default=RAMP_BW)
    args = ap.parse_args()
    sweep(args.inc_lat, args.node_red_lat, args.ramp_bw)


if __name__ == "__main__":
    main()
