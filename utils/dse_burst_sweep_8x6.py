#!/usr/bin/env python3
"""8x6 allgather m=1: down-ramp burst-buffer sweep under a wide eject write.

Microarchitecture model (per the user's spec):
  * crossbar -> eject FIFO write width  = XBAR_WRITE (default 4 flits/cy),
  * eject FIFO depth                    = B (swept: 0,1,2,4,8,11),
  * FIFO -> down-ramp / PE drain rate    = rb = 2 flits/cy,
  * directed mesh link                   <= 1 flit/cy,
  * up-ramp (inject)                     <= rb.

A finite FIFO of depth B draining `rb`/cy, fed <= XBAR_WRITE/cy, is feasible at
a node iff for every cycle the post-drain residual stays <= B, i.e.
    occ_next = max(0, occ_prev + arrivals - rb)  and  occ_next <= B,
    arrivals <= XBAR_WRITE.
B=0  <=>  strict down_cap=rb (no burst absorption).
B=inf <=> down_cap=XBAR_WRITE fully absorbed.

For each scheme and B we rigidly pack per-source inject offsets (corner-first,
smallest feasible offset) honouring link/up/FIFO constraints, then report the
PE-completion makespan = last drain cycle + RAMP (max over nodes).

The formal lower bound for this mesh/m is 96.  We flag schemes that reach it.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import sched_zerobuf_compare as S
from dse_tree_allgather_6x8 import (
    MX, MY, H, V, N, RAMP, RAMP_BW, formal_bounds, coord, nid,
    footprint, validate_tree, axis_ccw_tree, edge_comb_tree, col_comb_tree,
    dim_tree, hamilton_tree,
)

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results" / "burst_sweep_8x6.json"

XBAR_WRITE = 4
DRAIN = RAMP_BW
BUFFERS = [0, 1, 2, 4, 8, 11]
CAP = 400  # cycle horizon


def corner_order() -> list[int]:
    return sorted(range(N), key=lambda s: min(
        coord(s)[0] * H + coord(s)[1] * V,
        (MX - 1 - coord(s)[0]) * H + coord(s)[1] * V,
        coord(s)[0] * H + (MY - 1 - coord(s)[1]) * V,
        (MX - 1 - coord(s)[0]) * H + (MY - 1 - coord(s)[1]) * V),
        reverse=True)


def fifo_ok(arr: list[int], b: int) -> bool:
    """Feasibility of an arrival timeline through depth-b FIFO, drain=DRAIN."""
    occ = 0
    for a in arr:
        if a > XBAR_WRITE:
            return False
        occ = occ + a - DRAIN
        if occ < 0:
            occ = 0
        if occ > b:
            return False
    return True


def node_completion(arr: list[int]) -> int:
    """Last drain cycle (absolute) for an arrival timeline; +RAMP = complete."""
    occ = 0
    last = 0
    for t, a in enumerate(arr):
        occ += a
        if occ > 0:
            drained = min(DRAIN, occ)
            occ -= drained
            if drained:
                last = t
    # drain remaining
    t = len(arr)
    while occ > 0:
        occ -= min(DRAIN, occ)
        last = t
        t += 1
    return last + RAMP


def _pack_one(footprints: dict, b: int, order: list[int]):
    """Greedy smallest-feasible-offset pack under link/up/FIFO(depth b)."""
    link_used: dict[int, set] = {}
    up_arr = [[0] * CAP for _ in range(N)]
    down_arr = [[0] * CAP for _ in range(N)]
    offs = {}
    for s in order:
        slots = footprints[s]
        chosen = None
        for o in range(CAP):
            ok = True
            for kind, key, rel in slots:
                c = o + rel
                if c >= CAP:
                    ok = False
                    break
                if kind == "L":
                    if c in link_used.get(key, ()):
                        ok = False
                        break
                elif kind == "U":
                    if up_arr[key][c] + 1 > RAMP_BW:
                        ok = False
                        break
            if not ok:
                continue
            # FIFO check per affected down node
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
    makespan = max(node_completion(down_arr[n]) for n in range(N))
    fifo_peak = 0
    for n in range(N):
        occ = 0
        for a in down_arr[n]:
            occ = max(0, occ + a - DRAIN)
            fifo_peak = max(fifo_peak, occ)
    return {"makespan": makespan, "max_offset": max(offs.values()),
            "fifo_peak_occ": fifo_peak}


def pack_with_buffer(footprints: dict, b: int, orders: list[list[int]]):
    """Best (min-makespan) pack over several source orders."""
    best = None
    for order in orders:
        rec = _pack_one(footprints, b, order)
        if rec and (best is None or rec["makespan"] < best["makespan"]):
            best = rec
    return best


SCHEMES = {
    "axis_ccw": axis_ccw_tree,
    "dim_xy": lambda s: dim_tree(s, "xy"),
    "dim_yx": lambda s: dim_tree(s, "yx"),
    "col_comb3": col_comb_tree,
    "nec3": edge_comb_tree,
    "nec2": lambda s: edge_comb_tree(s, fanout_two=True),
    "hamilton_bi_tree": lambda s: hamilton_tree(s, True),
}


def build(builder) -> dict:
    fps, stretch = {}, 0
    for s in range(N):
        e = builder(s)
        chk = validate_tree(s, e)
        assert chk["ok"], (s, chk["errors"])
        fps[s] = footprint(s, e, chk)
        sx, sy = coord(s)
        for d, dist in chk["distance"].items():
            dx, dy = coord(d)
            man = abs(sx - dx) * H + abs(sy - dy) * V
            stretch = max(stretch, dist - man)
    dilation = max(2 * RAMP + max(validate_tree(s, builder(s))["distance"].values())
                   for s in range(N))
    return fps, stretch, dilation


def main() -> None:
    S.cfg(MX, MY, H, V)
    S.init_ring()
    S.init_quadrants()
    lb = formal_bounds(1)["T_lb"]
    orders = [corner_order()]
    for _name, gen in S.SRC_ORDERS.items():
        try:
            orders.append(list(gen()))
        except TypeError:
            continue
    results = {}
    print(f"mesh={MX}x{MY} LB={lb} xbar_write={XBAR_WRITE} drain={DRAIN}")
    header = "scheme            dil  " + "  ".join(f"B={b}" for b in BUFFERS)
    print(header)
    for name, builder in SCHEMES.items():
        fps, stretch, dilation = build(builder)
        row = {}
        cells = []
        for b in BUFFERS:
            rec = pack_with_buffer(fps, b, orders)
            row[str(b)] = rec
            mk = rec["makespan"] if rec else None
            flag = "*" if mk == lb else " "
            cells.append(f"{mk}{flag}")
        results[name] = {
            "tree_dilation": dilation,
            "shortest_path": stretch == 0,
            "makespan_by_buffer": row,
            "reaches_lb": any(v and v["makespan"] == lb
                              for v in row.values()),
            "min_buffer_for_lb": next(
                (int(b) for b in map(str, BUFFERS)
                 if row[b] and row[b]["makespan"] == lb), None),
        }
        print(f"{name:16s} {dilation:4d}  " + "  ".join(f"{c:>5s}" for c in cells))

    OUT.write_text(json.dumps({
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "model": {"mesh": [MX, MY], "H": H, "V": V, "rb": RAMP_BW,
                  "xbar_write": XBAR_WRITE, "drain": DRAIN,
                  "buffers": BUFFERS, "lower_bound": lb, "m": 1},
        "schemes": results,
    }, indent=2), encoding="utf-8")
    hit = [n for n, r in results.items() if r["reaches_lb"]]
    print(f"reach LB {lb}: {hit}")
    print(f"Wrote {OUT}")


if __name__ == "__main__":
    main()
