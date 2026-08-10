#!/usr/bin/env python3
"""Collective traffic patterns for the request-grant NoC study.

Patterns:
  alltoall   — async unicast: every (s,d) is an independent flow
  broadcast  — async: one multicast tree from root
  reduce     — async: gather tree to root (PE-local reduction, no in-net ALU)
  allgather  — sync barrier by default; async variant = one multicast tree / src
  allreduce  — sync barrier: gather-to-root + broadcast (Tier A / ADR-002)
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Literal

from rg_topo import MX, MY, N, Topology, central_arbiter_node, coord

Pattern = Literal["alltoall", "allgather", "allreduce", "broadcast", "reduce"]
RGMode = Literal["async_flow", "async_tree", "sync_barrier"]


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


def build_collective(topo: Topology, pattern: Pattern, m: int = 1,
                     sync: bool | None = None, root: int | None = None
                     ) -> Collective:
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
