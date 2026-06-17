#!/usr/bin/env python3
"""Time-division fused 4-ring allgather: how close to "one lap + tail"?

Question (from the design discussion):
  * How long does an 8x8 Hamilton-ring allgather take on its own?
  * If 16x16 is split into four 8x8 Hamilton rings that TIME-DIVISION exchange
    each other's data at the center, the foreign blocks can be streamed into a
    ring DURING its single lap (instead of doing a 2nd full foreign lap), so the
    whole allgather finishes in ~one ring lap plus a tail. What is that latency?

Model: event-driven GLOBAL link-time calendar (the same conflict-free, in-network
fork model as sim_dim_multitree). Each directed mesh link carries <=1 flit/cycle;
each node down-ramp carries <= ramp_bw flit/cycle. Flits may be time-division
interleaved on a link (that is exactly the "时分" the rings use). This is the
buffered/pipelined optimum; the strictly-rigid 0-buffer version is an upper bound
on top of it (see sched_zerobuf_compare.py -> quad = 717).

Bounds reported alongside the simulation:
  * ring-link bound : busiest ring link must carry all flits routed over it.
  * eject  bound    : every node ejects N-1 flits over one down-ramp / ramp_bw.
"""

import heapq
from collections import defaultdict

_MX, _MY, H, V, RAMP = 16, 16, 4, 6, 1


def cfg(mx, my, h=4, v=6):
    global _MX, _MY, H, V
    _MX, _MY, H, V = mx, my, h, v


def nid(x, y):
    return x + _MX * y


def coord(n):
    return n % _MX, n // _MX


def edge_lat(u, v):
    return H if (u // _MX) == (v // _MX) else V


def ham_cycle_rect(x0, y0, w, h):
    """Closed Hamilton cycle over the w x h sub-grid at (x0,y0) (needs w even)."""
    order = [nid(x0 + x, y0) for x in range(w)]
    for i, x in enumerate(range(w - 1, -1, -1)):
        rows = range(1, h) if i % 2 == 0 else range(h - 1, 0, -1)
        for yloc in rows:
            order.append(nid(x0 + x, y0 + yloc))
    return order


class Cal:
    def __init__(self, cap=1):
        self.cap = cap
        self.busy = defaultdict(dict)

    def reserve(self, key, earliest):
        d = self.busy[key]
        t = earliest
        while d.get(t, 0) >= self.cap:
            t += 1
        d[t] = d.get(t, 0) + 1
        return t

    def busiest(self):
        return max((sum(d.values()) for d in self.busy.values()), default=0)


def add_ring_chain(ch, order, entry, bidir):
    """Append, into children-map `ch`, the hops of a flit that enters `order`
    (a cyclic node list) at `entry` and rides it (uni or bi) to all other nodes."""
    n = len(order)
    i = order.index(entry)
    if not bidir:
        for k in range(n - 1):
            ch[order[(i + k) % n]].append(order[(i + k + 1) % n])
    else:
        a = n // 2
        b = (n - 1) - a
        for k in range(a):
            ch[order[(i + k) % n]].append(order[(i + k + 1) % n])
        for k in range(b):
            ch[order[(i - k) % n]].append(order[(i - k - 1) % n])


def simulate(deliveries, ramp_bw):
    link = Cal(1)
    down = Cal(ramp_bw)
    up = Cal(ramp_bw)
    pq = []
    seq = 0
    avail = {}
    for s, ch in deliveries.items():
        inj = up.reserve(s, 0)
        avail[(s, s)] = inj + RAMP
        for c in ch.get(s, []):
            heapq.heappush(pq, (avail[(s, s)], seq, s, s, c))
            seq += 1
    makespan = 0
    eject = defaultdict(int)
    while pq:
        ready, _, s, p, c = heapq.heappop(pq)
        send = link.reserve((p, c), ready)
        arrive = send + edge_lat(p, c)
        e = down.reserve(c, arrive)
        makespan = max(makespan, e + RAMP)
        eject[c] += 1
        avail[(s, c)] = arrive
        for g in deliveries[s].get(c, []):
            heapq.heappush(pq, (arrive, seq, s, c, g))
            seq += 1
    return makespan, eject, link.busiest(), down.busiest()


# ---------------------------------------------------------------------------
def ring_allgather(order, ramp_bw, bidir):
    deliveries = {}
    for s in order:
        ch = defaultdict(list)
        add_ring_chain(ch, order, s, bidir)
        deliveries[s] = ch
    n = len(order)
    mk, ej, bl, bd = simulate(deliveries, ramp_bw)
    ok = all(ej[x] == n - 1 for x in order)
    circ = sum(edge_lat(order[k], order[(k + 1) % n]) for k in range(n))
    return {"makespan": mk, "ok": ok, "busiest_link": bl, "circ": circ}


def quad_setup():
    hw, hh = _MX // 2, _MY // 2
    specs = [(0, 0), (hw, 0), (0, hh), (hw, hh)]
    reps = [(hw - 1, hh - 1), (hw, hh - 1), (hw - 1, hh), (hw, hh)]
    quads = []
    for (x0, y0), (rx, ry) in zip(specs, reps):
        order = ham_cycle_rect(x0, y0, hw, hh)
        quads.append({"rep": nid(rx, ry), "order": order})
    return quads, [0, 1, 3, 2]


def quad_of(s):
    sx, sy = coord(s)
    return (0 if sx < _MX // 2 else 1) + (0 if sy < _MY // 2 else 2)


def fused_4ring(ramp_bw, bidir):
    quads, ring4 = quad_setup()
    deliveries = {}
    for s in range(_MX * _MY):
        qi = quad_of(s)
        q = quads[qi]
        ch = defaultdict(list)
        add_ring_chain(ch, q["order"], s, bidir)         # own ring lap
        own = q["rep"]
        ci = ring4.index(qi)
        cw = quads[ring4[(ci + 1) % 4]]
        op = quads[ring4[(ci + 2) % 4]]
        cc = quads[ring4[(ci + 3) % 4]]
        ch[own].append(cw["rep"])                        # cross to cw neighbour
        ch[own].append(cc["rep"])                        # cross to ccw neighbour
        add_ring_chain(ch, cw["order"], cw["rep"], bidir)
        ch[cw["rep"]].append(op["rep"])                  # cw -> opposite
        add_ring_chain(ch, op["order"], op["rep"], bidir)
        add_ring_chain(ch, cc["order"], cc["rep"], bidir)
        deliveries[s] = ch
    n = _MX * _MY
    mk, ej, bl, bd = simulate(deliveries, ramp_bw)
    ok = all(ej[x] == n - 1 for x in range(n))
    return {"makespan": mk, "ok": ok, "busiest_link": bl, "busiest_down": bd}


def build_border_delivery(s, bidir):
    """Border multi-point injection: local ring lap, then push s's flit across the
    SHARED quadrant borders at all 8 points and spread by short row/column arcs
    (no full foreign lap). Diagonal quadrant is reached via the horizontal
    neighbour. Every other node ejects s exactly once."""
    hw, hh = _MX // 2, _MY // 2
    sx, sy = coord(s)
    qx = 0 if sx < hw else 1
    qy = 0 if sy < hh else 1
    qx0, qy0 = qx * hw, qy * hh
    ch = defaultdict(list)
    add_ring_chain(ch, ham_cycle_rect(qx0, qy0, hw, hh), s, bidir)   # local lap

    # horizontal neighbour QH: cross x-border, arc across QH rows
    if qx == 0:
        bxQ, arc_xs = hw - 1, list(range(hw, 2 * hw))
    else:
        bxQ, arc_xs = hw, list(range(hw - 1, -1, -1))
    for y in range(qy0, qy0 + hh):
        ch[nid(bxQ, y)].append(nid(arc_xs[0], y))
        for k in range(len(arc_xs) - 1):
            ch[nid(arc_xs[k], y)].append(nid(arc_xs[k + 1], y))

    # vertical neighbour QV: cross y-border, arc down QV columns
    if qy == 0:
        byQ, arc_ys = hh - 1, list(range(hh, 2 * hh))
    else:
        byQ, arc_ys = hh, list(range(hh - 1, -1, -1))
    for x in range(qx0, qx0 + hw):
        ch[nid(x, byQ)].append(nid(x, arc_ys[0]))
        for k in range(len(arc_ys) - 1):
            ch[nid(x, arc_ys[k])].append(nid(x, arc_ys[k + 1]))

    # diagonal QD via QH: from QH's border row cross into QD, arc down columns
    for x in arc_xs:
        ch[nid(x, byQ)].append(nid(x, arc_ys[0]))
        for k in range(len(arc_ys) - 1):
            ch[nid(x, arc_ys[k])].append(nid(x, arc_ys[k + 1]))
    return ch


def border_fused_4ring(ramp_bw, bidir):
    deliveries = {s: build_border_delivery(s, bidir) for s in range(_MX * _MY)}
    n = _MX * _MY
    mk, ej, bl, bd = simulate(deliveries, ramp_bw)
    ok = all(ej[x] == n - 1 for x in range(n))
    return {"makespan": mk, "ok": ok, "busiest_link": bl, "busiest_down": bd}


def main():
    print("=== 8x8 Hamilton-ring allgather (standalone, 64 nodes) ===")
    cfg(8, 8, 4, 6)
    order8 = ham_cycle_rect(0, 0, 8, 8)
    for bidir in (False, True):
        tag = "bi " if bidir else "uni"
        for rb in (1, 2):
            r = ring_allgather(order8, rb, bidir)
            print(f"  {tag} ramp_bw={rb}: makespan={r['makespan']:4d}  "
                  f"circ(1 lap)={r['circ']}  busiest_link={r['busiest_link']}  ok={r['ok']}")

    print("\n=== 16x16 fused 4-ring (time-division center exchange, 256 nodes) ===")
    cfg(16, 16, 4, 6)
    N = 256
    for rb in (1, 2):
        lb = (N - 1 + rb - 1) // rb
        print(f"  -- ramp_bw={rb}  (eject LB={lb}) --")
        for bidir in (False, True):
            tag = "bi " if bidir else "uni"
            r = fused_4ring(rb, bidir)
            print(f"    center-only {tag}: makespan={r['makespan']:4d}  "
                  f"busiest_link={r['busiest_link']}  busiest_down={r['busiest_down']}  ok={r['ok']}")
        for bidir in (False, True):
            tag = "bi " if bidir else "uni"
            r = border_fused_4ring(rb, bidir)
            print(f"    border      {tag}: makespan={r['makespan']:4d}  "
                  f"busiest_link={r['busiest_link']}  busiest_down={r['busiest_down']}  ok={r['ok']}")


if __name__ == "__main__":
    main()
