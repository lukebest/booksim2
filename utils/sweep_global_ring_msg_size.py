#!/usr/bin/env python3
"""Ring (Q=1) global Hamilton allgather under reticle-AFIFO model.

Model: H=4, V=6; reticle (quad) boundary links use AFIFO latency cross_lat (default 6 cy);
ramp→router gap injection via sched_ring_zerobuf (0 router buffer).

Searches Hamilton cycle shape, spread, lb_cross, and atomic orderings.
Reports best mk for m=4,5 @ ramp 1,2 with optional m×mk(1) replay bound.

Output: results/global_ring_msg_size.json
"""

import json
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import sched_ring_zerobuf as S
import sim_fused_rings as fr

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results" / "global_ring_msg_size.json"
SZ = 16
CROSS_LAT = 6
AFIFO_CAP = 5
FLITS = (4, 5)
RAMPS = (1, 2)
SPREAD_MAX = 80
ATOMIC_ORDERS = ("natural", "interleave", "quad_first")
ATOMIC_CAPS = (0, 1, 2, 3, 4, 5, None)


def validate_order(order):
    n = len(order)
    if len(set(order)) != n:
        return False
    pos = {nd: i for i, nd in enumerate(order)}
    mx, my = fr._MX, fr._MY
    for i, nd in enumerate(order):
        x, y = fr.coord(nd)
        nbrs = []
        if x > 0:
            nbrs.append(fr.nid(x - 1, y))
        if x < mx - 1:
            nbrs.append(fr.nid(x + 1, y))
        if y > 0:
            nbrs.append(fr.nid(x, y - 1))
        if y < my - 1:
            nbrs.append(fr.nid(x, y + 1))
        nxt = order[(i + 1) % n]
        prv = order[(i - 1) % n]
        if nxt not in nbrs or prv not in nbrs:
            return False
    return True


def ring_shapes():
    """Candidate global Hamilton cycles on 16×16."""
    shapes = {}
    shapes["rect"] = fr.ham_cycle_rect(0, 0, fr._MX, fr._MY)
    shapes["vflip"] = fr.ham_cycle_rect_vflip(0, 0, fr._MX, fr._MY)
    shapes["vband"] = fr.ham_cycle_vband(fr._MX, 0)
    # horizontal-band snake: 4×16 bands stacked
    R = 4
    order = []
    for b in range(fr._MY // R):
        y0 = b * R
        band = fr.ham_cycle_rect(0, y0, fr._MX, R)
        if b % 2 == 1:
            band = list(reversed(band))
        if order:
            # stitch last of prev band to first of this band via mesh edge
            if band[0] not in _mesh_nbrs(order[-1]):
                band = list(reversed(band))
        order.extend(band)
    if validate_order(order):
        shapes["hband4"] = order
    return {k: v for k, v in shapes.items() if validate_order(v)}


def _mesh_nbrs(nd):
    x, y = fr.coord(nd)
    out = []
    if x > 0:
        out.append(fr.nid(x - 1, y))
    if x < fr._MX - 1:
        out.append(fr.nid(x + 1, y))
    if y > 0:
        out.append(fr.nid(x, y - 1))
    if y < fr._MY - 1:
        out.append(fr.nid(x, y + 1))
    return out


def make_deliv(order):
    def deliv(s, bidir, o=order):
        ch = defaultdict(list)
        fr.add_ring_chain(ch, o, s, bidir)
        return ch
    return deliv


def count_cross(order):
    c = 0
    n = len(order)
    for i in range(n):
        u, v = order[i], order[(i + 1) % n]
        if fr.quad_of(u) != fr.quad_of(v):
            c += 1
    return c


def afifo_ok(r, cap):
    if cap is None:
        return True
    return r.get("afifo_depth", 0) <= cap


def collect_schedule(deliv, bidir, ramp, flits, cap):
    best = None
    for sp in range(SPREAD_MAX):
        for lb in (False, True):
            r = S.schedule(SZ, bidir, ramp, deliv, spread=sp, lb_cross=lb, flits=flits)
            if not r.get("ok") or not afifo_ok(r, cap):
                continue
            rec = dict(r, method="schedule", spread=sp, lb_cross=lb)
            if best is None or rec["makespan"] < best["makespan"]:
                best = rec
    return best


def collect_atomic(deliv, bidir, ramp, flits, cap):
    best = None
    caps = ATOMIC_CAPS if cap is None else tuple(c for c in ATOMIC_CAPS if c is None or c <= cap)
    for order in ATOMIC_ORDERS:
        for ac in caps:
            r = S.schedule_atomic(SZ, bidir, ramp, deliv, afifo_cap=ac, order=order, flits=flits)
            if not r.get("ok") or not afifo_ok(r, cap):
                continue
            rec = dict(r, method="atomic", order=order, afifo_cap=ac)
            if best is None or rec["makespan"] < best["makespan"]:
                best = rec
    return best


def mk1_cache(shapes):
    cache = {}
    fr.cfg(SZ, SZ, 4, 6, cross=CROSS_LAT)
    for shape_name, order in shapes.items():
        deliv = make_deliv(order)
        for bidir, ramp in ((False, 1), (True, 2)):
            b = collect_schedule(deliv, bidir, ramp, 1, AFIFO_CAP)
            a = collect_atomic(deliv, bidir, ramp, 1, AFIFO_CAP)
            cands = [x for x in (b, a) if x]
            if not cands:
                continue
            best = min(cands, key=lambda x: x["makespan"])
            cache[(shape_name, bidir, ramp)] = best["makespan"]
    return cache


def best_for(flits, bidir, ramp, shapes, mk1):
    fr.cfg(SZ, SZ, 4, 6, cross=CROSS_LAT)
    tag = "bi" if bidir else "uni"
    overall = None
    per_shape = {}
    for shape_name, order in shapes.items():
        deliv = make_deliv(order)
        cands = []
        b = collect_schedule(deliv, bidir, ramp, flits, AFIFO_CAP)
        a = collect_atomic(deliv, bidir, ramp, flits, AFIFO_CAP)
        if b:
            b["shape"] = shape_name
            b["cross_hops"] = count_cross(order)
            cands.append(b)
        if a:
            a["shape"] = shape_name
            a["cross_hops"] = count_cross(order)
            cands.append(a)
        if not cands:
            continue
        best = min(cands, key=lambda x: x["makespan"])
        replay = flits * mk1.get((shape_name, bidir, ramp), best["makespan"])
        best["replay_bound"] = replay
        best["mk_final"] = min(best["makespan"], replay)
        best["bound_source"] = "replay" if best["mk_final"] == replay and replay < best["makespan"] else "wormhole"
        per_shape[shape_name] = best
        if overall is None or best["mk_final"] < overall["mk_final"]:
            overall = dict(best, direction=tag, ramp=ramp, flits=flits)
    return overall, per_shape


def main():
    t0 = time.time()
    fr.cfg(SZ, SZ, 4, 6, cross=CROSS_LAT)
    shapes = ring_shapes()
    print(f"shapes: {list(shapes.keys())}", flush=True)
    mk1 = mk1_cache(shapes)
    n = SZ * SZ
    out = {
        "updated": datetime.now(timezone.utc).isoformat(),
        "model": {
            "scheme": "ring (Q=1) global Hamilton",
            "H": 4, "V": 6, "cross_lat": CROSS_LAT,
            "afifo_cap": AFIFO_CAP,
            "scheduler": "sched_ring_zerobuf (gap inject, router_buf=0)",
            "reticle": "quad 8×8 (quad_of)",
        },
        "eject_lb": {str(r): (n - 1 + r - 1) // r for r in RAMPS},
        "mk1": {f"{s}_{'bi' if b else 'uni'}_r{r}": v for (s, b, r), v in mk1.items()},
        "results": {},
    }
    for flits in FLITS:
        for bidir, ramp in ((False, 1), (True, 2)):
            key = f"m{flits}_{'bi' if bidir else 'uni'}_r{ramp}"
            print(f"== {key} ==", flush=True)
            best, per_shape = best_for(flits, bidir, ramp, shapes, mk1)
            slim = lambda v: {kk: vv for kk, vv in v.items()
                              if kk not in ("events", "afifo_profile")}
            out["results"][key] = {
                "best": slim(best) if best else None,
                "by_shape": {k: slim(v) for k, v in per_shape.items()},
            }
            if best:
                print(f"  mk={best['mk_final']} ({best['bound_source']}) "
                      f"shape={best['shape']} method={best['method']} "
                      f"wormhole={best['makespan']} replay={best['replay_bound']}", flush=True)
    out["elapsed_s"] = round(time.time() - t0, 1)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, indent=2))
    print(f"wrote {OUT} ({out['elapsed_s']}s)", flush=True)


if __name__ == "__main__":
    main()
