#!/usr/bin/env python3
"""Batch-barrier alltoall: serialize deadlock-free path subsets with sync.

Not link TDM. Two constructions:

1. **VC-layer serialization** — schemes that already pack OD pairs into
   acyclic CDG layers (LASH, Dual-UD) normally run layers on concurrent VCs.
   Here each layer is one batch on a single physical VC; between batches we
   pay an explicit software barrier after the network has drained.

2. **Multi-table OD partition** — several independently acyclic Up*/Down*
   tables (different roots); OD pairs are round-robin assigned; each table
   is one batch (same sync model).

A batch that itself has a cyclic CDG is illegal — we do *not* “cut a deadlocked
table into pieces hoping time will save it”; each batch must validate alone.
"""
from __future__ import annotations

import heapq
import math
from collections import defaultdict, deque
from typing import Any, Callable

from pg_routing import H, V, build_cdg, cdg_acyclic, link_lat
from pg_vc1_explore import enum_ud_tables, simulate_alltoall_ex


def _bfs_dist(adj: dict[int, list[int]], node_set: set[int],
              src: int) -> dict[int, int] | None:
    """Unweighted hop distances (legacy / reporting)."""
    dist = {src: 0}
    q = deque([src])
    while q:
        u = q.popleft()
        for v in adj.get(u, ()):
            if v not in node_set or v in dist:
                continue
            dist[v] = dist[u] + 1
            q.append(v)
    return dist if len(dist) == len(node_set) else None


def _wire_dist(adj: dict[int, list[int]], node_set: set[int],
               src: int) -> dict[int, int] | None:
    """Shortest-path distances in link_lat cycles (H horizontal, V vertical)."""
    dist = {src: 0}
    pq: list[tuple[int, int]] = [(0, src)]
    while pq:
        d, u = heapq.heappop(pq)
        if d != dist.get(u):
            continue
        for v in adj.get(u, ()):
            if v not in node_set:
                continue
            nd = d + link_lat(u, v)
            if nd < dist.get(v, 1 << 60):
                dist[v] = nd
                heapq.heappush(pq, (nd, v))
    return dist if len(dist) == len(node_set) else None


def graph_eccentricity(adj: dict[int, list[int]], nodes: list[int],
                       src: int, *, weighted: bool = True) -> int | None:
    dist = (_wire_dist if weighted else _bfs_dist)(adj, set(nodes), src)
    return None if dist is None else max(dist.values())


def graph_diameter(adj: dict[int, list[int]], nodes: list[int],
                   *, weighted: bool = True) -> int:
    """Diameter on compute-induced route graph (wire-delay or hop)."""
    if len(nodes) < 2:
        return 0
    diam = 0
    for src in nodes:
        ecc = graph_eccentricity(adj, nodes, src, weighted=weighted)
        if ecc is None:
            # Disconnected: crude fallback
            return max(diam, (len(nodes) - 1) * (max(H, V) if weighted else 1))
        diam = max(diam, ecc)
    return diam


def graph_center(adj: dict[int, list[int]], nodes: list[int],
                 *, weighted: bool = True) -> tuple[int, int]:
    """Return (center, radius): node minimising eccentricity (tie → smaller id).

    With weighted=True (default), radius is in link_lat cycles — matches DES.
    """
    if not nodes:
        return 0, 0
    if len(nodes) == 1:
        return nodes[0], 0
    best_c, best_r = nodes[0], 1 << 60
    for src in sorted(nodes):
        ecc = graph_eccentricity(adj, nodes, src, weighted=weighted)
        if ecc is None:
            continue
        if ecc < best_r or (ecc == best_r and src < best_c):
            best_c, best_r = src, ecc
    return best_c, best_r


def barrier_sync_cycles(pg: dict, model: str = "center"
                        ) -> tuple[int, dict[str, Any]]:
    """Software barrier among A PEs after a batch has drained.

    Network drain is already inside each batch DES makespan; this is only the
    collective “wave done / start next”.

    Models
    ------
    center (default)
        Graph centre c minimising *wire-delay* eccentricity (Dijkstra with
        link_lat: H=7 horiz, V=9 vert — same as DES). Gather all→c then
        broadcast c→all: T_sync = 2·radius_wire. Optimal among single-hub
        barriers under that metric.

    center_hop (legacy)
        Same hub idea but unweighted hops: T_sync = 2·radius_hops (~14 cy).

    binomial (legacy, pessimistic)
        2·⌈log₂ A⌉·diam_wire — each binomial-tree round charged full
        wire-delay diameter.
    """
    compute = list(pg["compute_nodes"])
    adj = pg["route_adj"]
    A = len(compute)
    if A < 2:
        return 0, {"model": model, "A": A, "center": None, "radius": 0,
                   "radius_hops": 0, "diam": 0, "diam_hops": 0,
                   "H": H, "V": V}
    diam_w = graph_diameter(adj, compute, weighted=True)
    diam_h = graph_diameter(adj, compute, weighted=False)
    center_w, radius_w = graph_center(adj, compute, weighted=True)
    center_h, radius_h = graph_center(adj, compute, weighted=False)
    if model == "binomial":
        rounds = math.ceil(math.log2(A))
        sync = 2 * rounds * max(diam_w, 1)
        center, radius, diam = center_w, radius_w, diam_w
    elif model == "center_hop":
        sync = 2 * max(radius_h, 1)
        center, radius, diam = center_h, radius_h, diam_h
    else:  # center — wire-delay (default)
        sync = 2 * max(radius_w, 1)
        center, radius, diam = center_w, radius_w, diam_w
    meta = {
        "model": model, "A": A, "center": center,
        "radius": radius, "diam": diam,
        "radius_wire": radius_w, "diam_wire": diam_w,
        "radius_hops": radius_h, "diam_hops": diam_h,
        "center_hop": center_h,
        "H": H, "V": V, "sync_cy": sync,
    }
    return sync, meta


def batched_makespan(
    pg: dict,
    batches: list[dict[tuple[int, int], list[int]]],
    m: int,
    Q: int,
    sync_cy: int | None = None,
) -> dict[str, Any] | None:
    """Run each non-empty batch DES; T = Σ mk_i + (K_active−1)·T_sync."""
    compute = pg["compute_nodes"]
    adj = pg["route_adj"]
    sync_meta: dict[str, Any] = {}
    if sync_cy is None:
        sync_cy, sync_meta = barrier_sync_cycles(pg)
    phase_mks: list[int] = []
    for paths in batches:
        if not paths:
            phase_mks.append(0)
            continue
        # Each batch must be deadlock-free alone (1 VC).
        cdg = build_cdg(paths, None)
        if not cdg_acyclic(cdg):
            return None
        sim = simulate_alltoall_ex(paths, compute, adj, m=m, Q=Q,
                                   od_filter=set(paths))
        if sim is None:
            return None
        phase_mks.append(int(sim["makespan"]))
    active = [k for k in phase_mks if k > 0]
    if not active:
        return {"makespan": 0, "phase_mks": phase_mks, "n_batches": 0,
                "sync_cy": sync_cy, "sync_total": 0}
    sync_total = (len(active) - 1) * sync_cy
    out = {
        "makespan": sum(active) + sync_total,
        "phase_mks": phase_mks,
        "n_batches": len(active),
        "sync_cy": sync_cy,
        "sync_total": sync_total,
    }
    if sync_meta:
        out["sync_meta"] = sync_meta
    return out


def batches_from_vc_assign(
    paths: dict[tuple[int, int], list[int]],
    assign: dict[tuple[int, int], int],
) -> list[dict[tuple[int, int], list[int]]]:
    """Split a constant-per-path VC assignment into OD batches."""
    if not assign:
        return [dict(paths)]
    n = max(assign.values()) + 1
    batches: list[dict[tuple[int, int], list[int]]] = [{} for _ in range(n)]
    for sd, p in paths.items():
        batches[assign[sd]][sd] = p
    return batches


def assign_from_vc_of(paths: dict[tuple[int, int], list[int]],
                     vc_of: Callable) -> dict[tuple[int, int], int] | None:
    """Require constant VC along each path; return OD→layer map."""
    assign: dict[tuple[int, int], int] = {}
    for sd, p in paths.items():
        if len(p) < 2:
            continue
        vcs = {int(vc_of(p, i)) for i in range(len(p) - 1)}
        if len(vcs) != 1:
            return None  # hop-varying VC — not OD-batchable this way
        assign[sd] = next(iter(vcs))
    return assign


def bal_partition_tables(
    pg: dict,
    tabs: list[dict],
) -> list[dict[tuple[int, int], list[int]]] | None:
    """Round-robin OD → tables (prefer preferred table's path)."""
    if not tabs:
        return None
    R = len(tabs)
    compute = pg["compute_nodes"]
    batches: list[dict[tuple[int, int], list[int]]] = [{} for _ in range(R)]
    ods = [(s, d) for s in compute for d in compute if s != d]
    ods.sort()
    for k, (s, d) in enumerate(ods):
        order = [k % R] + [i for i in range(R) if i != k % R]
        chosen = None
        for i in order:
            p = tabs[i]["paths"].get((s, d))
            if p is not None:
                chosen = (i, p)
                break
        if chosen is None:
            return None
        batches[chosen[0]][(s, d)] = chosen[1]
    return batches


def ud_bal_batches(pg: dict, R: int) -> list[dict] | None:
    tabs = enum_ud_tables(pg, ("ud",))
    if len(tabs) < R:
        return None
    return bal_partition_tables(pg, tabs[:R])


def dual_ud_batches(sol: dict) -> list[dict] | None:
    paths = sol["paths"]
    vc_of = sol.get("vc_of")
    if vc_of is None:
        return None
    assign = assign_from_vc_of(paths, vc_of)
    if assign is None:
        return None
    return batches_from_vc_assign(paths, assign)


def lash_batches(sol: dict) -> list[dict] | None:
    return dual_ud_batches(sol)  # same constant-VC-per-path shape
