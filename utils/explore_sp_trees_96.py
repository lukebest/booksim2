#!/usr/bin/env python3
"""Explore shortest-path multicast trees that reach the 8x6 allgather LB (96).

Only Manhattan-preserving (dilation=96) arborescences can reach 96.  axis+CCW
does, but with high Pmax(15) and crossbar out-peak(6).  Here we build several
low-congestion shortest-path trees and check: reach-96, min burst buffer,
max directed-link multiplicity, Pmax, crossbar out-peak.
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import sched_zerobuf_compare as S
import slide_metrics as SM
from dse_tree_allgather_6x8 import (
    MX, MY, H, V, N, RAMP, RAMP_BW, formal_bounds, coord, nid,
    footprint, validate_tree, axis_ccw_tree,
)
from dse_burst_sweep_8x6 import (
    BUFFERS, pack_with_buffer, corner_order, build as build_fps,
)

OUT = ROOT_OUT = Path(__file__).resolve().parents[1] / "results" / "burst_sweep_8x6.json"


def cross_arms(sx: int, sy: int) -> list[tuple[int, int]]:
    """Full '+' cross: flood source row and source column."""
    e = []
    for x in range(sx + 1, MX):
        e.append((nid(x - 1, sy), nid(x, sy)))
    for x in range(sx - 1, -1, -1):
        e.append((nid(x + 1, sy), nid(x, sy)))
    for y in range(sy + 1, MY):
        e.append((nid(sx, y - 1), nid(sx, y)))
    for y in range(sy - 1, -1, -1):
        e.append((nid(sx, y + 1), nid(sx, y)))
    return e


def quad_balanced(s: int) -> list[tuple[int, int]]:
    """Cross arms; each interior node filled from whichever arm keeps the two
    fill directions balanced: UR & LL quadrants fill vertically (from row arm),
    UL & LR fill horizontally (from column arm)."""
    sx, sy = coord(s)
    e = cross_arms(sx, sy)
    for x in range(MX):
        if x == sx:
            continue
        for y in range(MY):
            if y == sy:
                continue
            right = x > sx
            up = y > sy
            vertical = (right and up) or (not right and not up)
            if vertical:
                if up:
                    e.append((nid(x, y - 1), nid(x, y)))
                else:
                    e.append((nid(x, y + 1), nid(x, y)))
            else:
                if right:
                    e.append((nid(x - 1, y), nid(x, y)))
                else:
                    e.append((nid(x + 1, y), nid(x, y)))
    return e


def axis_cw(s: int) -> list[tuple[int, int]]:
    """Cross arms + true CW-90° fanout (mirror of axis+CCW).

    Each arm turns 90° clockwise before fanning out:
    right→up, up→left, left→down, down→right.  Result per quadrant:
    UR & LL fill vertically (from the row arm), UL & LR fill horizontally
    (from the column arm).  This is the exact mirror of axis+CCW and, for a
    2-D mesh, coincides edge-for-edge with :func:`quad_balanced`.
    """
    sx, sy = coord(s)
    e = cross_arms(sx, sy)
    for x in range(MX):
        if x == sx:
            continue
        for y in range(MY):
            if y == sy:
                continue
            right = x > sx
            up = y > sy
            if right and up:        # UR: row arm fans up (vertical)
                e.append((nid(x, y - 1), nid(x, y)))
            elif not right and not up:  # LL: row arm fans down (vertical)
                e.append((nid(x, y + 1), nid(x, y)))
            elif right and not up:  # LR: col arm fans right (horizontal)
                e.append((nid(x - 1, y), nid(x, y)))
            else:                   # UL: col arm fans left (horizontal)
                e.append((nid(x + 1, y), nid(x, y)))
    return e


def quad_nearest(s: int) -> list[tuple[int, int]]:
    """Cross arms; interior filled from the nearer arm (fewer hops on the
    branch), breaking ties toward vertical fill."""
    sx, sy = coord(s)
    e = cross_arms(sx, sy)
    for x in range(MX):
        if x == sx:
            continue
        for y in range(MY):
            if y == sy:
                continue
            dx = abs(x - sx)
            dy = abs(y - sy)
            # vertical fill uses the row arm node (x,sy): branch length dy
            # horizontal fill uses the col arm node (sx,y): branch length dx
            vertical = dy <= dx
            if vertical:
                e.append((nid(x, y - 1), nid(x, y)) if y > sy
                         else (nid(x, y + 1), nid(x, y)))
            else:
                e.append((nid(x - 1, y), nid(x, y)) if x > sx
                         else (nid(x + 1, y), nid(x, y)))
    return e


CANDIDATES = {
    "axis_ccw": axis_ccw_tree,
    "axis_cw": axis_cw,
    "quad_balanced": quad_balanced,
    "quad_nearest": quad_nearest,
}


def metrics(name: str, builder) -> dict:
    fps, stretch, dilation = build_fps(builder)
    # link multiplicity
    lm = defaultdict(int)
    for s, slots in fps.items():
        for kind, key, rel in slots:
            if kind == "L":
                lm[key] += 1
    # Pmax + out-peak via a strict pack (down_cap=rb) events
    S.cfg(MX, MY, H, V)
    best = None
    for oname, gen in S.SRC_ORDERS.items():
        try:
            order = list(gen())
        except TypeError:
            continue
        mk, mo, busy, offs, events = S.export_events(
            fps, RAMP_BW, order, flits=1)
        if not S.verify(busy, RAMP_BW, flits=1):
            continue
        if best is None or mk < best[0]:
            best = (mk, events, offs)
    slot = SM.slot_table_depth(best[1], MX, MY, best[0]) if best else {}
    # crossbar out-peak
    out = defaultdict(int)
    for s in range(N):
        chk = validate_tree(s, builder(s))
        dist, children = chk["distance"], chk["children"]
        for node in range(N):
            t = (best[2][s] if best else 0) + RAMP + dist[node]
            fan = len(children.get(node, [])) + (0 if node == s else 1)
            out[node, t] += fan
    orders = [corner_order()]
    for _n, g in S.SRC_ORDERS.items():
        try:
            orders.append(list(g()))
        except TypeError:
            pass
    sweep = {}
    for b in BUFFERS:
        rec = pack_with_buffer(fps, b, orders)
        sweep[str(b)] = rec["makespan"] if rec else None
    lb = formal_bounds(1)["T_lb"]
    return {
        "dilation": dilation,
        "shortest_path": stretch == 0,
        "max_link_multiplicity": max(lm.values()),
        "topo_period_max": slot.get("max_period"),
        "crossbar_out_peak": max(out.values()),
        "makespan_by_buffer": sweep,
        "reaches_lb": any(v == lb for v in sweep.values()),
        "min_buffer_for_lb": next((int(b) for b in map(str, BUFFERS)
                                   if sweep[b] == lb), None),
    }


def main() -> None:
    S.cfg(MX, MY, H, V)
    S.init_ring()
    S.init_quadrants()
    lb = formal_bounds(1)["T_lb"]
    print(f"LB={lb}  (dilation must be 96 to have any chance)")
    print(f"{'scheme':16s} {'dil':>4s} {'linkX':>6s} {'Pmax':>5s} "
          f"{'out':>4s} {'minB':>5s}  sweep")
    out = {}
    for name, builder in CANDIDATES.items():
        try:
            m = metrics(name, builder)
        except Exception as exc:  # noqa: BLE001
            print(f"{name}: FAILED {exc}")
            continue
        out[name] = m
        sweep = " ".join(f"{m['makespan_by_buffer'][str(b)]}" for b in BUFFERS)
        print(f"{name:16s} {m['dilation']:4d} {m['max_link_multiplicity']:6d} "
              f"{str(m['topo_period_max']):>5s} {m['crossbar_out_peak']:4d} "
              f"{str(m['min_buffer_for_lb']):>5s}  [{sweep}]")

    doc = json.loads(OUT.read_text()) if OUT.exists() else {}
    doc.setdefault("sp_exploration", {})
    doc["sp_exploration"].update({
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "buffers": BUFFERS, "lower_bound": lb,
        "candidates": out,
    })
    OUT.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    winners = [n for n, m in out.items() if m["reaches_lb"]]
    print(f"reach LB: {winners}")
    # better-than-axis?
    ax = out.get("axis_ccw", {})
    for n, m in out.items():
        if n == "axis_ccw" or not m["reaches_lb"]:
            continue
        if (m["topo_period_max"] or 99) <= (ax.get("topo_period_max") or 99) and \
           m["crossbar_out_peak"] <= ax.get("crossbar_out_peak", 99):
            print(f"  {n} dominates/ties axis on Pmax & out-peak")
    print(f"Wrote {OUT}")


if __name__ == "__main__":
    main()
