#!/usr/bin/env python3
"""Can a shortest-path tree reach 8x6 LB=96 with crossbar out-peak <= 5?

Adds a per-(router,cycle) output constraint to the burst packer:
  outputs(r,t) = (#mesh children of r firing at t) + (eject at r at t)
and sweeps the output cap in {4,5,6} while keeping a large burst buffer
(so the down-ramp never binds).  Reports achievable makespan per out-cap.
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import sched_zerobuf_compare as S
from dse_tree_allgather_6x8 import (
    MX, MY, H, V, N, RAMP, RAMP_BW, formal_bounds, coord, nid,
    footprint, validate_tree, axis_ccw_tree,
)
from dse_burst_sweep_8x6 import corner_order, fifo_ok, node_completion, DRAIN
from explore_sp_trees_96 import quad_balanced, quad_nearest, axis_cw

OUT = Path(__file__).resolve().parents[1] / "results" / "burst_sweep_8x6.json"
CAP = 400


def child_send_slots(source: int, edges, dist):
    """Per-router mesh-output events: (router, send_cycle_rel) with a count."""
    fire = defaultdict(int)  # (router, rel_cycle) -> #children firing
    for p, c in edges:
        fire[(p, RAMP + dist[p])] += 1
    return fire


def pack_outcap(footprints, trees, b: int, out_cap: int, order):
    link_used: dict[int, set] = {}
    up_arr = [[0] * CAP for _ in range(N)]
    down_arr = [[0] * CAP for _ in range(N)]
    out_use = [[0] * CAP for _ in range(N)]  # per router total outputs/cy
    offs = {}
    for s in order:
        slots = footprints[s]
        dist = trees[s]["distance"]
        edges = trees[s]["edges"]
        fire = child_send_slots(s, edges, dist)
        chosen = None
        for o in range(CAP):
            ok = True
            # link + up
            for kind, key, rel in slots:
                c = o + rel
                if c >= CAP:
                    ok = False
                    break
                if kind == "L" and c in link_used.get(key, ()):
                    ok = False
                    break
                if kind == "U" and up_arr[key][c] + 1 > RAMP_BW:
                    ok = False
                    break
            if not ok:
                continue
            # output capacity: mesh children + eject per (router,cycle)
            add = defaultdict(int)
            for (r, rel), cnt in fire.items():
                add[(r, o + rel)] += cnt
            for kind, key, rel in slots:
                if kind == "D":  # eject at this router
                    add[(key, o + rel)] += 1
            for (r, c), cnt in add.items():
                if c >= CAP or out_use[r][c] + cnt > out_cap:
                    ok = False
                    break
            if not ok:
                continue
            # FIFO
            touched = {}
            for kind, key, rel in slots:
                if kind == "D":
                    touched.setdefault(key, down_arr[key][:])
                    touched[key][o + rel] += 1
            for key, arr in touched.items():
                if not fifo_ok(arr, b):
                    ok = False
                    break
            if ok:
                chosen = o
                break
        if chosen is None:
            return None
        offs[s] = chosen
        for kind, key, rel in slots:
            c = chosen + rel
            if kind == "L":
                link_used.setdefault(key, set()).add(c)
            elif kind == "U":
                up_arr[key][c] += 1
            else:
                down_arr[key][c] += 1
        for (r, rel), cnt in fire.items():
            out_use[r][chosen + rel] += cnt
        for kind, key, rel in slots:
            if kind == "D":
                out_use[key][chosen + rel] += 1
    makespan = max(node_completion(down_arr[n]) for n in range(N))
    return {"makespan": makespan, "max_offset": max(offs.values())}


def build_trees(builder):
    fps, trees = {}, {}
    for s in range(N):
        e = builder(s)
        chk = validate_tree(s, e)
        assert chk["ok"], (s, chk["errors"])
        chk["edges"] = e
        trees[s] = chk
        fps[s] = footprint(s, e, chk)
    return fps, trees


SCHEMES = {
    "axis_ccw": axis_ccw_tree,
    "axis_cw": axis_cw,
    "quad_balanced": quad_balanced,
    "quad_nearest": quad_nearest,
}


def main() -> None:
    S.cfg(MX, MY, H, V)
    S.init_ring()
    S.init_quadrants()
    lb = formal_bounds(1)["T_lb"]
    orders = [corner_order()]
    for _n, g in S.SRC_ORDERS.items():
        try:
            orders.append(list(g()))
        except TypeError:
            pass
    print(f"LB={lb}; big buffer B=11; sweep out_cap in 4,5,6")
    print(f"{'scheme':16s} {'out=4':>6s} {'out=5':>6s} {'out=6':>6s}")
    results = {}
    for name, builder in SCHEMES.items():
        fps, trees = build_trees(builder)
        row = {}
        for oc in (4, 5, 6):
            best = None
            for order in orders:
                rec = pack_outcap(fps, trees, 11, oc, order)
                if rec and (best is None or rec["makespan"] < best["makespan"]):
                    best = rec
            row[str(oc)] = best["makespan"] if best else None
        results[name] = row
        print(f"{name:16s} {str(row['4']):>6s} {str(row['5']):>6s} "
              f"{str(row['6']):>6s}")

    doc = json.loads(OUT.read_text()) if OUT.exists() else {}
    doc.setdefault("outcap_study", {})
    doc["outcap_study"] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "buffer": 11, "lower_bound": lb,
        "makespan_by_outcap": results,
        "note": "outputs(r,t)=mesh children firing + eject; cap limits crossbar "
                "concurrency. out=6 is 4 mesh + 2 eject.",
    }
    OUT.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    hit5 = [n for n, r in results.items() if r["5"] == lb]
    print(f"reach LB at out_cap<=5: {hit5}")
    print(f"Wrote {OUT}")


if __name__ == "__main__":
    main()
