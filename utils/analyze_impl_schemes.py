#!/usr/bin/env python3
"""Implementation-scheme cost analysis for the rigid allgather trees on 8x6.

Three routing-delivery mechanisms are quantified against the actual axis+CCW /
split trees:
  1. source routing      - packet carries the (compressible) route + multicast,
  2. router calendar     - per-router time-indexed in->out table (no header),
  3. hybrid color        - packet carries a small tag selecting a preset route
                           (Cerebras-style color).

Key structural facts driving cost (measured, not assumed):
  * distinct source trees                       : 48 (all sources differ),
  * distinct out-masks per router (any source)  : <=9  (relative-quadrant class),
  * distinct out-masks per (router,in-port)     : <=2  (arm-flit vs fill-flit),
    => a single phase bit disambiguates routing at every hop.
Timing (rigid inject offsets) is orthogonal: all three need it for 0-buffer;
only the calendar additionally couples routing to a global time base.
"""

from __future__ import annotations

import json
import math
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import ppa_analytic_model as PPA
import sched_zerobuf_compare as S
from dse_tree_allgather_6x8 import (
    MX, MY, H, V, N, coord, validate_tree, pack_scheme, axis_ccw_tree,
)
from dse_split_axis_8x6 import split_vertical, split_horizontal

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results" / "impl_schemes.json"

BUILDERS = {
    "axis_ccw": axis_ccw_tree,
    "split_vertical_4x6": split_vertical,
    "split_horizontal_8x3": split_horizontal,
}

# SparseCal event = 23 bits; color/class entry ~= out_mask(5) + eject(1) +
# next-color(1) + valid(1) = 8 bits (single bank, no time index).
CAL_EVENT_BITS = PPA.SPARSE_CAL_EVENT_BITS
COLOR_ENTRY_BITS = 8
KBIT = PPA.K_CTRL
PORTS_IN = 5  # N,E,S,W,Local


def _dir(p: int, c: int) -> str:
    px, py = coord(p)
    cx, cy = coord(c)
    return "E" if cx > px else "W" if cx < px else "N" if cy > py else "S"


def pow2(v: int) -> int:
    return 1 if v <= 1 else 1 << (v - 1).bit_length()


def structural(builder) -> dict:
    per_router: dict[int, set] = defaultdict(set)
    per_inport: dict[tuple, set] = defaultdict(set)
    trees = set()
    max_hops = 0
    for s in range(N):
        edges = builder(s)
        chk = validate_tree(s, edges)
        children = chk["children"]
        parent = {c: p for p, c in edges}
        trees.add(frozenset(edges))
        # hop depth (unweighted) root->leaf
        depth = {s: 0}
        stack = [s]
        while stack:
            u = stack.pop()
            for v in children.get(u, []):
                depth[v] = depth[u] + 1
                stack.append(v)
        max_hops = max(max_hops, max(depth.values()))
        for r in range(N):
            outs = frozenset(_dir(r, c) for c in children.get(r, []))
            per_router[r].add(outs)
            indir = "L" if r == s else _dir(parent[r], r)
            per_inport[(r, indir)].add(outs)
    router_cfg = [len(v) for v in per_router.values()]
    inport_cfg = [len(v) for v in per_inport.values()]
    return {
        "distinct_trees": len(trees),
        "router_configs_max": max(router_cfg),
        "router_configs_mean": round(sum(router_cfg) / len(router_cfg), 2),
        "inport_configs_max": max(inport_cfg),
        "inport_configs_mean": round(sum(inport_cfg) / len(inport_cfg), 2),
        "color_bits_min": max(1, math.ceil(math.log2(max(inport_cfg)))),
        "max_tree_hops": max_hops,
    }


def impl_costs(struct: dict, pmax: int, issue: int) -> dict:
    # 1. calendar (time-indexed, no header)
    cal_depth = pow2(pmax)
    cal_bits = PPA.SPARSE_CAL_BANKS * cal_depth * CAL_EVENT_BITS * issue
    calendar = {
        "header_bits": 0,
        "router_sram_bits": cal_bits,
        "router_sram_area": round(cal_bits * KBIT, 5),
        "needs_global_time": True,
        "notes": f"depth={cal_depth}(Pmax={pmax}) x {CAL_EVENT_BITS}b x{issue} issue",
    }
    # 2. source routing (compressed algorithmic vs full turn-list)
    coord_bits = math.ceil(math.log2(MX)) + math.ceil(math.log2(MY))
    full_route_bits = struct["max_tree_hops"] * 3
    src = {
        "header_bits_compressed": coord_bits + 2,   # +scheme id
        "header_bits_full": full_route_bits,
        "router_sram_bits": struct["router_configs_max"] * COLOR_ENTRY_BITS,
        "router_sram_area": round(struct["router_configs_max"] * COLOR_ENTRY_BITS * KBIT, 5),
        "needs_global_time": False,
        "notes": "router computes out-mask = f(rel-quadrant of src); tiny LUT/ALU",
    }
    # 3. hybrid color (tag = phase bit, per-(in-port,color) static table)
    C = 1 << struct["color_bits_min"]
    color_bits = C * PORTS_IN * COLOR_ENTRY_BITS
    color = {
        "header_bits": struct["color_bits_min"],
        "colors": C,
        "router_sram_bits": color_bits,
        "router_sram_area": round(color_bits * KBIT, 5),
        "needs_global_time": False,
        "notes": f"{C}-color x {PORTS_IN} in-port static route; fork swaps color",
    }
    return {"source_routing": src, "router_calendar": calendar, "hybrid_color": color}


def main() -> None:
    S.cfg(MX, MY, H, V)
    S.init_ring()
    S.init_quadrants()
    schemes = {}
    for name, builder in BUILDERS.items():
        struct = structural(builder)
        m1 = pack_scheme(name, builder, 1)
        pmax = m1["microarchitecture"]["topology_period_max"]
        issue = m1["microarchitecture"]["calendar_issue_width"]
        schemes[name] = {
            "structural": struct,
            "pmax": pmax,
            "issue_width": issue,
            "implementations": impl_costs(struct, pmax, issue),
        }
        print(f"== {name} == Pmax={pmax} issue={issue}")
        print(f"   {struct}")
        for impl, c in schemes[name]["implementations"].items():
            print(f"   {impl:16s} {c}")
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "model": {"mesh": [MX, MY], "H": H, "V": V,
                  "cal_event_bits": CAL_EVENT_BITS,
                  "color_entry_bits": COLOR_ENTRY_BITS,
                  "k_per_bit": KBIT},
        "schemes": schemes,
    }
    OUT.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"Wrote {OUT}")


if __name__ == "__main__":
    main()
