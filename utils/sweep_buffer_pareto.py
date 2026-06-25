#!/usr/bin/env python3
"""Sweep multicast-tree fork placements vs router buffer budget and ramp bandwidth.

Outputs results/buffer_pareto_16x16.json with:
  * pipelined makespan + peak link_buf / ramp_buf (all scheme families)
  * strict_afifo5: router_buf=0, border AFIFO depth <= 5 (per scheme)
  * burst_pareto: link_buf <= 6, down-ramp burst buffer R in 0..6 (per ramp_bw)

Down-ramp bandwidth: 1 or 2 flit/cycle/node only.
"""

import json
import time
from collections import defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path

import sched_ring_zerobuf as S
import sched_zerobuf_compare as Z
import sim_fused_rings as fr
from optimize_quad_shapes import quads_for

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results" / "buffer_pareto_16x16.json"
AFIFO_CAP = 5
LINK_CAP = 6
RAMP_BURST_CAPS = tuple(range(0, 7))


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


def ch_to_edges(ch):
    return [(p, c) for p, kids in ch.items() for c in kids]


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


def evaluate(builder, bidir, ramp_bw, flits=1):
    n = fr._MX * fr._MY
    deliveries = {s: builder(s, bidir) for s in range(n)}
    fill = max(tree_depth(deliveries[s], s) for s in range(n)) + 2 * fr.RAMP + flits - 1
    Lmax = link_load(deliveries)
    eject = ((n - 1) * flits + ramp_bw - 1) // ramp_bw
    mk, lb, rbuf = fr.measure_buffers(deliveries, ramp_bw, flits=flits)
    _, ej, bl, _ = fr.simulate(deliveries, ramp_bw, flits=flits)
    ok = all(ej[x] == (n - 1) * flits for x in range(n))
    peak = max(lb, rbuf)
    dom = max((("fill", fill), ("Lmax", Lmax * flits), ("eject", eject)), key=lambda kv: kv[1])[0]
    return dict(fill=fill, Lmax=Lmax, eject=eject, pipe=mk, link_buf=lb,
                ramp_buf=rbuf, buf_peak=peak, busiest=bl, ok=ok, dom=dom)


def _best_ring_zerobuf(sz, bidir, ramp_bw, deliv_fn, quads, cap=AFIFO_CAP, flits=1, fast=False):
    best = None
    spreads = (0,) if fast else range(25)
    for sp in spreads:
        for lb in (False, True):
            r = S.schedule(sz, bidir, ramp_bw, deliv_fn, spread=sp, lb_cross=lb,
                           quads=quads, flits=flits)
            if not r.get("ok"):
                continue
            if r["afifo_depth"] > cap or r["afifo_balanced"]["peak"] > cap:
                continue
            rec = dict(makespan=r["makespan"], afifo=r["afifo_balanced"]["peak"],
                       method="schedule", ok=True)
            if best is None or rec["makespan"] < best["makespan"]:
                best = rec
    for order in ("interleave", "natural", "quad"):
        r = S.schedule_atomic(sz, bidir, ramp_bw, deliv_fn, afifo_cap=cap,
                              order=order, quads=quads, flits=flits)
        if not r.get("ok") or r["afifo_balanced"]["peak"] > cap:
            continue
        rec = dict(makespan=r["makespan"], afifo=r["afifo_balanced"]["peak"],
                   method=f"atomic:{order}", ok=True)
        if best is None or rec["makespan"] < best["makespan"]:
            best = rec
    return best


def eval_strict_afifo5(name, builder, bidir, ramp_bw, sz, flits=1, fast_border=False):
    """Strict router_buf=0; border AFIFO <= 5 where applicable."""
    if flits > 1 or fast_border:
        fr.cfg(sz, sz, 4, 6, cross=6)
    else:
        fr.cfg(sz, sz, 4, 6)
    Z.cfg(sz, sz, 4, 6)
    Z.init_ring()
    Z.init_quadrants()
    tag = "bi" if bidir else "uni"
    mx, h, v = sz, 4, 6

    if name == "border (Q=4)":
        quads = quads_for(sz, "border", tag)
        deliv = lambda s, b, q=quads: S.deliv_border_quads(s, b, q)
        rec = _best_ring_zerobuf(sz, bidir, ramp_bw, deliv, quads, flits=flits,
                                 fast=fast_border)
        if rec:
            rec["mode"] = "ring_zerobuf"
            return rec
        return dict(makespan=None, ok=False, afifo=None, mode="ring_zerobuf")

    def rigid_fp(s):
        if name == "ring (Q=1)":
            return Z.fp_ring(s, Z.RING_ORDER, Z.RING_POS, bidir, ramp_bw)
        if name == "quad-center (Q=4)":
            return Z.fp_quadrant(s, bidir, ramp_bw)
        if name == "multitree (Q=N)":
            return Z.fp_multitree(s)
        if name.startswith("hybrid B="):
            B = int(name.split("=")[1])
            return Z.fp_hybrid(s, B, bidir, ramp_bw)
        if name.startswith("grid "):
            ch = builder(s, bidir)
            from tree_fork_research import fp_from_edges
            return fp_from_edges(s, ch_to_edges(ch), mx, h, v, ramp_bw)
        raise ValueError(name)

    mk, _, _, ok = Z.run_scheme(rigid_fp, ramp_bw, flits=flits)
    return dict(makespan=mk, ok=ok, afifo=0, mode="zerobuf_rigid", method="pack")


def burst_frontier(schemes, link_cap=LINK_CAP, ramp_caps=RAMP_BURST_CAPS):
    """For each down-ramp burst budget R, best pipelined mk with link_buf<=6, ramp_buf<=R."""
    out = {}
    for R in ramp_caps:
        feas = [s for s in schemes if s["ok"] and s["link_buf"] <= link_cap and s["ramp_buf"] <= R]
        if not feas:
            out[str(R)] = None
            continue
        rec = min(feas, key=lambda s: s["pipe"])
        out[str(R)] = dict(
            ramp_burst=R,
            makespan=rec["pipe"],
            scheme=rec["name"],
            dir=rec["dir"],
            link_buf=rec["link_buf"],
            ramp_buf=rec["ramp_buf"],
            fill=rec["fill"],
            dom=rec["dom"],
        )
    return out


def sweep(sz=16, ramp_bws=(1, 2), flits=1, fast_border=False):
    fr.cfg(sz, sz, 4, 6)
    n = sz * sz
    fams = all_families(sz)
    out = dict(
        mesh=f"{sz}x{sz}", n=n, H=4, V=6, ramp=1, flits=flits,
        afifo_cap=AFIFO_CAP, link_cap=LINK_CAP,
        ramp_burst_caps=list(RAMP_BURST_CAPS),
        updated=datetime.now(timezone.utc).isoformat(),
        ramp_bws=list(ramp_bws),
        schemes={}, strict_afifo5={}, burst_pareto={},
    )
    for rb in ramp_bws:
        key = str(rb)
        schemes = []
        strict = []
        for name, fn, has_bi in fams:
            for d, bidir in (("uni", False), ("bi", True)):
                if d == "bi" and not has_bi:
                    continue
                m = evaluate(fn, bidir, rb, flits=flits)
                rec = dict(name=name, dir=d, ramp_bw=rb, **m)
                schemes.append(rec)
                srec = eval_strict_afifo5(name, fn, bidir, rb, sz, flits=flits,
                                          fast_border=fast_border)
                strict.append(dict(name=name, dir=d, ramp_bw=rb, **srec))
        schemes.sort(key=lambda s: (s["pipe"], s["buf_peak"]))
        strict.sort(key=lambda s: (s.get("makespan") or 1 << 30))
        out["schemes"][key] = schemes
        out["strict_afifo5"][key] = strict
        eject_lb = ((n - 1) * flits + rb - 1) // rb
        out["burst_pareto"][key] = dict(
            eject_lb=eject_lb,
            link_cap=LINK_CAP,
            by_ramp_burst=burst_frontier(schemes),
        )
    return out


def main():
    t0 = time.time()
    data = sweep(16, (1, 2))
    data["elapsed_s"] = time.time() - t0
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(data, indent=2), encoding="utf-8")
    print(f"Wrote {OUT} ({data['elapsed_s']:.1f}s)")
    for rb in data["ramp_bws"]:
        print(f"\n=== ramp_bw={rb} ===")
        feas = [s for s in data["strict_afifo5"][str(rb)] if s.get("makespan")]
        if feas:
            b = min(feas, key=lambda s: s["makespan"])
            print(f"  strict AFIFO<=5: {b['name']} ({b['dir']}) mk={b['makespan']}")
        bp = data["burst_pareto"][str(rb)]["by_ramp_burst"]
        for R in ("0", "2", "6"):
            p = bp.get(R)
            if p:
                print(f"  burst R={R}: {p['scheme']} ({p['dir']}) mk={p['makespan']} "
                      f"link={p['link_buf']} ramp={p['ramp_buf']}")


if __name__ == "__main__":
    main()
