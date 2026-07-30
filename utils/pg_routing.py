#!/usr/bin/env python3
"""Deadlock-free order-preserving routing for 8x6 PG mesh alltoall.

Turn-restriction class (1 VC, deadlock freedom from forbidden turns):
  M1 xy            — dimension-order XY (with sacrifice recovery)
  M2 rect_xy       — mask rows/cols containing faults → rectangular XY
  M3 updown        — Up*/Down* on a BFS spanning tree
  M4 segment       — simplified segment-based routing (turn restrictions)

Turn-restriction (adaptive):
  M0s  super_turn     — Glass–Ni, escalate 1→2 VC then sac
  M0s1 super_turn_1vc — Glass–Ni hard-capped at 1 VC (sac, never dual)

VC-layering class (deadlock freedom from ordered channel classes):
  M5  fault_ring_vc   — true f-ring (Boppana–Chalasani), 4 VCs
  M5h fault_half_ring — XY + one-sided half-ring detour, 2 VCs (X/Y phase)
  M6  lash            — shortest paths packed into acyclic VC layers
  M6b lash_tor        — LASH with mid-path layer climb (TOR)
  M7  stripe_vc       — shortest/XY paths; VC += 1 at each vertical dateline
  M9  dual_updown     — VC0=Up*/Down*, VC1=Down*/Up*, pick shorter per pair
  M10 virtual_mesh    — logical XY on full mesh; physical detours; X/Y → 2 VCs

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

def _turn_bfs(src: int, dst: int, adj: dict[int, list[int]],
              turn_ok: Callable[[int, int], bool]) -> list[int] | None:
    """Shortest legal path under a turn model `turn_ok(d_in, d_out)`.

    Searches (node, incoming direction) states. `shortest_path(..., allowed_next)`
    dedupes by node, so the direction of the first arrival silently fixes which
    continuations stay legal and legal paths can be missed. Keying the frontier
    on the incoming direction makes an unreachable verdict mean genuinely
    unreachable under the turn rules.
    """
    if src == dst:
        return [src]
    if src not in adj or dst not in adj:
        return None
    start = (src, -1)  # -1 = injected, no incoming direction yet
    prev: dict[tuple[int, int], tuple[int, int] | None] = {start: None}
    q = deque([start])
    while q:
        u, din = q.popleft()
        for v in adj[u]:
            dout = dir_of(u, v)
            st = (v, dout)
            if st in prev:
                continue
            if din >= 0 and not turn_ok(din, dout):
                continue
            prev[st] = (u, din)
            if v == dst:
                path = [v]
                cur: tuple[int, int] | None = st
                while prev[cur] is not None:
                    cur = prev[cur]
                    path.append(cur[0])
                path.reverse()
                return path
            q.append(st)
    return None


def gen_east_first(pg: dict) -> dict[str, Any] | None:
    """M0: Glass-Ni east-first minimal turn model (mirror of west-first).

    Prohibits the two turns *into* east (N->E, S->E) plus all 180 turns. That
    removes one turn from each of the two abstract cycles of a 2D mesh, so the
    CDG is acyclic on the full mesh *and on every subgraph of it* — deleting
    links cannot create a turn. 1 VC.

    Consequence for routing: no turn leads back to east, so every eastward hop
    must precede the first N/S/W hop. A packet therefore walks east inside its
    source row and can never detour around a fault while doing so.

    Every XY path is legal here (E..E then N/S is E->N / E->S; W..W then N/S is
    W->N / W->S), so XY is preferred and the turn-aware BFS only kicks in where
    XY is blocked — same reachability as a pure BFS, far better link balance.
    """
    adj = pg["route_adj"]
    compute = pg["compute_nodes"]

    def turn_ok(d_in: int, d_out: int) -> bool:
        if d_in == d_out:
            return True  # straight
        if d_in == (d_out ^ 1):
            return False  # 180 turn
        # d: E=0 W=1 N=2 S=3 — forbid N->E and S->E
        return (d_in, d_out) not in ((2, 0), (3, 0))

    paths = {}
    for s in compute:
        for d in compute:
            if s == d:
                continue
            p = xy_path(s, d, adj) or _turn_bfs(s, d, adj, turn_ok)
            if p is None:
                return None
            paths[(s, d)] = p
    return {"paths": paths, "vc_of": None, "scheme": "east_first"}


# Glass–Ni minimal turn models: each bans two turns, one from each abstract
# cycle, plus all 180s. CDG acyclicity is constructive on every subgraph.
_TURN_MODELS: dict[str, frozenset[tuple[int, int]]] = {
    "east_first": frozenset(((2, 0), (3, 0))),   # ban N→E, S→E
    "west_first": frozenset(((2, 1), (3, 1))),   # ban N→W, S→W
    "north_last": frozenset(((0, 2), (1, 2))),   # ban E→N, W→N
    "south_last": frozenset(((0, 3), (1, 3))),   # ban E→S, W→S
}


def _turn_ok_factory(banned: frozenset[tuple[int, int]]):
    def turn_ok(d_in: int, d_out: int) -> bool:
        if d_in == d_out:
            return True
        if d_in == (d_out ^ 1):
            return False
        return (d_in, d_out) not in banned
    return turn_ok


def _path_obeys_turns(path: list[int], turn_ok) -> bool:
    if len(path) < 3:
        return True
    for i in range(len(path) - 2):
        if not turn_ok(dir_of(path[i], path[i + 1]),
                       dir_of(path[i + 1], path[i + 2])):
            return False
    return True


def _paths_under_turn(adj, compute, banned) -> dict[tuple[int, int], list[int]] | None:
    turn_ok = _turn_ok_factory(banned)
    # XY is always legal under east/west-first, but E→N / W→N break north-last
    # (and E→S / W→S break south-last) — only accept XY when it obeys the model.
    paths = {}
    for s in compute:
        for d in compute:
            if s == d:
                continue
            p = xy_path(s, d, adj)
            if p is None or not _path_obeys_turns(p, turn_ok):
                p = _turn_bfs(s, d, adj, turn_ok)
            if p is None:
                return None
            paths[(s, d)] = p
    return paths


def _pick_turn_path(s: int, d: int, adj: dict, turn_ok) -> list[int] | None:
    p = xy_path(s, d, adj)
    if p is not None and _path_obeys_turns(p, turn_ok):
        return p
    return _turn_bfs(s, d, adj, turn_ok)


def _assign_turn_layers(
    adj: dict, compute: list[int], model_names: list[str],
) -> tuple[dict, dict, int, list[tuple[int, int]]] | None:
    """Lock each OD onto one Glass–Ni model (→ one VC). Prefer shorter paths.

    Returns (paths, which_vc, total_hops, miss_pairs). miss_pairs empty ⇒ full
    cover. which_vc maps (s,d) → index into model_names.
    """
    oks = [_turn_ok_factory(_TURN_MODELS[n]) for n in model_names]
    paths: dict[tuple[int, int], list[int]] = {}
    which: dict[tuple[int, int], int] = {}
    hops = 0
    miss: list[tuple[int, int]] = []
    for s in compute:
        for d in compute:
            if s == d:
                continue
            best_p, best_i = None, -1
            for i, ok in enumerate(oks):
                p = _pick_turn_path(s, d, adj, ok)
                if p is None:
                    continue
                if best_p is None or len(p) < len(best_p):
                    best_p, best_i = p, i
            if best_p is None:
                miss.append((s, d))
                continue
            paths[(s, d)] = best_p
            which[(s, d)] = best_i
            hops += len(best_p) - 1
    return paths, which, hops, miss


def _hit_set_endpoints(miss: list[tuple[int, int]], k_max: int = 4) -> set[int]:
    """Greedy vertex cover of miss OD endpoints (small forced sacrifice)."""
    remain = list(miss)
    forced: set[int] = set()
    while remain and len(forced) < k_max:
        counts: dict[int, int] = {}
        for s, d in remain:
            counts[s] = counts.get(s, 0) + 1
            counts[d] = counts.get(d, 0) + 1
        n = max(counts, key=counts.get)
        forced.add(n)
        remain = [(s, d) for s, d in remain if s != n and d != n]
    return forced if not remain else set()


def _pack_super_turn(paths, which, hops, model_names, tag, forced=None):
    used = sorted(set(which.values()))
    remap = {old: i for i, old in enumerate(used)}
    which2 = {k: remap[v] for k, v in which.items()}
    n_vc = max(1, len(used))

    def vc_of(path, i, _w=which2):
        del i
        return _w[(path[0], path[-1])]

    out = {
        "paths": paths, "vc_of": vc_of if n_vc > 1 else None,
        "num_vc": n_vc, "scheme": "super_turn",
        "turn_mode": tag, "turn_vc": n_vc, "total_hops": hops,
        "turn_layers": [model_names[u] for u in used],
    }
    if forced:
        out["forced_sacrificed"] = sorted(forced)
    if n_vc > 1 and which2:
        out["vc1_frac"] = sum(1 for v in which2.values() if v > 0) / len(which2)
    return out


def gen_super_turn(pg: dict) -> dict[str, Any] | None:
    """M0s: adaptive Glass–Ni turn model — ≤2 VC, escalate via dual then sac.

    1. Try each of the four minimal 1-VC models globally; keep shortest cover.
    2. Else try every 2-model dual (complementary first); 2 VC, pair-locked.
    3. Else greedily force-sacrifice OD endpoints that block the best dual and
       retry — prefer sacrifice over a 4th VC so the silicon VC budget stays 2.

    Each layer's CDG is acyclic on every subgraph; layers do not share channels.
    Paths are locked to one VC end-to-end → order-preserving.
    """
    duals = [
        ("east_west", ["east_first", "west_first"]),
        ("north_south", ["north_last", "south_last"]),
        ("east_north", ["east_first", "north_last"]),
        ("east_south", ["east_first", "south_last"]),
        ("west_north", ["west_first", "north_last"]),
        ("west_south", ["west_first", "south_last"]),
    ]

    def try_build(adj_i, compute_i, forced=None):
        # 1 VC
        best_1: tuple[int, str, dict] | None = None
        for name, banned in _TURN_MODELS.items():
            paths = _paths_under_turn(adj_i, compute_i, banned)
            if paths is None:
                continue
            hops = sum(len(p) - 1 for p in paths.values())
            cand = (hops, name, paths)
            if best_1 is None or cand < best_1:
                best_1 = cand
        if best_1 is not None:
            hops, name, paths = best_1
            ok, _ = validate_routing(paths, compute_i, adj_i, None)
            if ok:
                return {
                    "paths": paths, "vc_of": None, "num_vc": 1,
                    "scheme": "super_turn", "turn_mode": name,
                    "turn_vc": 1, "total_hops": hops,
                    **({"forced_sacrificed": sorted(forced)} if forced else {}),
                }

        best_2 = None
        best_miss = None
        for tag, models in duals:
            paths, which, hops, miss = _assign_turn_layers(
                adj_i, compute_i, models)
            if miss:
                if best_miss is None or len(miss) < len(best_miss[0]):
                    best_miss = (miss, tag, models)
                continue

            def vc_tmp(path, i, _w=which):
                del i
                return _w[(path[0], path[-1])]

            ok, _ = validate_routing(paths, compute_i, adj_i, vc_tmp)
            if not ok:
                continue
            cand = (hops, tag, paths, which, models)
            if best_2 is None or cand[:2] < best_2[:2]:
                best_2 = cand
        if best_2 is not None:
            hops, tag, paths, which, models = best_2
            return _pack_super_turn(
                paths, which, hops, models, tag, forced)
        return best_miss  # (miss, tag, models) or None

    forced: set[int] = set()
    view = pg
    for _ in range(8):
        adj_i = view["route_adj"]
        compute_i = view["compute_nodes"]
        if len(compute_i) < 2:
            return None
        built = try_build(adj_i, compute_i, forced or None)
        if isinstance(built, dict):
            if forced:
                built = dict(built)
                built["compute_nodes"] = compute_i
                built["route_adj"] = adj_i
                built["forced_sacrificed"] = sorted(forced)
            return built
        if not built:
            return None
        miss, _tag, _models = built
        hit = _hit_set_endpoints(miss, k_max=1)
        if not hit:
            hit = {miss[0][1]}
        if hit & forced:
            return None
        forced |= hit
        view = apply_sacrifice(pg, forced, remove_from_route=True)
    return None


def gen_super_turn_1vc(pg: dict) -> dict[str, Any] | None:
    """M0s1: Glass–Ni turn model hard-capped at 1 VC.

    Same four minimal models as super_turn, but never opens a second VC layer:
    if no single model covers all OD pairs, force-sacrifice miss endpoints and
    retry. Prefer sacrifice over dual VC so silicon stays at 1 VC.
    """
    forced: set[int] = set()
    view = pg
    for _ in range(12):
        adj_i = view["route_adj"]
        compute_i = view["compute_nodes"]
        if len(compute_i) < 2:
            return None

        best_1: tuple[int, str, dict] | None = None
        miss_best: list[tuple[int, int]] | None = None
        for name, banned in _TURN_MODELS.items():
            okf = _turn_ok_factory(banned)
            paths: dict[tuple[int, int], list[int]] = {}
            miss: list[tuple[int, int]] = []
            hops = 0
            for s in compute_i:
                for d in compute_i:
                    if s == d:
                        continue
                    p = _pick_turn_path(s, d, adj_i, okf)
                    if p is None:
                        miss.append((s, d))
                        continue
                    paths[(s, d)] = p
                    hops += len(p) - 1
            if miss:
                if miss_best is None or len(miss) < len(miss_best):
                    miss_best = miss
                continue
            cand = (hops, name, paths)
            if best_1 is None or cand < best_1:
                best_1 = cand

        if best_1 is not None:
            hops, name, paths = best_1
            ok, _ = validate_routing(paths, compute_i, adj_i, None)
            if ok:
                out: dict[str, Any] = {
                    "paths": paths, "vc_of": None, "num_vc": 1,
                    "scheme": "super_turn_1vc", "turn_mode": name,
                    "turn_vc": 1, "total_hops": hops,
                }
                if forced:
                    out["forced_sacrificed"] = sorted(forced)
                    out["compute_nodes"] = compute_i
                    out["route_adj"] = adj_i
                return out

        if not miss_best:
            return None
        hit = _hit_set_endpoints(miss_best, k_max=1)
        if not hit:
            hit = {miss_best[0][1]}
        if hit & forced:
            return None
        forced |= hit
        view = apply_sacrifice(pg, forced, remove_from_route=True)
    return None


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


def _tree_path(s: int, d: int, adj: dict[int, list[int]],
               labels: dict[int, int], mode: str = "ud"
               ) -> list[int] | None:
    """BFS under Up*/Down* (`ud`) or Down*/Up* (`du`) turn rules."""
    if s == d:
        return [s]
    # phase 0 = first direction still allowed; 1 = second direction only
    prev: dict[tuple[int, int], tuple[int, int] | None] = {(s, 0): None}
    q = deque([(s, 0)])
    found = None
    while q:
        u, ph = q.popleft()
        for v in adj.get(u, ()):
            if u not in labels or v not in labels:
                continue
            going_up = labels[v] < labels[u]
            going_down = labels[v] > labels[u]
            lateral = labels[v] == labels[u]
            if mode == "ud":
                # up then down (lateral counts as down)
                if going_up:
                    if ph == 1:
                        continue
                    nph = 0
                else:
                    nph = 1
            else:
                # down then up (lateral counts as down / first phase)
                if going_down or lateral:
                    if ph == 1:
                        continue
                    nph = 0
                else:
                    # up
                    nph = 1
            st = (v, nph)
            if st in prev:
                continue
            prev[st] = (u, ph)
            if v == d:
                found = st
                q.clear()
                break
            q.append(st)
    if found is None:
        return None
    path = [found[0]]
    cur = found
    while prev[cur] is not None:
        cur = prev[cur]  # type: ignore
        path.append(cur[0])
    path.reverse()
    out = [path[0]]
    for n in path[1:]:
        if n != out[-1]:
            out.append(n)
    return out if out[0] == s and out[-1] == d else None


def gen_updown(pg: dict) -> dict[str, Any] | None:
    adj = pg["route_adj"]
    compute = pg["compute_nodes"]
    if len(compute) < 2 or not adj:
        return None
    root = max(adj.keys(), key=lambda n: (len(adj[n]), -n))
    labels = _updown_labels(adj, root)
    if labels is None:
        return None
    paths = {}
    for s in compute:
        for d in compute:
            if s == d:
                continue
            p = _tree_path(s, d, adj, labels, "ud")
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


# ---------------------------------------------------------------------------
# M5: true fault-ring (Boppana-Chalasani style) XY with 4 VCs
# ---------------------------------------------------------------------------
#
# Fault model: every fault is absorbed into an axis-aligned *rectangular fault
# block* of deactivated nodes; the healthy nodes hugging a block form its
# fault ring. Base routing is plain XY; a packet that would enter a block walks
# the ring to the far side and resumes XY.
#
# VC assignment (constant per hop-phase, so still one path + deterministic VC
# sequence per (src,dst) -> in-order):
#     VC0 = X-phase, packet's X direction is East
#     VC1 = X-phase, packet's X direction is West
#     VC2 = Y-phase, packet's Y direction is North
#     VC3 = Y-phase, packet's Y direction is South
#
# Why this is deadlock free:
#   * X-phase detours only step vertically or in the packet's X direction, so
#     inside VC0 every horizontal channel points East (VC1: West). A CDG cycle
#     would need x to return to its start, so it can contain no horizontal
#     channel; a purely vertical cycle inside one column needs a 180 turn,
#     which the construction never emits.
#   * Y-phase detours are the mirror image: inside VC2 every vertical channel
#     points North (VC3: South), same argument on y.
#   * A packet only ever moves X-phase -> Y-phase, so dependencies run from
#     {VC0,VC1} to {VC2,VC3} and never back. Acyclic per VC + one-way между
#     the two groups => the whole CDG is acyclic.

FRING_NUM_VC = 4


def _link_seed_nodes(dead_links, dead_nodes) -> set[int]:
    """Pick one endpoint per faulty link to deactivate (greedy set cover).

    The rectangular-block model has no notion of a broken link between two live
    routers, so f-ring must retire a node to absorb each link fault.
    """
    dead = set(dead_nodes)
    todo = [tuple(l) for l in dead_links
            if l[0] not in dead and l[1] not in dead]
    chosen: set[int] = set()
    while todo:
        cnt: dict[int, int] = defaultdict(int)
        for a, b in todo:
            cnt[a] += 1
            cnt[b] += 1
        pick = max(cnt, key=lambda n: (cnt[n], -n))
        chosen.add(pick)
        todo = [l for l in todo if pick not in l]
    return chosen


def _rect_blocks(pg: dict) -> list[tuple[int, int, int, int]]:
    """Grow faults into disjoint rectangular blocks (merge on overlap/touch)."""
    seeds = set(pg.get("dead_nodes", []))
    seeds |= _link_seed_nodes(pg.get("route_dead_links", []), seeds)
    seeds |= set(pg.get("route_dead_nodes", []))
    if not seeds:
        return []
    rects = [(coord(n)[0], coord(n)[1], coord(n)[0], coord(n)[1])
             for n in seeds]
    merged = True
    while merged:
        merged = False
        for i in range(len(rects)):
            for j in range(i + 1, len(rects)):
                ax0, ay0, ax1, ay1 = rects[i]
                bx0, by0, bx1, by1 = rects[j]
                # touching or overlapping bounding boxes -> one block
                if (ax0 <= bx1 + 1 and bx0 <= ax1 + 1
                        and ay0 <= by1 + 1 and by0 <= ay1 + 1):
                    rects[i] = (min(ax0, bx0), min(ay0, by0),
                                max(ax1, bx1), max(ay1, by1))
                    rects.pop(j)
                    merged = True
                    break
            if merged:
                break
    return sorted(rects)


def _block_at(x: int, y: int, blocks) -> tuple[int, int, int, int] | None:
    if not (0 <= x < MX and 0 <= y < MY):
        return None
    for b in blocks:
        if b[0] <= x <= b[2] and b[1] <= y <= b[3]:
            return b
    return None


def _between(a: int, b: int) -> list[int]:
    """Coordinates strictly after a up to and including b."""
    if a == b:
        return []
    step = 1 if b > a else -1
    return list(range(a + step, b + step, step))


def _leg_ok(cells, alive, avoid) -> bool:
    """All ring cells live, and the walk does not start with a 180 turn."""
    if not cells or cells[0] == avoid:
        return False
    return all(0 <= x < MX and 0 <= y < MY and nid(x, y) in alive
               for x, y in cells)


def _pick_leg(opts, alive, avoid):
    legal = [c for c in opts if _leg_ok(c, alive, avoid)]
    return min(legal, key=len) if legal else None


def _x_detour(x, y, xdir, dx, blk, alive, avoid, prefer_up: bool):
    """Ring-walk around blk during the X phase.

    Normally rejoins row y on the far side. When the destination column lies
    inside the block, the walk stops on the ring row at column dx instead —
    otherwise it would overshoot and never converge.
    """
    x0, y0, x1, y1 = blk
    inside = x0 <= dx <= x1
    hstop = dx if inside else (x1 + 1 if xdir > 0 else x0 - 1)
    opts = []
    for side in ((1, -1) if prefer_up else (-1, 1)):
        yring = y1 + 1 if side > 0 else y0 - 1
        cells = [(x, yy) for yy in _between(y, yring)]
        cells += [(xx, yring) for xx in _between(x, hstop)]
        if not inside:
            cells += [(hstop, yy) for yy in _between(yring, y)]
        opts.append(cells)
    return _pick_leg(opts, alive, avoid)


def _y_detour(x, y, ydir, blk, alive, avoid, prefer_east: bool):
    """Ring-walk around blk during the Y phase; rejoins column x beyond it."""
    x0, y0, x1, y1 = blk
    far = y1 + 1 if ydir > 0 else y0 - 1
    opts = []
    for side in ((1, -1) if prefer_east else (-1, 1)):
        xring = x1 + 1 if side > 0 else x0 - 1
        cells = [(xx, y) for xx in _between(x, xring)]
        cells += [(xring, yy) for yy in _between(y, far)]
        cells += [(xx, far) for xx in _between(xring, x)]
        opts.append(cells)
    return _pick_leg(opts, alive, avoid)


def _fring_path(s: int, d: int, blocks, alive
                ) -> tuple[list[int], int, int] | None:
    """XY with fault-ring detours → (path, n_hops_in_X_phase, y_direction)."""
    sx, sy = coord(s)
    dx, dy = coord(d)
    xdir = (dx > sx) - (dx < sx)
    path = [s]
    x, y = sx, sy

    def prev_cell():
        return coord(path[-2]) if len(path) >= 2 else None

    def emit(cells) -> bool:
        nonlocal x, y
        for cx, cy in cells:
            n = nid(cx, cy)
            if n not in alive or n == path[-1]:
                return False
            path.append(n)
            x, y = cx, cy
        return True

    guard = 0
    while x != dx:
        guard += 1
        if guard > MX * MY:
            return None
        blk = _block_at(x + xdir, y, blocks)
        if blk is None:
            if not emit([(x + xdir, y)]):
                return None
        else:
            # Prefer the ring side that also carries us toward the dst row.
            leg = _x_detour(x, y, xdir, dx, blk, alive, prev_cell(),
                            prefer_up=(dy >= y))
            if leg is None or not emit(leg):
                return None
    x_hops = len(path) - 1

    # Y direction is taken *after* the X phase: a ring detour may have already
    # moved the packet in y, and the VC class must match the direction actually
    # travelled for the monotonicity argument to hold.
    ydir = (dy > y) - (dy < y)
    y_sign = ydir
    while y != dy:
        guard += 1
        if guard > 2 * MX * MY:
            return None
        blk = _block_at(x, y + ydir, blocks)
        if blk is None:
            if not emit([(x, y + ydir)]):
                return None
        else:
            leg = _y_detour(x, y, ydir, blk, alive, prev_cell(),
                            prefer_east=(xdir >= 0))
            if leg is None or not emit(leg):
                return None
    return path, x_hops, y_sign


def gen_fault_ring_vc(pg: dict) -> dict[str, Any] | None:
    """True fault-ring XY on rectangular fault blocks with 4 VCs (see above)."""
    blocks = _rect_blocks(pg)
    deact = {nid(x, y)
             for (x0, y0, x1, y1) in blocks
             for x in range(x0, x1 + 1) for y in range(y0, y1 + 1)}
    alive = set(range(N)) - deact
    radj = {n: sorted(m for m in grid_neighbors(n) if m in alive)
            for n in alive}
    compute = [n for n in pg["compute_nodes"] if n in alive]
    if len(compute) < 2:
        return None

    paths: dict[tuple[int, int], list[int]] = {}
    meta: dict[tuple[int, int], tuple[int, int, int]] = {}
    for s in compute:
        sx, _ = coord(s)
        for d in compute:
            if s == d:
                continue
            r = _fring_path(s, d, blocks, alive)
            if r is None:
                return None
            p, x_hops, y_sign = r
            meta[(s, d)] = (x_hops,
                            0 if coord(d)[0] >= sx else 1,
                            2 if y_sign >= 0 else 3)
            paths[(s, d)] = p

    def vc_of(path: list[int], i: int) -> int:
        x_hops, vx, vy = meta[(path[0], path[-1])]
        return vx if i < x_hops else vy

    forced = sorted(set(pg["compute_nodes"]) - set(compute))
    return {
        "paths": paths,
        "vc_of": vc_of,
        "num_vc": FRING_NUM_VC,
        "scheme": "fault_ring_vc",
        "forced_sacrificed": forced,
        "compute_nodes": compute,
        "route_adj": radj,
        "blocks": blocks,
    }


# ---------------------------------------------------------------------------
# M5h: fault half-ring — XY + one-sided detour, 2 VCs (X/Y phase)
# ---------------------------------------------------------------------------
#
# Same rectangular fault blocks as M5, but each detour uses only the preferred
# half of the ring (toward the destination row/col) — never the opposite side.
# VC0 = X-phase hops, VC1 = Y-phase hops. Acyclicity is not purely constructive
# under overlapping rings; every table is checked with validate_routing, and
# the shared sacrifice recoverer expands blocks when the half-ring is blocked
# or the CDG has a cycle.
#
FHRING_NUM_VC = 2


def _x_detour_half(x, y, xdir, dx, blk, alive, avoid, prefer_up: bool):
    """One-sided ring walk for X-phase (preferred half only)."""
    x0, y0, x1, y1 = blk
    inside = x0 <= dx <= x1
    hstop = dx if inside else (x1 + 1 if xdir > 0 else x0 - 1)
    side = 1 if prefer_up else -1
    yring = y1 + 1 if side > 0 else y0 - 1
    cells = [(x, yy) for yy in _between(y, yring)]
    cells += [(xx, yring) for xx in _between(x, hstop)]
    if not inside:
        cells += [(hstop, yy) for yy in _between(yring, y)]
    return cells if _leg_ok(cells, alive, avoid) else None


def _y_detour_half(x, y, ydir, blk, alive, avoid, prefer_east: bool):
    """One-sided ring walk for Y-phase (preferred half only)."""
    x0, y0, x1, y1 = blk
    far = y1 + 1 if ydir > 0 else y0 - 1
    side = 1 if prefer_east else -1
    xring = x1 + 1 if side > 0 else x0 - 1
    cells = [(xx, y) for xx in _between(x, xring)]
    cells += [(xring, yy) for yy in _between(y, far)]
    cells += [(xx, far) for xx in _between(xring, x)]
    return cells if _leg_ok(cells, alive, avoid) else None


def _fhring_path(s: int, d: int, blocks, alive
                 ) -> tuple[list[int], int] | None:
    """XY with half-ring detours → (path, n_hops_in_X_phase)."""
    sx, sy = coord(s)
    dx, dy = coord(d)
    xdir = (dx > sx) - (dx < sx)
    path = [s]
    x, y = sx, sy

    def prev_cell():
        return coord(path[-2]) if len(path) >= 2 else None

    def emit(cells) -> bool:
        nonlocal x, y
        for cx, cy in cells:
            n = nid(cx, cy)
            if n not in alive or n == path[-1]:
                return False
            path.append(n)
            x, y = cx, cy
        return True

    guard = 0
    while x != dx:
        guard += 1
        if guard > MX * MY:
            return None
        blk = _block_at(x + xdir, y, blocks)
        if blk is None:
            if not emit([(x + xdir, y)]):
                return None
        else:
            leg = _x_detour_half(x, y, xdir, dx, blk, alive, prev_cell(),
                                 prefer_up=(dy >= y))
            if leg is None or not emit(leg):
                return None
    x_hops = len(path) - 1

    ydir = (dy > y) - (dy < y)
    while y != dy:
        guard += 1
        if guard > 2 * MX * MY:
            return None
        blk = _block_at(x, y + ydir, blocks)
        if blk is None:
            if not emit([(x, y + ydir)]):
                return None
        else:
            leg = _y_detour_half(x, y, ydir, blk, alive, prev_cell(),
                                 prefer_east=(xdir >= 0))
            if leg is None or not emit(leg):
                return None
    return path, x_hops


def _inflate_blocks(blocks, grow: int) -> list[tuple[int, int, int, int]]:
    if grow <= 0:
        return list(blocks)
    out = [(max(0, x0 - grow), max(0, y0 - grow),
            min(MX - 1, x1 + grow), min(MY - 1, y1 + grow))
           for x0, y0, x1, y1 in blocks]
    merged = True
    while merged and len(out) > 1:
        merged = False
        for i in range(len(out)):
            for j in range(i + 1, len(out)):
                ax0, ay0, ax1, ay1 = out[i]
                bx0, by0, bx1, by1 = out[j]
                if (ax0 <= bx1 + 1 and bx0 <= ax1 + 1
                        and ay0 <= by1 + 1 and by0 <= ay1 + 1):
                    out[i] = (min(ax0, bx0), min(ay0, by0),
                              max(ax1, bx1), max(ay1, by1))
                    out.pop(j)
                    merged = True
                    break
            if merged:
                break
    return sorted(out)


def _fhring_try(pg: dict, blocks) -> dict[str, Any] | None:
    """Half-ring XY + X/Y-phase 2 VC on fixed blocks."""
    deact = {nid(x, y)
             for (x0, y0, x1, y1) in blocks
             for x in range(x0, x1 + 1) for y in range(y0, y1 + 1)}
    alive = set(range(N)) - deact
    radj = {n: sorted(m for m in grid_neighbors(n) if m in alive)
            for n in alive}
    compute = [n for n in pg["compute_nodes"] if n in alive]
    if len(compute) < 2:
        return None

    paths: dict[tuple[int, int], list[int]] = {}
    x_hops_of: dict[tuple[int, int], int] = {}
    for s in compute:
        for d in compute:
            if s == d:
                continue
            r = _fhring_path(s, d, blocks, alive)
            if r is None:
                return None
            p, x_hops = r
            paths[(s, d)] = p
            x_hops_of[(s, d)] = x_hops

    def vc_of(path: list[int], i: int) -> int:
        return 0 if i < x_hops_of[(path[0], path[-1])] else 1

    ok, _ = validate_routing(paths, compute, radj, vc_of)
    if not ok:
        return None

    forced = sorted(set(pg["compute_nodes"]) - set(compute))
    return {
        "paths": paths,
        "vc_of": vc_of,
        "num_vc": FHRING_NUM_VC,
        "scheme": "fault_half_ring",
        "forced_sacrificed": forced,
        "compute_nodes": compute,
        "route_adj": radj,
        "blocks": blocks,
    }


def gen_fault_half_ring(pg: dict) -> dict[str, Any] | None:
    """XY + one-sided fault half-ring with 2 VCs (X-phase / Y-phase).

    Preferred half toward the destination. VC0 = X-phase hops, VC1 = Y-phase.
    If the CDG is cyclic or a half-ring leg is blocked, inflate fault blocks
    (grow≤3) then defer to the shared sacrifice recoverer.
    """
    base = _rect_blocks(pg)
    for grow in range(0, 4):
        built = _fhring_try(pg, _inflate_blocks(base, grow))
        if built is not None:
            return built
    return None


# ---------------------------------------------------------------------------
# M6: LASH — Layered Shortest Path (Skeie et al.)
# ---------------------------------------------------------------------------

LASH_MAX_LAYERS = 8


def _cdg_add_path(cdg: dict[Any, set[Any]], path: list[int]) -> list[tuple]:
    """Add consecutive channel deps of path; return list of added edges for undo."""
    added = []
    if len(path) < 2:
        return added
    chans = [(path[i], path[i + 1]) for i in range(len(path) - 1)]
    for i in range(len(chans) - 1):
        a, b = chans[i], chans[i + 1]
        if b not in cdg[a]:
            cdg[a].add(b)
            added.append((a, b))
        _ = cdg[b]
    return added


def _cdg_undo(cdg: dict[Any, set[Any]], added: list[tuple]) -> None:
    for a, b in added:
        cdg[a].discard(b)


def gen_lash(pg: dict) -> dict[str, Any] | None:
    """Shortest path per pair; greedy-pack into fewest VC layers with acyclic CDG.

    Each (src,dst) gets one layer for its entire path (constant VC) → in-order.
    """
    adj = pg["route_adj"]
    compute = pg["compute_nodes"]
    if len(compute) < 2:
        return None

    paths: dict[tuple[int, int], list[int]] = {}
    for s in compute:
        for d in compute:
            if s == d:
                continue
            p = shortest_path(s, d, adj)
            if p is None:
                return None
            paths[(s, d)] = p

    pairs = sorted(paths.keys(), key=lambda sd: (-len(paths[sd]), sd[0], sd[1]))
    layers: list[dict[Any, set[Any]]] = [defaultdict(set)]
    assign: dict[tuple[int, int], int] = {}

    for sd in pairs:
        p = paths[sd]
        placed = False
        for li, cdg in enumerate(layers):
            added = _cdg_add_path(cdg, p)
            if cdg_acyclic(cdg):
                assign[sd] = li
                placed = True
                break
            _cdg_undo(cdg, added)
        if not placed:
            if len(layers) >= LASH_MAX_LAYERS:
                return None
            cdg: dict[Any, set[Any]] = defaultdict(set)
            _cdg_add_path(cdg, p)
            if not cdg_acyclic(cdg):
                return None  # single path forming a cycle — impossible for simple path
            layers.append(cdg)
            assign[sd] = len(layers) - 1

    num_vc = len(layers)

    def vc_of(path: list[int], i: int) -> int:
        del i
        return assign[(path[0], path[-1])]

    return {
        "paths": paths,
        "vc_of": vc_of,
        "num_vc": num_vc,
        "scheme": "lash",
        "n_layers": num_vc,
    }


def _cdg_add_segment(cdg: dict[Any, set[Any]], path: list[int],
                     i0: int, i1: int) -> list[tuple]:
    """Add deps for hops path[i0]..path[i1] (node indices); i1 exclusive on nodes
    so last hop is (path[i1-2], path[i1-1]) wait — hops covering nodes[i0:i1+1]."""
    # segment nodes path[i0 .. i1] inclusive; hops between them
    added = []
    nodes = path[i0:i1 + 1]
    if len(nodes) < 2:
        return added
    return _cdg_add_path(cdg, nodes)


def gen_lash_tor(pg: dict) -> dict[str, Any] | None:
    """LASH-TOR: allow one mid-path climb to a higher layer to pack denser.

    Hop VC is non-decreasing along each path (in-order preserved: same
    deterministic sequence per pair). Each layer's CDG stays acyclic.
    """
    adj = pg["route_adj"]
    compute = pg["compute_nodes"]
    if len(compute) < 2:
        return None

    paths: dict[tuple[int, int], list[int]] = {}
    for s in compute:
        for d in compute:
            if s == d:
                continue
            p = shortest_path(s, d, adj)
            if p is None:
                return None
            paths[(s, d)] = p

    pairs = sorted(paths.keys(), key=lambda sd: (-len(paths[sd]), sd[0], sd[1]))
    layers: list[dict[Any, set[Any]]] = [defaultdict(set)]
    # assign[(s,d)] = list of per-hop VC, length = len(path)-1
    hop_vc: dict[tuple[int, int], list[int]] = {}

    def try_place_flat(p, li) -> bool:
        added = _cdg_add_path(layers[li], p)
        if cdg_acyclic(layers[li]):
            return True
        _cdg_undo(layers[li], added)
        return False

    def try_place_split(p, lo, hi, split_after_hop: int) -> bool:
        """Hops [0..split] on `lo`, hops (split..end] on `hi` (hi >= lo)."""
        # nodes: hop k is (path[k], path[k+1])
        n = len(p) - 1
        if split_after_hop < 0 or split_after_hop >= n - 1:
            return False
        # segment0: nodes 0 .. split_after_hop+1
        # segment1: nodes split_after_hop+1 .. end
        a0 = _cdg_add_segment(layers[lo], p, 0, split_after_hop + 1)
        a1 = _cdg_add_segment(layers[hi], p, split_after_hop + 1, n)
        ok = cdg_acyclic(layers[lo]) and cdg_acyclic(layers[hi])
        if not ok:
            _cdg_undo(layers[lo], a0)
            _cdg_undo(layers[hi], a1)
        return ok

    for sd in pairs:
        p = paths[sd]
        n_hops = len(p) - 1
        placed = False
        # 1) flat into existing layer
        for li in range(len(layers)):
            if try_place_flat(p, li):
                hop_vc[sd] = [li] * n_hops
                placed = True
                break
        if placed:
            continue
        # 2) TOR split across existing layers (lo <= hi)
        for lo in range(len(layers)):
            for hi in range(lo, len(layers)):
                if lo == hi:
                    continue
                for sp in range(n_hops - 1):
                    if try_place_split(p, lo, hi, sp):
                        hop_vc[sd] = [lo] * (sp + 1) + [hi] * (n_hops - sp - 1)
                        placed = True
                        break
                if placed:
                    break
            if placed:
                break
        if placed:
            continue
        # 3) open a new top layer; try flat then TOR climb into it
        if len(layers) >= LASH_MAX_LAYERS:
            return None
        layers.append(defaultdict(set))
        top = len(layers) - 1
        if try_place_flat(p, top):
            hop_vc[sd] = [top] * n_hops
            continue
        ok_tor = False
        for lo in range(top):
            for sp in range(n_hops - 1):
                if try_place_split(p, lo, top, sp):
                    hop_vc[sd] = [lo] * (sp + 1) + [top] * (n_hops - sp - 1)
                    ok_tor = True
                    break
            if ok_tor:
                break
        if not ok_tor:
            return None

    num_vc = len(layers)

    def vc_of(path: list[int], i: int) -> int:
        return hop_vc[(path[0], path[-1])][i]

    return {
        "paths": paths,
        "vc_of": vc_of,
        "num_vc": num_vc,
        "scheme": "lash_tor",
        "n_layers": num_vc,
    }


# ---------------------------------------------------------------------------
# M7: Stripe dateline — vertical bands, VC += 1 per boundary cross
# ---------------------------------------------------------------------------

def _stripe_datelines(pg: dict, width: int = 2) -> list[int]:
    """Vertical dateline columns: every `width` cols, plus fault-touched cols."""
    lines = set(range(width, MX, width))
    _, fcols = _fault_rows_cols(pg)
    for x in fcols:
        if 0 < x < MX:
            lines.add(x)
        if 0 < x + 1 < MX:
            lines.add(x + 1)
    return sorted(lines)


def _dateline_crossings(path: list[int], datelines: list[int],
                        upto: int) -> int:
    """How many times hops [0..upto] inclusive cross a vertical dateline."""
    crosses = 0
    for j in range(upto + 1):
        ax, _ = coord(path[j])
        bx, _ = coord(path[j + 1])
        if ax == bx:
            continue
        lo, hi = (ax, bx) if ax < bx else (bx, ax)
        for d in datelines:
            if lo < d <= hi:
                crosses += 1
    return crosses


def gen_stripe_vc(pg: dict) -> dict[str, Any] | None:
    """Shortest paths (XY preferred); VC = # vertical-dateline crossings so far.

    Datelines at every 2 columns and at fault-column edges. Monotonic VC along
    each path; CDG is checked — denser datelines tried if the sparse set fails.
    """
    adj = pg["route_adj"]
    compute = pg["compute_nodes"]
    if len(compute) < 2:
        return None

    paths: dict[tuple[int, int], list[int]] = {}
    for s in compute:
        for d in compute:
            if s == d:
                continue
            p = xy_path(s, d, adj)
            if p is None:
                p = shortest_path(s, d, adj)
            if p is None:
                return None
            paths[(s, d)] = p

    # Try sparse then dense datelines until CDG is acyclic
    trials = [
        _stripe_datelines(pg, width=2),
        list(range(1, MX)),  # every column boundary
    ]
    # dedupe trial lists
    seen_dl: set[tuple[int, ...]] = set()
    best = None
    for dlines in trials:
        key = tuple(dlines)
        if key in seen_dl:
            continue
        seen_dl.add(key)

        def vc_of(path: list[int], i: int, _dl=dlines) -> int:
            return _dateline_crossings(path, _dl, i)

        ok, _ = validate_routing(paths, compute, adj, vc_of)
        if not ok:
            continue
        num_vc = 1
        for p in paths.values():
            if len(p) >= 2:
                num_vc = max(num_vc, 1 + _dateline_crossings(p, dlines, len(p) - 2))
        best = {
            "paths": paths,
            "vc_of": vc_of,
            "num_vc": num_vc,
            "scheme": "stripe_vc",
            "datelines": dlines,
        }
        break
    return best


# ---------------------------------------------------------------------------
# M9: Dual Up*/Down* — VC0 = UD, VC1 = DU, pick shorter path per pair
# ---------------------------------------------------------------------------

def gen_dual_updown(pg: dict) -> dict[str, Any] | None:
    """VC0 runs Up*/Down*, VC1 runs Down*/Up*; each pair picks the shorter."""
    adj = pg["route_adj"]
    compute = pg["compute_nodes"]
    if len(compute) < 2 or not adj:
        return None
    root = max(adj.keys(), key=lambda n: (len(adj[n]), -n))
    labels = _updown_labels(adj, root)
    if labels is None:
        return None

    paths: dict[tuple[int, int], list[int]] = {}
    which: dict[tuple[int, int], int] = {}
    for s in compute:
        for d in compute:
            if s == d:
                continue
            pud = _tree_path(s, d, adj, labels, "ud")
            pdu = _tree_path(s, d, adj, labels, "du")
            if pud is None and pdu is None:
                return None
            if pud is None:
                paths[(s, d)] = pdu
                which[(s, d)] = 1
            elif pdu is None or len(pud) <= len(pdu):
                paths[(s, d)] = pud
                which[(s, d)] = 0
            else:
                paths[(s, d)] = pdu
                which[(s, d)] = 1

    def vc_of(path: list[int], i: int) -> int:
        del i
        return which[(path[0], path[-1])]

    return {
        "paths": paths,
        "vc_of": vc_of,
        "num_vc": 2,
        "scheme": "dual_updown",
        "root": root,
    }


# ---------------------------------------------------------------------------
# M10: Virtual regular mesh — logical XY, physical detours, X/Y → 2 VC
# ---------------------------------------------------------------------------

def _logical_xy(s: int, d: int) -> list[int]:
    """XY on the complete healthy mesh (ignores faults)."""
    sx, sy = coord(s)
    dx, dy = coord(d)
    path = [s]
    x, y = sx, sy
    step = 1 if dx > sx else -1
    while x != dx:
        x += step
        path.append(nid(x, y))
    step = 1 if dy > sy else -1
    while y != dy:
        y += step
        path.append(nid(x, y))
    return path


def _expand_logical_edge(a: int, b: int, adj: dict[int, list[int]],
                         ban: set[int] | None = None) -> list[int] | None:
    """Physical realisation of one logical hop a→b (neighbor on full mesh)."""
    if b in adj.get(a, ()):
        return [a, b]
    # Detour: shortest path avoiding `ban` (other logical endpoints mid-detour
    # may still be used). Prefer paths that stay near the broken edge.
    if ban:
        radj = {u: [v for v in nbs if v not in ban or v in (a, b)]
                for u, nbs in adj.items() if u not in ban or u in (a, b)}
        p = shortest_path(a, b, radj)
    else:
        p = shortest_path(a, b, adj)
    return p


def _trim_revisits(path: list[int]) -> list[int]:
    """Drop the loop between repeated nodes (concatenated detours can overshoot)."""
    out: list[int] = []
    pos: dict[int, int] = {}
    for n in path:
        if n in pos:
            for m in out[pos[n] + 1:]:
                del pos[m]
            del out[pos[n] + 1:]
        else:
            pos[n] = len(out)
            out.append(n)
    return out


def gen_virtual_mesh(pg: dict) -> dict[str, Any] | None:
    """Logical full-mesh XY; missing links replaced by fixed physical detours.

    VC0 = logical X-phase hops (after expansion), VC1 = logical Y-phase.
    Compute nodes that are route-dead cannot host logical hops and are
    forced-sacrificed.
    """
    adj = pg["route_adj"]
    # Logical routers = nodes with at least one live link (isolates are unusable).
    alive = {n for n, nbs in adj.items() if nbs}
    compute = [n for n in pg["compute_nodes"] if n in alive]
    if len(compute) < 2:
        return None

    # Precompute physical expansion for every logical unit edge among alive
    expand: dict[tuple[int, int], list[int]] = {}
    for a in alive:
        ax, ay = coord(a)
        for dx, dy in DIRS:
            bx, by = ax + dx, ay + dy
            if not (0 <= bx < MX and 0 <= by < MY):
                continue
            b = nid(bx, by)
            if b not in alive:
                continue
            p = _expand_logical_edge(a, b, adj)
            if p is None:
                # cannot bridge this logical edge — mark unusable
                continue
            expand[(a, b)] = p

    concat: dict[tuple[int, int], list[int]] = {}
    for s in compute:
        for d in compute:
            if s == d:
                continue
            full = _logical_xy(s, d)
            # Drop dead routers on the logical polyline; bridge gaps physically.
            way = [n for n in full if n in alive]
            if not way or way[0] != s or way[-1] != d:
                return None
            phys = [s]
            for i in range(len(way) - 1):
                a, b = way[i], way[i + 1]
                seg = expand.get((a, b)) or shortest_path(a, b, adj)
                if seg is None:
                    return None
                phys.extend(seg[1:])
            concat[(s, d)] = phys

    # A detour can overshoot through a node that the remaining logical hops
    # revisit, leaving a 180° U-turn in the concatenated path. Trimming those
    # loops is shorter and usually kills the resulting CDG cycles, but it also
    # shifts the X/Y phase boundary and can create a cycle of its own — so try
    # the trimmed table first and fall back to the plain concatenation.
    for trim in (True, False):
        paths: dict[tuple[int, int], list[int]] = {}
        x_hops: dict[tuple[int, int], int] = {}
        for (s, d), raw_phys in concat.items():
            phys = _trim_revisits(raw_phys) if trim else raw_phys
            # X-phase = hops until the packet first sits in dst's column.
            dx = coord(d)[0]
            n_x = 0
            if coord(s)[0] != dx:
                for i in range(len(phys) - 1):
                    n_x += 1
                    if coord(phys[i + 1])[0] == dx:
                        break
            paths[(s, d)] = phys
            x_hops[(s, d)] = n_x

        def vc_of(path: list[int], i: int, _x=x_hops) -> int:
            return 0 if i < _x[(path[0], path[-1])] else 1

        ok, _ = validate_routing(paths, compute, adj, vc_of)
        if ok:
            break
    else:
        return None

    forced = sorted(set(pg["compute_nodes"]) - set(compute))
    return {
        "paths": paths,
        "vc_of": vc_of,
        "num_vc": 2,
        "scheme": "virtual_mesh",
        "forced_sacrificed": forced,
        "compute_nodes": compute,
        "route_adj": adj,
    }


SCHEME_GENERATORS = {
    "east_first": gen_east_first,
    "super_turn": gen_super_turn,
    "super_turn_1vc": gen_super_turn_1vc,
    "xy": gen_xy,
    "rect_xy": gen_rect_xy,
    "updown": gen_updown,
    "segment": gen_segment,
    "fault_ring_vc": gen_fault_ring_vc,
    "fault_half_ring": gen_fault_half_ring,
    "lash": gen_lash,
    "lash_tor": gen_lash_tor,
    "stripe_vc": gen_stripe_vc,
    "dual_updown": gen_dual_updown,
    "virtual_mesh": gen_virtual_mesh,
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
        "num_vc": raw.get("num_vc", 1),
        "max_load": max_link_load(paths),
        "reason": "ok",
        "turn_mode": raw.get("turn_mode"),
        "turn_vc": raw.get("turn_vc"),
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


def _fc_attempt(pg: dict, scheme: str, sac: set[int]) -> dict[str, Any] | None:
    remove_route = (scheme in ("rect_xy", "fault_ring_vc", "fault_half_ring")
                    or pg["semantics"] == "dead")
    view = apply_sacrifice(pg, sac, remove_route) if sac else pg
    if view["n_compute"] < 2:
        return None
    raw = SCHEME_GENERATORS[scheme](view)
    if raw is None:
        return None
    return _finalize(view, raw, set(sac), remove_route)


def _fc_lines(pg: dict) -> list[list[int]]:
    """Whole-row / whole-column keep-sets, largest first (last-resort coarse)."""
    out = []
    for y in range(MY):
        keep = [n for n in pg["compute_nodes"] if coord(n)[1] == y]
        if len(keep) >= 2:
            out.append(keep)
    for x in range(MX):
        keep = [n for n in pg["compute_nodes"] if coord(n)[0] == x]
        if len(keep) >= 2:
            out.append(keep)
    return sorted(out, key=len, reverse=True)


def solve_scheme_fc(pg: dict, scheme: str, k_max: int = 24) -> dict[str, Any]:
    """`solve_scheme`, then keep sacrificing until a legal table exists.

    `solve_scheme` searches only a small candidate pool and stops at a
    minimum-cardinality recovery, so it reports INFEASIBLE for schemes whose
    constraints need a much larger sacrifice (M0s1 at 1 VC, M5h half-ring).
    This wrapper answers the separate question "does full coverage exist if we
    pay more good hardware?" by escalating: greedy grow along the fault-nearest
    candidate order, then the largest healthy rectangle, then a single surviving
    row / column (a line is legal under every turn model).

    Adds `fc_stage` to the returned solution. Feasible results from
    `solve_scheme` pass through untouched (`fc_stage = "solve_scheme"`).
    """
    sol = solve_scheme(pg, scheme)
    if sol["feasible"]:
        sol["fc_stage"] = "solve_scheme"
        return sol

    sac = {n for n in pg["compute_nodes"] if not pg["route_adj"].get(n)}
    for n in sacrifice_candidates(pg):
        if n in sac:
            continue
        sac.add(n)
        if len(sac) > k_max:
            break
        fin = _fc_attempt(pg, scheme, sac)
        if fin is not None and fin["feasible"]:
            fin["fc_stage"] = "greedy_grow"
            return fin

    fin = _try_rect_recovery(pg, scheme, True)
    if fin is not None and fin["feasible"]:
        fin["fc_stage"] = "rect"
        return fin
    for keep in _fc_lines(pg):
        fin = _fc_attempt(pg, scheme, set(pg["compute_nodes"]) - set(keep))
        if fin is not None and fin["feasible"]:
            fin["fc_stage"] = "line"
            return fin

    sol["fc_stage"] = "none"
    return sol


def solve_scheme(pg: dict, scheme: str) -> dict[str, Any]:
    """Generate a deadlock-free order-preserving routing, sacrificing if needed.

    Trials are ordered by increasing sacrifice size so the first hit is a
    minimum-cardinality recovery (among the candidate set), not a coarse
    row/col wipe.
    """
    gen = SCHEME_GENERATORS[scheme]
    # M2/M5/M5h always remove sacrificed routers from route; others follow PG
    remove_route = (scheme in ("rect_xy", "fault_ring_vc", "fault_half_ring")
                    or pg["semantics"] == "dead")

    # Isolated compute nodes can never send/recv — drop them first.
    base_sac = {n for n in pg["compute_nodes"]
                if not pg["route_adj"].get(n)}
    base_pg = (apply_sacrifice(pg, base_sac, remove_route)
               if base_sac else pg)

    raw = gen(base_pg)
    if raw is not None:
        fin = _finalize(base_pg, raw, base_sac, remove_route)
        if fin is not None:
            return fin

    if scheme == "rect_xy":
        fin = _try_rect_recovery(base_pg, scheme, remove_route)
        if fin is not None:
            # fold isolation sac into the result
            if base_sac and fin.get("feasible"):
                fin = dict(fin)
                fin["sacrificed"] = sorted(set(fin["sacrificed"]) | base_sac)
                fin["n_sacrificed"] = len(fin["sacrificed"])
                n_good = pg["n_originally_good"]
                fin["sacrifice_cost"] = (fin["n_sacrificed"] / n_good
                                         if n_good else 0.0)
            return fin

    cands = [n for n in sacrifice_candidates(base_pg) if n not in base_sac]
    bundles = row_col_bundles(base_pg)
    k_max = min(16, max(1, len(base_pg["compute_nodes"]) // 2))

    # Prefer small sets; coarse bundles only after k<=2 singles/pairs fail.
    ordered_trials: list[list[int]] = []
    pool = cands[: min(12, len(cands))]
    for n in pool:
        ordered_trials.append([n])
    ordered_trials.extend(list(p) for p in itertools.combinations(pool[:8], 2))
    for k in range(3, min(k_max, 6) + 1):
        ordered_trials.append(pool[:k])
    for b in bundles:
        if 1 <= len(b) <= k_max:
            ordered_trials.append(b)
    frows, fcols = _fault_rows_cols(base_pg)
    for y in list(frows)[:2]:
        for x in list(fcols)[:2]:
            combo = [n for n in base_pg["compute_nodes"]
                     if coord(n)[0] == x or coord(n)[1] == y]
            if combo:
                ordered_trials.append(combo)

    seen_t: set[frozenset[int]] = set()
    # Track best feasible by (n_sac, max_load) in case early trial is larger.
    best: dict[str, Any] | None = None
    for trial in ordered_trials:
        key = frozenset(trial) | base_sac
        if key in seen_t or not (frozenset(trial) or base_sac):
            continue
        # Skip if we already have a feasible with fewer or equal sac and this
        # trial cannot improve (larger than best sac).
        if best is not None and len(key) > best["n_sacrificed"]:
            continue
        seen_t.add(key)
        if len(key) > k_max:
            continue
        view = apply_sacrifice(pg, set(key), remove_route)
        if view["n_compute"] < 2:
            continue
        raw = gen(view)
        if raw is None:
            continue
        fin = _finalize(view, raw, set(key), remove_route)
        if fin is None:
            continue
        if (best is None
                or fin["n_sacrificed"] < best["n_sacrificed"]
                or (fin["n_sacrificed"] == best["n_sacrificed"]
                    and fin.get("max_load", 10 ** 9)
                    < best.get("max_load", 10 ** 9))):
            best = fin
            if best["n_sacrificed"] <= len(base_sac) + 1:
                # Already near-minimal; stop early.
                break

    if best is not None:
        return best

    fin = _try_rect_recovery(base_pg, scheme, remove_route)
    if fin is not None:
        if base_sac and fin.get("feasible"):
            fin = dict(fin)
            fin["sacrificed"] = sorted(set(fin["sacrificed"]) | base_sac)
            fin["n_sacrificed"] = len(fin["sacrificed"])
            n_good = pg["n_originally_good"]
            fin["sacrifice_cost"] = (fin["n_sacrificed"] / n_good
                                     if n_good else 0.0)
        return fin

    return {
        "feasible": False,
        "scheme": scheme,
        "paths": {},
        "vc_of": None,
        "num_vc": 1,
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


_MINIMAX_CACHE: dict[Any, int] = {}
_WIREDIAM_CACHE: dict[Any, int] = {}


def _topology_key(compute: list[int], adj: dict[int, list[int]]) -> Any:
    return (tuple(sorted(compute)),
            tuple(sorted((u, v) for u, nbs in adj.items() for v in nbs)))


def minimax_load_lb(compute: list[int], adj: dict[int, list[int]]) -> int:
    """True lower bound on max directed link load (counted in src-dst pairs).

    For any cut (S, S̄), all |S∩C|·|S̄∩C| pairs going S→S̄ must share the live
    directed links leaving S, so some link carries at least the ceiling of that
    ratio. Maximising over all axis-aligned rectangles S gives a bound that no
    routing — deadlock-free or not — can beat. On the healthy 8×6 this is 96,
    exactly what XY achieves, so the bound is tight there.
    """
    cs = set(compute)
    a = len(cs)
    if a < 2:
        return 0
    key = _topology_key(compute, adj)
    hit = _MINIMAX_CACHE.get(key)
    if hit is not None:
        return hit

    best = 0
    for x0 in range(MX):
        for x1 in range(x0, MX):
            for y0 in range(MY):
                for y1 in range(y0, MY):
                    inside = set()
                    for x in range(x0, x1 + 1):
                        for y in range(y0, y1 + 1):
                            n = nid(x, y)
                            if n in adj:
                                inside.add(n)
                    if not inside:
                        continue
                    k = len(cs & inside)
                    demand = k * (a - k)
                    if demand == 0:
                        continue
                    out_edges = sum(1 for u in inside
                                    for v in adj[u] if v not in inside)
                    if out_edges == 0:
                        continue
                    lb = -(-demand // out_edges)
                    if lb > best:
                        best = lb
    _MINIMAX_CACHE[key] = best
    return best


def wire_diameter_lb(compute: list[int], adj: dict[int, list[int]]) -> int:
    """Max over compute pairs of the minimum achievable wire delay."""
    if len(compute) < 2:
        return 0
    key = _topology_key(compute, adj)
    hit = _WIREDIAM_CACHE.get(key)
    if hit is not None:
        return hit
    cs = set(compute)
    worst = 0
    for s in compute:
        dist = {s: 0}
        pq = [(0, s)]
        while pq:
            d, u = heapq.heappop(pq)
            if d != dist[u]:
                continue
            for v in adj.get(u, ()):
                nd = d + link_lat(u, v)
                if v not in dist or nd < dist[v]:
                    dist[v] = nd
                    heapq.heappush(pq, (nd, v))
        for t in cs:
            if t != s and t in dist:
                worst = max(worst, dist[t])
    _WIREDIAM_CACHE[key] = worst
    return worst


def analytical_lb(paths: dict[tuple[int, int], list[int]],
                  compute: list[int], m: int = 1,
                  adj: dict[int, list[int]] | None = None,
                  unbound_max_load: int | None = None,
                  compute_unbound: bool = False) -> dict[str, Any]:
    a = len(compute)
    if a < 2:
        return {"lb": 0, "bw_term": 0, "inj_term": 0, "lat_term": 0,
                "max_load": 0, "true_lb": 0}
    max_load = max_link_load(paths)
    bw_term = max_load * m
    inj_term = ((a - 1) * m + RAMP_BW - 1) // RAMP_BW
    lat_term = 0
    for p in paths.values():
        lat_term = max(lat_term, path_wire_delay(p) + 2 * RAMP + (m - 1))
    lb = max(bw_term, inj_term, lat_term)

    # Routing-independent lower bound on the same compute set: no routing can
    # finish faster than this, so mk / true_lb - 1 is always >= 0.
    mm_load = minimax_load_lb(compute, adj) if adj is not None else 0
    lat_lb = ((wire_diameter_lb(compute, adj) + 2 * RAMP + (m - 1))
              if adj is not None else 0)
    true_lb = max(mm_load * m, inj_term, lat_lb, 1)

    unbound = unbound_max_load
    if unbound is None and compute_unbound and adj is not None:
        unbound = unbound_minimax_load(compute, adj)
    return {
        "lb": lb,
        "bw_term": bw_term,
        "inj_term": inj_term,
        "lat_term": lat_term,
        "max_load": max_load,
        "minimax_load_lb": mm_load,
        "true_bw_lb": mm_load * m,
        "true_lat_lb": lat_lb,
        "true_lb": true_lb,
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
