#!/usr/bin/env python3
"""Sweep MANY multicast-tree fork placements and report, for each, the four
quantities that decide a bufferless conflict-free schedule:

  fill      = tree depth in latency        (latency floor; shrinks with rootward forks)
  Lmax      = peak directed-link load       (bandwidth floor; grows with rootward forks)
  link_buf  = peak router output buffer     (must be ~0 and pushable to AFIFO)
  ramp_buf  = peak router eject buffer       (must be 0 for "bufferless")
  pipe_mk   = pipelined (buffered) makespan  (best case IF buffering allowed)

A fork placement is "bufferless-capable" only if link_buf/ramp_buf are small and
localized to region borders (=> AFIFO). Otherwise its pipe_mk is unattainable
under the 0-router-buffer constraint.

Families (fork position varies):
  ring            : 1 fork at root, then none           (Q=1)
  quad-center     : 4 sub-rings + center representative exchange
  border          : 4 sub-rings + 3-level border multicast (Q=4)
  hybrid(B)       : B band-rings (row fork) + per-column vertical tree
  multitree       : root row-fork + per-column tree (Q=N, all forks at root)
"""

import argparse
from collections import defaultdict, deque

import sim_fused_rings as fr


def tree_depth(ch, s):
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
    load = defaultdict(int)
    for s, ch in deliveries.items():
        for p, kids in ch.items():
            for c in kids:
                load[(p, c)] += 1
    return max(load.values(), default=0)


def build_grid_border(s, Qx, Qy, bidir):
    """Generalized border scheme on a Qx x Qy grid of (wx x wy) regions.
    Home region: Hamilton sub-ring (fork at s). Then spread the home row-strip
    across all region-columns (phase X, cross vertical borders + arc), then
    spread every column across all region-rows (phase Y, cross horizontal
    borders + arc). All cross-border forks land on region-boundary links."""
    M = fr._MX
    wx, wy = M // Qx, M // Qy
    sx, sy = fr.coord(s)
    rx, ry = sx // wx, sy // wy
    x0, y0 = rx * wx, ry * wy
    ys = list(range(y0, y0 + wy))
    ch = defaultdict(list)
    fr.add_ring_chain(ch, fr.ham_cycle_rect(x0, y0, wx, wy), s, bidir)

    # Phase X: extend the home strip (rows ys) rightward then leftward.
    for a in range(rx, Qx - 1):                 # rightward
        bx = (a + 1) * wx
        for y in ys:
            ch[fr.nid(bx - 1, y)].append(fr.nid(bx, y))
            for k in range(wx - 1):
                ch[fr.nid(bx + k, y)].append(fr.nid(bx + k + 1, y))
    for a in range(rx, 0, -1):                  # leftward
        bx = a * wx
        for y in ys:
            ch[fr.nid(bx, y)].append(fr.nid(bx - 1, y))
            for k in range(wx - 1):
                ch[fr.nid(bx - 1 - k, y)].append(fr.nid(bx - 2 - k, y))

    # Phase Y: for every column, extend down then up from the home strip.
    for x in range(M):
        for b in range(ry, Qy - 1):             # downward
            by = (b + 1) * wy
            ch[fr.nid(x, by - 1)].append(fr.nid(x, by))
            for k in range(wy - 1):
                ch[fr.nid(x, by + k)].append(fr.nid(x, by + k + 1))
        for b in range(ry, 0, -1):              # upward
            by = b * wy
            ch[fr.nid(x, by)].append(fr.nid(x, by - 1))
            for k in range(wy - 1):
                ch[fr.nid(x, by - 1 - k)].append(fr.nid(x, by - 2 - k))
    return ch


def evaluate(name, builder, rb):
    n = fr._MX * fr._MY
    deliveries = {s: builder(s) for s in range(n)}
    fill = max(tree_depth(deliveries[s], s) for s in range(n)) + 2 * fr.RAMP
    Lmax = link_load(deliveries)
    eject = (n - 1 + rb - 1) // rb
    mk, lb, rbuf = fr.measure_buffers(deliveries, rb)
    _, ej, bl, bd = fr.simulate(deliveries, rb)
    ok = all(ej[x] == n - 1 for x in range(n))
    bufferless = (lb <= 2 and rbuf <= 2)
    return dict(name=name, fill=fill, Lmax=Lmax, eject=eject, pipe=mk,
                link_buf=lb, ramp_buf=rbuf, ok=ok, bufferless=bufferless)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--size", type=int, default=16)
    args = ap.parse_args()
    sz = args.size
    fr.cfg(sz, sz, 4, 6)
    full = fr.ham_cycle_rect(0, 0, sz, sz)
    quads, ring4 = fr.quad_setup()

    fams = []
    fams.append(("ring (Q=1)", lambda s, b: fr.build_ring_delivery(full, s, b), True))
    fams.append(("quad-center (Q=4)",
                 lambda s, b: fr.build_quad_delivery(s, b, quads, ring4), True))
    fams.append(("border (Q=4)", fr.build_border_delivery, True))
    for B in (2, 4, 8, 16):
        if sz % B == 0:
            fams.append((f"hybrid B={B:<2d}",
                         (lambda s, b, B=B: fr.build_hybrid_delivery(s, B, b)), True))
    fams.append(("multitree (Q=N)", lambda s, b: fr.build_multitree_delivery(s), False))
    for Qx, Qy in ((2, 2), (4, 2), (2, 4), (4, 4), (8, 2), (2, 8), (8, 4), (4, 8)):
        if sz % Qx or sz % Qy or (sz // Qx) % 2:
            continue
        fams.append((f"grid {Qx}x{Qy}",
                     (lambda s, b, Qx=Qx, Qy=Qy: build_grid_border(s, Qx, Qy, b)), True))

    for rb, dirs in ((1, ("uni", "bi")), (2, ("bi",))):
        print(f"\n================ {sz}x{sz}  ramp_bw={rb}  "
              f"(eject floor {(sz*sz-1+rb-1)//rb}) ================")
        print(f"{'family':18s} {'dir':3s} {'fill':>5s} {'Lmax':>5s} {'pipe_mk':>7s} "
              f"{'link_buf':>8s} {'ramp_buf':>8s} {'0-buf?':>6s}")
        for name, fn, has_bi in fams:
            for d in dirs:
                if d == "bi" and not has_bi:
                    continue
                bidir = (d == "bi")
                m = evaluate(name, lambda s, fn=fn, bidir=bidir: fn(s, bidir), rb)
                print(f"{name:18s} {d:3s} {m['fill']:5d} {m['Lmax']:5d} "
                      f"{m['pipe']:7d} {m['link_buf']:8d} {m['ramp_buf']:8d} "
                      f"{('YES' if m['bufferless'] else 'no'):>6s}")


if __name__ == "__main__":
    main()
