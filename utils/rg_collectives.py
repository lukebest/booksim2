#!/usr/bin/env python3
"""Collective traffic patterns for the request-grant NoC study.

Patterns:
  alltoall   — async unicast: every (s,d) is an independent flow
  broadcast  — async: one multicast tree from root
  reduce     — async: gather tree to root (PE-local reduction, no in-net ALU)
  allgather  — sync barrier by default; async variant = one multicast tree / src
  allreduce  — sync barrier: gather-to-root + broadcast (Tier A / ADR-002)

any-to-any family (permutation, k_permutation, transpose, bitcomp, cluster,
hotspot_any, halfxhalf, cornerAtoB) — arbitrary (s,d) sets used to decide when
non-XY routing pays for itself. One flow per DISTINCT (s,d): a repeated pair is
one VOQ carrying more flits, never two concurrent flows.
"""

from __future__ import annotations

import random
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Literal

from rg_topo import MX, MY, N, Topology, central_arbiter_node, coord, nid

AnyToAny = Literal["permutation", "k_permutation", "transpose", "bitcomp",
                   "cluster", "hotspot_any", "halfxhalf", "cornerAtoB"]
Pattern = Literal["alltoall", "allgather", "allreduce", "broadcast", "reduce",
                  "permutation", "k_permutation", "transpose", "bitcomp",
                  "cluster", "hotspot_any", "halfxhalf", "cornerAtoB"]
RGMode = Literal["async_flow", "async_tree", "sync_barrier"]

ANYTOANY: tuple[str, ...] = ("permutation", "k_permutation", "transpose",
                             "bitcomp", "cluster", "hotspot_any", "halfxhalf",
                             "cornerAtoB")


@dataclass
class Flow:
    """A unicast or multicast grant unit."""
    flow_id: int
    src: int
    dsts: list[int]                 # one dst for unicast; many for tree
    paths: dict[int, list[int]]     # dst -> node path (from src)
    tree_edges: list[tuple[int, int]] = field(default_factory=list)
    kind: str = "unicast"           # "unicast" | "tree"
    m: int = 1


@dataclass
class Collective:
    pattern: Pattern
    mode: RGMode
    root: int
    flows: list[Flow]
    n: int
    m: int
    topo_kind: str

    @property
    def n_flows(self) -> int:
        return len(self.flows)

    def summary(self) -> dict[str, Any]:
        return {
            "pattern": self.pattern,
            "mode": self.mode,
            "root": self.root,
            "n_flows": self.n_flows,
            "m": self.m,
            "topo": self.topo_kind,
            "n_unicast_paths": sum(1 for f in self.flows if f.kind == "unicast"),
            "n_trees": sum(1 for f in self.flows if f.kind == "tree"),
        }


def _multicast_tree(topo: Topology, src: int,
                    members: list[int] | None = None
                    ) -> tuple[list[tuple[int, int]], dict[int, list[int]]]:
    """Shortest-path multicast tree via reverse union of DOR paths.

    Edges are (parent, child) directed away from src. Also returns the
    unicast DOR path from src to each member (for reservation / DES).
    """
    members = members if members is not None else list(range(topo.n))
    edges: set[tuple[int, int]] = set()
    paths: dict[int, list[int]] = {}
    for d in members:
        if d == src:
            continue
        p = topo.dor_path(src, d)
        paths[d] = p
        for i in range(len(p) - 1):
            edges.add((p[i], p[i + 1]))
    # parent map: each node except src has exactly one parent in a tree;
    # if DOR union creates multi-parent, keep shortest-wire parent
    parent: dict[int, int] = {}
    children: dict[int, list[int]] = defaultdict(list)
    # BFS from src over the edge set to build a proper tree
    from collections import deque
    adj: dict[int, list[int]] = defaultdict(list)
    for u, v in edges:
        adj[u].append(v)
    seen = {src}
    q = deque([src])
    tree_edges: list[tuple[int, int]] = []
    while q:
        u = q.popleft()
        for v in sorted(adj[u]):
            if v in seen:
                continue
            seen.add(v)
            parent[v] = u
            children[u].append(v)
            tree_edges.append((u, v))
            q.append(v)
    # ensure all members reachable (DOR union on torus/mesh always is)
    return tree_edges, paths


def _gather_tree(topo: Topology, root: int
                 ) -> tuple[list[tuple[int, int]], dict[int, list[int]]]:
    """Gather = reverse of broadcast tree: edges (child, parent) toward root.
    Paths are from each source to root (unicast DOR).
    """
    bcast_edges, _ = _multicast_tree(topo, root)
    # reverse edges
    gather_edges = [(c, p) for p, c in bcast_edges]
    paths = {}
    for s in range(topo.n):
        if s == root:
            continue
        paths[s] = topo.dor_path(s, root)
    return gather_edges, paths


def build_alltoall(topo: Topology, m: int = 1) -> Collective:
    flows = []
    fid = 0
    for s in range(topo.n):
        for d in range(topo.n):
            if s == d:
                continue
            path = topo.dor_path(s, d)
            flows.append(Flow(fid, s, [d], {d: path}, kind="unicast", m=m))
            fid += 1
    return Collective("alltoall", "async_flow", root=-1, flows=flows,
                      n=topo.n, m=m, topo_kind=topo.kind)


def build_broadcast(topo: Topology, m: int = 1, root: int | None = None
                    ) -> Collective:
    root = root if root is not None else 0
    edges, paths = _multicast_tree(topo, root)
    flow = Flow(0, root, [d for d in range(topo.n) if d != root],
                paths, tree_edges=edges, kind="tree", m=m)
    return Collective("broadcast", "async_tree", root=root, flows=[flow],
                      n=topo.n, m=m, topo_kind=topo.kind)


def build_reduce(topo: Topology, m: int = 1, root: int | None = None
                 ) -> Collective:
    """Gather + PE-local reduction (ADR-002 / Arch-A2). Each non-root source
    is an independent unicast flow to root (async)."""
    root = root if root is not None else 0
    edges, paths = _gather_tree(topo, root)
    flows = []
    fid = 0
    for s in range(topo.n):
        if s == root:
            continue
        flows.append(Flow(fid, s, [root], {root: paths[s]},
                          tree_edges=[], kind="unicast", m=m))
        fid += 1
    # attach full gather tree on the collective for documentation
    col = Collective("reduce", "async_flow", root=root, flows=flows,
                     n=topo.n, m=m, topo_kind=topo.kind)
    # stash tree on first flow for report convenience
    if flows:
        flows[0].tree_edges = edges
    return col


def build_allgather(topo: Topology, m: int = 1, sync: bool = True
                    ) -> Collective:
    """Allgather: each source multicasts its data to all others.

    sync=True  → sync_barrier mode (wait for all 48 requests, then grant)
    sync=False → async_tree mode (each grant = one multicast tree)
    """
    flows = []
    for s in range(topo.n):
        edges, paths = _multicast_tree(topo, s)
        flows.append(Flow(s, s, [d for d in range(topo.n) if d != s],
                          paths, tree_edges=edges, kind="tree", m=m))
    mode: RGMode = "sync_barrier" if sync else "async_tree"
    return Collective("allgather", mode, root=-1, flows=flows,
                      n=topo.n, m=m, topo_kind=topo.kind)


def build_allreduce(topo: Topology, m: int = 1, root: int | None = None
                    ) -> Collective:
    """Allreduce Tier A: sync gather to root + broadcast from root.

    Modeled as sync_barrier over two phases packaged as:
      phase-0: N-1 unicast gathers to root
      phase-1: 1 broadcast tree from root
    For request-grant we issue one sync barrier covering both phases
    (single grant epoch). Flows listed in order.
    """
    root = root if root is not None else central_arbiter_node()
    g_edges, g_paths = _gather_tree(topo, root)
    b_edges, b_paths = _multicast_tree(topo, root)
    flows = []
    fid = 0
    for s in range(topo.n):
        if s == root:
            continue
        flows.append(Flow(fid, s, [root], {root: g_paths[s]},
                          kind="unicast", m=m))
        fid += 1
    flows.append(Flow(fid, root, [d for d in range(topo.n) if d != root],
                      b_paths, tree_edges=b_edges, kind="tree", m=m))
    return Collective("allreduce", "sync_barrier", root=root, flows=flows,
                      n=topo.n, m=m, topo_kind=topo.kind)


# ---------------------------------------------------------------------------
# any-to-any family: arbitrary (s,d) sets, used to test whether ROMM helps
# ---------------------------------------------------------------------------

def anytoany_pairs(topo: Topology, kind: str, *, k: int = 4, seed: int = 0,
                   n_hot: int = 2, cluster: tuple[int, int] = (4, 3)
                   ) -> list[tuple[int, int]]:
    """(src, dst) list WITH multiplicity -- callers must dedup (see below).

    permutation    one random derangement: 48 flows, every node sends once
    k_permutation  k superposed derangements. Draws may repeat a pair, and a
                   repeat is NOT a second flow: the same VOQ carrying twice the
                   data is one flow with 2x the flit count. Returning the raw
                   multiset here keeps that decision in one place
                   (`build_anytoany`), because counting duplicates as separate
                   flows inflates every load bound.
    transpose      geometric (x,y) -> (y, x mod my). NOT a bijection on a
                   non-square mesh (x and x+my collide), which is the point:
                   it is an imbalanced any-to-any with real hot links.
    bitcomp        point reflection (x,y) -> (mx-1-x, my-1-y). A true
                   permutation that crosses BOTH bisections maximally, so it is
                   the rectangular stand-in for the classic worst case.
    cluster        all-to-all inside each cluster only (locality)
    hotspot_any    every node -> n_hot fixed destinations
    halfxhalf      left half -> right half, all pairs
    cornerAtoB     top-left 2x2 block -> bottom-right 2x2 block, all pairs
    """
    mx, my, n = topo.mx, topo.my, topo.n
    rng = random.Random(seed)
    out: list[tuple[int, int]] = []

    def derangement() -> list[tuple[int, int]]:
        while True:
            perm = list(range(n))
            rng.shuffle(perm)
            if all(perm[i] != i for i in range(n)):
                return [(i, perm[i]) for i in range(n)]

    if kind == "permutation":
        return derangement()
    if kind == "k_permutation":
        for _ in range(max(1, k)):
            out.extend(derangement())
        return out
    if kind == "transpose":
        for s in range(n):
            x, y = coord(s, mx)
            d = nid(y % mx, x % my, mx)
            if d != s:
                out.append((s, d))
        return out
    if kind == "bitcomp":
        for s in range(n):
            x, y = coord(s, mx)
            d = nid(mx - 1 - x, my - 1 - y, mx)
            if d != s:
                out.append((s, d))
        return out
    if kind == "cluster":
        cw, ch = cluster
        groups: dict[tuple[int, int], list[int]] = defaultdict(list)
        for s in range(n):
            x, y = coord(s, mx)
            groups[(x // cw, y // ch)].append(s)
        for members in groups.values():
            for a in members:
                for b in members:
                    if a != b:
                        out.append((a, b))
        return out
    if kind == "hotspot_any":
        hots = [nid(mx // 2, my // 2, mx), nid(0, 0, mx)][:max(1, n_hot)]
        for s in range(n):
            for h in hots:
                if s != h:
                    out.append((s, h))
        return out
    if kind == "halfxhalf":
        left = [s for s in range(n) if coord(s, mx)[0] < mx // 2]
        right = [s for s in range(n) if coord(s, mx)[0] >= mx // 2]
        for a in left:
            for b in right:
                out.append((a, b))
        return out
    if kind == "cornerAtoB":
        a_blk = [nid(x, y, mx) for x in range(2) for y in range(2)]
        b_blk = [nid(x, y, mx) for x in range(mx - 2, mx)
                 for y in range(my - 2, my)]
        for a in a_blk:
            for b in b_blk:
                out.append((a, b))
        return out
    raise ValueError(f"unknown any-to-any pattern: {kind}")


def build_anytoany(topo: Topology, kind: str, m: int = 1, *, k: int = 4,
                   seed: int = 0, n_hot: int = 2,
                   cluster: tuple[int, int] = (4, 3)) -> Collective:
    """One unicast flow per DISTINCT (s,d); duplicates raise m, not flow count.

    A VOQ is identified by (s,d), so two requests for the same VOQ are two
    grants of one queue -- modelling them as two independent flows would let
    the scheduler run them concurrently on the same path and would inflate
    every lower bound. Deduplication here is what keeps the load bounds honest.
    """
    raw = anytoany_pairs(topo, kind, k=k, seed=seed, n_hot=n_hot,
                         cluster=cluster)
    mult: dict[tuple[int, int], int] = defaultdict(int)
    for pair in raw:
        mult[pair] += 1
    flows: list[Flow] = []
    for fid, ((s, d), c) in enumerate(sorted(mult.items())):
        flows.append(Flow(fid, s, [d], {d: topo.dor_path(s, d)},
                          kind="unicast", m=m * c))
    col = Collective(kind, "async_flow", root=-1, flows=flows, n=topo.n,
                     m=m, topo_kind=topo.kind)
    col.n_raw_pairs = len(raw)          # type: ignore[attr-defined]
    col.n_unique_pairs = len(mult)      # type: ignore[attr-defined]
    return col


def build_collective(topo: Topology, pattern: Pattern, m: int = 1,
                     sync: bool | None = None, root: int | None = None,
                     **kw: Any) -> Collective:
    if pattern in ANYTOANY:
        return build_anytoany(topo, pattern, m, **kw)
    if pattern == "alltoall":
        return build_alltoall(topo, m)
    if pattern == "broadcast":
        return build_broadcast(topo, m, root=root)
    if pattern == "reduce":
        return build_reduce(topo, m, root=root)
    if pattern == "allgather":
        s = True if sync is None else sync
        return build_allgather(topo, m, sync=s)
    if pattern == "allreduce":
        return build_allreduce(topo, m, root=root)
    raise ValueError(pattern)


def tree_link_schedule(topo: Topology, flow: Flow
                       ) -> list[tuple[tuple[int, int], int]]:
    """For a multicast tree: list of (edge, prefix_delay_from_src).

    prefix_delay = wire delay along the unique tree path from src to the
    tail of the edge. Used by the bufferless reservation engine.
    """
    if flow.kind != "tree" or not flow.tree_edges:
        # fall back to unicast paths
        out = []
        for d, path in flow.paths.items():
            delay = 0
            for i in range(len(path) - 1):
                out.append(((path[i], path[i + 1]), delay))
                delay += topo.link_lat(path[i], path[i + 1])
        return out

    # Build parent→children and compute depth delays via BFS
    children: dict[int, list[int]] = defaultdict(list)
    for p, c in flow.tree_edges:
        children[p].append(c)
    delay_at = {flow.src: 0}
    from collections import deque
    q = deque([flow.src])
    out: list[tuple[tuple[int, int], int]] = []
    while q:
        u = q.popleft()
        for v in children.get(u, ()):
            pref = delay_at[u]
            out.append(((u, v), pref))
            delay_at[v] = pref + topo.link_lat(u, v)
            q.append(v)
    return out


if __name__ == "__main__":
    import json
    topo = Topology("mesh")
    for pat in ("alltoall", "broadcast", "reduce", "allgather", "allreduce"):
        col = build_collective(topo, pat, m=1,
                               sync=(pat in ("allgather", "allreduce")))
        print(json.dumps(col.summary(), indent=2))
    col_async = build_allgather(topo, m=1, sync=False)
    print("async allgather:", col_async.summary())

    from rg_mesh_paths import cut_bound, max_load, pairs_of, xy_plan
    print("\n--- any-to-any family (XY load vs cut bound) ---")
    print(f"{'pattern':14} {'raw':>6} {'uniq':>6} {'flits':>7} "
          f"{'xy_max':>7} {'cut_lb':>7} {'romm?':>6}")
    for pat in ANYTOANY:
        col = build_anytoany(topo, pat, m=1, k=4, seed=1)
        prs = pairs_of(col)
        plan = xy_plan(prs)
        cb = cut_bound(prs)["cut_bound"]
        print(f"{pat:14} {col.n_raw_pairs:>6} {col.n_unique_pairs:>6} "
              f"{sum(f.m for f in col.flows):>7} {plan.max_load:>7} "
              f"{cb:>7} {str(plan.max_load > cb):>6}")
