#!/usr/bin/env python3
"""Buffer vs makespan tradeoff sweep for dimensional multi-tree allgather.

We cap the per-flit in-network wait at W cycles: when forwarding a flit on a
link, its send must lie in [ready, ready+W]. If the earliest free link slot
would exceed ready+W, the flit is NOT allowed to occupy that congested port yet
— instead its *injection* (whole-path) is pushed later, deferring the conflict
to the source PE queue (which is free). Smaller W => less in-network buffering
but (potentially) larger makespan. W=0 reproduces the strict zero-buffer rigid
schedule; W=inf reproduces the bandwidth-optimal calendar.

For each W we report makespan and the realized max per-output-port buffer depth.
"""

import argparse
import heapq
from collections import defaultdict


def coord(n, mx):
    return n % mx, n // mx


def nid(x, y, mx):
    return x + mx * y


def link_lat(u, v, mx, h, vv):
    return h if coord(u, mx)[1] == coord(v, mx)[1] else vv


def tree_children(s, mx, my):
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


def free_slot(busy, e, cap=None):
    """earliest free cycle >= e; if cap given, only accept if <= e+cap else None."""
    t = e
    s = busy
    while t in s:
        t += 1
        if cap is not None and t > e + cap:
            return None
    return t


def schedule(mx, my, h, vv, ramp, wcap):
    n = mx * my
    trees = {s: tree_children(s, mx, my) for s in range(n)}
    link_busy = defaultdict(set)
    down_busy = defaultdict(set)

    # per source we may need to retry with a larger injection offset when a hop
    # would exceed the wait cap. We schedule one source at a time, fully, so a
    # rejected hop just bumps that source's injection and restarts the source.
    inject = {}
    makespan = 0
    # process sources far-from-center first (helps packing)
    cx0, cy0 = (mx - 1) / 2, (my - 1) / 2
    srcs = sorted(range(n), key=lambda s: -(abs(coord(s, mx)[0] - cx0) + abs(coord(s, mx)[1] - cy0)))

    for s in srcs:
        off = 0
        while True:
            # try to schedule the whole tree of s with injection offset `off`
            tentative_links = []  # (linkkey, cycle)
            tentative_downs = []  # (node, cycle)
            tentative_set = set()  # (linkkey, cycle)
            avail = {s: off + ramp}
            ok = True
            # BFS over tree in arrival order
            order = [s]
            qi = 0
            while qi < len(order):
                p = order[qi]; qi += 1
                for c in trees[s][p]:
                    ready = avail[p]
                    lk = p * 100000 + c
                    gb = link_busy[lk]
                    t = ready
                    while t in gb or (lk, t) in tentative_set:
                        t += 1
                        if wcap is not None and t - ready > wcap:
                            break
                    if wcap is not None and t - ready > wcap:
                        ok = False
                        break
                    arrive = t + link_lat(p, c, mx, h, vv)
                    tentative_links.append((lk, t))
                    tentative_set.add((lk, t))
                    avail[c] = arrive
                    order.append(c)
                if not ok:
                    break
            if ok:
                # ejects: each node d != s ejects at avail[d]; must be free slot,
                # but eject waiting is the down-ramp/PE buffer — allow it freely
                # (we are minimizing *in-network link* buffer, not PE eject queue).
                for d in range(n):
                    if d == s:
                        continue
                    ej = free_slot(down_busy[d], avail[d])
                    tentative_downs.append((d, ej))
                # commit
                for (lk, t) in tentative_links:
                    link_busy[lk].add(t)
                for (d, ej) in tentative_downs:
                    down_busy[d].add(ej)
                    makespan = max(makespan, ej + ramp)
                inject[s] = off
                break
            off += 1

    # realized per-output-port buffer depth: max waiting = derived from sends vs ready
    # Recompute by replay is complex; instead bound via link_busy gaps is not exact.
    # We instead recompute occupancy from committed schedule.
    # Rebuild send/ready per link:
    # (For reporting we approximate max port depth by re-deriving arrivals.)
    return makespan, inject


def realized_port_depth(mx, my, h, vv, ramp, inject):
    """Replay with fixed injects and earliest-free (cap=inf) to get sends, then
    measure max per-output-port queue depth."""
    n = mx * my
    trees = {s: tree_children(s, mx, my) for s in range(n)}
    link_busy = defaultdict(set)
    down_busy = defaultdict(set)
    intervals = defaultdict(list)  # linkkey -> (ready, send)
    eject_iv = defaultdict(list)   # node -> (arrive, eject) waiting at down-ramp
    # deterministic order by (inject, source) then BFS
    pq = []
    seq = 0
    avail = {}
    for s in range(n):
        avail[(s, s)] = inject[s] + ramp
        for c in trees[s][s]:
            heapq.heappush(pq, (inject[s] + ramp, seq, s, s, c)); seq += 1
    while pq:
        ready, _, s, p, c = heapq.heappop(pq)
        rp = avail[(s, p)]
        lk = p * 100000 + c
        t = rp
        while t in link_busy[lk]:
            t += 1
        link_busy[lk].add(t)
        intervals[lk].append((rp, t))
        arrive = t + link_lat(p, c, mx, h, vv)
        avail[(s, c)] = arrive
        ej = arrive
        while ej in down_busy[c]:
            ej += 1
        down_busy[c].add(ej)
        eject_iv[c].append((arrive, ej))
        for gc in trees[s][c]:
            heapq.heappush(pq, (arrive, seq, s, c, gc)); seq += 1

    def maxoverlap(iv):
        ev = []
        for r, ss in iv:
            if ss <= r:
                continue
            ev.append((r, 1)); ev.append((ss, -1))
        cur = mx_ = 0
        for t, d in sorted(ev, key=lambda x: (x[0], x[1])):
            cur += d; mx_ = max(mx_, cur)
        return mx_
    port = max((maxoverlap(v) for v in intervals.values()), default=0)
    eject = max((maxoverlap(v) for v in eject_iv.values()), default=0)
    return port, eject


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--mx", type=int, default=6)
    ap.add_argument("--my", type=int, default=8)
    ap.add_argument("--h", type=int, default=4)
    ap.add_argument("--v", type=int, default=8)
    ap.add_argument("--ramp", type=int, default=1)
    args = ap.parse_args()
    ref = {(6, 8): 78, (12, 16): 205}.get((args.mx, args.my))

    print(f"mesh {args.mx}x{args.my} (buffered-opt makespan={ref})")
    print(f"{'wait cap W':>10} | {'makespan':>8} | {'link-port buf':>13} | {'eject(PE) buf':>13}")
    caps = [0, 1, 2, 4, 8, None]
    for w in caps:
        mk, inj = schedule(args.mx, args.my, args.h, args.v, args.ramp, w)
        port, eject = realized_port_depth(args.mx, args.my, args.h, args.v, args.ramp, inj)
        label = "inf" if w is None else str(w)
        print(f"{label:>10} | {mk:>8} | {port:>13} | {eject:>13}")


if __name__ == "__main__":
    main()
