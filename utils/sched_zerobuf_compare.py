#!/usr/bin/env python3
"""Conflict-free, non-blocking, ZERO-in-network-buffer allgather schedulers,
compared across three schemes on a configurable mesh.

Default study config (this task): 16x16 mesh, H=4, V=6, down-ramp BW in {1,2}.

0-buffer model (same philosophy as utils/sched_zero_buffer.py):
  Within one source's delivery structure (ring / tree) NO flit ever waits at an
  intermediate router -> the timing is RIGID. Every directed link (p->c) it uses
  is occupied at      inject_s + ramp + dist(s, p)
  and every node d it reaches ejects at  inject_s + ramp + dist(s, d)
  where dist is the realized path latency (sum of H/V hops on that structure).
  The only freedom is a per-source injection offset inject_s (data held in the
  source PE/SRAM, NOT a router buffer). We slide each source's whole rigid
  footprint by one offset so that:
    * each directed mesh link carries <= 1 flit per cycle (conflict-free links),
    * each node down-ramp carries <= RAMP_BW flits per cycle,
    * each node up-ramp  carries <= RAMP_BW flits per cycle.
  => conflict-free + non-blocking + 0 in-network buffer BY CONSTRUCTION.

Schemes compared (all under the same 0-buffer packer):
  1. multitree : bidirectional dimensional X-then-Y multicast tree per source.
  2. ring      : a single global Hamilton ring (uni- or bi-directional).
  3. hybrid    : local Hamilton ring inside each of B horizontal bands
                 (intra-band allgather) + a global vertical tree broadcast
                 (each column forks up/down to the other bands).

Lower bound (any scheme, 0-buffer or not): every node must eject N-1 flits over
its single down-ramp, so makespan >= (N-1)/RAMP_BW + minimal delivery latency.
"""

import argparse
from collections import defaultdict

MX, MY, H, V, RAMP = 16, 16, 4, 6, 1
N = MX * MY


def cfg(mx, my, h, v):
    global MX, MY, H, V, N
    MX, MY, H, V = mx, my, h, v
    N = mx * my


def nid(x, y):
    return x + MX * y


def coord(n):
    return n % MX, n // MX


def edge_lat(u, v):
    uy, vy = u // MX, v // MX
    return H if uy == vy else V


def lk(u, v):
    return u * 100000 + v


def manh(s, d):
    sx, sy = coord(s)
    dx, dy = coord(d)
    return abs(sx - dx) * H + abs(sy - dy) * V


# --------------------------------------------------------------------------
# Hamilton cycle (comb construction) on an MX(even) x R grid, rows offset by y0.
# --------------------------------------------------------------------------
def ham_cycle_band(R, y0):
    """Closed Hamilton cycle over rows [y0, y0+R) (needs MX even, R>=2)."""
    order = [nid(x, y0) for x in range(MX)]            # bottom spine
    for i, x in enumerate(range(MX - 1, -1, -1)):
        rows = range(1, R) if i % 2 == 0 else range(R - 1, 0, -1)
        for yloc in rows:
            order.append(nid(x, y0 + yloc))
    return order


def row_path(y0):
    return [nid(x, y0) for x in range(MX)]


def ham_cycle_rect(x0, y0, w, h):
    """Closed Hamilton cycle over the w x h sub-grid at (x0,y0) (needs w even)."""
    order = [nid(x0 + x, y0) for x in range(w)]        # bottom spine
    for i, x in enumerate(range(w - 1, -1, -1)):
        rows = range(1, h) if i % 2 == 0 else range(h - 1, 0, -1)
        for yloc in rows:
            order.append(nid(x0 + x, y0 + yloc))
    return order


def ham_cycle_rect_vflip(x0, y0, w, h):
    """ham_cycle_rect reflected vertically inside the sub-grid: spine on the TOP
    row (y0+h-1), teeth pointing down. Reflection preserves grid adjacency, so it
    stays a valid Hamilton cycle."""
    out = []
    for nd in ham_cycle_rect(x0, y0, w, h):
        x, y = coord(nd)
        out.append(nid(x, (2 * y0 + h - 1) - y))
    return out


def quad_ring_border(x0, y0, w, h):
    """Quadrant ring oriented so its long edge (spine) hugs the central border:
    bottom quadrants flip up so the spine sits on their TOP row (toward center),
    top quadrants keep the spine on their BOTTOM row (= the center border)."""
    if y0 == 0:
        return ham_cycle_rect_vflip(x0, y0, w, h)
    return ham_cycle_rect(x0, y0, w, h)


def ham_cycle_vband(C, x0):
    """Closed Hamilton cycle over columns [x0, x0+C) x all MY rows (needs MY even,
    C>=2): a VERTICAL comb (left spine along column x0, horizontal teeth)."""
    order = [nid(x0, y) for y in range(MY)]            # left spine (column x0)
    for i, y in enumerate(range(MY - 1, -1, -1)):
        cols = range(1, C) if i % 2 == 0 else range(C - 1, 0, -1)
        for xloc in cols:
            order.append(nid(x0 + xloc, y))
    return order


# Four 8x8 quadrants + the central 4-cycle of their innermost corners.
QUAD = None        # list of {'rep','order','pos'} for Q0,Q1,Q2,Q3
RING4 = None       # quadrant indices in central-cycle order [Q0,Q1,Q3,Q2]


def init_quadrants():
    global QUAD, RING4
    hw, hh = MX // 2, MY // 2
    specs = [(0, 0), (hw, 0), (0, hh), (hw, hh)]                 # Q0..Q3 origins
    reps = [(hw - 1, hh - 1), (hw, hh - 1), (hw - 1, hh), (hw, hh)]  # inner corners
    QUAD = []
    for (x0, y0), (rx, ry) in zip(specs, reps):
        order = ham_cycle_rect(x0, y0, hw, hh)
        assert len(order) == hw * hh and len(set(order)) == hw * hh
        QUAD.append({"rep": nid(rx, ry), "order": order,
                     "pos": {nd: k for k, nd in enumerate(order)}})
    RING4 = [0, 1, 3, 2]   # (7,7)->(8,7)->(8,8)->(7,8)->(7,7)


def quad_of(s):
    sx, sy = coord(s)
    return (0 if sx < MX // 2 else 1) + (0 if sy < MY // 2 else 2)


def _circulate(order, pos, entry, start_rel, bidir, second_rel):
    """Slots for a flit that enters `order` ring at `entry` (already present at
    start_rel) and rides around it; returns (slots, arrival_rel_per_node).
    Does NOT eject at `entry` itself (caller handles that)."""
    n = len(order)
    i = pos[entry]
    slots = []
    arr = {entry: start_rel}
    if not bidir:
        chain = [order[(i + k) % n] for k in range(n)]
        a, _ = _arc(chain, start_rel)
        slots += a
        t = start_rel
        for k in range(n - 1):
            t += edge_lat(chain[k], chain[k + 1])
            arr[chain[k + 1]] = t
    else:
        half = n // 2
        b = (n - 1) - half
        fwd = [order[(i + k) % n] for k in range(half + 1)]
        bwd = [order[(i - k) % n] for k in range(b + 1)]
        sf, _ = _arc(fwd, start_rel)
        sb, _ = _arc(bwd, second_rel)
        slots += sf + sb
        t = start_rel
        for k in range(len(fwd) - 1):
            t += edge_lat(fwd[k], fwd[k + 1])
            arr[fwd[k + 1]] = t
        t = second_rel
        for k in range(len(bwd) - 1):
            t += edge_lat(bwd[k], bwd[k + 1])
            arr[bwd[k + 1]] = t
    return slots, arr


def fp_quadrant(s, bidir, ramp_bw):
    """4x (8x8 Hamilton ring) + central 4-ring exchange + re-circulation.

    Phase A: s circulates its own 8x8 quadrant ring (allgather within quadrant).
    Phase B: from its quadrant's inner-corner rep, s's flit hops along the central
    4-cycle to the other 3 quadrant reps (cw 1 & 2 hops, ccw 1 hop) and, at each
    foreign rep, re-circulates that quadrant's ring so every node there gets it.
    """
    qi = quad_of(s)
    q = QUAD[qi]
    d2 = 0 if ramp_bw >= 2 else 1
    slots = [('U', s, 0)]
    if bidir:
        slots.append(('U', s, d2))
    sa, arrA = _circulate(q["order"], q["pos"], s, RAMP, bidir, RAMP + d2)
    slots += sa
    own = q["rep"]
    crel = arrA[own]

    ci = RING4.index(qi)
    nb_cw = RING4[(ci + 1) % 4]
    nb_op = RING4[(ci + 2) % 4]
    nb_cc = RING4[(ci + 3) % 4]
    rep_cw, rep_op, rep_cc = QUAD[nb_cw]["rep"], QUAD[nb_op]["rep"], QUAD[nb_cc]["rep"]

    def enter(rep, qidx, arrive_rel):
        out = [('D', rep, arrive_rel)]
        sc, _ = _circulate(QUAD[qidx]["order"], QUAD[qidx]["pos"], rep,
                           arrive_rel, bidir, arrive_rel)
        return out + sc

    # cw: own -> rep_cw  (then rep_cw -> rep_op for the opposite quadrant)
    t = crel + edge_lat(own, rep_cw)
    slots.append(('L', lk(own, rep_cw), crel))
    slots += enter(rep_cw, nb_cw, t)
    t_op = t + edge_lat(rep_cw, rep_op)
    slots.append(('L', lk(rep_cw, rep_op), t))
    slots += enter(rep_op, nb_op, t_op)
    # ccw: own -> rep_cc
    t_cc = crel + edge_lat(own, rep_cc)
    slots.append(('L', lk(own, rep_cc), crel))
    slots += enter(rep_cc, nb_cc, t_cc)
    return slots


def _ring_arrivals(order, pos, s, bidir, ramp_bw):
    """Rigid local-ring slots + arrival-rel per node (s present at RAMP)."""
    n = len(order)
    i = pos[s]
    d2 = 0 if ramp_bw >= 2 else 1
    slots = [('U', s, 0)]
    arr = {s: RAMP}
    if not bidir:
        chain = [order[(i + k) % n] for k in range(n)]
        a, _ = _arc(chain, RAMP)
        slots += a
        t = RAMP
        for k in range(n - 1):
            t += edge_lat(chain[k], chain[k + 1])
            arr[chain[k + 1]] = t
    else:
        a = n // 2
        b = (n - 1) - a
        fwd = [order[(i + k) % n] for k in range(a + 1)]
        bwd = [order[(i - k) % n] for k in range(b + 1)]
        slots.append(('U', s, d2))
        sf, _ = _arc(fwd, RAMP)
        sb, _ = _arc(bwd, RAMP + d2)
        slots += sf + sb
        t = RAMP
        for k in range(len(fwd) - 1):
            t += edge_lat(fwd[k], fwd[k + 1])
            arr[fwd[k + 1]] = t
        t = RAMP + d2
        for k in range(len(bwd) - 1):
            t += edge_lat(bwd[k], bwd[k + 1])
            arr[bwd[k + 1]] = t
    return slots, arr


def _arc_track(slots, arr_out, start_node, start_rel, xs_or_ys, axis_x, fixed):
    """Emit a straight arc of nodes (along x if axis_x else y) starting from
    start_node at start_rel, recording arrival rel of each visited node."""
    prev = start_node
    t = start_rel
    for v in xs_or_ys:
        cur = nid(v, fixed) if axis_x else nid(fixed, v)
        slots.append(('L', lk(prev, cur), t))
        t += edge_lat(prev, cur)
        slots.append(('D', cur, t))
        arr_out[cur] = t
        prev = cur


def fp_border(s, bidir, ramp_bw):
    """4x(8x8) quadrant ring + BORDER multi-point injection (rigid 0-buffer form
    of sim_fused_rings.build_border_delivery)."""
    hw, hh = MX // 2, MY // 2
    sx, sy = coord(s)
    qx0, qy0 = (0 if sx < hw else hw), (0 if sy < hh else hh)
    order = quad_ring_border(qx0, qy0, hw, hh)   # long edge hugs the central border
    pos = {nd: k for k, nd in enumerate(order)}
    slots, arr = _ring_arrivals(order, pos, s, bidir, ramp_bw)

    if qx0 == 0:
        bxQ, arc_xs = hw - 1, list(range(hw, 2 * hw))
    else:
        bxQ, arc_xs = hw, list(range(hw - 1, -1, -1))
    if qy0 == 0:
        byQ, arc_ys = hh - 1, list(range(hh, 2 * hh))
    else:
        byQ, arc_ys = hh, list(range(hh - 1, -1, -1))

    arrH = {}
    for y in range(qy0, qy0 + hh):                       # horizontal neighbour QH
        _arc_track(slots, arrH, nid(bxQ, y), arr[nid(bxQ, y)], arc_xs, True, y)
    for x in range(qx0, qx0 + hw):                       # vertical neighbour QV
        _arc_track(slots, {}, nid(x, byQ), arr[nid(x, byQ)], arc_ys, False, x)
    for x in arc_xs:                                     # diagonal QD via QH
        _arc_track(slots, {}, nid(x, byQ), arrH[nid(x, byQ)], arc_ys, False, x)
    return slots


# --------------------------------------------------------------------------
# Footprints: list of ('L', linkkey, rel) / ('D', node, rel) / ('U', node, rel)
# --------------------------------------------------------------------------
def tree_edges(s):
    sx, sy = coord(s)
    e = []
    for x in range(sx + 1, MX):
        e.append((nid(x - 1, sy), nid(x, sy)))
    for x in range(sx - 1, -1, -1):
        e.append((nid(x + 1, sy), nid(x, sy)))
    for x in range(MX):
        for y in range(sy + 1, MY):
            e.append((nid(x, y - 1), nid(x, y)))
        for y in range(sy - 1, -1, -1):
            e.append((nid(x, y + 1), nid(x, y)))
    return e


def fp_multitree(s):
    slots = [('U', s, 0)]
    for (p, c) in tree_edges(s):
        slots.append(('L', lk(p, c), RAMP + manh(s, p)))
    for d in range(N):
        if d != s:
            slots.append(('D', d, RAMP + manh(s, d)))
    return slots


def _arc(chain, start_rel):
    """Rigid slots for a flit leaving chain[0] at start_rel, visiting chain[1:]."""
    slots = []
    t = start_rel
    for k in range(len(chain) - 1):
        u, w = chain[k], chain[k + 1]
        slots.append(('L', lk(u, w), t))
        t += edge_lat(u, w)
        slots.append(('D', w, t))
    return slots, t


def fp_ring(s, order, pos, bidir, ramp_bw):
    i = pos[s]
    n = len(order)
    slots = [('U', s, 0)]
    if not bidir:
        chain = [order[(i + k) % n] for k in range(n)]
        a, _ = _arc(chain, RAMP)
        slots += a
        return slots
    a = n // 2
    b = (n - 1) - a
    fwd = [order[(i + k) % n] for k in range(a + 1)]
    bwd = [order[(i - k) % n] for k in range(b + 1)]
    d2 = 0 if ramp_bw >= 2 else 1
    slots.append(('U', s, d2))
    sf, _ = _arc(fwd, RAMP)
    sb, _ = _arc(bwd, RAMP + d2)
    return slots + sf + sb


def fp_hybrid(s, B, bidir, ramp_bw):
    R = MY // B
    sx, sy = coord(s)
    b = sy // R
    y0 = b * R
    # ---- local order + position ----
    if R >= 2:
        order = ham_cycle_band(R, y0)
    else:
        order = row_path(y0)                 # 1-row band: open path (bi only)
    pos = {nd: k for k, nd in enumerate(order)}
    n = len(order)
    i = pos[s]

    slots = [('U', s, 0)]
    arr = {s: RAMP}                           # arrival rel of s's flit per band node

    # ---- phase A: local ring allgather inside the band ----
    if R == 1:
        bidir_local = True                    # a single row has no cycle -> path
    else:
        bidir_local = bidir
    if not bidir_local:
        chain = [order[(i + k) % n] for k in range(n)]
        a, _ = _arc(chain, RAMP)
        slots += a
        t = RAMP
        for k in range(n - 1):
            t += edge_lat(order[(i + k) % n], order[(i + k + 1) % n])
            arr[order[(i + k + 1) % n]] = t
    else:
        if R == 1:                            # open path both directions
            fwd = order[i:]
            bwd = order[i::-1]
        else:                                 # closed cycle, split halves
            a = n // 2
            bb = (n - 1) - a
            fwd = [order[(i + k) % n] for k in range(a + 1)]
            bwd = [order[(i - k) % n] for k in range(bb + 1)]
        d2 = 0 if ramp_bw >= 2 else 1
        slots.append(('U', s, d2))
        sf, _ = _arc(fwd, RAMP)
        sb, _ = _arc(bwd, RAMP + d2)
        slots += sf + sb
        t = RAMP
        for k in range(len(fwd) - 1):
            t += edge_lat(fwd[k], fwd[k + 1])
            arr[fwd[k + 1]] = t
        t = RAMP + d2
        for k in range(len(bwd) - 1):
            t += edge_lat(bwd[k], bwd[k + 1])
            arr[bwd[k + 1]] = t

    # ---- phase B: global vertical tree (each column forks up & down) ----
    for x in range(MX):
        top = nid(x, y0)
        bot = nid(x, y0 + R - 1)
        # climb up to the bands above
        t = arr[top]
        prev = top
        for yy in range(y0 - 1, -1, -1):
            cur = nid(x, yy)
            slots.append(('L', lk(prev, cur), t))
            t += V
            slots.append(('D', cur, t))
            prev = cur
        # climb down to the bands below
        t = arr[bot]
        prev = bot
        for yy in range(y0 + R, MY):
            cur = nid(x, yy)
            slots.append(('L', lk(prev, cur), t))
            t += V
            slots.append(('D', cur, t))
            prev = cur
    return slots


def fp_hybrid_v(s, B, bidir, ramp_bw):
    """Transpose of fp_hybrid: B VERTICAL bands (each C=MX/B columns).
    Phase A: local VERTICAL Hamilton ring allgather inside the band.
    Phase B: per-row HORIZONTAL multicast tree forks left/right to other bands."""
    C = MX // B
    sx, _ = coord(s)
    x0 = (sx // C) * C
    order = ham_cycle_vband(C, x0)
    pos = {nd: k for k, nd in enumerate(order)}
    slots, arr = _ring_arrivals(order, pos, s, bidir, ramp_bw)
    for y in range(MY):                                  # horizontal tree per row
        t = arr[nid(x0, y)]
        prev = nid(x0, y)
        for xx in range(x0 - 1, -1, -1):                # fork left
            cur = nid(xx, y)
            slots.append(('L', lk(prev, cur), t))
            t += H
            slots.append(('D', cur, t))
            prev = cur
        t = arr[nid(x0 + C - 1, y)]
        prev = nid(x0 + C - 1, y)
        for xx in range(x0 + C, MX):                     # fork right
            cur = nid(xx, y)
            slots.append(('L', lk(prev, cur), t))
            t += H
            slots.append(('D', cur, t))
            prev = cur
    return slots


# --------------------------------------------------------------------------
# Rigid offset packer (conflict-free links + capacity-RAMP_BW ramps).
# --------------------------------------------------------------------------
def pack(footprints, ramp_bw, src_order, flits=1):
    link_busy = defaultdict(dict)
    up_busy = defaultdict(dict)
    down_busy = defaultdict(dict)

    def table(kind):
        return link_busy if kind == 'L' else up_busy if kind == 'U' else down_busy

    def cap(kind):
        return 1 if kind == 'L' else ramp_bw

    makespan = 0
    max_off = 0
    for s in src_order:
        slots = footprints[s]
        forbidden = set()
        for kind, key, rel in slots:
            d = table(kind).get(key)
            if not d:
                continue
            c = cap(kind)
            for cyc, ct in d.items():
                if ct >= c:
                    for i in range(flits):
                        off = cyc - rel - i
                        if off >= 0:
                            forbidden.add(off)
        off = 0
        while off in forbidden:
            off += 1
        # intra-source footprints are collision-free by construction (each link
        # and down-ramp touched once per source; bi up-ramp pre-staggered).
        for kind, key, rel in slots:
            c = off + rel
            t = table(kind)
            for i in range(flits):
                t[key][c + i] = t[key].get(c + i, 0) + 1
            if kind == 'D':
                makespan = max(makespan, c + flits - 1 + RAMP)
        max_off = max(max_off, off)
    return makespan, max_off, (link_busy, up_busy, down_busy)


def verify(busy, ramp_bw, flits=1):
    link_busy, up_busy, down_busy = busy
    link_ok = all(ct <= 1 for d in link_busy.values() for ct in d.values())
    up_ok = all(ct <= ramp_bw for d in up_busy.values() for ct in d.values())
    down_ok = all(ct <= ramp_bw for d in down_busy.values() for ct in d.values())
    ejects = {n: sum(d.values()) for n, d in down_busy.items()}
    need = (N - 1) * flits
    eject_ok = all(ejects.get(n, 0) == need for n in range(N))
    return link_ok and up_ok and down_ok and eject_ok


SRC_ORDERS = {
    "corner": lambda: sorted(range(N), key=lambda s: -(abs(coord(s)[0] - (MX - 1) / 2) + abs(coord(s)[1] - (MY - 1) / 2))),
    "center": lambda: sorted(range(N), key=lambda s: (abs(coord(s)[0] - (MX - 1) / 2) + abs(coord(s)[1] - (MY - 1) / 2))),
    "natural": lambda: list(range(N)),
    "rev": lambda: list(range(N - 1, -1, -1)),
    "col": lambda: sorted(range(N), key=lambda s: (coord(s)[0], coord(s)[1])),
    "ring": lambda: list(RING_ORDER),
}


def run_scheme(build_fp, ramp_bw, flits=1):
    foot = {s: build_fp(s) for s in range(N)}
    best = None
    for name, gen in SRC_ORDERS.items():
        mk, mo, busy = pack(foot, ramp_bw, gen(), flits=flits)
        ok = verify(busy, ramp_bw, flits=flits)
        if best is None or mk < best[0]:
            best = (mk, mo, name, ok)
    return best  # (makespan, max_offset, order, ok)


def divisors_bands():
    bs = []
    b = 1
    while b <= MY:
        if MY % b == 0:
            bs.append(b)
        b *= 2
    return bs


def study(ramp_bw):
    out = {}
    out["multitree"] = run_scheme(fp_multitree, ramp_bw)
    out["ring_uni"] = run_scheme(lambda s: fp_ring(s, RING_ORDER, RING_POS, False, ramp_bw), ramp_bw)
    out["ring_bi"] = run_scheme(lambda s: fp_ring(s, RING_ORDER, RING_POS, True, ramp_bw), ramp_bw)
    out["hybrid_uni"] = {}
    out["hybrid_bi"] = {}
    for B in divisors_bands():
        R = MY // B
        if R >= 2:
            out["hybrid_uni"][B] = run_scheme(lambda s, B=B: fp_hybrid(s, B, False, ramp_bw), ramp_bw)
        out["hybrid_bi"][B] = run_scheme(lambda s, B=B: fp_hybrid(s, B, True, ramp_bw), ramp_bw)
    out["hybrid_v_uni"] = {}
    out["hybrid_v_bi"] = {}
    for B in divisors_bands():
        C = MX // B
        if C >= 2:
            out["hybrid_v_uni"][B] = run_scheme(lambda s, B=B: fp_hybrid_v(s, B, False, ramp_bw), ramp_bw)
            out["hybrid_v_bi"][B] = run_scheme(lambda s, B=B: fp_hybrid_v(s, B, True, ramp_bw), ramp_bw)
    out["quad_uni"] = run_scheme(lambda s: fp_quadrant(s, False, ramp_bw), ramp_bw)
    out["quad_bi"] = run_scheme(lambda s: fp_quadrant(s, True, ramp_bw), ramp_bw)
    out["border_uni"] = run_scheme(lambda s: fp_border(s, False, ramp_bw), ramp_bw)
    out["border_bi"] = run_scheme(lambda s: fp_border(s, True, ramp_bw), ramp_bw)
    return out


RING_ORDER = None
RING_POS = None


def init_ring():
    global RING_ORDER, RING_POS
    RING_ORDER = ham_cycle_band(MY, 0)
    RING_POS = {nd: k for k, nd in enumerate(RING_ORDER)}
    assert len(RING_ORDER) == N and len(set(RING_ORDER)) == N, "ring not Hamiltonian"


def study_json(ramp_bw):
    res = study(ramp_bw)
    d = {
        "ramp_bw": ramp_bw,
        "eject_lb": (N - 1 + ramp_bw - 1) // ramp_bw,
        "multitree": {"makespan": res["multitree"][0], "order": res["multitree"][2], "ok": res["multitree"][3]},
        "ring_uni": {"makespan": res["ring_uni"][0], "ok": res["ring_uni"][3]},
        "ring_bi": {"makespan": res["ring_bi"][0], "ok": res["ring_bi"][3]},
        "hybrid_uni": {B: {"makespan": r[0], "ok": r[3]} for B, r in res["hybrid_uni"].items()},
        "hybrid_bi": {B: {"makespan": r[0], "ok": r[3]} for B, r in res["hybrid_bi"].items()},
        "hybrid_v_uni": {B: {"makespan": r[0], "ok": r[3]} for B, r in res["hybrid_v_uni"].items()},
        "hybrid_v_bi": {B: {"makespan": r[0], "ok": r[3]} for B, r in res["hybrid_v_bi"].items()},
        "quad_uni": {"makespan": res["quad_uni"][0], "ok": res["quad_uni"][3]},
        "quad_bi": {"makespan": res["quad_bi"][0], "ok": res["quad_bi"][3]},
        "border_uni": {"makespan": res["border_uni"][0], "ok": res["border_uni"][3]},
        "border_bi": {"makespan": res["border_bi"][0], "ok": res["border_bi"][3]},
    }
    return d


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--mx", type=int, default=16)
    ap.add_argument("--my", type=int, default=16)
    ap.add_argument("--h", type=int, default=4)
    ap.add_argument("--v", type=int, default=6)
    ap.add_argument("--json", default=None, help="dump results to this JSON path")
    args = ap.parse_args()
    cfg(args.mx, args.my, args.h, args.v)
    init_ring()
    init_quadrants()

    print(f"Mesh {MX}x{MY}, H={H}, V={V}, N={N}, 0-buffer rigid schedules\n")
    payload = {"mx": MX, "my": MY, "h": H, "v": V, "n": N, "bw": {}}
    for rb in (1, 2):
        d = study_json(rb)
        payload["bw"][rb] = d
        print(f"===== down-ramp BW = {rb} flit/cy  (eject LB = {d['eject_lb']}) =====")
        print(f"  multitree            makespan={d['multitree']['makespan']:5d}  ({d['multitree']['order']}, ok={d['multitree']['ok']})")
        print(f"  ring  unidirectional makespan={d['ring_uni']['makespan']:5d}  ok={d['ring_uni']['ok']}")
        print(f"  ring  bidirectional  makespan={d['ring_bi']['makespan']:5d}  ok={d['ring_bi']['ok']}")
        for mode in ("hybrid_uni", "hybrid_bi"):
            print(f"  {mode} (横带环+纵树):")
            for B, r in sorted(d[mode].items()):
                print(f"      B={B:2d} (R={MY//B:2d})  makespan={r['makespan']:5d}  ok={r['ok']}")
        for mode in ("hybrid_v_uni", "hybrid_v_bi"):
            print(f"  {mode} (纵带环+横树):")
            for B, r in sorted(d[mode].items()):
                print(f"      B={B:2d} (C={MX//B:2d})  makespan={r['makespan']:5d}  ok={r['ok']}")
        print(f"  quad 4x(8x8)+center uni makespan={d['quad_uni']['makespan']:5d}  ok={d['quad_uni']['ok']}")
        print(f"  quad 4x(8x8)+center bi  makespan={d['quad_bi']['makespan']:5d}  ok={d['quad_bi']['ok']}")
        print(f"  border multi-point  uni makespan={d['border_uni']['makespan']:5d}  ok={d['border_uni']['ok']}")
        print(f"  border multi-point  bi  makespan={d['border_bi']['makespan']:5d}  ok={d['border_bi']['ok']}")
        print()

    if args.json:
        import json
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        print(f"Wrote {args.json}")


if __name__ == "__main__":
    main()
