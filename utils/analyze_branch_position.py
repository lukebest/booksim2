#!/usr/bin/env python3
"""Formal branch-position analysis for tree allgather under the
conflict-free / non-blocking / link-time-division model.

For each multicast-tree family we measure the three quantities that a
bufferless conflict-free schedule is bounded by, AS A FUNCTION OF WHERE THE
TREE BRANCHES (forks) sit:

  fill   = tree DEPTH in latency  = max over sources of the longest
           inject->leaf latency in that source's delivery DAG.  This is the
           pure latency floor; it shrinks when forks move toward the source
           (shallow/wide tree) and grows toward the circumference when the
           only fork is at the root (deep/narrow tree = a ring).

  Lmax   = peak directed-link aggregate load = max over directed mesh links e
           of the number of (source) flits whose tree-path uses e.  With the
           1 flit/cycle calendar this is a bandwidth floor: makespan >= Lmax.
           Forks near the root spread load onto many links (low Lmax but they
           pile latency); a ring perfectly balances Lmax = floor(N/2) (bi) or
           N-1 (uni) on every ring edge.

  eject  = ceil((N-1)/ramp_bw): every node must receive N-1 foreign flits
           through its single down-ramp.  Hardware floor, scheme-independent.

The conflict-free makespan is bounded below by max(fill, Lmax, eject) (+O(ramp)
tails); we print that prediction next to the measured sim makespan so the
attribution (which bound dominates) is explicit.
"""

import argparse
from collections import defaultdict, deque

import sim_fused_rings as fr


def tree_depth(ch, s):
    """Longest inject->leaf latency in source s's delivery DAG (the fill)."""
    depth = {s: 0}
    dq = deque([s])
    best = 0
    while dq:
        p = dq.popleft()
        for c in ch.get(p, []):
            d = depth[p] + fr.edge_lat(p, c)
            if c not in depth or d > depth[c]:
                depth[c] = d
                dq.append(c)
                best = max(best, d)
    return best


def link_load(deliveries):
    """Aggregate flit count routed over each directed link, peak."""
    load = defaultdict(int)
    for s, ch in deliveries.items():
        for p, kids in ch.items():
            for c in kids:
                load[(p, c)] += 1
    return max(load.values(), default=0)


def metrics(name, builder, sz, bidir, ramp_bw):
    fr.cfg(sz, sz, 4, 6)
    n = sz * sz
    deliveries = {s: builder(s, bidir) for s in range(n)}
    fill = max(tree_depth(deliveries[s], s) for s in range(n)) + 2 * fr.RAMP
    Lmax = link_load(deliveries)
    eject = (n - 1 + ramp_bw - 1) // ramp_bw
    mk, ej, bl, bd = fr.simulate(deliveries, ramp_bw)
    ok = all(ej[x] == n - 1 for x in range(n))
    pred = max(fill, Lmax, eject)
    dom = max((("fill", fill), ("Lmax", Lmax), ("eject", eject)),
              key=lambda kv: kv[1])[0]
    return dict(name=name, fill=fill, Lmax=Lmax, eject=eject, pred=pred,
                dom=dom, sim=mk, busiest=bl, ok=ok)


def builders():
    full = lambda sz: fr.ham_cycle_rect(0, 0, sz, sz)
    return {
        "single-ring (1 root fork)": lambda s, b: fr.build_ring_delivery(
            fr.ham_cycle_rect(0, 0, fr._MX, fr._MY), s, b),
        "border (3-level quad fork)": fr.build_border_delivery,
        "multitree (root row+col fork)": lambda s, b: fr.build_multitree_delivery(s),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sizes", type=int, nargs="+", default=[8, 16])
    args = ap.parse_args()
    bs = builders()
    for sz in args.sizes:
        n = sz * sz
        print(f"\n================ {sz}x{sz}  (N={n}, H=4 V=6 ramp=1) ================")
        print(f"{'scheme':30s} {'dir':3s} {'fill':>5s} {'Lmax':>5s} {'eject':>6s} "
              f"{'pred':>5s} {'dom':>6s} {'sim':>6s} {'busy':>5s} ok")
        for name, fn in bs.items():
            for bidir in (False, True):
                rb = 2 if bidir else 1
                if name.startswith("multitree") and bidir:
                    continue  # multitree is inherently directed (no bi variant)
                m = metrics(name, fn, sz, bidir, rb)
                tag = "bi " if bidir else "uni"
                print(f"{m['name']:30s} {tag:3s} {m['fill']:5d} {m['Lmax']:5d} "
                      f"{m['eject']:6d} {m['pred']:5d} {m['dom']:>6s} {m['sim']:6d} "
                      f"{m['busiest']:5d} {('Y' if m['ok'] else 'N')}")


if __name__ == "__main__":
    main()
