#!/usr/bin/env python3
"""Path construction for the iSLIP-2D mesh variant (definition D-M).

Three things live here, all of them prerequisites for the D-M scheduler:

1. ROMM waypoints inside the minimal bounding rectangle.
   A path is built as XY(s -> w) ++ XY(w -> d) with w in the rectangle spanned
   by s and d. Because w is inside the rectangle, both coordinates advance
   monotonically, so the path uses exactly |dx| horizontal and |dy| vertical
   hops -- the SAME counts as plain XY.

   That is the *latency invariance* property, and it is what makes strict
   in-order delivery survive path switching on a mesh: with H/V wire delays and
   a zero-slack (bufferless) reservation, wire delay is dx*H + dy*V for every
   waypoint choice, so two grants of one VOQ cannot reorder even if they take
   different routes. `check_latency_invariance` asserts it exhaustively.
   (The ring has no such property -- see rg_ring_topo, clause R5.)

2. Static balanced waypoint assignment (greedy + rip-up), which is the
   `romm_static` path mode: one waypoint fixed per (s,d) offline, chosen to
   flatten max_e load(e).

3. The cut bound, which is the lower bound on LDPS rounds for a mesh and the
   decision rule for whether ROMM can help at all:

       ROMM is worth its cost  <=>  max_e load(e) under XY  >  cut_bound

   If XY already sits ON the cut bound, no routing can do better: the bound is
   a property of the traffic and the bisection, not of the route choice. This
   is why all-to-all gets exactly zero benefit from ROMM (96 = 576/6 = bound)
   while transpose/hotspot get 25-45%.
"""

from __future__ import annotations

import math
import random
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

from rg_topo import MX, MY, Topology, coord, nid

Pair = tuple[int, int]
Edge = tuple[int, int]

PathMode = ("xy", "romm_static", "romm_dyn")


# ---------------------------------------------------------------------------
# 1. Rectangle enumeration + two-segment construction
# ---------------------------------------------------------------------------

def rect_waypoints(s: int, d: int, mx: int = MX) -> list[int]:
    """Every node in the minimal bounding rectangle of (s, d).

    Size = (|dx|+1)*(|dy|+1). Contains s (=> plain XY) and (s.x, d.y)
    (=> plain YX), so the classic DOR routes are both members.
    """
    sx, sy = coord(s, mx)
    dx, dy = coord(d, mx)
    return [nid(x, y, mx)
            for y in range(min(sy, dy), max(sy, dy) + 1)
            for x in range(min(sx, dx), max(sx, dx) + 1)]


def xy_segment(a: int, b: int, mx: int = MX) -> list[int]:
    """X-first dimension-order node list from a to b (mesh, no wrap)."""
    ax, ay = coord(a, mx)
    bx, by = coord(b, mx)
    out = [a]
    x, y = ax, ay
    step = 1 if bx > ax else -1
    while x != bx:
        x += step
        out.append(nid(x, y, mx))
    step = 1 if by > ay else -1
    while y != by:
        y += step
        out.append(nid(x, y, mx))
    return out


def romm_path(s: int, d: int, w: int, mx: int = MX) -> list[int]:
    """XY(s->w) ++ XY(w->d). Minimal iff w lies in the (s,d) rectangle."""
    p1 = xy_segment(s, w, mx)
    p2 = xy_segment(w, d, mx)
    return p1 + p2[1:]


def xy_waypoint(s: int, d: int, mx: int = MX) -> int:
    """The waypoint that reproduces plain XY: turn at (d.x, s.y)."""
    dx, _ = coord(d, mx)
    _, sy = coord(s, mx)
    return nid(dx, sy, mx)


def yx_waypoint(s: int, d: int, mx: int = MX) -> int:
    """The waypoint that reproduces plain YX: turn at (s.x, d.y)."""
    sx, _ = coord(s, mx)
    _, dy = coord(d, mx)
    return nid(sx, dy, mx)


def path_links(path: Sequence[int]) -> list[Edge]:
    return [(path[i], path[i + 1]) for i in range(len(path) - 1)]


def path_hops(path: Sequence[int], mx: int = MX) -> tuple[int, int]:
    """(horizontal hops, vertical hops) actually taken."""
    hx = hy = 0
    for i in range(len(path) - 1):
        ax, ay = coord(path[i], mx)
        bx, by = coord(path[i + 1], mx)
        if ay == by:
            hx += 1
        else:
            hy += 1
    return hx, hy


# ---------------------------------------------------------------------------
# 2. Latency invariance (the ordering guarantee for D-M)
# ---------------------------------------------------------------------------

def check_latency_invariance(topo: Topology, *, sample: int = 0,
                             seed: int = 0) -> dict[str, Any]:
    """Every rectangle waypoint must give identical (hx, hy) and wire delay.

    sample=0 checks ALL waypoints of ALL pairs (48*47 pairs, up to 48
    waypoints each). sample=k checks k random waypoints per pair.
    """
    rng = random.Random(seed)
    n_checked = 0
    mismatch_hops = 0
    mismatch_wire = 0
    examples: list[dict[str, Any]] = []
    for s in range(topo.n):
        for d in range(topo.n):
            if s == d:
                continue
            sx, sy = coord(s)
            dx, dy = coord(d)
            want_hx, want_hy = abs(dx - sx), abs(dy - sy)
            want_wire = want_hx * topo.H + want_hy * topo.V
            ws = rect_waypoints(s, d)
            if sample and len(ws) > sample:
                ws = rng.sample(ws, sample)
            for w in ws:
                p = romm_path(s, d, w)
                hx, hy = path_hops(p)
                wire = topo.path_wire_delay(p)
                n_checked += 1
                if (hx, hy) != (want_hx, want_hy):
                    mismatch_hops += 1
                    if len(examples) < 5:
                        examples.append({"s": s, "d": d, "w": w,
                                         "got": [hx, hy],
                                         "want": [want_hx, want_hy]})
                if wire != want_wire:
                    mismatch_wire += 1
    return {
        "n_checked": n_checked,
        "mismatch_hops": mismatch_hops,
        "mismatch_wire": mismatch_wire,
        "invariant": mismatch_hops == 0 and mismatch_wire == 0,
        "examples": examples,
    }


# ---------------------------------------------------------------------------
# 3. Loads, cut bound
# ---------------------------------------------------------------------------

def load_of(paths: dict[Pair, list[int]]) -> dict[Edge, int]:
    load: dict[Edge, int] = defaultdict(int)
    for p in paths.values():
        for e in path_links(p):
            load[e] += 1
    return load


def max_load(paths: dict[Pair, list[int]]) -> int:
    load = load_of(paths)
    return max(load.values()) if load else 0


def cut_bound(pairs: Iterable[Pair], mx: int = MX, my: int = MY
              ) -> dict[str, Any]:
    """Max over all straight cuts of ceil(crossing flows / crossing links).

    A vertical cut at column c is crossed by `my` same-direction links; a
    horizontal cut at row r by `mx`. Any route set must put every crossing
    flow on one of those links, so this is a routing-independent lower bound
    on max_e load(e) and hence on the number of LDPS rounds.
    """
    pl = list(pairs)
    best = 0
    witness: dict[str, Any] | None = None
    details: list[dict[str, Any]] = []
    for c in range(1, mx):
        e_cnt = sum(1 for s, d in pl
                    if coord(s, mx)[0] < c <= coord(d, mx)[0])
        w_cnt = sum(1 for s, d in pl
                    if coord(d, mx)[0] < c <= coord(s, mx)[0])
        for cnt, direction in ((e_cnt, "E"), (w_cnt, "W")):
            lb = math.ceil(cnt / my) if cnt else 0
            details.append({"cut": f"x={c}", "dir": direction, "flows": cnt,
                            "links": my, "lb": lb})
            if lb > best:
                best = lb
                witness = details[-1]
    for r in range(1, my):
        n_cnt = sum(1 for s, d in pl
                    if coord(s, mx)[1] < r <= coord(d, mx)[1])
        s_cnt = sum(1 for s, d in pl
                    if coord(d, mx)[1] < r <= coord(s, mx)[1])
        for cnt, direction in ((n_cnt, "N"), (s_cnt, "S")):
            lb = math.ceil(cnt / mx) if cnt else 0
            details.append({"cut": f"y={r}", "dir": direction, "flows": cnt,
                            "links": mx, "lb": lb})
            if lb > best:
                best = lb
                witness = details[-1]
    return {"cut_bound": best, "witness": witness,
            "n_pairs": len(pl), "details": details}


# ---------------------------------------------------------------------------
# 4. Static balanced waypoint assignment (greedy + rip-up)
# ---------------------------------------------------------------------------

@dataclass
class WaypointPlan:
    waypoints: dict[Pair, int]
    paths: dict[Pair, list[int]] = field(default_factory=dict)
    max_load: int = 0
    cut_bound: int = 0
    sweeps_run: int = 0
    trace: list[int] = field(default_factory=list)

    @property
    def at_bound(self) -> bool:
        return self.cut_bound > 0 and self.max_load <= self.cut_bound

    def summary(self) -> dict[str, Any]:
        return {"n_pairs": len(self.waypoints), "max_load": self.max_load,
                "cut_bound": self.cut_bound, "at_bound": self.at_bound,
                "sweeps": self.sweeps_run, "trace": self.trace}


def xy_plan(pairs: Iterable[Pair], mx: int = MX, my: int = MY) -> WaypointPlan:
    pl = list(pairs)
    wp = {(s, d): xy_waypoint(s, d, mx) for s, d in pl}
    paths = {k: romm_path(k[0], k[1], w, mx) for k, w in wp.items()}
    return WaypointPlan(waypoints=wp, paths=paths, max_load=max_load(paths),
                        cut_bound=cut_bound(pl, mx, my)["cut_bound"])


def balanced_plan(pairs: Iterable[Pair], *, mx: int = MX, my: int = MY,
                  sweeps: int = 8, seed: int = 0,
                  start: str = "xy") -> WaypointPlan:
    """Choose one waypoint per pair to minimize max_e load(e).

    Greedy descent with rip-up: each sweep removes a pair's current path,
    re-scores every rectangle waypoint by (resulting peak on its own links,
    then total load) and re-inserts the best. Ties break on the incumbent so a
    sweep never churns without cause. Stops early once max_load reaches the
    cut bound, since no assignment can beat it.
    """
    pl = list(pairs)
    rng = random.Random(seed)
    cb = cut_bound(pl, mx, my)["cut_bound"]

    if start == "xy":
        wp = {(s, d): xy_waypoint(s, d, mx) for s, d in pl}
    else:
        wp = {(s, d): rng.choice(rect_waypoints(s, d, mx)) for s, d in pl}
    paths = {k: romm_path(k[0], k[1], w, mx) for k, w in wp.items()}
    load = load_of(paths)
    trace = [max(load.values()) if load else 0]

    order = list(wp.keys())
    done = 0
    for sweep in range(max(0, sweeps)):
        done = sweep + 1
        # Rip up the most congested pairs first.
        order.sort(key=lambda k: -max((load[e] for e in path_links(paths[k])),
                                      default=0))
        moved = 0
        for k in order:
            s, d = k
            for e in path_links(paths[k]):
                load[e] -= 1
            best_w = wp[k]
            best_key: tuple[int, int, int] | None = None
            for w in rect_waypoints(s, d, mx):
                links = path_links(romm_path(s, d, w, mx))
                peak = max((load[e] for e in links), default=0)
                total = sum(load[e] for e in links)
                key = (peak, total, 0 if w == wp[k] else 1)
                if best_key is None or key < best_key:
                    best_key, best_w = key, w
            if best_w != wp[k]:
                moved += 1
                wp[k] = best_w
                paths[k] = romm_path(s, d, best_w, mx)
            for e in path_links(paths[k]):
                load[e] += 1
        peak = max(load.values()) if load else 0
        trace.append(peak)
        if moved == 0 or (cb and peak <= cb):
            break

    return WaypointPlan(waypoints=wp, paths=paths,
                        max_load=max(load.values()) if load else 0,
                        cut_bound=cb, sweeps_run=done, trace=trace)


def random_plan(pairs: Iterable[Pair], *, mx: int = MX, my: int = MY,
                seed: int = 0) -> WaypointPlan:
    """`romm_dyn` in its purest form: an independent uniform waypoint draw.

    Kept as the control for `balanced_plan` -- classic ROMM randomizes, and on
    a load-balanced pattern that is strictly worse than XY because it converts
    a deterministic peak into a Poisson one.
    """
    rng = random.Random(seed)
    pl = list(pairs)
    wp = {(s, d): rng.choice(rect_waypoints(s, d, mx)) for s, d in pl}
    paths = {k: romm_path(k[0], k[1], w, mx) for k, w in wp.items()}
    return WaypointPlan(waypoints=wp, paths=paths, max_load=max_load(paths),
                        cut_bound=cut_bound(pl, mx, my)["cut_bound"])


def build_plan(pairs: Iterable[Pair], mode: str, *, mx: int = MX,
               my: int = MY, seed: int = 0, sweeps: int = 8) -> WaypointPlan:
    if mode == "xy":
        return xy_plan(pairs, mx, my)
    if mode == "romm_static":
        return balanced_plan(pairs, mx=mx, my=my, sweeps=sweeps, seed=seed)
    if mode == "romm_dyn":
        return random_plan(pairs, mx=mx, my=my, seed=seed)
    raise ValueError(f"unknown path mode: {mode}")


def romm_worthwhile(pairs: Iterable[Pair], mx: int = MX, my: int = MY
                    ) -> dict[str, Any]:
    """The ROMM decision rule, evaluated for one traffic pattern."""
    pl = list(pairs)
    xy = xy_plan(pl, mx, my)
    return {
        "xy_max_load": xy.max_load,
        "cut_bound": xy.cut_bound,
        "slack": xy.max_load - xy.cut_bound,
        "worthwhile": xy.max_load > xy.cut_bound,
    }


# ---------------------------------------------------------------------------
# 5. Applying a plan to a Collective (downstream code unchanged)
# ---------------------------------------------------------------------------

def apply_plan(col, plan: WaypointPlan) -> int:
    """Overwrite unicast Flow.paths in place from a waypoint plan.

    Only unicast flows are touched; multicast trees keep DOR. Returns the
    number of flows rerouted. `build_footprint` / `link_set` read Flow.paths,
    so nothing downstream needs to know that the route is not XY.
    """
    n = 0
    for f in col.flows:
        if f.kind != "unicast":
            continue
        d = f.dsts[0]
        key = (f.src, d)
        if key in plan.paths:
            f.paths[d] = list(plan.paths[key])
            n += 1
    return n


def pairs_of(col) -> list[Pair]:
    """Unicast (src, dst) pairs of a collective, deduplicated."""
    seen: set[Pair] = set()
    out: list[Pair] = []
    for f in col.flows:
        if f.kind != "unicast":
            continue
        k = (f.src, f.dsts[0])
        if k not in seen:
            seen.add(k)
            out.append(k)
    return out


if __name__ == "__main__":
    import json

    topo = Topology("mesh")
    print("--- latency invariance (all pairs, all waypoints) ---")
    inv = check_latency_invariance(topo)
    print(json.dumps(inv, indent=2))

    a2a = [(s, d) for s in range(topo.n) for d in range(topo.n) if s != d]
    print("\n--- alltoall cut bound ---")
    cb = cut_bound(a2a)
    print(json.dumps({k: cb[k] for k in ("cut_bound", "witness", "n_pairs")},
                     indent=2))

    print("\n--- plans on alltoall ---")
    for mode in ("xy", "romm_static", "romm_dyn"):
        p = build_plan(a2a, mode)
        print(f"  {mode:12} {json.dumps(p.summary())}")
    print("\n--- ROMM decision rule ---")
    print(json.dumps(romm_worthwhile(a2a), indent=2))
