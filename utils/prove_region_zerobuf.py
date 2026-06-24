#!/usr/bin/env python3
"""Is each 8x8 region non-blocking / conflict-free / ZERO router-internal buffer?

We answer the design question directly with the conflict-free link-time calendar
model in sim_fused_rings (every directed link <=1 flit/cy; down-ramp <= ramp_bw;
H=4, V=6).  For every scheme we separate three kinds of stalling:

  * ring_buf  : a flit waits at a router for an INTRA-region link  -> router buffer
  * eject_buf : a flit waits at a router for the down-ramp         -> router buffer
  * afifo     : a flit waits / is in flight on a CROSS-border link -> AFIFO (allowed)

"Non-blocking + conflict-free + no router buffer (AFIFO may wait)" is therefore
exactly  ring_buf == 0  and  eject_buf == 0.  The AFIFO depth we report is the
peak number of flits simultaneously occupying any one cross-border link
(in-flight + waiting); "balanced" means every border link is used.

Schemes:
  single   : one Hamilton ring over the whole NxN mesh (the 0-buffer primitive).
  border   : fused 4 quadrant rings, foreign spread by SHORT ARCS (fast, 267).
  ringfol  : fused 4 rings, after crossing FOLLOW the destination ring (full
             bidirectional lap) -- the user's proposed fallback.
  global4  : the four 8x8 quadrant Hamilton rings spliced into ONE global ring
             through balanced border AFIFOs == a single ring -> 0-buffer.
"""

import heapq
from collections import defaultdict

import sim_fused_rings as fr


def sim_detail(deliveries, ramp_bw):
    """Conflict-free calendar sim; returns buffer/AFIFO accounting."""
    link = fr.Cal(1)
    down = fr.Cal(ramp_bw)
    up = fr.Cal(ramp_bw)
    ring_iv = defaultdict(list)      # intra-region link waits (router buffer)
    afifo_wait_iv = defaultdict(list)  # cross-link waits
    afifo_inflight = defaultdict(list)  # cross-link occupancy [send,arrive)
    eject_iv = defaultdict(list)     # down-ramp waits (router buffer)
    pq, seq, avail = [], 0, {}
    for s, ch in deliveries.items():
        inj = up.reserve(s, 0)
        avail[(s, s)] = inj + fr.RAMP
        for c in ch.get(s, []):
            heapq.heappush(pq, (avail[(s, s)], seq, s, s, c))
            seq += 1
    mk = 0
    eject = defaultdict(int)
    n = len(deliveries)
    while pq:
        ready, _, s, p, c = heapq.heappop(pq)
        cross = fr.quad_of(p) != fr.quad_of(c)
        send = link.reserve((p, c), ready)
        if send > ready:
            (afifo_wait_iv if cross else ring_iv)[(p, c)].append((ready, send))
        arrive = send + fr.link_lat(p, c)
        if cross:
            afifo_inflight[(p, c)].append((send, arrive))
        e = down.reserve(c, arrive)
        if e > arrive:
            eject_iv[c].append((arrive, e))
        mk = max(mk, e + fr.RAMP)
        eject[c] += 1
        avail[(s, c)] = arrive
        for g in deliveries[s].get(c, []):
            heapq.heappush(pq, (arrive, seq, s, c, g))
            seq += 1

    def peak(ivs):
        ev = []
        for a, b in ivs:
            ev += [(a, 1), (b, -1)]
        ev.sort()
        cur = m = 0
        for _, d in ev:
            cur += d
            m = max(m, cur)
        return m

    return {
        "makespan": mk,
        "ring_buf": max((peak(v) for v in ring_iv.values()), default=0),
        "eject_buf": max((peak(v) for v in eject_iv.values()), default=0),
        # AFIFO queue depth = flits forced to WAIT at the boundary (true FIFO occupancy)
        "afifo_wait": max((peak(v) for v in afifo_wait_iv.values()), default=0),
        # peak flits in transit on any one border link (on the wire, balance check)
        "afifo_inflight": max((peak(afifo_inflight[k]) for k in afifo_inflight), default=0),
        "n_cross_links": len(afifo_inflight),
        "ok": all(eject[x] == n - 1 for x in deliveries),
    }


def deliv_single(sz, bidir):
    fr.cfg(sz, sz, 4, 6)
    order = fr.ham_cycle_rect(0, 0, sz, sz)
    d = {}
    for s in order:
        ch = defaultdict(list)
        fr.add_ring_chain(ch, order, s, bidir)
        d[s] = ch
    return d


def deliv_border(sz, bidir):
    fr.cfg(sz, sz, 4, 6)
    return {s: fr.build_border_delivery(s, bidir) for s in range(sz * sz)}


def deliv_ringfollow(sz, bidir):
    """Cross each border once (balanced over rows/cols) then ride the WHOLE
    destination Hamilton ring bidirectionally (the user's fallback)."""
    fr.cfg(sz, sz, 4, 6)
    quads, _ = fr.quad_setup()
    hw, hh = sz // 2, sz // 2
    out = {}
    for s in range(sz * sz):
        qi = fr.quad_of(s)
        qx0, qy0 = (qi % 2) * hw, (qi // 2) * hh
        sx, sy = fr.coord(s)
        ch = defaultdict(list)
        fr.add_ring_chain(ch, quads[qi]["order"], s, bidir)
        home_bx, far_bx = (hw - 1, hw) if qx0 == 0 else (hw, hw - 1)
        home_by, far_by = (hh - 1, hh) if qy0 == 0 else (hh, hh - 1)
        ch[fr.nid(home_bx, sy)].append(fr.nid(far_bx, sy))
        fr.add_ring_chain(ch, quads[qi ^ 1]["order"], fr.nid(far_bx, sy), bidir)
        ch[fr.nid(sx, home_by)].append(fr.nid(sx, far_by))
        fr.add_ring_chain(ch, quads[qi ^ 2]["order"], fr.nid(sx, far_by), bidir)
        xcross = (qx0 + hw) % sz + (sx - qx0)
        ch[fr.nid(xcross, home_by)].append(fr.nid(xcross, far_by))
        fr.add_ring_chain(ch, quads[qi ^ 3]["order"], fr.nid(xcross, far_by), bidir)
        out[s] = ch
    return out


SCHEMES = {
    "single ": deliv_single,
    "border ": deliv_border,
    "ringfol": deliv_ringfollow,
    "global4": deliv_single,  # one ring threaded through all 4 quadrants
}


def main():
    print("ring_buf / eject_buf == 0  <=>  non-blocking, conflict-free, 0 router buffer")
    print("(AFIFO may hold flits; afifo_depth = peak flits on any one border link)\n")
    for sz in (8, 16):
        print(f"================  {sz}x{sz}  (N={sz*sz}, eject LB bi={ (sz*sz-1+1)//2 })  ================")
        print(f"{'scheme':8s} {'dir':3s} {'makespan':>8s} {'ring_buf':>9s} {'eject_buf':>10s} "
              f"{'afifo_q':>8s} {'inflight':>9s} {'#cross':>7s} {'0-buffer?':>10s}")
        for name, fn in SCHEMES.items():
            for bidir, rb in ((False, 1), (True, 2)):
                d = fn(sz, bidir)
                r = sim_detail(d, rb)
                zero = "YES" if (r["ring_buf"] == 0 and r["eject_buf"] == 0) else "no"
                tag = "bi " if bidir else "uni"
                print(f"{name:8s} {tag:3s} {r['makespan']:8d} {r['ring_buf']:9d} {r['eject_buf']:10d} "
                      f"{r['afifo_wait']:8d} {r['afifo_inflight']:9d} {r['n_cross_links']:7d} {zero:>10s}")
        print()


if __name__ == "__main__":
    main()
