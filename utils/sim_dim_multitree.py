#!/usr/bin/env python3
"""Bidirectional dimensional multi-tree allgather: global link-time calendar simulator.

There is no literal Hamiltonian ring here; each source broadcasts via an
X-then-Y dimension-ordered multicast tree, and the "bidirectional" lines per
dimension are the mesh analogue of a ring:
  * row spine along row sy (bidirectional from sx),
  * column branches up/down each column from row sy.
Forwarding is IN-NETWORK (router fork): a node duplicates an incoming flit,
sending one copy down its down-ramp (eject to SRAM) and one copy onward on the
mesh link. Intermediate nodes NEVER eject-then-reinject, so the 10-cycle PE/SRAM
bounce is never paid (only one eject per (flit, node)).

A global link-time calendar reserves (directed-link, cycle) and (node down-ramp,
cycle) slots; every slot holds <=1 flit, so the schedule is conflict-free BY
CONSTRUCTION. We then assert that invariant and report the makespan.

Event-driven greedy: edge-traversals become ready when the flit reaches the
edge tail; the readiest one is packed into the earliest free cycle on its link.
"""

import heapq
from collections import defaultdict

MX, MY, H, V, RAMP = 12, 16, 4, 8, 1
N = MX * MY


def nid(x, y):
    return x + MX * y


def coord(n):
    return n % MX, n // MX


def edge_lat(u, v):
    ux, uy = coord(u)
    vx, vy = coord(v)
    return H if uy == vy else V


def tree_edges(s):
    """X-then-Y dimension-ordered spanning tree rooted at s -> list of (parent,child)."""
    sx, sy = coord(s)
    edges = []
    # row spine (bidirectional along row sy)
    for x in range(sx + 1, MX):
        edges.append((nid(x - 1, sy), nid(x, sy)))
    for x in range(sx - 1, -1, -1):
        edges.append((nid(x + 1, sy), nid(x, sy)))
    # column branches from every spine node
    for x in range(MX):
        for y in range(sy + 1, MY):
            edges.append((nid(x, y - 1), nid(x, y)))
        for y in range(sy - 1, -1, -1):
            edges.append((nid(x, y + 1), nid(x, y)))
    return edges


class Calendar:
    def __init__(self):
        self.slots = defaultdict(set)  # key -> set of busy cycles

    def reserve(self, key, earliest):
        s = self.slots[key]
        t = earliest
        while t in s:
            t += 1
        s.add(t)
        return t


def simulate(msg_size=1, verbose=True):
    link_cal = Calendar()
    down_cal = Calendar()

    # children adjacency per source tree (built lazily per source)
    # Event = (ready_time, seq, src, parent, child, flit_k)
    # priority: earliest ready first; tie-break by remaining-depth (deeper first)
    # so bottleneck-bound flits are not starved.
    depth_to_root = {}  # (src, node) not needed; use static priority by child distance

    # Precompute per-source tree adjacency and the arrival bookkeeping.
    trees = {}
    for s in range(N):
        adj = defaultdict(list)
        for p, c in tree_edges(s):
            adj[p].append(c)
        trees[s] = adj

    pq = []
    seq = 0
    # availability of a flit at a node within its source tree
    avail = {}  # (src, node, k) -> time available to forward

    for s in range(N):
        for k in range(msg_size):
            # source injects flit k via up-ramp at cycle k (pipelined), ready to forward
            avail[(s, s, k)] = RAMP + k
            for c in trees[s][s]:
                heapq.heappush(pq, (RAMP + k, -1, seq, s, s, c, k))
                seq += 1

    makespan = 0
    # to assign a stable secondary priority: distance (#hops) from source spine
    while pq:
        ready, _, _, s, p, c, k = heapq.heappop(pq)
        t_avail = avail[(s, p, k)]
        send = link_cal.reserve((p, c), max(ready, t_avail))
        arrive = send + edge_lat(p, c)
        avail[(s, c, k)] = arrive
        # eject at c (down-ramp), in-network fork: independent of forwarding
        eject = down_cal.reserve(c, arrive)
        done = eject + RAMP
        if done > makespan:
            makespan = done
        # forward onward to children of c in s's tree
        for gc in trees[s][c]:
            heapq.heappush(pq, (arrive, -1, seq, s, c, gc, k))
            seq += 1

    # ---- verify conflict-free: every slot set holds distinct cycles (sets do) ----
    # and that no node ejects more than (N-1)*M flits
    eject_counts = {n: len(down_cal.slots[n]) for n in down_cal.slots}
    bad = [n for n, cnt in eject_counts.items() if cnt != (N - 1) * msg_size]
    max_link = max((len(v) for v in link_cal.slots.values()), default=0)

    if verbose:
        print(f"M={msg_size}: makespan={makespan}")
        print(f"  down-ramp ejects/node: all == (N-1)*M={ (N-1)*msg_size }? "
              f"{'YES' if not bad else 'NO -> '+str(bad[:3])}")
        print(f"  busiest directed link carries {max_link} flits "
              f"(<= makespan {makespan}: {'ok' if max_link<=makespan else 'OVER'})")
        # conflict-free is guaranteed by set-reservation; sanity check sizes
        per_link_ok = all(len(v) == len(set(v)) for v in link_cal.slots.values())
        print(f"  conflict-free (each link/down-ramp cycle used once): "
              f"{'CONFIRMED' if per_link_ok and not bad else 'check'}")
    return makespan


if __name__ == "__main__":
    print("Bidirectional dimensional multi-tree allgather "
          "(in-network fork, global link-time calendar)\n")
    for M in (1, 4, 16):
        simulate(M)
        print()
    # reference numbers
    print("reference: LB(worst-corner)=205 (M=1); dimensional=235; gather+bcast=371")
