#!/usr/bin/env python3
"""Event-driven allgather simulator, generalized to arbitrary MX x MY 2D mesh
sizes, multi-flit messages (1..5 flit), and down-ramp bandwidth 1 or 2.

WHY A NEW ENGINE (vs. sched_zerobuf_compare.py's rigid 0-buffer packer):
sched_zerobuf_compare finds, per source, a SINGLE global injection offset that
must simultaneously satisfy every one of that source's O(N) footprint slots
(no in-network wait at all: once injected, a flit's whole path is rigid). That
"all slots must agree on one offset" search costs O(N) per source at 16x16 and
already needs ~O(N^3) work overall (every scheme's delivery structure touches
O(N) destinations, and popular links/ramps end up shared by O(N) different
sources' schedules) -> unusable beyond 16x16 (confirmed empirically: 32x32
does not finish in minutes).

This module keeps the SAME exact per-source delivery topologies (dimensional
tree / Hamilton ring / horizontal-band or vertical-band local-ring + tree) but
schedules them EVENT-DRIVEN: each hop is reserved at the earliest cycle its
own link/ramp is free, in causal (heapq) order, using FastCal's O(1)-amortized
union-find calendar (utils/fast_zerobuf_pack.py).

CORRECTION (previously this docstring said flits wait "at most a few cycles
in a router's own pipeline register" -- that was WRONG and has been
disproved empirically; see results/report_allgather_scale.html sec 3.5):
FastCal.reserve() finds the NEXT FREE cycle with NO upper bound on the wait,
i.e. this models an UNBOUNDED per-resource queue, not a small pipeline
register. Measured worst case: multitree at 64x64/ramp_bw=1/m=5 needs a
single node to hold 10233 flits at its down-ramp to realize its recorded
makespan. This unbounded-wait assumption is NOT equally "free" across
schemes: high-fanout schemes (multitree, fine-B hybrid) rely on much deeper
implicit queuing than low-fanout ones (ring, coarse-B hybrid/hybrid_v), so
raw cross-scheme makespan comparisons from this engine are NOT an
apples-to-apples zero/small-buffer comparison. utils/autogen_allgather.py's
recommend() compensates by filtering on each candidate's recorded
max_link_wait/max_ramp_wait (buffer_budget, default 2 flits) before picking
the fastest -- use that, not raw run_*() results, when buffer realism
matters. sched_zerobuf_compare.py's rigid packer remains the only source of
TRUE zero-buffer numbers (utils/sweep_zerobuf_strict.py, m=1 only, up to
16x16 -- its own cost blows up faster than linearly with message size m, not
just mesh size).

Because it is a different (more permissive) buffering assumption than the
strict 0-buffer packer, its numbers are not bit-identical to
results/zerobuf_16x16.json / results/zerobuf_strict_m1.json (they are equal
or lower, sometimes substantially so); both are reported side by side in the
study for transparency.

Scheme families implemented (same shapes as sched_zerobuf_compare.py):
  multitree   : bidirectional X-then-Y dimensional multicast tree per source.
  ring        : single global Hamilton (snake) ring, uni- or bi-directional.
  hybrid      : B horizontal bands, local Hamilton ring per band (uni/bi) +
                every column forks vertically to the other bands.
  hybrid_v    : B vertical bands, local Hamilton ring per band (uni/bi) +
                every row forks horizontally to the other bands.

quad/border (4-quadrant ring + central exchange) are NOT re-implemented here:
at 16x16 they already trail hybrid/hybrid_v by 1.6-3x (see
results/zerobuf_16x16.json: quad_bi=1097/523, border_bi=540 vs
hybrid_v_bi=334), so the multi-scale study focuses compute on the winning
families; 16x16 quad/border numbers are still cited from the existing JSON.
"""

import heapq
from collections import defaultdict

from fast_zerobuf_pack import FastCal

RAMP = 1


def nid(x, y, mx):
    return x + mx * y


def coord(n, mx):
    return n % mx, n // mx


def edge_lat(u, v, mx, h, vlat):
    return h if (u // mx) == (v // mx) else vlat


# --------------------------------------------------------------------------
# Topology builders: per-source {parent: [children]} in-network fork trees.
# --------------------------------------------------------------------------
def multitree_children(s, mx, my):
    sx, sy = coord(s, mx)
    children = defaultdict(list)
    prev = s
    for x in range(sx + 1, mx):
        cur = nid(x, sy, mx)
        children[prev].append(cur)
        prev = cur
    prev = s
    for x in range(sx - 1, -1, -1):
        cur = nid(x, sy, mx)
        children[prev].append(cur)
        prev = cur
    for x in range(mx):
        prev = nid(x, sy, mx)
        for y in range(sy + 1, my):
            cur = nid(x, y, mx)
            children[prev].append(cur)
            prev = cur
        prev = nid(x, sy, mx)
        for y in range(sy - 1, -1, -1):
            cur = nid(x, y, mx)
            children[prev].append(cur)
            prev = cur
    return children


def ham_cycle_band(mx, R, y0):
    """Closed Hamilton cycle over rows [y0, y0+R) x all mx columns.
    Requires mx even, R>=2 (comb construction)."""
    order = [nid(x, y0, mx) for x in range(mx)]
    for i, x in enumerate(range(mx - 1, -1, -1)):
        rows = range(1, R) if i % 2 == 0 else range(R - 1, 0, -1)
        for yloc in rows:
            order.append(nid(x, y0 + yloc, mx))
    return order


def ham_cycle_vband(mx, my, C, x0):
    """Closed Hamilton cycle over columns [x0, x0+C) x all my rows.
    Requires my even, C>=2 (comb construction)."""
    order = [nid(x0, y, mx) for y in range(my)]
    for i, y in enumerate(range(my - 1, -1, -1)):
        cols = range(1, C) if i % 2 == 0 else range(C - 1, 0, -1)
        for xloc in cols:
            order.append(nid(x0 + xloc, y, mx))
    return order


def _ring_chains(order, pos, s, bidir):
    n = len(order)
    i = pos[s]
    if bidir:
        a = n // 2
        b = (n - 1) - a
        fwd = [order[(i + k) % n] for k in range(a + 1)]
        bwd = [order[(i - k) % n] for k in range(b + 1)]
        return [fwd, bwd]
    chain = [order[(i + k) % n] for k in range(n)]
    return [chain]


def _path_chains(order, s, bidir):
    i = order.index(s)
    if not bidir:
        raise ValueError("an open path (single-row/column band) has no cycle; "
                          "unidirectional ring is undefined")
    fwd = order[i:]
    bwd = order[i::-1]
    return [c for c in (fwd, bwd) if len(c) > 1]


def _chains_to_children(chains):
    children = defaultdict(list)
    for ch in chains:
        for k in range(len(ch) - 1):
            children[ch[k]].append(ch[k + 1])
    return children


def ring_children(s, order, pos, bidir):
    return _chains_to_children(_ring_chains(order, pos, s, bidir))


def hybrid_children(s, mx, my, B, bidir):
    R = my // B
    sx, sy = coord(s, mx)
    y0 = (sy // R) * R
    if R >= 2:
        order = ham_cycle_band(mx, R, y0)
        pos = {nd: k for k, nd in enumerate(order)}
        chains = _ring_chains(order, pos, s, bidir)
    else:
        order = [nid(x, y0, mx) for x in range(mx)]
        chains = _path_chains(order, s, True)
    children = _chains_to_children(chains)

    for x in range(mx):
        top = nid(x, y0, mx)
        bot = nid(x, y0 + R - 1, mx)
        prev = top
        for yy in range(y0 - 1, -1, -1):
            cur = nid(x, yy, mx)
            children[prev].append(cur)
            prev = cur
        prev = bot
        for yy in range(y0 + R, my):
            cur = nid(x, yy, mx)
            children[prev].append(cur)
            prev = cur
    return children


def hybrid_v_children(s, mx, my, B, bidir):
    C = mx // B
    sx, sy = coord(s, mx)
    x0 = (sx // C) * C
    if C >= 2:
        order = ham_cycle_vband(mx, my, C, x0)
        pos = {nd: k for k, nd in enumerate(order)}
        chains = _ring_chains(order, pos, s, bidir)
    else:
        order = [nid(x0, y, mx) for y in range(my)]
        chains = _path_chains(order, s, True)
    children = _chains_to_children(chains)

    for y in range(my):
        left = nid(x0, y, mx)
        right = nid(x0 + C - 1, y, mx)
        prev = left
        for xx in range(x0 - 1, -1, -1):
            cur = nid(xx, y, mx)
            children[prev].append(cur)
            prev = cur
        prev = right
        for xx in range(x0 + C, mx):
            cur = nid(xx, y, mx)
            children[prev].append(cur)
            prev = cur
    return children


# --------------------------------------------------------------------------
# Generic event-driven engine: shared link/ramp calendars across all sources.
# --------------------------------------------------------------------------
def simulate_tree_family(children_per_source, n, mx, h, v, ramp_bw, flits,
                          ramp=RAMP):
    """Note: a hop's "own availability" (when its parent-side copy landed) is
    always exactly the `ready` timestamp it was pushed onto the heap with --
    every push sets ready=arrive (or inj+ramp for the root) at the same time
    the corresponding node "becomes available" for that (source, flit). So
    there is no need for a separate avail{} dict keyed by (s, node, k); that
    would just duplicate `ready` at O(N^2*flits) memory cost."""
    link_cal, up_cal, down_cal = FastCal(), FastCal(), FastCal()
    pq = []
    seq = 0
    for s, children in children_per_source.items():
        roots = children.get(s, ())
        if not roots:
            continue
        for k in range(flits):
            inj = up_cal.reserve(s, k, ramp_bw)
            ready = inj + ramp
            for c in roots:
                heapq.heappush(pq, (ready, seq, s, s, c, k))
                seq += 1

    makespan = 0
    max_link_wait = 0
    max_ramp_wait = 0
    down_eject_count = defaultdict(int)
    while pq:
        ready, _, s, p, c, k = heapq.heappop(pq)
        lat = edge_lat(p, c, mx, h, v)
        send = link_cal.reserve((p, c), ready, 1)
        if send - ready > max_link_wait:
            max_link_wait = send - ready
        arrive = send + lat
        eject = down_cal.reserve(c, arrive, ramp_bw)
        if eject - arrive > max_ramp_wait:
            max_ramp_wait = eject - arrive
        done = eject + ramp
        if done > makespan:
            makespan = done
        down_eject_count[c] += 1
        for gc in children_per_source[s].get(c, ()):
            heapq.heappush(pq, (arrive, seq, s, c, gc, k))
            seq += 1
    return makespan, down_eject_count, max_link_wait, max_ramp_wait


def verify_ejects(down_eject_count, n, flits):
    need = (n - 1) * flits
    bad = [node for node in range(n) if down_eject_count.get(node, 0) != need]
    return not bad, bad[:5]


# --------------------------------------------------------------------------
# Scheme runners.
# --------------------------------------------------------------------------
def run_multitree(mx, my, h, v, ramp_bw, flits):
    n = mx * my
    children = {s: multitree_children(s, mx, my) for s in range(n)}
    mk, ejc, mlw, mrw = simulate_tree_family(children, n, mx, h, v, ramp_bw, flits)
    ok, bad = verify_ejects(ejc, n, flits)
    return mk, ok, bad, mlw, mrw


def run_ring(mx, my, h, v, ramp_bw, flits, bidir):
    n = mx * my
    order = ham_cycle_band(mx, my, 0) if my >= 2 else [nid(x, 0, mx) for x in range(mx)]
    pos = {nd: k for k, nd in enumerate(order)}
    children = {s: ring_children(s, order, pos, bidir) for s in range(n)}
    mk, ejc, mlw, mrw = simulate_tree_family(children, n, mx, h, v, ramp_bw, flits)
    ok, bad = verify_ejects(ejc, n, flits)
    return mk, ok, bad, mlw, mrw


def run_hybrid(mx, my, h, v, ramp_bw, flits, B, bidir):
    n = mx * my
    children = {s: hybrid_children(s, mx, my, B, bidir) for s in range(n)}
    mk, ejc, mlw, mrw = simulate_tree_family(children, n, mx, h, v, ramp_bw, flits)
    ok, bad = verify_ejects(ejc, n, flits)
    return mk, ok, bad, mlw, mrw


def run_hybrid_v(mx, my, h, v, ramp_bw, flits, B, bidir):
    n = mx * my
    children = {s: hybrid_v_children(s, mx, my, B, bidir) for s in range(n)}
    mk, ejc, mlw, mrw = simulate_tree_family(children, n, mx, h, v, ramp_bw, flits)
    ok, bad = verify_ejects(ejc, n, flits)
    return mk, ok, bad, mlw, mrw


def divisors_pow2(m):
    bs = []
    b = 1
    while b <= m:
        if m % b == 0:
            bs.append(b)
        b *= 2
    return bs


if __name__ == "__main__":
    import time
    mx, my, h, v = 16, 16, 4, 6
    for ramp_bw in (1, 2):
        print(f"=== {mx}x{my} ramp_bw={ramp_bw} (event-driven, cf. zerobuf_16x16.json) ===")
        t0 = time.time()
        mk, ok, bad = run_multitree(mx, my, h, v, ramp_bw, 1)
        print(f"  multitree           mk={mk:5d} ok={ok} bad={bad} ({time.time()-t0:.2f}s)")
        t0 = time.time()
        mk, ok, bad = run_ring(mx, my, h, v, ramp_bw, 1, False)
        print(f"  ring_uni            mk={mk:5d} ok={ok} bad={bad} ({time.time()-t0:.2f}s)")
        t0 = time.time()
        mk, ok, bad = run_ring(mx, my, h, v, ramp_bw, 1, True)
        print(f"  ring_bi             mk={mk:5d} ok={ok} bad={bad} ({time.time()-t0:.2f}s)")
        for B in divisors_pow2(my):
            if my // B < 2:
                continue
            t0 = time.time()
            mk, ok, bad = run_hybrid(mx, my, h, v, ramp_bw, 1, B, True)
            print(f"  hybrid_bi   B={B:<3d} mk={mk:5d} ok={ok} bad={bad} ({time.time()-t0:.2f}s)")
        for B in divisors_pow2(mx):
            if mx // B < 2:
                continue
            t0 = time.time()
            mk, ok, bad = run_hybrid_v(mx, my, h, v, ramp_bw, 1, B, True)
            print(f"  hybrid_v_bi B={B:<3d} mk={mk:5d} ok={ok} bad={bad} ({time.time()-t0:.2f}s)")
