#!/usr/bin/env python3
"""Sweep border short-arc makespan vs border AFIFO depth cap.

Model: router zero-buffer, conflict-free, non-blocking (sched_ring_zerobuf).
Ring shapes: optimized per size from optimal_quad_shapes.json.
Constraint: peak per-link AFIFO depth <= cap (cap=0 means no border waiting).

Output: results/border_afifo_depth_sweep.json
"""

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import sched_ring_zerobuf as S
from optimize_quad_shapes import load_optimal
from sweep_quad_ring_shapes import cfg_str, make_quads

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results" / "border_afifo_depth_sweep.json"
CAPS = (0, 1, 2, 3, 4, 5, 8, 12, 16, 20, 24, 32, 40, 48)
SIZES = (4, 8, 16)


def shape_cfg(sz, scheme, tag):
    """Min-makespan ring shape (best_any), not AFIFO≤5 chosen."""
    data = load_optimal()
    block = data["sizes"].get(f"{sz}x{sz}", {}).get(scheme, {}).get(tag, {})
    rec = block.get("best_any") or block.get("chosen")
    if not rec:
        return (("rect", 0), ("rect", 0), ("rect", 0), ("rect", 0))
    return tuple(tuple(x) for x in rec["cfg"])


def eject_lb(n, ramp_bw):
    return (n - 1 + ramp_bw - 1) // ramp_bw


def cache_schedules(sz, bidir, ramp_bw, quads, spread_max=80):
    """Run spread×lb_cross schedule once; reuse for all caps."""
    deliv = lambda s, b, q=quads: S.deliv_border_quads(s, b, q)
    cached = []
    for sp in range(spread_max):
        for lb in (False, True):
            r = S.schedule(sz, bidir, ramp_bw, deliv, spread=sp,
                             lb_cross=lb, quads=quads)
            if not r.get("ok"):
                continue
            cached.append({
                "makespan": r["makespan"],
                "afifo_depth": r["afifo_depth"],
                "afifo_balanced": r["afifo_balanced"]["peak"],
                "method": "schedule",
                "spread": sp,
                "lb_cross": lb,
            })
    return cached


def best_at_cap(sz, bidir, ramp_bw, quads, cap, cached, spread_max=80):
    """Min makespan with afifo_depth <= cap."""
    deliv = lambda s, b, q=quads: S.deliv_border_quads(s, b, q)
    best = None

    def consider(rec):
        nonlocal best
        if rec is None or rec["afifo_depth"] > cap:
            return
        if best is None or rec["makespan"] < best["makespan"]:
            best = rec

    for rec in cached:
        consider(rec)

    cap_arg = 0 if cap == 0 else cap
    for order in ("interleave", "natural", "quad"):
        r = S.schedule_atomic(sz, bidir, ramp_bw, deliv, afifo_cap=cap_arg,
                              order=order, quads=quads)
        if r.get("ok"):
            consider({
                "makespan": r["makespan"],
                "afifo_depth": r["afifo_depth"],
                "afifo_balanced": r["afifo_balanced"]["peak"],
                "method": "atomic",
                "order": order,
            })

    return best


def sweep_config(sz, bidir, ramp_bw, caps=CAPS):
    tag = "bi" if bidir else "uni"
    cfg = shape_cfg(sz, "border", tag)
    quads = make_quads(cfg, sz)
    shape = {"cfg": list(cfg), "cfg_str": cfg_str(cfg)}
    n = sz * sz
    spread_max = 30 if sz <= 4 else (50 if sz <= 8 else 80)
    print(f"  caching spread 0..{spread_max-1}...", flush=True)
    t_cache = time.time()
    cached = cache_schedules(sz, bidir, ramp_bw, quads, spread_max)
    print(f"  {len(cached)} schedules in {time.time()-t_cache:.1f}s", flush=True)
    points = []
    for cap in caps:
        t0 = time.time()
        rec = best_at_cap(sz, bidir, ramp_bw, quads, cap, cached, spread_max)
        points.append({
            "cap": cap,
            "makespan": rec["makespan"] if rec else None,
            "feasible": rec is not None,
            "detail": rec,
            "elapsed_s": time.time() - t0,
        })
        mk = rec["makespan"] if rec else "—"
        print(f"  cap={cap:2d}  mk={mk}  ({time.time()-t0:.1f}s)", flush=True)
    return {
        "size": sz,
        "bidir": bidir,
        "ramp_bw": ramp_bw,
        "n": n,
        "eject_lb": eject_lb(n, ramp_bw),
        "ring_shape": shape,
        "points": points,
    }


def run(sizes=SIZES, caps=CAPS):
    out = {
        "updated": datetime.now(timezone.utc).isoformat(),
        "model": "border short-arc, router_buf=0, per-link AFIFO cap",
        "caps": list(caps),
        "configs": {},
    }
    t0 = time.time()
    for sz in sizes:
        for bidir, tag, rb in ((False, "uni", 1), (True, "bi", 2)):
            key = f"{sz}x{sz}_{tag}"
            print(f"== {key} ==", flush=True)
            out["configs"][key] = sweep_config(sz, bidir, rb, caps)
    out["elapsed_s"] = time.time() - t0
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"Wrote {OUT} ({out['elapsed_s']:.0f}s)")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sizes", type=int, nargs="+", default=list(SIZES))
    ap.add_argument("--caps", type=int, nargs="+", default=list(CAPS))
    args = ap.parse_args()
    run(tuple(args.sizes), tuple(args.caps))


if __name__ == "__main__":
    main()
