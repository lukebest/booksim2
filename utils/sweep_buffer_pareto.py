#!/usr/bin/env python3
"""Sweep multicast-tree fork placements vs router buffer budget K and ramp bandwidth.

For each scheme we record pipelined (conflict-free TDM) makespan and peak router
buffers (link_buf, ramp_buf).  A scheme is feasible at budget K iff
  max(link_buf, ramp_buf) <= K.

The Pareto frontier at ramp_bw R is: for each K, the minimum makespan among
feasible schemes (best over uni/bi when both exist).

Output: results/buffer_pareto_16x16.json
"""

import json
import time
from collections import defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path

import sim_fused_rings as fr

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results" / "buffer_pareto_16x16.json"


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
    M = fr._MX
    wx, wy = M // Qx, M // Qy
    sx, sy = fr.coord(s)
    rx, ry = sx // wx, sy // wy
    x0, y0 = rx * wx, ry * wy
    ys = list(range(y0, y0 + wy))
    ch = defaultdict(list)
    fr.add_ring_chain(ch, fr.ham_cycle_rect(x0, y0, wx, wy), s, bidir)
    for a in range(rx, Qx - 1):
        bx = (a + 1) * wx
        for y in ys:
            ch[fr.nid(bx - 1, y)].append(fr.nid(bx, y))
            for k in range(wx - 1):
                ch[fr.nid(bx + k, y)].append(fr.nid(bx + k + 1, y))
    for a in range(rx, 0, -1):
        bx = a * wx
        for y in ys:
            ch[fr.nid(bx, y)].append(fr.nid(bx - 1, y))
            for k in range(wx - 1):
                ch[fr.nid(bx - 1 - k, y)].append(fr.nid(bx - 2 - k, y))
    for x in range(M):
        for b in range(ry, Qy - 1):
            by = (b + 1) * wy
            ch[fr.nid(x, by - 1)].append(fr.nid(x, by))
            for k in range(wy - 1):
                ch[fr.nid(x, by + k)].append(fr.nid(x, by + k + 1))
        for b in range(ry, 0, -1):
            by = b * wy
            ch[fr.nid(x, by)].append(fr.nid(x, by - 1))
            for k in range(wy - 1):
                ch[fr.nid(x, by - 1 - k)].append(fr.nid(x, by - 2 - k))
    return ch


def all_families(sz):
    fr.cfg(sz, sz, 4, 6)
    full = fr.ham_cycle_rect(0, 0, sz, sz)
    quads, ring4 = fr.quad_setup()
    fams = [
        ("ring (Q=1)", lambda s, b: fr.build_ring_delivery(full, s, b), True),
        ("quad-center (Q=4)", lambda s, b: fr.build_quad_delivery(s, b, quads, ring4), True),
        ("border (Q=4)", fr.build_border_delivery, True),
        ("multitree (Q=N)", lambda s, b: fr.build_multitree_delivery(s), False),
    ]
    for B in (2, 4, 8, 16):
        if sz % B == 0:
            fams.append((f"hybrid B={B}", lambda s, b, B=B: fr.build_hybrid_delivery(s, B, b), True))
    for Qx, Qy in ((2, 2), (4, 2), (2, 4), (4, 4), (8, 2), (2, 8), (8, 4), (4, 8)):
        if sz % Qx or sz % Qy or (sz // Qx) % 2:
            continue
        fams.append((f"grid {Qx}x{Qy}",
                     lambda s, b, Qx=Qx, Qy=Qy: build_grid_border(s, Qx, Qy, b), True))
    return fams


def evaluate(builder, bidir, ramp_bw):
    n = fr._MX * fr._MY
    deliveries = {s: builder(s, bidir) for s in range(n)}
    fill = max(tree_depth(deliveries[s], s) for s in range(n)) + 2 * fr.RAMP
    Lmax = link_load(deliveries)
    eject = (n - 1 + ramp_bw - 1) // ramp_bw
    mk, lb, rbuf = fr.measure_buffers(deliveries, ramp_bw)
    _, ej, bl, _ = fr.simulate(deliveries, ramp_bw)
    ok = all(ej[x] == n - 1 for x in range(n))
    peak = max(lb, rbuf)
    dom = max((("fill", fill), ("Lmax", Lmax), ("eject", eject)), key=lambda kv: kv[1])[0]
    return dict(fill=fill, Lmax=Lmax, eject=eject, pipe=mk, link_buf=lb,
                ramp_buf=rbuf, buf_peak=peak, busiest=bl, ok=ok, dom=dom)


def pareto_frontier(schemes, k_max):
    """Monotone frontier: at each K, best makespan with buf_peak <= K."""
    pts = []
    best_mk = None
    best_rec = None
    for K in range(0, k_max + 1):
        cand = [s for s in schemes if s["buf_peak"] <= K and s["ok"]]
        if not cand:
            continue
        rec = min(cand, key=lambda s: s["pipe"])
        if best_mk is None or rec["pipe"] < best_mk:
            best_mk = rec["pipe"]
            best_rec = rec
            pts.append(dict(K=K, makespan=rec["pipe"], scheme=rec["name"],
                            dir=rec["dir"], link_buf=rec["link_buf"],
                            ramp_buf=rec["ramp_buf"], fill=rec["fill"],
                            Lmax=rec["Lmax"], dom=rec["dom"]))
    return pts


def sweep(sz=16, ramp_bws=(1, 2, 4)):
    fr.cfg(sz, sz, 4, 6)
    n = sz * sz
    fams = all_families(sz)
    out = dict(mesh=f"{sz}x{sz}", n=n, H=4, V=6, ramp=1,
               updated=datetime.now(timezone.utc).isoformat(),
               ramp_bws=list(ramp_bws), schemes={}, pareto={})
    for rb in ramp_bws:
        key = str(rb)
        schemes = []
        for name, fn, has_bi in fams:
            for d, bidir in (("uni", False), ("bi", True)):
                if d == "bi" and not has_bi:
                    continue
                m = evaluate(fn, bidir, rb)
                rec = dict(name=name, dir=d, ramp_bw=rb, **m)
                schemes.append(rec)
        schemes.sort(key=lambda s: (s["pipe"], s["buf_peak"]))
        out["schemes"][key] = schemes
        k_max = max((s["buf_peak"] for s in schemes), default=0)
        out["pareto"][key] = dict(eject_lb=(n - 1 + rb - 1) // rb,
                                   k_max=k_max,
                                   frontier=pareto_frontier(schemes, k_max))
    return out


def main():
    t0 = time.time()
    data = sweep(16, (1, 2, 4))
    data["elapsed_s"] = time.time() - t0
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(data, indent=2), encoding="utf-8")
    print(f"Wrote {OUT} ({data['elapsed_s']:.1f}s)")
    for rb in data["ramp_bws"]:
        pf = data["pareto"][str(rb)]["frontier"]
        print(f"\nramp_bw={rb}  eject_lb={data['pareto'][str(rb)]['eject_lb']}")
        for p in pf:
            print(f"  K<={p['K']:3d}  mk={p['makespan']:4d}  {p['scheme']} ({p['dir']})  "
                  f"link={p['link_buf']} ramp={p['ramp_buf']}")


if __name__ == "__main__":
    main()
