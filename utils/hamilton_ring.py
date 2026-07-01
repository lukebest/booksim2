#!/usr/bin/env python3
"""Hamiltonian ring construction and fault-aware ring search on a 2D mesh.

The mesh is the BookSim convention: node id = x + mx*y, x in [0,mx), y in [0,my).
Mesh edges connect grid neighbours; a horizontal edge (same row) costs H cycles,
a vertical edge (same column) costs V cycles (latency handled by the simulator).

This module answers: given a healthy mesh and a set of failed nodes / failed
links, find a Hamiltonian RING over the surviving nodes that uses only surviving
links, so an allgather can be pipelined around it.

==========================================================================
FAULT-AWARE HAMILTONIAN RING SEARCH ALGORITHM
==========================================================================
A grid graph G(mx,my) is bipartite with colour c(x,y) = (x+y) mod 2. Two facts
drive the whole search:

  * A Hamiltonian CYCLE requires the two colour classes to be equal in size.
  * A Hamiltonian PATH requires the colour classes to differ by at most one.

Removing nodes changes the colour balance; removing links does not. Hence:

  * Link faults keep colour balance -> we still target a closed cycle and just
    have to route the ring around the missing links.
  * A 2x2 node hole removes 2 black + 2 white nodes -> balance preserved -> a
    cycle is still possible.
  * A 1x1 hole (1 node) or a 3x3 hole (9 nodes) makes the classes unequal ->
    NO Hamiltonian cycle exists; only an open Hamiltonian PATH is possible.

A unidirectional ring allgather fundamentally needs a closed cycle (one-way data
must return to every node), so when only a path exists the unidirectional scheme
is reported infeasible while the bidirectional scheme runs over the open path.

The search proceeds in stages (see find_ring):

  1. Build the surviving graph G' = grid - dead_nodes - dead_links.
  2. Feasibility pre-checks:
       - connectivity (BFS): an unreachable node makes any ring impossible;
       - colour balance: decide target = cycle (balanced) or path (|diff|==1);
       - minimum degree: a cycle needs every node to keep degree >= 2.
  3. Healthy fast path: with no faults the canonical boustrophedon (snake)
     Hamiltonian cycle is returned directly (no search).
  4. Search: a depth-first Hamiltonian walk with
       - Warnsdorff ordering: always extend toward the surviving neighbour with
         the fewest remaining options first (keeps the search almost backtrack
         free on grids with small holes),
       - a snake-order tie-break so the recovered ring stays close to the
         healthy snake (good, comparable allgather makespans),
       - connectivity pruning: after every tentative step the still-unvisited
         sub-graph must remain connected, otherwise some node can never be
         reached and we backtrack immediately,
       - a wall-clock budget as a final safety net.
     For a cycle we additionally require the last node to be adjacent to the
     start; for a path we accept any full cover.
  5. The returned order is validated (every surviving node once, every
     consecutive pair a surviving edge, closure for cycles) before use.

Because the specified faults are small, localized holes/link-cuts on a 16x16
grid, Warnsdorff + connectivity pruning resolves every scenario almost
instantly; the time-budgeted full search is only a safety net.
"""

import sys
import time


def nid(x, y, mx):
    return x + mx * y


def coord(n, mx):
    return n % mx, n // mx


def grid_neighbors(n, mx, my):
    x, y = coord(n, mx)
    res = []
    if x + 1 < mx:
        res.append(nid(x + 1, y, mx))
    if x - 1 >= 0:
        res.append(nid(x - 1, y, mx))
    if y + 1 < my:
        res.append(nid(x, y + 1, mx))
    if y - 1 >= 0:
        res.append(nid(x, y - 1, mx))
    return res


def build_adj(mx, my, dead_nodes=(), dead_links=()):
    """Surviving-graph adjacency: node -> sorted list of surviving neighbours."""
    dead_nodes = set(dead_nodes)
    dead_links = {frozenset(l) for l in dead_links}
    adj = {}
    for n in range(mx * my):
        if n in dead_nodes:
            continue
        nb = []
        for m in grid_neighbors(n, mx, my):
            if m in dead_nodes:
                continue
            if frozenset((n, m)) in dead_links:
                continue
            nb.append(m)
        adj[n] = sorted(nb)
    return adj


def snake_cycle(mx, my):
    """Canonical boustrophedon Hamiltonian cycle on a healthy mesh (my even).

    Row 0 is traversed left-to-right, rows 1..my-1 snake within columns
    1..mx-1, and column 0 (rows my-1..1) is the return spine that closes the
    cycle back to (0,0). Rows are favoured so the ring uses the cheap
    horizontal hops (H < V), which makes this the best healthy ("golden") ring.
    """
    if my % 2 != 0:
        raise ValueError("snake_cycle requires an even number of rows (my)")
    order = []
    for x in range(mx):                        # row 0, full, left to right
        order.append(nid(x, 0, mx))
    for y in range(1, my):                      # rows 1..my-1, columns 1..mx-1
        if y % 2 == 1:                          # odd row: right -> left (to col 1)
            for x in range(mx - 1, 0, -1):
                order.append(nid(x, y, mx))
        else:                                   # even row: left (col 1) -> right
            for x in range(1, mx):
                order.append(nid(x, y, mx))
    for y in range(my - 1, 0, -1):              # return spine along column 0
        order.append(nid(0, y, mx))
    return order


def color_counts(nodes, mx):
    c = [0, 0]
    for n in nodes:
        x, y = coord(n, mx)
        c[(x + y) & 1] += 1
    return c


def is_connected(adj):
    nodes = list(adj.keys())
    if not nodes:
        return True
    seen = {nodes[0]}
    stack = [nodes[0]]
    while stack:
        u = stack.pop()
        for v in adj[u]:
            if v not in seen:
                seen.add(v)
                stack.append(v)
    return len(seen) == len(adj)


def validate_ring(order, adj, is_cycle):
    """True if order visits every surviving node once via surviving edges."""
    nodes = set(adj.keys())
    if len(order) != len(nodes) or set(order) != nodes:
        return False
    L = len(order)
    last = L if is_cycle else L - 1
    for i in range(last):
        u = order[i]
        v = order[(i + 1) % L]
        if v not in adj[u]:
            return False
    return True


def _unvisited_connected(adj, visited):
    """True if the still-unvisited sub-graph is connected (or empty)."""
    start = None
    for u in adj:
        if u not in visited:
            start = u
            break
    if start is None:
        return True
    seen = {start}
    stack = [start]
    count_unvisited = sum(1 for u in adj if u not in visited)
    while stack:
        u = stack.pop()
        for v in adj[u]:
            if v not in visited and v not in seen:
                seen.add(v)
                stack.append(v)
    return len(seen) == count_unvisited


def _search(adj, start, target, color_of, snake_pos=None, deadline=None):
    """DFS Hamiltonian walk from start. target in {'cycle','path'}.

    Pruning: Warnsdorff ordering, bipartite colour-alternation (a strong
    necessary condition on a bipartite grid), and connectivity of the
    still-unvisited sub-graph.
    """
    n = len(adj)
    visited = {start}
    path = [start]
    # Remaining (unvisited) node counts per colour.
    unv = [0, 0]
    for node in adj:
        unv[color_of[node]] += 1
    unv[color_of[start]] -= 1

    def undeg(u):
        return sum(1 for v in adj[u] if v not in visited)

    def key(u, v):
        # Warnsdorff first, then prefer following the snake order, then id.
        if snake_pos is not None:
            follows = 0 if snake_pos.get(v) == snake_pos.get(u, -99) + 1 else 1
            return (undeg(v), follows, v)
        return (undeg(v), v)

    def dfs(u):
        if deadline is not None and time.time() > deadline:
            return None
        if len(path) == n:
            if target == "cycle":
                return path[:] if start in adj[u] else None
            return path[:]
        cands = [v for v in adj[u] if v not in visited]
        cands.sort(key=lambda v: key(u, v))
        for v in cands:
            cv = color_of[v]
            visited.add(v)
            path.append(v)
            unv[cv] -= 1
            ok = True
            r = n - len(path)
            if r > 0:
                # next node after v must have colour 1-cv and the remaining
                # alternating cover needs exactly these per-colour counts.
                opp = 1 - cv
                if unv[opp] != (r + 1) // 2:
                    ok = False
                elif target == "cycle" and not any(
                        nb not in visited for nb in adj[start]):
                    # the ring can only close if a neighbour of the start is
                    # still free to become the final node.
                    ok = False
                elif r == 1 and target == "cycle":
                    last = next(w for w in adj if w not in visited)
                    if start not in adj[last]:
                        ok = False
                    elif not _unvisited_connected(adj, visited):
                        ok = False
                elif not _unvisited_connected(adj, visited):
                    ok = False
            if ok:
                res = dfs(v)
                if res is not None:
                    return res
            unv[cv] += 1
            visited.discard(v)
            path.pop()
        return None

    return dfs(start)


def _candidate_starts(adj, k=4):
    """Lowest-degree surviving nodes make the best Warnsdorff seeds/endpoints."""
    return [n for n, _ in sorted(adj.items(), key=lambda kv: (len(kv[1]), kv[0]))][:k]


def find_ring(mx, my, dead_nodes=(), dead_links=(), time_budget=20.0):
    """Find a Hamiltonian ring over the surviving mesh.

    Returns a dict:
      feasible    : bool
      is_cycle    : bool (True closed cycle, False open path)
      order       : list[int] surviving-node visit order, or None
      reason      : short explanation
    """
    dead_nodes = set(dead_nodes)
    dead_links = list(dead_links)

    if not dead_nodes and not dead_links:
        order = snake_cycle(mx, my)
        return {"feasible": True, "is_cycle": True, "order": order,
                "reason": "healthy boustrophedon snake cycle"}

    adj = build_adj(mx, my, dead_nodes, dead_links)
    nodes = set(adj.keys())
    if not nodes:
        return {"feasible": False, "is_cycle": False, "order": None,
                "reason": "no surviving nodes"}
    if not is_connected(adj):
        return {"feasible": False, "is_cycle": False, "order": None,
                "reason": "surviving graph is disconnected"}

    c0, c1 = color_counts(nodes, mx)
    color_of = {node: ((node % mx + node // mx) & 1) for node in nodes}
    snake_full = snake_cycle(mx, my)
    snake_pos = {node: i for i, node in enumerate(snake_full) if node in nodes}

    sys.setrecursionlimit(max(10000, len(nodes) + 100))
    deadline = time.time() + time_budget
    starts = _candidate_starts(adj)

    # Target a closed cycle only when colour classes are balanced and every
    # node can keep degree >= 2.
    if c0 == c1 and all(len(adj[u]) >= 2 for u in adj):
        for s in starts:
            order = _search(adj, s, "cycle", color_of, snake_pos, deadline)
            if order and validate_ring(order, adj, True):
                return {"feasible": True, "is_cycle": True, "order": order,
                        "reason": "Hamiltonian cycle recovered"}

    # Fall back to an open Hamiltonian path. On an imbalanced bipartite graph
    # both endpoints must be the majority colour, so seed from those nodes.
    if abs(c0 - c1) <= 1:
        maj = 0 if c0 >= c1 else 1
        path_starts = [n for n, _ in sorted(adj.items(),
                                            key=lambda kv: (len(kv[1]), kv[0]))
                       if color_of[n] == maj][:4] or starts
        for s in path_starts:
            order = _search(adj, s, "path", color_of, snake_pos, deadline)
            if order and validate_ring(order, adj, False):
                return {"feasible": True, "is_cycle": False, "order": order,
                        "reason": "Hamiltonian path recovered (colour imbalance "
                                  "or no surviving cycle)"}

    return {"feasible": False, "is_cycle": False, "order": None,
            "reason": "no Hamiltonian ring found within time budget"}


# --------------------------------------------------------------------------
# Fault scenario catalogue: corner / edge / center regions.
# --------------------------------------------------------------------------
def _link_region_sets(mx, my):
    """Three near-region links per region that do not drop any node below
    degree 2, so a cycle stays feasible and the comparison is meaningful."""
    cx, cy = mx // 2, my // 2
    return {
        "corner": [((1, 0), (1, 1)), ((2, 0), (2, 1)), ((1, 1), (2, 1))],
        "edge":   [((cx, 0), (cx, 1)), ((cx + 1, 0), (cx + 1, 1)),
                   ((cx, 1), (cx + 1, 1))],
        "center": [((cx, cy), (cx, cy + 1)), ((cx + 1, cy), (cx + 1, cy + 1)),
                   ((cx, cy), (cx + 1, cy))],
    }


def link_fault_scenarios(mx, my):
    regions = _link_region_sets(mx, my)
    out = []
    for region, links in regions.items():
        for cnt in (1, 2, 3):
            sel = links[:cnt]
            dl = [(nid(ax, ay, mx), nid(bx, by, mx)) for (ax, ay), (bx, by) in sel]
            human = ", ".join(f"({ax},{ay})-({bx},{by})"
                              for (ax, ay), (bx, by) in sel)
            out.append({
                "name": f"link_{region}_{cnt}",
                "fault_class": "link",
                "region": region,
                "detail": str(cnt),
                "dead_nodes": [],
                "dead_links": dl,
                "desc": f"{cnt} link fault(s) @ {region}: {human}",
            })
    return out


def _block(x0, y0, s, mx):
    return [nid(x, y, mx) for x in range(x0, x0 + s) for y in range(y0, y0 + s)]


def node_fault_scenarios(mx, my):
    cx, cy = mx // 2, my // 2
    out = []
    for s in (1, 2, 3):
        anchors = {
            "corner": (0, 0),
            "edge":   (cx - (s - 1) // 2, 0),
            "center": (cx - (s - 1) // 2, cy - (s - 1) // 2),
        }
        for region, (x0, y0) in anchors.items():
            dn = _block(x0, y0, s, mx)
            out.append({
                "name": f"node_{region}_{s}x{s}",
                "fault_class": "node",
                "region": region,
                "detail": f"{s}x{s}",
                "dead_nodes": dn,
                "dead_links": [],
                "desc": f"{s}x{s} node hole @ {region} anchor ({x0},{y0})",
            })
    return out


def _node_color(n, mx):
    x, y = coord(n, mx)
    return (x + y) & 1


def find_ring_rebalanced(mx, my, dead_nodes, dead_links, time_budget=12.0):
    """Recover a Hamiltonian CYCLE on a colour-imbalanced node hole by disabling
    a few extra ("redundant") majority-colour nodes near the hole.

    An odd hole (1x1 = 1 node, 3x3 = 9 nodes) leaves the bipartite colour
    classes unequal, which forbids any cycle. Colour balance is necessary but
    not sufficient, so sacrifice candidates (majority-colour, hole-boundary
    first) are tried until one actually yields a cycle. Returns a find_ring-like
    dict augmented with 'sacrificed' (extra disabled nodes) and 'dead_nodes_used'.
    """
    import itertools

    dead = set(dead_nodes)
    nodes = set(build_adj(mx, my, dead, dead_links).keys())
    c0, c1 = color_counts(nodes, mx)
    if c0 == c1:
        r = find_ring(mx, my, dead, dead_links, time_budget)
        r["sacrificed"] = []
        r["dead_nodes_used"] = sorted(dead)
        return r

    maj = 0 if c0 > c1 else 1
    need = abs(c0 - c1)
    boundary = sorted(
        n for n in nodes
        if _node_color(n, mx) == maj
        and any(m in dead for m in grid_neighbors(n, mx, my)))
    others = sorted(n for n in nodes
                    if _node_color(n, mx) == maj and n not in boundary)
    cands = boundary + others[:24]
    per_try = min(3.0, time_budget)

    for combo in itertools.combinations(cands, need):
        trial = dead | set(combo)
        adj = build_adj(mx, my, trial, dead_links)
        if not is_connected(adj):
            continue
        cc = color_counts(set(adj), mx)
        if cc[0] != cc[1]:
            continue
        r = find_ring(mx, my, trial, dead_links, time_budget=per_try)
        if r["feasible"] and r["is_cycle"]:
            r["sacrificed"] = sorted(combo)
            r["dead_nodes_used"] = sorted(trial)
            return r

    return {"feasible": False, "is_cycle": False, "order": None,
            "sacrificed": [], "dead_nodes_used": sorted(dead),
            "reason": "no rebalanced cycle found within budget"}


def rebalanced_node_scenarios(mx, my):
    """For colour-imbalanced node holes (no cycle), produce a variant that
    sacrifices nearby nodes to restore a feasible Hamiltonian cycle."""
    out = []
    for sc in node_fault_scenarios(mx, my):
        nodes = set(build_adj(mx, my, sc["dead_nodes"], sc["dead_links"]).keys())
        c0, c1 = color_counts(nodes, mx)
        if c0 == c1:
            continue  # already balanced (e.g. 2x2 holes); no sacrifice needed
        r = find_ring_rebalanced(mx, my, sc["dead_nodes"], sc["dead_links"])
        if not r["feasible"]:
            continue
        extra = r["sacrificed"]
        coords = ",".join(f"({n % mx},{n // mx})" for n in extra)
        out.append({
            "name": sc["name"] + "_rebal",
            "fault_class": "node_rebal",
            "region": sc["region"],
            "detail": sc["detail"] + f"+{len(extra)}",
            "dead_nodes": r["dead_nodes_used"],
            "dead_links": list(sc["dead_links"]),
            "sacrificed": extra,
            "desc": sc["desc"] + f"; 牺牲邻近 {len(extra)} 个节点恢复成环 [{coords}]",
        })
    return out


def quadrant_fault_scenarios(mx, my):
    """One full quadrant (mx/2 x my/2) dead — four corner placements."""
    hw, hh = mx // 2, my // 2
    if hw != hh:
        raise ValueError("quadrant_fault_scenarios requires square quadrants")
    quads = [
        ("Q0", (0, 0)),
        ("Q1", (hw, 0)),
        ("Q2", (0, hh)),
        ("Q3", (hw, hh)),
    ]
    out = []
    for qname, (x0, y0) in quads:
        dn = _block(x0, y0, hw, mx)
        region = "corner" if qname in ("Q0", "Q1", "Q3") else "edge"
        out.append({
            "name": f"quad_{qname}",
            "fault_class": "quadrant",
            "region": region,
            "detail": qname,
            "dead_nodes": dn,
            "dead_links": [],
            "desc": f"1/4 象限 {qname} 全部故障 ({hw}x{hh} @ ({x0},{y0}), "
                    f"{len(dn)} nodes)",
        })
    return out


def all_scenarios(mx, my):
    return (link_fault_scenarios(mx, my) + node_fault_scenarios(mx, my)
            + quadrant_fault_scenarios(mx, my))


if __name__ == "__main__":
    MX, MY = 16, 16
    base = snake_cycle(MX, MY)
    adj0 = build_adj(MX, MY)
    print(f"healthy snake cycle valid: {validate_ring(base, adj0, True)} "
          f"(N={len(base)})")
    for sc in all_scenarios(MX, MY):
        r = find_ring(MX, MY, sc["dead_nodes"], sc["dead_links"])
        kind = "cycle" if r["is_cycle"] else ("path" if r["feasible"] else "-")
        print(f"{sc['name']:18s} feasible={r['feasible']!s:5s} {kind:5s} "
              f"len={len(r['order']) if r['order'] else 0:3d}  {r['reason']}")
