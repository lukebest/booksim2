#!/usr/bin/env python3
"""Zero-in-network-buffer scheduler for dimensional multi-tree allgather.

Idea: if no flit ever waits at an intermediate router, then within one source's
X-then-Y multicast tree the timing is RIGID — every link (p->c) is used at
  inject_s + RAMP + dist(s, p)
and every node d ejects at
  inject_s + RAMP + dist(s, d)
where dist is Manhattan latency (|dx|*H + |dy|*V). All in-network waiting is
removed; the only "buffer" is the source PE injection queue.

We then slide each source's whole rigid footprint by a single injection offset
inject_s so that no directed-link slot and no down-ramp slot ever collide
(conflict-free + 0 in-network buffer by construction). Greedy: place sources in
an order, pick the smallest offset with no collision. Report makespan vs the
buffered optimum (6x8: 78, 12x16: 205).
"""

import argparse
from collections import defaultdict


def coord(n, mx):
    return n % mx, n // mx


def nid(x, y, mx):
    return x + mx * y


def dist(s, d, mx, h, v):
    sx, sy = coord(s, mx)
    dx, dy = coord(d, mx)
    return abs(sx - dx) * h + abs(sy - dy) * v


def tree_edges(s, mx, my):
    sx, sy = coord(s, mx)
    e = []
    for x in range(sx + 1, mx):
        e.append((nid(x - 1, sy, mx), nid(x, sy, mx)))
    for x in range(sx - 1, -1, -1):
        e.append((nid(x + 1, sy, mx), nid(x, sy, mx)))
    for x in range(mx):
        for y in range(sy + 1, my):
            e.append((nid(x, y - 1, mx), nid(x, y, mx)))
        for y in range(sy - 1, -1, -1):
            e.append((nid(x, y + 1, mx), nid(x, y, mx)))
    return e


def source_footprint(s, mx, my, h, v, ramp):
    """Relative (offset-0) reserved slots: list of ('L',p,c,rel) and ('D',d,rel)."""
    link_slots = []  # (linkkey, rel_cycle)
    for (p, c) in tree_edges(s, mx, my):
        link_slots.append((p * 10000 + c, ramp + dist(s, p, mx, h, v)))
    down_slots = []  # (node, rel_cycle)
    n = mx * my
    for d in range(n):
        if d == s:
            continue
        down_slots.append((d, ramp + dist(s, d, mx, h, v)))
    return link_slots, down_slots


def schedule(mx, my, h, v, ramp, order="corner-first"):
    n = mx * my
    foot = {s: source_footprint(s, mx, my, h, v, ramp) for s in range(n)}

    link_busy = defaultdict(set)   # linkkey -> set of absolute cycles
    down_busy = defaultdict(set)   # node    -> set of absolute cycles

    # ordering heuristic: schedule sources that are far from the crowd first
    cx0, cy0 = (mx - 1) / 2, (my - 1) / 2
    if order == "corner-first":
        srcs = sorted(range(n), key=lambda s: -(abs(coord(s, mx)[0] - cx0) + abs(coord(s, mx)[1] - cy0)))
    elif order == "center-first":
        srcs = sorted(range(n), key=lambda s: (abs(coord(s, mx)[0] - cx0) + abs(coord(s, mx)[1] - cy0)))
    else:
        srcs = list(range(n))

    inject = {}
    makespan = 0
    for s in srcs:
        links, downs = foot[s]
        off = 0
        while True:
            ok = True
            for (lk, rel) in links:
                if (off + rel) in link_busy[lk]:
                    ok = False
                    break
            if ok:
                for (d, rel) in downs:
                    if (off + rel) in down_busy[d]:
                        ok = False
                        break
            if ok:
                break
            off += 1
        for (lk, rel) in links:
            link_busy[lk].add(off + rel)
        for (d, rel) in downs:
            down_busy[d].add(off + rel)
            makespan = max(makespan, off + rel + ramp)
        inject[s] = off

    # verify: every node ejects exactly n-1 distinct cycles (0 buffer => distinct)
    ok_eject = all(len(down_busy[d]) == n - 1 for d in range(n))
    ok_link = all(len(v) == len(set(v)) for v in link_busy.values())  # sets => trivially true
    return makespan, inject, ok_eject and ok_link


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--mx", type=int, default=6)
    ap.add_argument("--my", type=int, default=8)
    ap.add_argument("--h", type=int, default=4)
    ap.add_argument("--v", type=int, default=8)
    ap.add_argument("--ramp", type=int, default=1)
    args = ap.parse_args()

    ref = {(6, 8): 78, (12, 16): 205}.get((args.mx, args.my))
    for order in ("center-first", "corner-first", "natural"):
        mk, inj, ok = schedule(args.mx, args.my, args.h, args.v, args.ramp, order)
        maxoff = max(inj.values())
        tag = f" (buffered-opt={ref})" if ref else ""
        print(f"order={order:13s} 0-buffer makespan={mk}{tag} | "
              f"max inject offset={maxoff} | conflict-free+0buf={'YES' if ok else 'NO'}")


if __name__ == "__main__":
    main()
