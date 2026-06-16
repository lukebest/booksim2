#!/usr/bin/env python3
"""Realistic-constraint scheduler for dimensional multi-tree allgather.

Two HARD physical constraints (vs the bandwidth-optimal calendar):

  1. ZERO down-ramp / eject buffer (E=0): a flit reaching its destination must
     be consumed by the PE the *same* cycle it arrives. No eject queue. Formally
     the eject cycle must equal the arrival cycle (ej == arrive); the down-ramp
     is simply busy on that cycle (<=1 eject/cycle still holds).

  2. BOUNDED per-hop in-network wait (W finite): a flit may sit in an output
     port at most W cycles before being sent. Unbounded holding is forbidden
     because a stalled flit blocks the wormhole stream behind it (踩踏: it would
     be trampled by / collide with following flits on that port).

When a hop would need to wait > W, or an eject would need to wait > E, the whole
source injection is pushed one cycle later (the contention is deferred to the
*source PE* injection queue, which is allowed to hold un-launched data). We
greedily pick, per source, the smallest injection offset that satisfies BOTH
caps with no link-slot and no down-ramp-slot collision.

W=0, E=0 reproduces the fully-rigid zero-buffer schedule. Larger W gives the
packer slack to dodge eject collisions without huge injection offsets.
"""

import argparse
from collections import defaultdict

K = 100000


def coord(n, mx):
    return n % mx, n // mx


def nid(x, y, mx):
    return x + mx * y


def link_lat(u, v, mx, h, vv):
    return h if coord(u, mx)[1] == coord(v, mx)[1] else vv


def tree_children(s, mx, my):
    """X-then-Y dimension-ordered multicast tree rooted at s (bidirectional)."""
    sx, sy = coord(s, mx)
    adj = defaultdict(list)
    for x in range(sx + 1, mx):
        adj[nid(x - 1, sy, mx)].append(nid(x, sy, mx))
    for x in range(sx - 1, -1, -1):
        adj[nid(x + 1, sy, mx)].append(nid(x, sy, mx))
    for x in range(mx):
        for y in range(sy + 1, my):
            adj[nid(x, y - 1, mx)].append(nid(x, y, mx))
        for y in range(sy - 1, -1, -1):
            adj[nid(x, y + 1, mx)].append(nid(x, y, mx))
    return adj


def schedule(mx, my, h, vv, ramp, wcap, ecap):
    """Return (makespan, inject, feasible). wcap/ecap=None means unbounded."""
    n = mx * my
    trees = {s: tree_children(s, mx, my) for s in range(n)}
    link_busy = defaultdict(set)
    down_busy = defaultdict(set)

    cx0, cy0 = (mx - 1) / 2, (my - 1) / 2
    srcs = sorted(range(n),
                  key=lambda s: -(abs(coord(s, mx)[0] - cx0) + abs(coord(s, mx)[1] - cy0)))

    inject = {}
    makespan = 0
    OFF_LIMIT = 100000  # safety bound on injection-offset search

    for s in srcs:
        off = 0
        placed = False
        while off < OFF_LIMIT:
            tent_links = []          # (lk, t)
            tent_downs = []          # (node, ej)
            tent_lset = set()        # (lk, t)
            tent_dset = set()        # (node, ej)
            avail = {s: off + ramp}
            order = [s]
            qi = 0
            ok = True
            while qi < len(order) and ok:
                p = order[qi]; qi += 1
                for c in trees[s][p]:
                    ready = avail[p]
                    lk = p * K + c
                    lat = link_lat(p, c, mx, h, vv)
                    # Choose the last-hop send time t in [ready, ready+W] so that
                    # BOTH the output link slot is free AND the resulting eject
                    # cycle (arrive = t+lat) is free at c's down-ramp. Using the
                    # bounded in-network wait W to *smooth* arrivals is exactly
                    # what lets the down-ramp run with ZERO eject buffer (E=ecap,
                    # here 0). If no such t exists within W, defer the whole
                    # source injection (bump off) -> contention goes to the
                    # source PE queue, not an in-network/eject buffer.
                    t = ready
                    found = False
                    while wcap is None or t - ready <= wcap:
                        if t not in link_busy[lk] and (lk, t) not in tent_lset:
                            arrive = t + lat
                            ej = arrive
                            slack_ok = True
                            while ej in down_busy[c] or (c, ej) in tent_dset:
                                ej += 1
                                if ecap is not None and ej - arrive > ecap:
                                    slack_ok = False
                                    break
                            if slack_ok:
                                found = True
                                break
                        t += 1
                    if not found:
                        ok = False
                        break
                    tent_links.append((lk, t)); tent_lset.add((lk, t))
                    tent_downs.append((c, ej)); tent_dset.add((c, ej))
                    avail[c] = arrive
                    order.append(c)
            if ok:
                for (lk, t) in tent_links:
                    link_busy[lk].add(t)
                for (d, ej) in tent_downs:
                    down_busy[d].add(ej)
                    makespan = max(makespan, ej + ramp)
                inject[s] = off
                placed = True
                break
            off += 1
        if not placed:
            return makespan, inject, False

    # sanity: every node ejects n-1 distinct cycles
    feasible = all(len(down_busy[d]) == n - 1 for d in range(n))
    return makespan, inject, feasible


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--mx", type=int, default=6)
    ap.add_argument("--my", type=int, default=8)
    ap.add_argument("--h", type=int, default=4)
    ap.add_argument("--v", type=int, default=8)
    ap.add_argument("--ramp", type=int, default=1)
    args = ap.parse_args()

    ref = {(6, 8): 78, (12, 16): 205}.get((args.mx, args.my))
    n = args.mx * args.my
    floor = (n - 1) + args.ramp  # down-ramp bandwidth floor: N-1 ejects @1/cy + ramp
    print(f"mesh {args.mx}x{args.my}  bandwidth-opt(with buffers)={ref}  "
          f"eject-bw floor={floor}")
    print("constraint: eject buffer E=0 (consume on arrival), in-network wait <= W")
    print(f"{'W':>6} | {'makespan':>8} | {'vs opt':>7} | {'feasible':>8}")
    for w in [0, 1, 2, 4, 8, 16, 32, None]:
        mk, inj, ok = schedule(args.mx, args.my, args.h, args.v, args.ramp, w, 0)
        label = "inf" if w is None else str(w)
        ratio = f"{mk/ref:.2f}x" if ref else "-"
        print(f"{label:>6} | {mk:>8} | {ratio:>7} | {'YES' if ok else 'NO':>8}")


if __name__ == "__main__":
    main()
