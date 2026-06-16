#!/usr/bin/env python3
"""Smarter zero-eject-buffer scheduler for dimensional multi-tree allgather.

Goal: get closer to the true zero-eject-buffer floor than the naive greedy
(sched_no_eject_buffer.py: 12x16 W=8 -> 330, W=inf -> 274).

Two parts:

1. RELEASE-TIME PACKING LOWER BOUND (per node down-ramp).
   Node d must eject N-1 flits, 1/cycle, and flit from source s cannot arrive
   before r = ramp + dist(s,d) (up-ramp + dimension-ordered path latency),
   because injection offset can only DELAY, never advance. Packing unit jobs
   with release times on one machine: process in release order, finish =
   max(r_i, prev+1). makespan_d = that + ramp(down). LB* = max over d. This is
   a valid lower bound for ANY zero-eject-buffer schedule (it ignores link
   contention and the inject-shared-across-dests coupling, which only hurt).

2. BOTTLENECK-ALIGNED SCHEDULER.
   Pick the bottleneck node B (max packing makespan, = far corner). Pre-pack B's
   ejects optimally and let each source's target B-eject cycle define its
   scheduling priority + a good starting injection offset. Then place sources
   in that order with the global link/down-ramp calendar, using bounded per-hop
   in-network wait W to smooth every node's arrivals onto free down-ramp cycles
   (E=0). On infeasibility within W, bump injection (defer to source PE queue).
"""

import argparse
from collections import defaultdict

from sched_no_eject_buffer import coord, nid, link_lat, tree_children

K = 100000


def dim_dist(s, d, mx, h, vv):
    sx, sy = coord(s, mx); dx, dy = coord(d, mx)
    return abs(sx - dx) * h + abs(sy - dy) * vv


def packing_lb(mx, my, h, vv, ramp):
    """Per-node release-time packing makespan; return (LB*, bottleneck node)."""
    n = mx * my
    best = -1; bnode = 0
    per_node = {}
    for d in range(n):
        rs = sorted(ramp + dim_dist(s, d, mx, h, vv) for s in range(n) if s != d)
        t = 0
        for r in rs:
            t = r if r > t + 1 else t + 1
        mk = t + ramp
        per_node[d] = mk
        if mk > best:
            best = mk; bnode = d
    return best, bnode, per_node


def bottleneck_targets(mx, my, h, vv, ramp, B):
    """Optimal packed eject cycle at B for each source; defines priority+offset."""
    n = mx * my
    rel = [(ramp + dim_dist(s, B, mx, h, vv), s) for s in range(n) if s != B]
    rel.sort()
    t = 0
    target = {}
    for r, s in rel:
        t = r if r > t + 1 else t + 1
        target[s] = t            # B-eject cycle for source s
    target[B] = 0                # B injects earliest
    return target


def schedule(mx, my, h, vv, ramp, wcap, B, target):
    n = mx * my
    trees = {s: tree_children(s, mx, my) for s in range(n)}
    link_busy = defaultdict(set)
    down_busy = defaultdict(set)

    # source order: far-from-center first (matches naive greedy's good ordering)
    cx0, cy0 = (mx - 1) / 2, (my - 1) / 2
    srcs = sorted(range(n),
                  key=lambda s: -(abs(coord(s, mx)[0] - cx0) + abs(coord(s, mx)[1] - cy0)))

    inject = {}
    makespan = 0
    for s in srcs:
        off0 = 0  # start from 0; ordering alone drives packing
        off = off0
        placed = False
        while off < off0 + 100000:
            tent_links = []; tent_downs = []
            tent_lset = set(); tent_dset = set()
            avail = {s: off + ramp}
            order = [s]; qi = 0; ok = True
            while qi < len(order) and ok:
                p = order[qi]; qi += 1
                for c in trees[s][p]:
                    ready = avail[p]
                    lk = p * K + c
                    lat = link_lat(p, c, mx, h, vv)
                    t = ready; found = False
                    while wcap is None or t - ready <= wcap:
                        if t not in link_busy[lk] and (lk, t) not in tent_lset:
                            arrive = t + lat
                            if arrive not in down_busy[c] and (c, arrive) not in tent_dset:
                                found = True; break
                        t += 1
                    if not found:
                        ok = False; break
                    tent_links.append((lk, t)); tent_lset.add((lk, t))
                    tent_downs.append((c, arrive)); tent_dset.add((c, arrive))
                    avail[c] = arrive
                    order.append(c)
            if ok:
                for (lk, t) in tent_links:
                    link_busy[lk].add(t)
                for (d, ej) in tent_downs:
                    down_busy[d].add(ej)
                    makespan = max(makespan, ej + ramp)
                inject[s] = off; placed = True; break
            off += 1
        if not placed:
            return makespan, inject, False
    feasible = all(len(down_busy[d]) == n - 1 for d in range(n))
    return makespan, inject, feasible


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mx", type=int, default=12)
    ap.add_argument("--my", type=int, default=16)
    ap.add_argument("--h", type=int, default=4)
    ap.add_argument("--v", type=int, default=8)
    ap.add_argument("--ramp", type=int, default=1)
    args = ap.parse_args()
    mx, my, h, vv, ramp = args.mx, args.my, args.h, args.v, args.ramp

    lb, B, _ = packing_lb(mx, my, h, vv, ramp)
    bx, by = coord(B, mx)
    naive = {(6, 8): "W8=103 / Winf=82", (12, 16): "W8=330 / Winf=274"}.get((mx, my), "?")
    print(f"mesh {mx}x{my}  N={mx*my}")
    print(f"  eject-bandwidth floor (N-1)+ramp     : {(mx*my-1)+ramp}")
    print(f"  release-packing LB* (zero eject buf)  : {lb}  @ bottleneck ({bx},{by})")
    print(f"  naive greedy (sched_no_eject_buffer)  : {naive}")
    target = bottleneck_targets(mx, my, h, vv, ramp, B)
    print(f"  -- bottleneck-aligned v2 --")
    print(f"  {'W':>5} | {'makespan':>8} | {'vs LB*':>7} | {'feasible':>8}")
    for w in [0, 1, 2, 4, 8, 16, None]:
        mk, inj, ok = schedule(mx, my, h, vv, ramp, w, B, target)
        label = "inf" if w is None else str(w)
        ratio = f"{mk/lb:.2f}x"
        print(f"  {label:>5} | {mk:>8} | {ratio:>7} | {'YES' if ok else 'NO':>8}")


if __name__ == "__main__":
    main()
