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
import sim_fused_rings as fr
from optimize_quad_shapes import load_optimal
from sweep_quad_ring_shapes import cfg_str, make_quads

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results" / "border_afifo_depth_sweep.json"
CAPS = (0, 1, 2, 3, 4, 5, 8, 12, 16, 20, 24, 32, 40, 45, 46, 47, 48)
SIZES = (4, 8, 16)


def out_path(scheme):
    return ROOT / "results" / f"{scheme}_afifo_depth_sweep.json"


def deliv_quads(scheme, quads):
    """Bound delivery builder for the requested scheme."""
    if scheme == "ringfollow":
        return lambda s, b, q=quads: S.deliv_ringfollow_quads(s, b, q)
    if scheme == "border_bal":
        return lambda s, b, q=quads: S.deliv_border_bal_quads(s, b, q)
    return lambda s, b, q=quads: S.deliv_border_quads(s, b, q)


def shape_cfg(sz, scheme, tag):
    """Min-makespan ring shape (best_any), not AFIFO≤5 chosen."""
    data = load_optimal()
    # border_bal reuses the optimized border home-ring shapes for a fair compare
    shape_scheme = "border" if scheme == "border_bal" else scheme
    block = data["sizes"].get(f"{sz}x{sz}", {}).get(shape_scheme, {}).get(tag, {})
    rec = block.get("best_any") or block.get("chosen")
    if not rec:
        return (("rect", 0), ("rect", 0), ("rect", 0), ("rect", 0))
    return tuple(tuple(x) for x in rec["cfg"])


def eject_lb(n, ramp_bw):
    return (n - 1 + ramp_bw - 1) // ramp_bw


def cache_schedules(sz, bidir, ramp_bw, quads, scheme="border", spread_max=80):
    """Run spread×lb_cross schedule once; reuse for all caps."""
    deliv = deliv_quads(scheme, quads)
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


def min_at_cap(candidates, cap):
    """Best makespan among schedules with per-link AFIFO peak <= cap."""
    feas = [c for c in candidates if c["afifo_depth"] <= cap]
    if not feas:
        return None
    return min(feas, key=lambda x: x["makespan"])


def collect_atomic(sz, bidir, ramp_bw, quads, caps, scheme="border", flits=1):
    """Run atomic once per (cap, order); pool must be merged across caps so
    a schedule found at cap=1 (depth=1) remains feasible at cap=2."""
    deliv = deliv_quads(scheme, quads)
    pool = []
    for cap in caps:
        cap_arg = 0 if cap == 0 else cap
        for order in ("interleave", "natural", "quad"):
            r = S.schedule_atomic(sz, bidir, ramp_bw, deliv, afifo_cap=cap_arg,
                                  order=order, quads=quads, flits=flits)
            if r.get("ok"):
                pool.append({
                    "makespan": r["makespan"],
                    "afifo_depth": r["afifo_depth"],
                    "afifo_balanced": r["afifo_balanced"]["peak"],
                    "method": "atomic",
                    "order": order,
                    "atomic_cap": cap_arg,
                })
    return pool


def sweep_config(sz, bidir, ramp_bw, caps=CAPS, scheme="border"):
    tag = "bi" if bidir else "uni"
    cfg = shape_cfg(sz, scheme, tag)
    quads = make_quads(cfg, sz)
    shape = {"cfg": list(cfg), "cfg_str": cfg_str(cfg)}
    n = sz * sz
    spread_max = 30 if sz <= 4 else (50 if sz <= 8 else 80)
    print(f"  caching spread 0..{spread_max-1}...", flush=True)
    t_cache = time.time()
    cached = cache_schedules(sz, bidir, ramp_bw, quads, scheme, spread_max)
    print(f"  {len(cached)} schedules in {time.time()-t_cache:.1f}s", flush=True)
    atomic_pool = collect_atomic(sz, bidir, ramp_bw, quads, caps, scheme)
    candidates = cached + atomic_pool
    points = []
    prev_mk = None
    for cap in caps:
        t0 = time.time()
        rec = min_at_cap(candidates, cap)
        mk = rec["makespan"] if rec else None
        if prev_mk is not None and mk is not None and mk > prev_mk:
            print(f"  WARNING cap={cap}: mk={mk} > prev={prev_mk} (pool merge should prevent this)",
                  flush=True)
        if mk is not None:
            prev_mk = mk
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


def run(sizes=SIZES, caps=CAPS, scheme="border"):
    model = {"border": "border short-arc",
             "border_bal": "border short-arc, balanced diagonal",
             "ringfollow": "ring-follow"}.get(scheme, scheme)
    out = {
        "updated": datetime.now(timezone.utc).isoformat(),
        "scheme": scheme,
        "model": f"{model}, router_buf=0, per-link AFIFO cap, cross_lat={fr.CROSS_LAT}",
        "caps": list(caps),
        "configs": {},
    }
    t0 = time.time()
    for sz in sizes:
        for bidir, tag, rb in ((False, "uni", 1), (True, "bi", 2)):
            key = f"{sz}x{sz}_{tag}"
            print(f"== {key} ({scheme}) ==", flush=True)
            out["configs"][key] = sweep_config(sz, bidir, rb, caps, scheme)
    out["elapsed_s"] = time.time() - t0
    dst = out_path(scheme)
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"Wrote {dst} ({out['elapsed_s']:.0f}s)")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sizes", type=int, nargs="+", default=list(SIZES))
    ap.add_argument("--caps", type=int, nargs="+", default=list(CAPS))
    ap.add_argument("--scheme", default="border",
                    choices=("border", "border_bal", "ringfollow"))
    args = ap.parse_args()
    run(tuple(args.sizes), tuple(args.caps), args.scheme)


if __name__ == "__main__":
    main()
