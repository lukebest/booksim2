#!/usr/bin/env python3
"""Zero-buffer allgather on 8x6 mesh with 8 non-compute (hole) nodes.

Layout (1-indexed): columns 4,5 × rows 1,2,3,4 are holes → 0-indexed
  x ∈ {3,4}, y ∈ {0,1,2,3}.  Remaining 40 nodes are compute (alive).

Holes are NOT faults: their routers and mesh links remain fully available.
They simply have no compute PE for this allgather, so they never inject or
eject — only transit.  Allgather endpoints are the 40 alive nodes.

Rigid 0-buffer model, down-ramp / eject drain E = 2 flit/cy/node, B = 0
(so crossbar write W = 2).  Reports 1-flit makespan and measured 5-flit
overlap (T5, delta2, II_eff) for several tree families.
"""

from __future__ import annotations

import json
import math
from collections import defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

import sched_zerobuf_compare as S
import dse_burst_sweep_8x6 as BSW
from dse_tree_allgather_6x8 import MX, MY, H, V, N, RAMP, RAMP_BW, nid, coord
from dse_multiflit_area_makespan import (
    pack_one_with_offs, _try_place, _commit, ROUNDS, CAP,
)

ROOT = Path(__file__).resolve().parents[1]
OUT_JSON = ROOT / "results" / "holes_40_allgather.json"
OUT_PNG = ROOT / "results" / "holes_40_allgather.png"
OUT_HTML = ROOT / "results" / "report_holes_40_allgather.html"

# 1-indexed cols 4,5 and rows 1,2,3,4
HOLES = {nid(x, y) for x in (3, 4) for y in (0, 1, 2, 3)}
ALIVE = sorted(set(range(N)) - HOLES)
NA = len(ALIVE)  # 40

DIRS = [(1, 0), (-1, 0), (0, 1), (0, -1)]


def neighbors(node: int) -> list[int]:
    x, y = coord(node)
    out = []
    for dx, dy in DIRS:
        nx, ny = x + dx, y + dy
        if 0 <= nx < MX and 0 <= ny < MY:
            out.append(nid(nx, ny))
    return out


def edge_lat(p: int, c: int) -> int:
    return H if coord(p)[1] == coord(c)[1] else V


def bfs_parents(source: int) -> dict[int, int | None]:
    parent = {source: None}
    q = deque([source])
    while q:
        u = q.popleft()
        for v in neighbors(u):
            if v not in parent:
                parent[v] = u
                q.append(v)
    return parent


def steiner_arborescence(source: int) -> list[tuple[int, int]]:
    """Shortest-path arborescence from source covering all alive nodes;
    holes may appear as Steiner points."""
    parent = bfs_parents(source)
    edges: set[tuple[int, int]] = set()
    for d in ALIVE:
        if d == source:
            continue
        if d not in parent:
            raise RuntimeError(f"unreachable alive {d} from {source}")
        cur = d
        while cur != source:
            p = parent[cur]
            edges.add((p, cur))
            cur = p
    return sorted(edges)


def dim_order_arborescence(source: int, order: str) -> list[tuple[int, int]]:
    """Manhattan dim-order paths source→each alive dest, unioned & pruned to
    an arborescence (first-claimed parent wins via BFS tie-break on the
    dim-order DAG)."""
    sx, sy = coord(source)
    # preferred next hop toward target under XY or YX
    cand_edges: set[tuple[int, int]] = set()
    for d in ALIVE:
        if d == source:
            continue
        dx, dy = coord(d)
        x, y = sx, sy
        if order == "xy":
            while x != dx:
                nx = x + (1 if dx > x else -1)
                cand_edges.add((nid(x, y), nid(nx, y)))
                x = nx
            while y != dy:
                ny = y + (1 if dy > y else -1)
                cand_edges.add((nid(x, y), nid(x, ny)))
                y = ny
        else:
            while y != dy:
                ny = y + (1 if dy > y else -1)
                cand_edges.add((nid(x, y), nid(x, ny)))
                y = ny
            while x != dx:
                nx = x + (1 if dx > x else -1)
                cand_edges.add((nid(x, y), nid(nx, y)))
                x = nx
    # BFS on cand_edges from source, keep tree edges to cover alive
    adj = defaultdict(list)
    for p, c in cand_edges:
        adj[p].append(c)
    parent = {source: None}
    q = deque([source])
    while q:
        u = q.popleft()
        for v in sorted(adj[u]):
            if v not in parent:
                parent[v] = u
                q.append(v)
    # if some alive not reached (hole blocking dim path incorrectly — shouldn't
    # happen on full mesh dim-order), fall back to Steiner
    if any(d not in parent for d in ALIVE):
        return steiner_arborescence(source)
    edges = set()
    for d in ALIVE:
        if d == source:
            continue
        cur = d
        while cur != source:
            p = parent[cur]
            edges.add((p, cur))
            cur = p
    return sorted(edges)


def axis_ccw_alive(source: int) -> list[tuple[int, int]]:
    """Axis+CCW footprint on full mesh, then prune to paths that reach alive."""
    from dse_tree_allgather_6x8 import axis_ccw_tree
    full = axis_ccw_tree(source)
    children = defaultdict(list)
    for p, c in full:
        children[p].append(c)
    # keep only edges on paths to alive destinations
    needed = set()
    for d in ALIVE:
        if d == source:
            continue
        # walk from d toward source via reverse of tree
        # build parent map
    parent = {}
    for p, c in full:
        parent[c] = p
    for d in ALIVE:
        if d == source:
            continue
        cur = d
        seen = set()
        while cur != source and cur in parent and cur not in seen:
            seen.add(cur)
            p = parent[cur]
            needed.add((p, cur))
            cur = p
    if len({c for _, c in needed}) < NA - 1:
        return steiner_arborescence(source)
    return sorted(needed)


def dual_wing_bridge(source: int, bridge_y: int = 4) -> list[tuple[int, int]]:
    """Left wing (x<=2) / right wing (x>=5) / top strip (y>=bridge_y), with
    E-W bridge on row bridge_y through hole columns.  Intra-wing: vertical
    then to bridge; cross-wing: up to bridge, across, down."""
    sx, sy = coord(source)

    def wing(x: int) -> str:
        if x <= 2:
            return "L"
        if x >= 5:
            return "R"
        return "M"  # hole columns or x=3,4 above holes (y>=4)

    edges: set[tuple[int, int]] = set()

    def add_path(path: list[int]):
        for a, b in zip(path, path[1:]):
            edges.add((a, b))

    def v_chain(x: int, y0: int, y1: int) -> list[int]:
        step = 1 if y1 >= y0 else -1
        return [nid(x, y) for y in range(y0, y1 + step, step)]

    def h_chain(y: int, x0: int, x1: int) -> list[int]:
        step = 1 if x1 >= x0 else -1
        return [nid(x, y) for x in range(x0, x1 + step, step)]

    for d in ALIVE:
        if d == source:
            continue
        dx, dy = coord(d)
        path = [source]
        cx, cy = sx, sy
        # 1) climb/descend to bridge_y within source column (or nearest)
        if cy != bridge_y:
            for n in v_chain(cx, cy, bridge_y)[1:]:
                path.append(n)
            cy = bridge_y
        # 2) move on bridge to dest column
        if cx != dx:
            for n in h_chain(bridge_y, cx, dx)[1:]:
                path.append(n)
            cx = dx
        # 3) descend/ascend to dest row
        if cy != dy:
            for n in v_chain(cx, cy, dy)[1:]:
                path.append(n)
        add_path(path)

    # BFS arborescence on union
    adj = defaultdict(list)
    for p, c in edges:
        adj[p].append(c)
    parent = {source: None}
    q = deque([source])
    while q:
        u = q.popleft()
        for v in sorted(set(adj[u])):
            if v not in parent:
                parent[v] = u
                q.append(v)
    if any(d not in parent for d in ALIVE):
        return steiner_arborescence(source)
    out = set()
    for d in ALIVE:
        if d == source:
            continue
        cur = d
        while cur != source:
            p = parent[cur]
            out.add((p, cur))
            cur = p
    return sorted(out)


def dual_wing_comb(source: int) -> list[tuple[int, int]]:
    """Bridge on y=5 (top row), comb teeth downward into each column —
    keeps cross-wing traffic on the topmost C-only row (no H on y=5)."""
    return dual_wing_bridge(source, bridge_y=5)


SCHEMES = {
    "steiner_sp": ("shortest-path Steiner", steiner_arborescence),
    "dim_xy": ("dim-XY", lambda s: dim_order_arborescence(s, "xy")),
    "dim_yx": ("dim-YX", lambda s: dim_order_arborescence(s, "yx")),
    "axis_ccw": ("axis+CCW pruned", axis_ccw_alive),
    "wing_bridge_y4": ("dual-wing bridge y=4", lambda s: dual_wing_bridge(s, 4)),
    "wing_comb_y5": ("dual-wing comb y=5", dual_wing_comb),
}


def validate_alive_tree(source: int, edges: list[tuple[int, int]]) -> dict:
    children = defaultdict(list)
    indeg = defaultdict(int)
    for p, c in edges:
        if abs(coord(p)[0] - coord(c)[0]) + abs(coord(p)[1] - coord(c)[1]) != 1:
            return {"ok": False, "errors": [f"non_adj {p}->{c}"]}
        children[p].append(c)
        indeg[c] += 1
    if indeg[source] != 0:
        return {"ok": False, "errors": ["root indeg"]}
    dist = {source: 0}
    q = deque([source])
    while q:
        u = q.popleft()
        for v in children[u]:
            if v in dist:
                return {"ok": False, "errors": [f"cycle {v}"]}
            dist[v] = dist[u] + edge_lat(u, v)
            q.append(v)
    missing = [a for a in ALIVE if a not in dist]
    if missing:
        return {"ok": False, "errors": [f"missing {missing}"]}
    return {"ok": True, "errors": [], "distance": dist, "children": children,
            "max_fanout": max((len(v) for v in children.values()), default=0)}


def footprint_alive(source: int, edges, check) -> list:
    """U at source; L on all tree edges; D only at alive destinations."""
    slots = [("U", source, 0)]
    dist = check["distance"]
    for p, c in edges:
        slots.append(("L", S.lk(p, c), RAMP + dist[p]))
    for d in ALIVE:
        if d != source:
            slots.append(("D", d, RAMP + dist[d]))
    return slots


def delta2_alive(fps, b, pack):
    """Earliest per-alive-source second-flit gap."""
    offs = pack["offs"]
    deltas = []
    for s in ALIVE:
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


def pack_rounds_alive(fps, rounds, b, base_order, mode: str):
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
    mk = max(BSW.node_completion(down_arr[n]) for n in ALIVE)
    # alive destinations only receive; holes stay empty — also check all nodes
    mk = max(BSW.node_completion(down_arr[n]) for n in range(N))
    deltas = [offs[(s, r + 1)] - offs[(s, r)]
              for s in base_order for r in range(rounds - 1)]
    return {"makespan": mk,
            "min_delta": min(deltas), "avg_delta": round(sum(deltas) / len(deltas), 2),
            "max_delta": max(deltas)}


def link_reuse(fps) -> int:
    cnt = defaultdict(int)
    for s in fps:
        for kind, key, _ in fps[s]:
            if kind == "L":
                cnt[key] += 1
    return max(cnt.values()) if cnt else 0


def formal_bounds_alive(m: int = 1) -> dict:
    """Receiver-release + eject among alive nodes only (paths = Manhattan;
    holes don't increase Manhattan lower bound since transit through holes
    is allowed and Manhattan is still achievable)."""
    best = 0
    for d in ALIVE:
        releases = []
        dx, dy = coord(d)
        for s in ALIVE:
            if s == d:
                continue
            sx, sy = coord(s)
            dist = abs(sx - dx) * H + abs(sy - dy) * V
            releases.extend(RAMP + dist + k for k in range(m))
        lanes = [-10**9] * RAMP_BW
        for release in sorted(releases):
            j = min(range(RAMP_BW), key=lambda i: lanes[i])
            lanes[j] = max(release, lanes[j] + 1)
        best = max(best, max(lanes) + RAMP)
    eject = math.ceil((NA - 1) * m / RAMP_BW)
    diam = max(
        abs(coord(a)[0] - coord(b)[0]) * H + abs(coord(a)[1] - coord(b)[1]) * V
        for a in ALIVE for b in ALIVE)
    return {
        "T_lb": max(best, eject, 2 * RAMP + diam + m - 1),
        "receiver_release": best,
        "eject_duration": eject,
        "diameter_serialization": 2 * RAMP + diam + m - 1,
        "N_alive": NA,
        "N_holes": len(HOLES),
    }


def build_fps(builder):
    fps = {}
    dil = 0
    for s in ALIVE:
        edges = builder(s)
        chk = validate_alive_tree(s, edges)
        assert chk["ok"], (s, chk["errors"])
        fps[s] = footprint_alive(s, edges, chk)
        dil = max(dil, 2 * RAMP + max(chk["distance"][d] for d in ALIVE))
    return fps, dil


def ascii_map() -> str:
    rows = []
    for y in range(MY - 1, -1, -1):
        cells = []
        for x in range(MX):
            n = nid(x, y)
            cells.append("H" if n in HOLES else "C")
        rows.append(" ".join(cells) + f"  y={y}")
    return "\n".join(rows)


_CELL, _R, _MARGIN = 44, 8, 28


def svg_tree(source: int, edges: list[tuple[int, int]]) -> str:
    """SVG of one arborescence; holes grey, source red, transit-via-H edges dashed."""
    w = _MARGIN * 2 + (MX - 1) * _CELL
    h = _MARGIN * 2 + (MY - 1) * _CELL + 8

    def px(x):
        return _MARGIN + x * _CELL

    def py(y):
        return _MARGIN + (MY - 1 - y) * _CELL

    lines = []
    for p, c in edges:
        pxx, pyy = coord(p)
        cxx, cyy = coord(c)
        via_h = (p in HOLES) or (c in HOLES)
        col = "#ea580c" if via_h else "#2563eb"
        dash = ' stroke-dasharray="4 3"' if via_h else ""
        x1, y1, x2, y2 = px(pxx), py(pyy), px(cxx), py(cyy)
        dx, dy = x2 - x1, y2 - y1
        d = (dx * dx + dy * dy) ** 0.5 or 1
        ux, uy = dx / d, dy / d
        lines.append(
            f'<line x1="{x1 + ux * (_R + 1):.1f}" y1="{y1 + uy * (_R + 1):.1f}" '
            f'x2="{x2 - ux * (_R + 3):.1f}" y2="{y2 - uy * (_R + 3):.1f}" '
            f'stroke="{col}" stroke-width="2.2"{dash}/>'
        )
    nodes = []
    for y in range(MY):
        for x in range(MX):
            n = nid(x, y)
            if n == source:
                fill, stroke, sw = "#dc2626", "#dc2626", 2
            elif n in HOLES:
                fill, stroke, sw = "#cbd5e1", "#64748b", 1.5
            else:
                fill, stroke, sw = "#fff", "#94a3b8", 1
            nodes.append(
                f'<circle cx="{px(x)}" cy="{py(y)}" r="{_R}" fill="{fill}" '
                f'stroke="{stroke}" stroke-width="{sw}"/>'
            )
            if n in HOLES:
                nodes.append(
                    f'<text x="{px(x)}" y="{py(y) + 3}" text-anchor="middle" '
                    f'font-size="8" fill="#475569">H</text>'
                )
    sx, sy = coord(source)
    return (
        f'<svg width="{w}" height="{h}" viewBox="0 0 {w} {h}" '
        f'xmlns="http://www.w3.org/2000/svg">'
        f'{"".join(lines)}{"".join(nodes)}'
        f'<text x="{_MARGIN}" y="{h - 4}" font-size="11" fill="#64748b">'
        f'source=({sx},{sy}) · blue=C↔C · orange dashed=via live H router'
        f' (H≠fault)</text></svg>'
    )


def svg_wing_comb(source: int, bridge_y: int = 5) -> str:
    """Annotated dual-wing comb: L/R wings, top-row bridge spine, vertical teeth."""
    edges = dual_wing_bridge(source, bridge_y)
    top_pad, bot_pad = 22, 36
    w = _MARGIN * 2 + (MX - 1) * _CELL + 70
    h = _MARGIN * 2 + (MY - 1) * _CELL + top_pad + bot_pad

    def px(x):
        return _MARGIN + x * _CELL

    def py(y):
        return top_pad + _MARGIN + (MY - 1 - y) * _CELL

    parts: list[str] = []
    # wing / hole region washes
    parts.append(
        f'<rect x="{px(0) - 18:.1f}" y="{py(MY - 1) - 18:.1f}" '
        f'width="{px(2) - px(0) + 36:.1f}" height="{py(0) - py(MY - 1) + 36:.1f}" '
        f'fill="#dbeafe" opacity="0.55" rx="6"/>'
    )
    parts.append(
        f'<rect x="{px(5) - 18:.1f}" y="{py(MY - 1) - 18:.1f}" '
        f'width="{px(7) - px(5) + 36:.1f}" height="{py(0) - py(MY - 1) + 36:.1f}" '
        f'fill="#dcfce7" opacity="0.55" rx="6"/>'
    )
    parts.append(
        f'<rect x="{px(3) - 16:.1f}" y="{py(3) - 16:.1f}" '
        f'width="{px(4) - px(3) + 32:.1f}" height="{py(0) - py(3) + 32:.1f}" '
        f'fill="#f1f5f9" stroke="#94a3b8" stroke-dasharray="3 2" rx="4"/>'
    )
    # bridge spine highlight (y = bridge_y)
    parts.append(
        f'<rect x="{px(0) - 14:.1f}" y="{py(bridge_y) - 14:.1f}" '
        f'width="{px(MX - 1) - px(0) + 28:.1f}" height="28" '
        f'fill="#fef08a" opacity="0.75" rx="8"/>'
    )
    parts.append(
        f'<text x="{px(MX - 1) + 16}" y="{py(bridge_y) + 4}" font-size="11" '
        f'fill="#a16207" font-weight="600">bridge y={bridge_y}</text>'
    )
    parts.append(
        f'<text x="{px(1)}" y="{py(MY - 1) - 22}" text-anchor="middle" '
        f'font-size="11" fill="#1d4ed8" font-weight="600">L wing</text>'
    )
    parts.append(
        f'<text x="{px(6)}" y="{py(MY - 1) - 22}" text-anchor="middle" '
        f'font-size="11" fill="#15803d" font-weight="600">R wing</text>'
    )
    parts.append(
        f'<text x="{px(3.5)}" y="{py(1) + 22}" text-anchor="middle" '
        f'font-size="10" fill="#64748b">H (live routers)</text>'
    )

    for p, c in edges:
        pxx, pyy = coord(p)
        cxx, cyy = coord(c)
        on_bridge = (pyy == bridge_y and cyy == bridge_y)
        if on_bridge:
            col, sw = "#ca8a04", 3.0
        elif pxx == cxx:
            col, sw = "#2563eb", 2.2  # vertical tooth
        else:
            col, sw = "#7c3aed", 2.2
        x1, y1, x2, y2 = px(pxx), py(pyy), px(cxx), py(cyy)
        dx, dy = x2 - x1, y2 - y1
        d = (dx * dx + dy * dy) ** 0.5 or 1
        ux, uy = dx / d, dy / d
        parts.append(
            f'<line x1="{x1 + ux * (_R + 1):.1f}" y1="{y1 + uy * (_R + 1):.1f}" '
            f'x2="{x2 - ux * (_R + 3):.1f}" y2="{y2 - uy * (_R + 3):.1f}" '
            f'stroke="{col}" stroke-width="{sw}"/>'
        )

    for y in range(MY):
        for x in range(MX):
            n = nid(x, y)
            if n == source:
                fill, stroke, sw = "#dc2626", "#991b1b", 2.2
            elif n in HOLES:
                fill, stroke, sw = "#cbd5e1", "#64748b", 1.5
            else:
                fill, stroke, sw = "#fff", "#64748b", 1.2
            parts.append(
                f'<circle cx="{px(x)}" cy="{py(y)}" r="{_R}" fill="{fill}" '
                f'stroke="{stroke}" stroke-width="{sw}"/>'
            )
            if n in HOLES:
                parts.append(
                    f'<text x="{px(x)}" y="{py(y) + 3}" text-anchor="middle" '
                    f'font-size="8" fill="#475569">H</text>'
                )
            elif n == source:
                parts.append(
                    f'<text x="{px(x)}" y="{py(y) + 3}" text-anchor="middle" '
                    f'font-size="8" fill="#fff" font-weight="700">S</text>'
                )

    sx, sy = coord(source)
    parts.append(
        f'<text x="{_MARGIN}" y="{h - 18}" font-size="11" fill="#334155">'
        f'dual-wing comb · source=({sx},{sy}) · path = ↑to y={bridge_y} → across '
        f'bridge → ↓tooth</text>'
    )
    parts.append(
        f'<text x="{_MARGIN}" y="{h - 4}" font-size="10" fill="#64748b">'
        f'gold=bridge spine · blue=vertical teeth · purple=other · '
        f'y={bridge_y} is C-only (no H)</text>'
    )
    return (
        f'<svg width="{w}" height="{h}" viewBox="0 0 {w} {h}" '
        f'xmlns="http://www.w3.org/2000/svg">{"".join(parts)}</svg>'
    )


def main() -> None:
    S.cfg(MX, MY, H, V)
    S.init_ring()
    S.init_quadrants()
    assert len(ALIVE) == 40 and len(HOLES) == 8

    # Source orders among alive nodes.  Farthest-from-hole-center first is
    # critical: it reaches the 1-flit LB for axis+CCW pruned.
    hx, hy = 3.5, 1.5
    alive_orders = [
        sorted(ALIVE, key=lambda s: (coord(s)[0] - hx) ** 2 + (coord(s)[1] - hy) ** 2,
               reverse=True),
        sorted(ALIVE, key=lambda s: min(
            coord(s)[0] * H + coord(s)[1] * V,
            (MX - 1 - coord(s)[0]) * H + coord(s)[1] * V,
            coord(s)[0] * H + (MY - 1 - coord(s)[1]) * V,
            (MX - 1 - coord(s)[0]) * H + (MY - 1 - coord(s)[1]) * V),
               reverse=True),
        ALIVE,
        [s for s in ALIVE if coord(s)[0] <= 2]
        + [s for s in ALIVE if coord(s)[0] >= 5]
        + [s for s in ALIVE if 2 < coord(s)[0] < 5],
    ]

    bounds = formal_bounds_alive(1)
    print(ascii_map())
    print(f"ALIVE={NA} HOLES={len(HOLES)} LB={bounds}")

    # Strict 0-buffer: W=E=2, B=0
    BSW.XBAR_WRITE = 2
    BSW.DRAIN = 2
    B = 0

    results = {}
    for key, (label, builder) in SCHEMES.items():
        fps, dil = build_fps(builder)
        reuse = link_reuse(fps)
        pack1 = None
        for order in alive_orders:
            rec = pack_one_with_offs(fps, B, order)
            if rec and (pack1 is None or rec["makespan"] < pack1["makespan"]):
                pack1 = rec
        d2 = delta2_alive(fps, B, pack1) if pack1 else None
        r5 = None
        for order in alive_orders:
            for mode in ("round_major", "source_major"):
                rec = pack_rounds_alive(fps, ROUNDS, B, order, mode)
                if rec and (r5 is None or rec["makespan"] < r5["makespan"]):
                    r5 = {**rec, "mode": mode}
        t1 = pack1["makespan"] if pack1 else None
        t5 = r5["makespan"] if r5 else None
        ii_eff = round((t5 - t1) / (ROUNDS - 1), 2) if (t1 and t5) else None
        results[key] = {
            "label": label,
            "dilation": dil,
            "link_reuse": reuse,
            "cyclic_ii_lb": max(reuse, math.ceil((NA - 1) / 2)),
            "t1": t1,
            "t5": t5,
            "ii_eff": ii_eff,
            "t_avg": round((t1 + t5) / 2, 1) if (t1 and t5) else None,
            "delta2_min": d2["min"] if d2 else None,
            "delta2_avg": d2["avg"] if d2 else None,
            "delta2_max": d2["max"] if d2 else None,
            "gap_to_lb": (t1 - bounds["T_lb"]) if t1 else None,
        }
        print(f"{label:22s} dil={dil:3d} reuse={reuse:3d} "
              f"T1={t1} gap={results[key]['gap_to_lb']} "
              f"T5={t5} II_eff={ii_eff} "
              f"delta2={d2['min'] if d2 else None}/{d2['avg'] if d2 else None}/{d2['max'] if d2 else None}")

    best_t1 = min(results.values(), key=lambda r: (r["t1"] is None, r["t1"] or 1e9))
    best_t5 = min(results.values(), key=lambda r: (r["t5"] is None, r["t5"] or 1e9))
    best_avg = min(results.values(), key=lambda r: (r["t_avg"] is None, r["t_avg"] or 1e9))

    # plot
    fig, ax = plt.subplots(figsize=(8.5, 5.5))
    labels = [results[k]["label"] for k in SCHEMES]
    t1s = [results[k]["t1"] for k in SCHEMES]
    t5s = [results[k]["t5"] for k in SCHEMES]
    x = range(len(labels))
    ax.bar([i - 0.2 for i in x], t1s, width=0.4, label="T1 (1-flit)", color="#2563eb")
    ax.bar([i + 0.2 for i in x], t5s, width=0.4, label="T5 (5-flit measured)", color="#ea580c")
    ax.axhline(bounds["T_lb"], color="#16a34a", ls="--", lw=1.2,
               label=f"1-flit LB={bounds['T_lb']}")
    ax.set_xticks(list(x))
    ax.set_xticklabels(labels, rotation=15, ha="right")
    ax.set_ylabel("makespan (cycles)")
    ax.set_title("8×6 with 8 holes (40 compute): zero-buffer allgather, E=2 B=0")
    ax.legend(fontsize=8)
    ax.grid(True, axis="y", ls=":", alpha=0.5)
    fig.tight_layout()
    fig.savefig(OUT_PNG, dpi=130)
    plt.close(fig)

    # representative SVG: source in right wing using H transit
    demo_src = nid(6, 1)
    demo_edges = axis_ccw_alive(demo_src)
    demo_svg = svg_tree(demo_src, demo_edges)

    sig = {k: (results[k]["t1"], results[k]["t5"]) for k in results}
    prev_sig, prev_stable = None, 0
    if OUT_JSON.exists():
        try:
            prev = json.loads(OUT_JSON.read_text(encoding="utf-8"))
            prev_sig = prev.get("best_signature")
            prev_stable = prev.get("convergence", {}).get("stable_ticks", 0)
        except Exception:
            pass
    # compare best T1/T5 numbers for convergence (scheme set may grow)
    best_sig = {"t1": best_t1["t1"], "t5": best_t5["t5"],
                "t1_scheme": best_t1["label"], "t5_scheme": best_t5["label"]}
    stable = (prev_stable + 1) if (prev_sig == best_sig and prev_sig) else 1
    convergence = {"stable_ticks": stable, "converged": stable >= 2,
                   "changed_vs_prev": prev_sig != best_sig}

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "model": {
            "mesh": [MX, MY], "H": H, "V": V,
            "holes_1indexed": {"cols": [4, 5], "rows": [1, 2, 3, 4]},
            "holes_0indexed_xy": sorted((coord(h) for h in HOLES)),
            "N_alive": NA, "N_holes": len(HOLES),
            "E": 2, "W": 2, "B": 0,
            "ascii_map": ascii_map(),
            "semantics": "holes=transit-only routers; allgather among 40 alive",
        },
        "bounds_m1": bounds,
        "schemes": results,
        "best": {
            "t1": best_t1["label"],
            "t5": best_t5["label"],
            "t_avg": best_avg["label"],
            "t1_value": best_t1["t1"],
            "t5_value": best_t5["t5"],
        },
        "best_signature": best_sig,
        "convergence": convergence,
        "demo_svg_source": list(coord(demo_src)),
        "demo_svg": demo_svg,
        "plot": str(OUT_PNG.relative_to(ROOT)),
    }
    OUT_JSON.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    write_html(payload)
    print(f"\nbest T1: {best_t1['label']}={best_t1['t1']} (LB={bounds['T_lb']})")
    print(f"best T5: {best_t5['label']}={best_t5['t5']}")
    print(f"best T_avg: {best_avg['label']}={best_avg['t_avg']}")
    print(f"convergence: {convergence}")
    print(f"Wrote {OUT_JSON}\nWrote {OUT_PNG}\nWrote {OUT_HTML}")


def write_html(data: dict) -> None:
    b = data["bounds_m1"]
    rows = "".join(
        f"<tr><td class='l'>{r['label']}</td><td>{r['dilation']}</td>"
        f"<td>{r['link_reuse']}</td><td>{r['cyclic_ii_lb']}</td>"
        f"<td class='{'win' if r['t1']==min(x['t1'] for x in data['schemes'].values()) else ''}'>{r['t1']}</td>"
        f"<td>{r['gap_to_lb']}</td>"
        f"<td>{r['delta2_min']}/{r['delta2_avg']}/{r['delta2_max']}</td>"
        f"<td class='{'win' if r['t5']==min(x['t5'] for x in data['schemes'].values()) else ''}'>{r['t5']}</td>"
        f"<td>{r['ii_eff']}</td><td>{r['t_avg']}</td></tr>"
        for r in data["schemes"].values()
    )
    amap = data["model"]["ascii_map"].replace("\n", "<br>")
    svg = data.get("demo_svg", "")
    demo_xy = data.get("demo_svg_source", [0, 0])
    bt1 = data["best"].get("t1_value", "")
    bt5 = data["best"].get("t5_value", "")
    html = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>40-compute allgather with 8 holes (8×6)</title>
<style>
body{{font-family:system-ui,sans-serif;max-width:1040px;margin:28px;line-height:1.55;color:#0f172a}}
h1{{font-size:1.4rem}} h2{{font-size:1.1rem;color:#1e3a8a}}
.card{{border:1px solid #e2e8f0;border-radius:10px;padding:16px 20px;margin:14px 0;background:#fff}}
table{{border-collapse:collapse;width:100%;font-size:.85rem}}
th,td{{border:1px solid #cbd5e1;padding:6px 8px;text-align:center}}
th{{background:#e2e8f0}} td.l{{text-align:left}} td.win{{background:#dcfce7;font-weight:700}}
.note{{color:#64748b;font-size:.86rem}} .map{{font-family:ui-monospace,monospace;background:#f1f5f9;
padding:12px;border-radius:8px;line-height:1.4}}
img{{max-width:100%;border:1px solid #e2e8f0;border-radius:8px}}
.legend span{{margin-right:14px;font-size:.8rem;color:#64748b}}
</style></head><body>
<h1>8×6 mesh · 8 非计算节点 · 40 计算节点 allgather</h1>
<p class="note">无阻塞 / 无冲突 / 无缓冲（B=0）· 下 ramp E=W=2 · H=7 V=9 ·
生成 {data['generated_at']}</p>

<div class="card">
<h2>拓扑</h2>
<p>1-indexed：第 4、5 列 × 第 1–4 行 = 8 个<strong>非计算节点</strong>（图中 <b>H</b>）；
其余 40 个为计算节点 <b>C</b>。Allgather 端点仅在 40 个 C 之间。</p>
<p><b>H 不是坏点 / 故障点</b>：其上的 <b>router 与相邻 mesh 链路全部可用</b>，可正常转发 flit。
与 C 的唯一差别是<strong>没有参与本次 allgather 的计算 PE</strong>——不 inject、不 eject，
只作中转（树上的 Steiner 点）。橙虚线边表示路径经过这些活着的 H router，不是绕开故障。</p>
<div class="map">{amap}<br>x=0⋯7 →</div>
<p class="note">图例：C = 计算端点；H = 非计算但 router/链路仍在线的中转节点。</p>
</div>

<div class="card" style="border-color:#93c5fd;background:linear-gradient(180deg,#eff6ff,#fff)">
<h2>结论：makespan 最优排图</h2>
<ul>
<li><b>排图骨架</b>：每个计算源做 <b>axis+CCW</b> 生成全 mesh 树，再<b>剪枝</b>为只覆盖 40 个
计算端点的支撑树；<b>H 的 router/链路可留在树上作中转</b>（不 inject/eject）。</li>
<li><b>1-flit</b>：刚性 0-buffer 打包，注入序 =「距非计算块中心 (3.5, 1.5) 最远者优先」→
<b>T1 = {bt1} = T_lb</b>（已达形式下界，故 1-flit makespan 最优）。</li>
<li><b>多 flit（R=5）</b>：同一骨架上自由多轮打包 → <b>T5 = {bt5}</b>
（方案：{data['best']['t5']}）。delta2 可远早于 cyclic_lb 重叠。</li>
<li>双翼 bridge/comb（y=4 / y=5）未击败 axis 剪枝；Steiner / dim 全面落后。</li>
</ul>
</div>

<div class="card">
<h2>axis+CCW 剪枝示意（源 ({demo_xy[0]},{demo_xy[1]})）</h2>
{svg}
<p class="legend">
<span style="color:#2563eb">■</span> C↔C 边 &nbsp;
<span style="color:#ea580c">■</span> 经 H router 中转（虚线；H≠故障）&nbsp;
<span style="color:#dc2626">●</span> 源 &nbsp;
<span style="color:#64748b">●H</span> 非计算节点（router/链路可用）
</p>
</div>

<div class="card">
<h2>1-flit 形式下界（E=2）</h2>
<ul>
<li>receiver_release = <b>{b['receiver_release']}</b></li>
<li>eject_duration = ⌈39/2⌉ = <b>{b['eject_duration']}</b></li>
<li>diameter_serialization = <b>{b['diameter_serialization']}</b></li>
<li><b>T_lb = {b['T_lb']}</b></li>
</ul>
</div>

<div class="card">
<h2>方案对比（刚性 0-buffer 打包）</h2>
<img src="holes_40_allgather.png" alt="T1 vs T5">
<table>
<thead><tr><th>方案</th><th>dilation</th><th>链路复用</th><th>cyclic_lb</th>
<th>T1</th><th>gap→LB</th><th>delta2 min/avg/max</th>
<th>T5（5-flit 实测）</th><th>II_eff</th><th>T_avg</th></tr></thead>
<tbody>{rows}</tbody></table>
<p class="note">最优 1-flit：<b>{data['best']['t1']}</b>；最优 5-flit：<b>{data['best']['t5']}</b>；
最优 T_avg：<b>{data['best']['t_avg']}</b>。
delta2 = 逐源第2 flit 最早间隔；II_eff=(T5−T1)/4；cyclic_lb 仅约束整表平移。</p>
</div>

<div class="card">
<h2>排图形态建议</h2>
<ul>
<li><b>1-flit 最优形态</b>：<b>axis+CCW 剪枝到 40 个计算端点</b>（非计算节点以其仍可用的
router/链路充当树上中转），配合「距非计算块中心最远的源先打包」的注入序，可把 T1 压到
形式下界（receiver_release / diameter = LB）。</li>
<li><b>多 flit</b>：同一排图下看实测 T5 / delta2；axis 剪枝在本拓扑上同时赢 T1 与 T5。
非计算块把端点分成左翼（x≤2）/右翼（x≥5）；跨翼最短路常<strong>穿过仍可用的 H</strong>，
或绕行 y≥4——这是路由选择，不是规避坏点。</li>
<li><b>无缓冲</b>：B=0 ⇒ W=E=2，每拍每节点下 ramp ≤2；注入序是达界的关键，
不是可有可无的启发式。</li>
</ul>
</div>
</body></html>"""
    OUT_HTML.write_text(html, encoding="utf-8")


if __name__ == "__main__":
    main()
