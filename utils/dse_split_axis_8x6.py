#!/usr/bin/env python3
"""Split-half axis+CCW allgather on 8x6: makespan + resource DSE.

Scheme family (user request):
  * Partition the 8x6 mesh into two equal halves — either two 4x6 halves
    (vertical cut between x=3 and x=4) or two 8x3 halves (horizontal cut
    between y=2 and y=3).
  * Inside the source's own half run a local axis+CCW spanning tree.
  * At the boundary column/row, multicast across into the other half and run
    straight in the boundary-perpendicular direction to the far edge, so the
    other half is covered by one comb per boundary line.

We validate every source tree, then measure:
  * strict rb=2 makespan (m=1..5) via the shared rigid packer,
  * wide-eject burst-buffer sweep (m=1, B in {0,1,2,4,8,11}),
  * router resources: slot-table depth (Pmax), directed-link reuse,
    crossbar output peak, mesh fanout, calendar issue width, analytic area.
Baseline axis+CCW (whole mesh) is included for comparison.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import sched_zerobuf_compare as S
import dse_burst_sweep_8x6 as BSW
from dse_tree_allgather_6x8 import (
    MX, MY, H, V, N, RAMP, RAMP_BW, formal_bounds, coord, nid,
    validate_tree, pack_scheme, axis_ccw_tree, architecture_variants,
)

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results" / "split_axis_8x6.json"

VMID = MX // 2  # left = 0..VMID-1, right = VMID..MX-1
HMID = MY // 2  # bottom = 0..HMID-1, top = HMID..MY-1


def axis_ccw_rect(s: int, x0: int, x1: int, y0: int, y1: int) -> list[tuple[int, int]]:
    """axis+CCW spanning arborescence over the sub-rectangle [x0,x1]x[y0,y1]."""
    sx, sy = coord(s)
    e: list[tuple[int, int]] = []
    for x in range(sx + 1, x1 + 1):
        e.append((nid(x - 1, sy), nid(x, sy)))
    for x in range(sx - 1, x0 - 1, -1):
        e.append((nid(x + 1, sy), nid(x, sy)))
    for y in range(sy - 1, y0 - 1, -1):
        e.append((nid(sx, y + 1), nid(sx, y)))
    for y in range(sy + 1, y1 + 1):
        e.append((nid(sx, y - 1), nid(sx, y)))
    for x in range(sx + 1, x1 + 1):            # right arm fans down (LR vertical)
        for y in range(sy - 1, y0 - 1, -1):
            e.append((nid(x, y + 1), nid(x, y)))
    for x in range(sx - 1, x0 - 1, -1):        # left arm fans up (UL vertical)
        for y in range(sy + 1, y1 + 1):
            e.append((nid(x, y - 1), nid(x, y)))
    for y in range(sy - 1, y0 - 1, -1):        # down arm fans left (LL horizontal)
        for x in range(sx - 1, x0 - 1, -1):
            e.append((nid(x + 1, y), nid(x, y)))
    for y in range(sy + 1, y1 + 1):            # up arm fans right (UR horizontal)
        for x in range(sx + 1, x1 + 1):
            e.append((nid(x - 1, y), nid(x, y)))
    return e


def split_vertical(s: int) -> list[tuple[int, int]]:
    """Two 4x6 halves; boundary-perpendicular run is horizontal."""
    sx, _ = coord(s)
    if sx < VMID:
        e = axis_ccw_rect(s, 0, VMID - 1, 0, MY - 1)
        for y in range(MY):
            e.append((nid(VMID - 1, y), nid(VMID, y)))
            for x in range(VMID + 1, MX):
                e.append((nid(x - 1, y), nid(x, y)))
    else:
        e = axis_ccw_rect(s, VMID, MX - 1, 0, MY - 1)
        for y in range(MY):
            e.append((nid(VMID, y), nid(VMID - 1, y)))
            for x in range(VMID - 2, -1, -1):
                e.append((nid(x + 1, y), nid(x, y)))
    return e


def split_horizontal(s: int) -> list[tuple[int, int]]:
    """Two 8x3 halves; boundary-perpendicular run is vertical."""
    _, sy = coord(s)
    if sy < HMID:
        e = axis_ccw_rect(s, 0, MX - 1, 0, HMID - 1)
        for x in range(MX):
            e.append((nid(x, HMID - 1), nid(x, HMID)))
            for y in range(HMID + 1, MY):
                e.append((nid(x, y - 1), nid(x, y)))
    else:
        e = axis_ccw_rect(s, 0, MX - 1, HMID, MY - 1)
        for x in range(MX):
            e.append((nid(x, HMID), nid(x, HMID - 1)))
            for y in range(HMID - 2, -1, -1):
                e.append((nid(x, y + 1), nid(x, y)))
    return e


BUILDERS = {
    "axis_ccw": axis_ccw_tree,
    "split_vertical_4x6": split_vertical,
    "split_horizontal_8x3": split_horizontal,
}


def burst_sweep(builder) -> dict:
    fps, stretch, dilation = BSW.build(builder)
    orders = [BSW.corner_order()]
    for _n, gen in S.SRC_ORDERS.items():
        try:
            orders.append(list(gen()))
        except TypeError:
            continue
    row = {}
    for b in BSW.BUFFERS:
        rec = BSW.pack_with_buffer(fps, b, orders)
        row[str(b)] = rec["makespan"] if rec else None
    lb = formal_bounds(1)["T_lb"]
    return {
        "tree_dilation": dilation,
        "shortest_path": stretch == 0,
        "makespan_by_buffer": row,
        "reaches_lb": any(v == lb for v in row.values()),
        "min_buffer_for_lb": next(
            (int(b) for b in map(str, BSW.BUFFERS) if row[b] == lb), None),
    }


def main() -> None:
    S.cfg(MX, MY, H, V)
    S.init_ring()
    S.init_quadrants()
    lb1 = formal_bounds(1)["T_lb"]
    schemes = {}
    print(f"mesh={MX}x{MY} LB(m=1)={lb1}")
    for name, builder in BUILDERS.items():
        for s in range(N):
            chk = validate_tree(s, builder(s))
            assert chk["ok"], (name, s, chk["errors"])
        messages = {str(m): pack_scheme(name, builder, m) for m in range(1, 6)}
        sweep = burst_sweep(builder)
        arch = architecture_variants(messages)
        m1 = messages["1"]
        mi = m1["microarchitecture"]
        schemes[name] = {
            "messages": messages,
            "burst_sweep": sweep,
            "architectures": arch,
        }
        print(
            f"{name:20s} strict_mk(m1)={m1['makespan']:4d} "
            f"Pmax={mi['topology_period_max']:2d} "
            f"linkreuse={m1['routing_lower_bounds']['directed_link_congestion']:2d} "
            f"xbar_out={mi['crossbar_outputs_peak']} "
            f"fanout={m1['tree']['max_mesh_fanout']} "
            f"sweep_min={min(v for v in sweep['makespan_by_buffer'].values() if v)} "
            f"reachLB={sweep['reaches_lb']}"
        )
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "model": {
            "mesh": [MX, MY], "H": H, "V": V, "ramp": RAMP, "rb": RAMP_BW,
            "xbar_write": BSW.XBAR_WRITE, "buffers": BSW.BUFFERS,
            "lower_bound_m1": lb1,
            "vmid": VMID, "hmid": HMID,
        },
        "formal_lower_bounds": {str(m): formal_bounds(m) for m in range(1, 6)},
        "schemes": schemes,
    }
    OUT.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"Wrote {OUT}")


if __name__ == "__main__":
    main()
