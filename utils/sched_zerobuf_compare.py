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


# --------------------------------------------------------------------------
# Rigid offset packer (conflict-free links + capacity-RAMP_BW ramps).
# --------------------------------------------------------------------------
def pack(footprints, ramp_bw, src_order):
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
                    off = cyc - rel
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
            t[key][c] = t[key].get(c, 0) + 1
            if kind == 'D':
                makespan = max(makespan, c + RAMP)
        max_off = max(max_off, off)
    return makespan, max_off, (link_busy, up_busy, down_busy)


def verify(busy, ramp_bw):
    link_busy, up_busy, down_busy = busy
    link_ok = all(ct <= 1 for d in link_busy.values() for ct in d.values())
    up_ok = all(ct <= ramp_bw for d in up_busy.values() for ct in d.values())
    down_ok = all(ct <= ramp_bw for d in down_busy.values() for ct in d.values())
    ejects = {n: sum(d.values()) for n, d in down_busy.items()}
    eject_ok = all(ejects.get(n, 0) == N - 1 for n in range(N))
    return link_ok and up_ok and down_ok and eject_ok


SRC_ORDERS = {
    "corner": lambda: sorted(range(N), key=lambda s: -(abs(coord(s)[0] - (MX - 1) / 2) + abs(coord(s)[1] - (MY - 1) / 2))),
    "center": lambda: sorted(range(N), key=lambda s: (abs(coord(s)[0] - (MX - 1) / 2) + abs(coord(s)[1] - (MY - 1) / 2))),
    "natural": lambda: list(range(N)),
    "rev": lambda: list(range(N - 1, -1, -1)),
    "col": lambda: sorted(range(N), key=lambda s: (coord(s)[0], coord(s)[1])),
    "ring": lambda: list(RING_ORDER),
}


def run_scheme(build_fp, ramp_bw):
    foot = {s: build_fp(s) for s in range(N)}
    best = None
    for name, gen in SRC_ORDERS.items():
        mk, mo, busy = pack(foot, ramp_bw, gen())
        ok = verify(busy, ramp_bw)
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
            print(f"  {mode}:")
            for B, r in sorted(d[mode].items()):
                print(f"      B={B:2d} (R={MY//B:2d})  makespan={r['makespan']:5d}  ok={r['ok']}")
        print()

    if args.json:
        import json
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        print(f"Wrote {args.json}")


if __name__ == "__main__":
    main()
