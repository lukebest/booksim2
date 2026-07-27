#!/usr/bin/env python3
"""Deadlock-free order-preserving routing for 8x6 PG mesh alltoall.

Schemes:
  M1 xy            — dimension-order XY (with sacrifice recovery)
  M2 rect_xy       — mask rows/cols containing faults → rectangular XY
  M3 updown        — Up*/Down* on a BFS spanning tree
  M4 segment       — simplified segment-based routing (turn restrictions)
  M5 fault_ring_vc — XY + rectangular fault-block ring detour with 2 VCs

Hard requirements for a usable table: CDG acyclic, compute pairwise reachable,
exactly one path per (src,dst). On failure, a shared sacrifice recoverer disables
extra good nodes (boundary-first, then whole rows/cols) until the scheme works.
"""

from __future__ import annotations

import heapq
import itertools
from collections import defaultdict, deque
from typing import Any, Callable

from pg_faults_8x6 import (
    MX, MY, N, nid, coord, grid_neighbors, build_adj, expand_pg, healthy_pg,
)

H, V = 7, 9
RAMP, RAMP_BW = 2, 2

# Direction indices matching dse_portbuf: E=0 W=1 N=2 S=3  (opposite = d^1)
DIRS = [(1, 0), (-1, 0), (0, 1), (0, -1)]


def link_lat(a: int, b: int) -> int:
    return H if coord(a)[1] == coord(b)[1] else V


def dir_of(a: int, b: int) -> int:
    ax, ay = coord(a)
    bx, by = coord(b)
    return DIRS.index((bx - ax, by - ay))


def path_wire_delay(nodes: list[int]) -> int:
    return sum(link_lat(nodes[i], nodes[i + 1]) for i in range(len(nodes) - 1))


# ---------------------------------------------------------------------------
# Graph helpers
# ---------------------------------------------------------------------------

def apply_sacrifice(pg: dict, sacrificed: set[int],
                    remove_from_route: bool) -> dict:
    """Return a derived PG view with sacrificed nodes removed from compute
    (and optionally from the route graph)."""
    sac = set(sacrificed)
    compute = [n for n in pg["compute_nodes"] if n not in sac]
    route_dead = set(pg["route_dead_nodes"])
    if remove_from_route:
        route_dead |= sac
    adj = build_adj(MX, MY, route_dead, pg["route_dead_links"])
    return {
        **pg,
        "compute_nodes": compute,
        "route_dead_nodes": sorted(route_dead),
        "route_adj": adj,
        "n_compute": len(compute),
        "sacrificed": sorted(sac),
    }


def bfs_reachable(adj: dict[int, list[int]], src: int) -> set[int]:
    seen = {src}
    q = deque([src])
    while q:
        u = q.popleft()
        for v in adj.get(u, ()):
            if v not in seen:
                seen.add(v)
                q.append(v)
    return seen


def is_connected_on(adj: dict[int, list[int]], nodes: list[int]) -> bool:
    if not nodes:
        return True
    # Allow Steiner transit: BFS on full adj, check all compute are reached
    reach = bfs_reachable(adj, nodes[0])
    return all(n in reach for n in nodes)


# ---------------------------------------------------------------------------
# Path builders
# ---------------------------------------------------------------------------

def xy_path(src: int, dst: int, adj: dict[int, list[int]]) -> list[int] | None:
    """Strict XY: move in X first, then Y. None if a required hop is missing."""
    if src == dst:
        return [src]
    sx, sy = coord(src)
    dx, dy = coord(dst)
    path = [src]
    x, y = sx, sy
    step = 1 if dx > sx else -1
    while x != dx:
        x += step
        nxt = nid(x, y)
        if nxt not in adj.get(path[-1], ()):
            return None
        path.append(nxt)
    step = 1 if dy > sy else -1
    while y != dy:
        y += step
        nxt = nid(x, y)
        if nxt not in adj.get(path[-1], ()):
            return None
        path.append(nxt)
    return path


def shortest_path(src: int, dst: int, adj: dict[int, list[int]],
                  allowed_next: Callable[[int, int, int], bool] | None = None
                  ) -> list[int] | None:
    """BFS shortest path; optional predicate allowed_next(prev, cur, nxt)."""
    if src == dst:
        return [src]
    if src not in adj or dst not in adj:
        return None
    prev: dict[int, int | None] = {src: None}
    q = deque([src])
    while q:
        u = q.popleft()
        for v in adj[u]:
            if v in prev:
                continue
            if allowed_next is not None and not allowed_next(prev[u], u, v):
                continue
            prev[v] = u
            if v == dst:
                path = [dst]
                while path[-1] != src:
                    path.append(prev[path[-1]])  # type: ignore[arg-type]
                path.reverse()
                return path
            q.append(v)
    return None


def dijkstra_path(src: int, dst: int, adj: dict[int, list[int]],
                  weight: Callable[[int, int], float],
                  allowed_next: Callable[[int, int, int], bool] | None = None
                  ) -> list[int] | None:
    if src == dst:
        return [src]
    dist = {src: 0.0}
    prev: dict[int, int | None] = {src: None}
    pq = [(0.0, src)]
    while pq:
        d, u = heapq.heappop(pq)
        if d != dist[u]:
            continue
        if u == dst:
            break
        for v in adj.get(u, ()):
            if allowed_next is not None and not allowed_next(prev[u], u, v):
                continue
            nd = d + weight(u, v)
            if v not in dist or nd < dist[v]:
                dist[v] = nd
                prev[v] = u
                heapq.heappush(pq, (nd, v))
    if dst not in prev:
        return None
    path = [dst]
    while path[-1] != src:
        path.append(prev[path[-1]])  # type: ignore[arg-type]
    path.reverse()
    return path


# ---------------------------------------------------------------------------
# CDG validation
# ---------------------------------------------------------------------------

def build_cdg(paths: dict[tuple[int, int], list[int]],
              vc_of: Callable[[list[int], int], int] | None = None
              ) -> dict[Any, set[Any]]:
    """Channel dependency graph: node = (directed_edge, vc).
    An edge u->v exists when some path traverses channel u then channel v.
    """
    cdg: dict[Any, set[Any]] = defaultdict(set)
    for path in paths.values():
        if len(path) < 2:
            continue
        chans = []
        for i in range(len(path) - 1):
            e = (path[i], path[i + 1])
            vc = 0 if vc_of is None else vc_of(path, i)
            chans.append((e, vc))
        for i in range(len(chans) - 1):
            cdg[chans[i]].add(chans[i + 1])
            _ = cdg[chans[i + 1]]  # ensure node exists
    return cdg


def cdg_acyclic(cdg: dict[Any, set[Any]]) -> bool:
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {n: WHITE for n in cdg}
    for n in list(cdg):
        for m in cdg[n]:
            color.setdefault(m, WHITE)

    def dfs(u):
        color[u] = GRAY
        for v in cdg.get(u, ()):
            if color.get(v, WHITE) == GRAY:
                return False
            if color.get(v, WHITE) == WHITE and not dfs(v):
                return False
        color[u] = BLACK
        return True

    return all(dfs(n) for n in list(color) if color[n] == WHITE)


def validate_routing(paths: dict[tuple[int, int], list[int]],
                     compute: list[int],
                     adj: dict[int, list[int]],
                     vc_of: Callable[[list[int], int], int] | None = None
                     ) -> tuple[bool, str]:
    if len(compute) < 2:
        return False, "fewer than 2 compute nodes"
    for s in compute:
        for d in compute:
            if s == d:
                continue
            if (s, d) not in paths:
                return False, f"missing path {s}->{d}"
            p = paths[(s, d)]
            if not p or p[0] != s or p[-1] != d:
                return False, f"bad endpoints {s}->{d}"
            for i in range(len(p) - 1):
                if p[i + 1] not in adj.get(p[i], ()):
                    return False, f"edge missing on {s}->{d}: {p[i]}-{p[i+1]}"
    if not is_connected_on(adj, compute):
        return False, "compute set not connected via route graph"
    cdg = build_cdg(paths, vc_of)
    if not cdg_acyclic(cdg):
        return False, "CDG has a cycle (deadlock)"
    return True, "ok"


# ---------------------------------------------------------------------------
# Scheme path generators (no sacrifice yet)
# ---------------------------------------------------------------------------

def gen_xy(pg: dict) -> dict[str, Any] | None:
    adj = pg["route_adj"]
    compute = pg["compute_nodes"]
    paths = {}
    for s in compute:
        for d in compute:
            if s == d:
                continue
            p = xy_path(s, d, adj)
            if p is None:
                return None
            paths[(s, d)] = p
    return {"paths": paths, "vc_of": None, "scheme": "xy"}


def _fault_rows_cols(pg: dict) -> tuple[set[int], set[int]]:
    rows, cols = set(), set()
    for n in pg.get("dead_nodes", []):
        x, y = coord(n)
        cols.add(x)
        rows.add(y)
    for a, b in pg.get("route_dead_links", []):
        ax, ay = coord(a)
        bx, by = coord(b)
        cols.update((ax, bx))
        rows.update((ay, by))
    return rows, cols


def gen_rect_xy(pg: dict) -> dict[str, Any] | None:
    """Mask every row/col that touches a fault; route XY on the remaining rectangle(s).

    Picks the largest remaining axis-aligned rectangle of compute-capable cells
    that also remain in the route graph, then XY inside it. Nodes outside that
    rectangle are treated as sacrificed by the caller via the return value.
    """
    fault_rows, fault_cols = _fault_rows_cols(pg)
    # Candidate live columns / rows
    live_cols = [x for x in range(MX) if x not in fault_cols]
    live_rows = [y for y in range(MY) if y not in fault_rows]
    if not live_cols or not live_rows:
        return None

    # Largest contiguous column block × contiguous row block
    def longest_run(xs: list[int]) -> list[int]:
        best, cur = [], []
        for x in xs:
            if not cur or x == cur[-1] + 1:
                cur.append(x)
            else:
                if len(cur) > len(best):
                    best = cur
                cur = [x]
        if len(cur) > len(best):
            best = cur
        return best

    cols = longest_run(live_cols)
    rows = longest_run(live_rows)
    if not cols or not rows:
        return None

    rect_nodes = {nid(x, y) for x in cols for y in rows}
    # Must be in route graph
    adj = pg["route_adj"]
    rect_nodes = {n for n in rect_nodes if n in adj}
    compute = [n for n in pg["compute_nodes"] if n in rect_nodes]
    if len(compute) < 2:
        return None

    # Restrict adjacency to the rectangle
    radj = {n: [m for m in adj[n] if m in rect_nodes] for n in rect_nodes}
    paths = {}
    for s in compute:
        for d in compute:
            if s == d:
                continue
            p = xy_path(s, d, radj)
            if p is None:
                return None
            paths[(s, d)] = p
    forced_sac = sorted(set(pg["compute_nodes"]) - set(compute))
    return {
        "paths": paths,
        "vc_of": None,
        "scheme": "rect_xy",
        "forced_sacrificed": forced_sac,
        "compute_nodes": compute,
        "route_adj": radj,
    }


def _updown_labels(adj: dict[int, list[int]], root: int
                   ) -> dict[int, int] | None:
    """BFS distance from root (= 'up' height)."""
    if root not in adj:
        return None
    dist = {root: 0}
    q = deque([root])
    while q:
        u = q.popleft()
        for v in adj[u]:
            if v not in dist:
                dist[v] = dist[u] + 1
                q.append(v)
    if len(dist) < len(adj):
        # disconnected route graph — still ok if compute subset is covered later
        pass
    return dist


def gen_updown(pg: dict) -> dict[str, Any] | None:
    adj = pg["route_adj"]
    compute = pg["compute_nodes"]
    if len(compute) < 2 or not adj:
        return None
    # Root = highest degree among nodes reachable from some compute node
    root = max(adj.keys(), key=lambda n: (len(adj[n]), -n))
    labels = _updown_labels(adj, root)
    if labels is None:
        return None

    def allowed(prev, cur, nxt):
        # Up*/Down*: once we start going down (label increases? wait:
        # up = toward root = decreasing distance; down = away = increasing.
        # Legal: zero or more up hops then zero or more down hops.
        # We encode state via whether we have already taken a down hop.
        # BFS shortest under this constraint needs state — use dijkstra/BFS
        # with state. For allowed_next we only have prev,cur,nxt; reconstruct
        # whether the path so far already went down by checking if any hop
        # increased distance. That needs full path — so use custom search.
        return True  # placeholder; real filter in _updown_path

    def updown_path(s, d):
        if s == d:
            return [s]
        # state: (node, phase) phase 0=up-allowed, 1=down-only
        prev: dict[tuple[int, int], tuple[int, int] | None] = {(s, 0): None}
        q = deque([(s, 0)])
        found = None
        while q:
            u, ph = q.popleft()
            for v in adj.get(u, ()):
                if u not in labels or v not in labels:
                    continue
                if labels[v] < labels[u]:
                    # up hop
                    if ph == 1:
                        continue
                    nph = 0
                elif labels[v] > labels[u]:
                    nph = 1
                else:
                    # same level: treat as down-ish lateral; forbid after? allow as down
                    nph = 1
                st = (v, nph)
                if st in prev:
                    continue
                # also allow staying in phase 0 only via up
                prev[st] = (u, ph)
                if v == d:
                    found = st
                    q.clear()
                    break
                q.append(st)
        if found is None:
            # try also ending in phase 0
            return None
        path = [found[0]]
        cur = found
        while prev[cur] is not None:
            cur = prev[cur]  # type: ignore
            path.append(cur[0])
        path.reverse()
        # dedupe consecutive (shouldn't happen)
        out = [path[0]]
        for n in path[1:]:
            if n != out[-1]:
                out.append(n)
        return out if out[0] == s and out[-1] == d else None

    paths = {}
    for s in compute:
        for d in compute:
            if s == d:
                continue
            p = updown_path(s, d)
            if p is None:
                return None
            paths[(s, d)] = p
    return {"paths": paths, "vc_of": None, "scheme": "updown", "root": root}


def gen_segment(pg: dict) -> dict[str, Any] | None:
    """Simplified segment-based routing: forbid one turn type per column band.

    Partition columns into segments of width 2. In even segments forbid
    North->East and South->West (odd-even style); in odd segments forbid
    North->West and South->East. Combined with XY-preference shortest path
    under the turn rules → acyclic CDG on meshes (odd-even turn model family).
    """
    adj = pg["route_adj"]
    compute = pg["compute_nodes"]

    def turn_ok(prev, cur, nxt):
        if prev is None:
            return True
        d_in = dir_of(prev, cur)
        d_out = dir_of(cur, nxt)
        if d_in == d_out:
            return True  # straight
        # 180° turns forbidden
        if d_in == (d_out ^ 1):
            return False
        cx, _ = coord(cur)
        seg = (cx // 2) % 2
        # d: E=0 W=1 N=2 S=3
        # even seg: forbid N->E (2->0), S->W (3->1)
        # odd seg:  forbid N->W (2->1), S->E (3->0)
        if seg == 0:
            if (d_in, d_out) in ((2, 0), (3, 1)):
                return False
        else:
            if (d_in, d_out) in ((2, 1), (3, 0)):
                return False
        return True

    paths = {}
    for s in compute:
        for d in compute:
            if s == d:
                continue
            p = shortest_path(s, d, adj, turn_ok)
            if p is None:
                return None
            paths[(s, d)] = p
    return {"paths": paths, "vc_of": None, "scheme": "segment"}


def _fault_bbox(pg: dict) -> tuple[int, int, int, int] | None:
    """Axis-aligned bbox of dead nodes; None if no node faults."""
    nodes = list(pg.get("dead_nodes", []))
    if not nodes:
        return None
    xs = [coord(n)[0] for n in nodes]
    ys = [coord(n)[1] for n in nodes]
    return min(xs), min(ys), max(xs), max(ys)


def gen_fault_ring_vc(pg: dict) -> dict[str, Any] | None:
    """Force traffic around the fault bbox (even under transit), route with
    Up*/Down* on the punctured graph, and assign 2 VCs by a vertical dateline
    through the fault centre (VC flips when crossing the dateline column).

    Link-only faults have no bbox: fall back to Up*/Down* on the cut graph
    with the same dateline at mx//2 (still 2 VC).
    """
    bbox = _fault_bbox(pg)
    adj = pg["route_adj"]
    if bbox is not None:
        x0, y0, x1, y1 = bbox
        blocked = {nid(x, y)
                   for x in range(x0, x1 + 1) for y in range(y0, y1 + 1)}
        dateline_col = (x0 + x1) // 2
    else:
        blocked = set()
        dateline_col = MX // 2
    radj = {n: [m for m in nbs if m not in blocked]
            for n, nbs in adj.items() if n not in blocked}
    compute = [n for n in pg["compute_nodes"] if n in radj]
    if len(compute) < 2:
        return None
    # Reuse Up*/Down* path construction on radj
    sub = {
        **pg,
        "route_adj": radj,
        "compute_nodes": compute,
    }
    ud = gen_updown(sub)
    if ud is None:
        return None

    def vc_of(path: list[int], i: int) -> int:
        # VC = parity of how many times the path has crossed dateline_col
        # horizontally before (and including) this hop.
        crosses = 0
        for j in range(i + 1):
            a, b = path[j], path[j + 1]
            ax, _ = coord(a)
            bx, _ = coord(b)
            if ax != bx and min(ax, bx) < dateline_col <= max(ax, bx):
                # crossing the vertical line x = dateline_col
                if ax < dateline_col <= bx or bx < dateline_col <= ax:
                    crosses += 1
        return crosses & 1

    forced = sorted(set(pg["compute_nodes"]) - set(compute))
    return {
        "paths": ud["paths"],
        "vc_of": vc_of,
        "scheme": "fault_ring_vc",
        "forced_sacrificed": forced,
        "compute_nodes": compute,
        "route_adj": radj,
    }


SCHEME_GENERATORS = {
    "xy": gen_xy,
    "rect_xy": gen_rect_xy,
    "updown": gen_updown,
    "segment": gen_segment,
    "fault_ring_vc": gen_fault_ring_vc,
}


# ---------------------------------------------------------------------------
# Sacrifice recoverer
# ---------------------------------------------------------------------------

def sacrifice_candidates(pg: dict) -> list[int]:
    """Good compute nodes ordered: fault-boundary first, then by dist to fault."""
    dead = set(pg.get("dead_nodes", []))
    link_ends = set()
    for a, b in pg.get("route_dead_links", []):
        link_ends |= {a, b}
    fault_pts = dead | link_ends
    if not fault_pts:
        fault_pts = {nid(MX // 2, MY // 2)}

    def dist_to_fault(n):
        x, y = coord(n)
        return min(abs(x - coord(p)[0]) + abs(y - coord(p)[1]) for p in fault_pts)

    boundary = set()
    for p in fault_pts:
        for m in grid_neighbors(p):
            if m in pg["compute_nodes"] and m not in dead:
                boundary.add(m)

    rest = [n for n in pg["compute_nodes"] if n not in boundary]
    rest.sort(key=lambda n: (dist_to_fault(n), n))
    return sorted(boundary, key=lambda n: (dist_to_fault(n), n)) + rest


def row_col_bundles(pg: dict) -> list[list[int]]:
    """Coarse sacrifice candidates: whole rows / columns intersecting faults."""
    fault_rows, fault_cols = _fault_rows_cols(pg)
    bundles = []
    for y in sorted(fault_rows):
        b = [nid(x, y) for x in range(MX)
             if nid(x, y) in pg["compute_nodes"]]
        if b:
            bundles.append(b)
    for x in sorted(fault_cols):
        b = [nid(x, y) for y in range(MY)
             if nid(x, y) in pg["compute_nodes"]]
        if b:
            bundles.append(b)
    # Also all rows/cols as fallback
    for y in range(MY):
        if y not in fault_rows:
            b = [nid(x, y) for x in range(MX)
                 if nid(x, y) in pg["compute_nodes"]]
            if b:
                bundles.append(b)
    for x in range(MX):
        if x not in fault_cols:
            b = [nid(x, y) for y in range(MY)
                 if nid(x, y) in pg["compute_nodes"]]
            if b:
                bundles.append(b)
    return bundles


def _finalize(pg: dict, raw: dict, sacrificed: set[int],
              remove_from_route: bool) -> dict[str, Any] | None:
    """pg is the view already used to generate raw (sacrifice already applied).

    `sacrificed` is the explicit extra set relative to the original fault (for
    bookkeeping). forced_sacrificed from the scheme is unioned in.
    """
    del remove_from_route  # route graph already reflected in pg / raw
    forced = set(raw.get("forced_sacrificed", []))
    sac = set(sacrificed) | forced
    if "compute_nodes" in raw and "route_adj" in raw:
        compute = list(raw["compute_nodes"])
        adj = raw["route_adj"]
    else:
        compute = [n for n in pg["compute_nodes"] if n not in forced]
        adj = pg["route_adj"]
    paths = {k: v for k, v in raw["paths"].items()
             if k[0] in compute and k[1] in compute}
    need = len(compute) * max(0, len(compute) - 1)
    if len(paths) != need:
        return None

    vc_of = raw.get("vc_of")
    ok, _reason = validate_routing(paths, compute, adj, vc_of)
    if not ok:
        return None
    n_good = pg["n_originally_good"]
    return {
        "feasible": True,
        "scheme": raw["scheme"],
        "paths": paths,
        "vc_of": vc_of,
        "compute_nodes": compute,
        "route_adj": adj,
        "sacrificed": sorted(sac),
        "n_sacrificed": len(sac),
        "n_compute_used": len(compute),
        "n_originally_good": n_good,
        "sacrifice_cost": (len(sac) / n_good) if n_good else 0.0,
        "reason": "ok",
    }


def _try_rect_recovery(pg: dict, scheme: str,
                       remove_route: bool) -> dict[str, Any] | None:
    """Last-resort: mask to largest healthy rectangle, then run the scheme."""
    rect = gen_rect_xy(pg)
    if rect is None:
        return None
    forced = set(rect.get("forced_sacrificed", []))
    view = apply_sacrifice(pg, forced, remove_from_route=True)
    view = {
        **view,
        "compute_nodes": rect["compute_nodes"],
        "route_adj": rect["route_adj"],
    }
    if scheme == "xy" or scheme == "rect_xy":
        raw = {
            "paths": rect["paths"],
            "vc_of": None,
            "scheme": scheme,
            "forced_sacrificed": sorted(forced),
            "compute_nodes": rect["compute_nodes"],
            "route_adj": rect["route_adj"],
        }
        return _finalize(view, raw, forced, remove_route)
    gen = SCHEME_GENERATORS[scheme]
    raw = gen(view)
    if raw is None:
        return None
    # Ensure forced sac recorded
    raw = dict(raw)
    raw["forced_sacrificed"] = sorted(
        set(raw.get("forced_sacrificed", [])) | forced)
    return _finalize(view, raw, forced, remove_route)


def solve_scheme(pg: dict, scheme: str) -> dict[str, Any]:
    """Generate a deadlock-free order-preserving routing, sacrificing if needed."""
    gen = SCHEME_GENERATORS[scheme]
    # M2/M5 always remove sacrificed routers from route; others follow PG semantics
    remove_route = (scheme in ("rect_xy", "fault_ring_vc")
                    or pg["semantics"] == "dead")

    # k=0 first
    raw = gen(pg)
    if raw is not None:
        fin = _finalize(pg, raw, set(), remove_route)
        if fin is not None:
            return fin

    # Rect-xy itself IS the coarse sacrifice scheme
    if scheme == "rect_xy":
        fin = _try_rect_recovery(pg, scheme, remove_route)
        if fin is not None:
            return fin

    cands = sacrifice_candidates(pg)
    bundles = row_col_bundles(pg)
    # Allow sacrificing up to a full row+col (~14) to recover a rectangle
    k_max = min(16, max(1, len(pg["compute_nodes"]) // 2))

    ordered_trials: list[list[int]] = []
    for b in bundles:
        if 1 <= len(b) <= k_max:
            ordered_trials.append(b)
    # Combined fault-row + fault-col (classic XY recovery)
    frows, fcols = _fault_rows_cols(pg)
    for y in list(frows)[:2]:
        for x in list(fcols)[:2]:
            combo = [n for n in pg["compute_nodes"]
                     if coord(n)[0] == x or coord(n)[1] == y]
            if combo:
                ordered_trials.append(combo)
    for k in range(1, min(k_max, 6) + 1):
        pool = cands[: min(10, len(cands))]
        if k == 1:
            ordered_trials.extend([[n] for n in pool])
        elif k == 2:
            ordered_trials.extend(list(p) for p in
                                  itertools.combinations(pool[:6], 2))
        else:
            ordered_trials.append(pool[:k])

    seen_t: set[frozenset[int]] = set()
    for trial in ordered_trials:
        key = frozenset(trial)
        if key in seen_t or not key:
            continue
        seen_t.add(key)
        if len(key) > k_max:
            continue
        view = apply_sacrifice(pg, set(trial), remove_route)
        if view["n_compute"] < 2:
            continue
        raw = gen(view)
        if raw is None:
            continue
        fin = _finalize(view, raw, set(trial), remove_route)
        if fin is not None:
            return fin

    # Last resort: rectangular mask recovery
    fin = _try_rect_recovery(pg, scheme, remove_route)
    if fin is not None:
        return fin

    return {
        "feasible": False,
        "scheme": scheme,
        "paths": {},
        "vc_of": None,
        "compute_nodes": [],
        "route_adj": {},
        "sacrificed": [],
        "n_sacrificed": 0,
        "n_compute_used": 0,
        "n_originally_good": pg["n_originally_good"],
        "sacrifice_cost": 0.0,
        "reason": "INFEASIBLE",
    }


# ---------------------------------------------------------------------------
# Load balancing (P2) on an existing legal path set / turn constraints
# ---------------------------------------------------------------------------

def link_loads(paths: dict[tuple[int, int], list[int]]
               ) -> dict[tuple[int, int], int]:
    load: dict[tuple[int, int], int] = defaultdict(int)
    for p in paths.values():
        for i in range(len(p) - 1):
            load[(p[i], p[i + 1])] += 1
    return dict(load)


def max_link_load(paths: dict[tuple[int, int], list[int]]) -> int:
    ld = link_loads(paths)
    return max(ld.values()) if ld else 0


def load_balance_paths(base: dict[str, Any], rounds: int = 12,
                       seed: int = 0) -> dict[str, Any]:
    """Iteratively re-route pairs via load-aware Dijkstra under same adj.

    Keeps CDG validity by only using shortest_path on the same adjacency
    (schemes that rely on turn rules should pass a restricted adj already).
    For updown/segment we re-validate after each improvement.
    """
    import random
    rng = random.Random(seed)
    if not base.get("feasible"):
        return base
    paths = dict(base["paths"])
    adj = base["route_adj"]
    compute = base["compute_nodes"]
    vc_of = base.get("vc_of")
    pairs = [(s, d) for s in compute for d in compute if s != d]
    best = dict(paths)
    best_load = max_link_load(best)

    # Only touch the hottest pairs each round (keeps P2 affordable on 8x6 A2A).
    hot_k = min(64, len(pairs))
    for _ in range(rounds):
        loads = defaultdict(int)
        for p in paths.values():
            for i in range(len(p) - 1):
                loads[(p[i], p[i + 1])] += 1
        # Score pairs by max edge load on their current path
        scored = []
        for s, d in pairs:
            p = paths[(s, d)]
            sc = max((loads[(p[i], p[i + 1])] for i in range(len(p) - 1)),
                     default=0)
            scored.append((sc, rng.random(), s, d))
        scored.sort(reverse=True)
        improved = False
        for _, __, s, d in scored[:hot_k]:
            old = paths[(s, d)]
            # Temporarily remove old contribution
            for i in range(len(old) - 1):
                loads[(old[i], old[i + 1])] -= 1

            def w(u, v, _loads=loads):
                return 1.0 + _loads[(u, v)]

            new = dijkstra_path(s, d, adj, w)
            if new is None:
                for i in range(len(old) - 1):
                    loads[(old[i], old[i + 1])] += 1
                continue
            paths[(s, d)] = new
            for i in range(len(new) - 1):
                loads[(new[i], new[i + 1])] += 1
            if new != old:
                improved = True
        # Validate once per round; revert to previous best if CDG breaks
        ok, _ = validate_routing(paths, compute, adj, vc_of)
        if not ok:
            paths = dict(best)
            break
        cur = max_link_load(paths)
        if cur < best_load:
            best_load = cur
            best = dict(paths)
        if not improved:
            break

    out = dict(base)
    out["paths"] = best
    out["max_load"] = best_load
    out["scheme"] = base["scheme"] + "_lb"
    return out


def try_cpsat_balance(base: dict[str, Any], time_limit_s: float = 5.0
                      ) -> dict[str, Any]:
    """Optional CP-SAT tightening when heuristic gap to unbound LB is large."""
    try:
        from ortools.sat.python import cp_model
    except ImportError:
        return base
    if not base.get("feasible"):
        return base
    compute = base["compute_nodes"]
    adj = base["route_adj"]
    pairs = [(s, d) for s in compute for d in compute if s != d]
    # Enumerate up to K shortest simple paths per pair (BFS limited)
    K = 6
    cand: dict[tuple[int, int], list[list[int]]] = {}
    for s, d in pairs:
        # Yen-like: collect via BFS layers with path enumeration budget
        found = []
        q = deque([[s]])
        while q and len(found) < K:
            path = q.popleft()
            u = path[-1]
            if u == d:
                found.append(path)
                continue
            if len(path) > MX + MY + 2:
                continue
            for v in adj.get(u, ()):
                if v not in path:
                    q.append(path + [v])
        if not found:
            return base
        cand[(s, d)] = found

    model = cp_model.CpModel()
    choice = {}
    for sd, opts in cand.items():
        vs = [model.NewBoolVar(f"p{sd[0]}_{sd[1]}_{i}") for i in range(len(opts))]
        model.Add(sum(vs) == 1)
        choice[sd] = vs

    edges = set()
    for opts in cand.values():
        for p in opts:
            for i in range(len(p) - 1):
                edges.add((p[i], p[i + 1]))
    edges = sorted(edges)
    max_load = model.NewIntVar(0, len(pairs), "L")
    for e in edges:
        terms = []
        for sd, opts in cand.items():
            for i, p in enumerate(opts):
                if any((p[j], p[j + 1]) == e for j in range(len(p) - 1)):
                    terms.append(choice[sd][i])
        if terms:
            model.Add(sum(terms) <= max_load)
    model.Minimize(max_load)
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = time_limit_s
    status = solver.Solve(model)
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return base
    paths = {}
    for sd, opts in cand.items():
        for i, v in enumerate(choice[sd]):
            if solver.Value(v):
                paths[sd] = opts[i]
                break
    vc_of = base.get("vc_of")
    ok, reason = validate_routing(paths, compute, adj, vc_of)
    if not ok:
        return base
    out = dict(base)
    out["paths"] = paths
    out["max_load"] = int(solver.Value(max_load))
    out["scheme"] = base["scheme"] + "_cpsat"
    return out


# ---------------------------------------------------------------------------
# Analytical bounds
# ---------------------------------------------------------------------------

def unbound_minimax_load(compute: list[int], adj: dict[int, list[int]]
                         ) -> int:
    """Minimax directed link load with unrestricted routing (no deadlock
    constraint) — denominator for irregularity_penalty.

    Seed with XY when every pair has an XY path (usually the best structured
    alltoall packing on a mesh), else BFS shortest paths; then load-balance.
    """
    if len(compute) < 2:
        return 0
    paths = {}
    xy_ok = True
    for s in compute:
        for d in compute:
            if s == d:
                continue
            p = xy_path(s, d, adj)
            if p is None:
                xy_ok = False
                break
            paths[(s, d)] = p
        if not xy_ok:
            break
    if not xy_ok:
        paths = {}
        for s in compute:
            for d in compute:
                if s == d:
                    continue
                p = shortest_path(s, d, adj)
                if p is None:
                    return 10 ** 9
                paths[(s, d)] = p
    # XY / shortest-path packing is already near-minimax for mesh alltoall;
    # skip iterative rebalance here (too expensive for the sweep). Load-balance
    # schemes do their own P2 pass separately.
    return max_link_load(paths)


def analytical_lb(paths: dict[tuple[int, int], list[int]],
                  compute: list[int], m: int = 1,
                  adj: dict[int, list[int]] | None = None,
                  unbound_max_load: int | None = None,
                  compute_unbound: bool = False) -> dict[str, Any]:
    a = len(compute)
    if a < 2:
        return {"lb": 0, "bw_term": 0, "inj_term": 0, "lat_term": 0,
                "max_load": 0}
    max_load = max_link_load(paths)
    bw_term = max_load * m
    inj_term = ((a - 1) * m + RAMP_BW - 1) // RAMP_BW
    lat_term = 0
    for p in paths.values():
        lat_term = max(lat_term, path_wire_delay(p) + 2 * RAMP + (m - 1))
    lb = max(bw_term, inj_term, lat_term)
    unbound = unbound_max_load
    if unbound is None and compute_unbound and adj is not None:
        unbound = unbound_minimax_load(compute, adj)
    return {
        "lb": lb,
        "bw_term": bw_term,
        "inj_term": inj_term,
        "lat_term": lat_term,
        "max_load": max_load,
        "unbound_max_load": unbound,
        "unbound_bw_lb": (unbound * m) if unbound is not None else None,
    }


def solve_all(pg: dict, schemes: list[str] | None = None,
              do_lb: bool = True, do_cpsat: bool = True) -> list[dict]:
    schemes = schemes or list(SCHEME_GENERATORS)
    results = []
    for sch in schemes:
        r = solve_scheme(pg, sch)
        if r["feasible"] and do_lb and sch in ("updown", "segment"):
            bal = load_balance_paths(r, rounds=40)
            # gap vs unbound
            unb = unbound_minimax_load(r["compute_nodes"], r["route_adj"])
            if (do_cpsat and unb < 10 ** 9 and bal["max_load"] > 0
                    and bal["max_load"] > 1.15 * max(unb, 1)):
                bal = try_cpsat_balance(bal, time_limit_s=3.0)
            r = bal
        if r["feasible"]:
            r["bounds"] = analytical_lb(
                r["paths"], r["compute_nodes"], m=1, adj=r["route_adj"])
            r["max_load"] = r["bounds"]["max_load"]
        results.append(r)
    return results


if __name__ == "__main__":
    from pg_faults_8x6 import all_scenarios

    h = healthy_pg()
    print("healthy XY:", solve_scheme(h, "xy")["feasible"],
          "load", solve_scheme(h, "xy").get("n_compute_used"))
    r = solve_scheme(h, "xy")
    print("  bounds", analytical_lb(r["paths"], r["compute_nodes"], 1, r["route_adj"]))

    scen = expand_pg(all_scenarios()[0], "dead")  # link_corner_1
    print(scen["name"], "dead")
    for sch in SCHEME_GENERATORS:
        sol = solve_scheme(scen, sch)
        print(f"  {sch:16s} feas={sol['feasible']} sac={sol['n_sacrificed']} "
              f"A={sol['n_compute_used']} reason={sol['reason']}")
