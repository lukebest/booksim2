#!/usr/bin/env python3
"""Effect of wider down-ramp (B flit/cycle eject) on zero-eject-buffer makespan.

Down-ramp bandwidth B: each node may eject up to B flits per cycle (still E=0,
i.e. each ejected flit leaves on its arrival cycle; we just allow B of them to
share a cycle). Link bandwidth stays 1 flit/cycle/directed-link.

We report, for B in {1,2,4}:
  - eject bandwidth term  ceil((N-1)*M / B) + ramp   (pure throughput floor)
  - latency floor         max_d max_{s!=d} (ramp + dist(s,d)) + ramp
                          (farthest flit must still physically arrive+eject;
                           independent of B -> the hard wall when B grows)
  - release-packing LB*(B): per-node, pack N-1 unit ejects with release times
                            r=ramp+dist(s,d) into slots of capacity B; max over d
  - greedy achievable makespan (E=0, bounded per-hop wait W) at this B
"""

import argparse
from collections import defaultdict

from sched_no_eject_buffer import coord, nid, link_lat, tree_children


def dim_dist(s, d, mx, h, vv):
    sx, sy = coord(s, mx); dx, dy = coord(d, mx)
    return abs(sx - dx) * h + abs(sy - dy) * vv


def latency_floor(mx, my, h, vv, ramp):
    """max over destinations of farthest source arrival + ramp (B-independent)."""
    n = mx * my
    best = 0
    for d in range(n):
        far = max(ramp + dim_dist(s, d, mx, h, vv) for s in range(n) if s != d)
        best = max(best, far)
    return best + ramp


def packing_lb_bw(mx, my, h, vv, ramp, B):
    """Per-node release-time packing with eject capacity B; max over nodes."""
    n = mx * my
    best = -1; bnode = 0
    for d in range(n):
        rs = sorted(ramp + dim_dist(s, d, mx, h, vv) for s in range(n) if s != d)
        cnt = defaultdict(int); last = 0
        for r in rs:
            t = r
            while cnt[t] >= B:
                t += 1
            cnt[t] += 1
            last = max(last, t)
        mk = last + ramp
        if mk > best:
            best = mk; bnode = d
    return best, bnode


def greedy_bw(mx, my, h, vv, ramp, wcap, B):
    """Greedy E=0 schedule with down-ramp capacity B and per-hop wait <= W."""
    n = mx * my
    trees = {s: tree_children(s, mx, my) for s in range(n)}
    link_busy = defaultdict(set)
    down_cnt = defaultdict(lambda: defaultdict(int))  # node -> cycle -> #ejects
    cx0, cy0 = (mx - 1) / 2, (my - 1) / 2
    srcs = sorted(range(n),
                  key=lambda s: -(abs(coord(s, mx)[0] - cx0) + abs(coord(s, mx)[1] - cy0)))
    makespan = 0
    for s in srcs:
        off = 0
        while True:
            tent_l, tent_d, tl = [], [], set()
            tent_dcnt = defaultdict(lambda: defaultdict(int))
            avail = {s: off + ramp}
            order = [s]; qi = 0; ok = True
            while qi < len(order) and ok:
                p = order[qi]; qi += 1
                for c in trees[s][p]:
                    ready = avail[p]; lk = p * 100000 + c
                    lat = link_lat(p, c, mx, h, vv)
                    t = ready; found = False
                    while wcap is None or t - ready <= wcap:
                        if t not in link_busy[lk] and (lk, t) not in tl:
                            arrive = t + lat
                            occ = down_cnt[c][arrive] + tent_dcnt[c][arrive]
                            if occ < B:
                                found = True; break
                        t += 1
                    if not found:
                        ok = False; break
                    tent_l.append((lk, t)); tl.add((lk, t))
                    tent_d.append((c, arrive)); tent_dcnt[c][arrive] += 1
                    avail[c] = arrive; order.append(c)
            if ok:
                for (lk, t) in tent_l:
                    link_busy[lk].add(t)
                for (d, ej) in tent_d:
                    down_cnt[d][ej] += 1
                    makespan = max(makespan, ej + ramp)
                break
            off += 1
    return makespan


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ramp", type=int, default=1)
    ap.add_argument("--w", type=int, default=8, help="per-hop wait cap for greedy")
    args = ap.parse_args()
    cases = [(6, 8, 4, 8), (12, 16, 4, 8)]
    for mx, my, h, vv in cases:
        n = mx * my
        lfloor = latency_floor(mx, my, h, vv, args.ramp)
        print(f"\n=== mesh {mx}x{my}  N={n}  (latency floor = {lfloor}, B-independent) ===")
        print(f"  {'B':>3} | {'eject-term':>10} | {'LB*(B)':>7} | {'greedy(W=%s)' % args.w:>12}")
        for B in (1, 2, 4):
            eterm = -(-(n - 1) // B) + args.ramp   # ceil((N-1)/B)+ramp
            lb, _ = packing_lb_bw(mx, my, h, vv, args.ramp, B)
            g = greedy_bw(mx, my, h, vv, args.ramp, args.w, B)
            print(f"  {B:>3} | {eterm:>10} | {lb:>7} | {g:>12}")


if __name__ == "__main__":
    main()
