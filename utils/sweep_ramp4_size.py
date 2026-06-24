#!/usr/bin/env python3
"""Two extra studies for the border short-arc report:

1. Down-ramp = 4 flit/cycle/node curve: same makespan-vs-AFIFO-depth sweep as
   sweep_afifo_depth, but the eject (down-ramp) bandwidth is forced to 4 for
   every size/direction.  Output: results/ramp4_afifo_depth_sweep.json (same
   structure as border_afifo_depth_sweep.json so gen_afifo_depth_curve can
   reuse line_chart/table_rows).

2. Message (data) size m = 1..5 flit: each src->dst delivery becomes an m-flit
   wormhole message (0 router buffer -> m consecutive cycles on every link and
   m eject cycles, capped at ramp_bw/cy/node).  We report the best (minimum)
   makespan at an effectively-unbounded AFIFO (cap=48) for ramp in {1,2,4}.
   Output: results/msg_size_sweep.json
"""

import json
import time
from datetime import datetime, timezone
from pathlib import Path

import sched_ring_zerobuf as S
from sweep_afifo_depth import CAPS, SIZES, collect_atomic, shape_cfg, sweep_config
from sweep_quad_ring_shapes import cfg_str, make_quads

ROOT = Path(__file__).resolve().parents[1]
RAMP4_OUT = ROOT / "results" / "ramp4_afifo_depth_sweep.json"
SIZE_OUT = ROOT / "results" / "msg_size_sweep.json"

RAMP4 = 4
MSG_SIZES = (1, 2, 3, 4, 5)
SIZE_RAMPS = (1, 2, 4)
SIZE_CAP = 48          # effectively unbounded border AFIFO for the size study
# Reduced atomic-cap pool for the size study: the low caps capture the
# pacing-optimal atomic schedules (which beat spread=0 for eject-bound uni
# rings) and cap=48 the unbounded one.  Verified to reproduce
# border_afifo_depth_sweep cap=48 for m=1 on every size/direction.
SIZE_ATOMIC_CAPS = (0, 1, 2, 3, 5, SIZE_CAP)


def run_ramp4(sizes=SIZES, caps=CAPS):
    out = {
        "updated": datetime.now(timezone.utc).isoformat(),
        "scheme": "border",
        "ramp_bw": RAMP4,
        "model": f"border short-arc, router_buf=0, per-link AFIFO cap, down-ramp={RAMP4}",
        "caps": list(caps),
        "configs": {},
    }
    t0 = time.time()
    for sz in sizes:
        for bidir, tag in ((False, "uni"), (True, "bi")):
            key = f"{sz}x{sz}_{tag}"
            print(f"== ramp4 {key} ==", flush=True)
            out["configs"][key] = sweep_config(sz, bidir, RAMP4, caps, "border")
    out["elapsed_s"] = time.time() - t0
    RAMP4_OUT.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"Wrote {RAMP4_OUT} ({out['elapsed_s']:.0f}s)")
    return out


def best_makespan(sz, bidir, ramp_bw, quads, flits, cap=SIZE_CAP):
    """Minimum makespan at AFIFO depth <= cap, using the same candidate pool as
    sweep_afifo_depth: the TDM spread=0 schedule plus atomic placements run at
    every afifo_cap (the atomic pacing threshold changes the makespan, so the
    optimum at an unbounded cap may come from an atomic run done at a *low* cap).
    Verified to reproduce border_afifo_depth_sweep cap=48 for m=1 on all sizes.
    A wide injection spread only trades makespan for shallower AFIFO, so it is
    omitted at this (effectively unbounded) cap."""
    deliv = lambda s, b, q=quads: S.deliv_border_quads(s, b, q)
    cands = []
    for lb in (False, True):
        r = S.schedule(sz, bidir, ramp_bw, deliv, spread=0, quads=quads,
                       lb_cross=lb, flits=flits)
        if r.get("ok"):
            cands.append(r)
    for r in collect_atomic(sz, bidir, ramp_bw, quads, SIZE_ATOMIC_CAPS,
                            "border", flits=flits):
        cands.append(r)
    feas = [c for c in cands if c["afifo_depth"] <= cap]
    return min((c["makespan"] for c in feas), default=None)


def run_size(sizes=SIZES, msg_sizes=MSG_SIZES, ramps=SIZE_RAMPS):
    out = {
        "updated": datetime.now(timezone.utc).isoformat(),
        "scheme": "border",
        "model": ("border short-arc, router_buf=0, wormhole m-flit messages, "
                  f"AFIFO cap={SIZE_CAP}"),
        "msg_sizes": list(msg_sizes),
        "ramps": list(ramps),
        "cap": SIZE_CAP,
        "configs": {},
    }
    t0 = time.time()
    for sz in sizes:
        for bidir, tag in ((False, "uni"), (True, "bi")):
            key = f"{sz}x{sz}_{tag}"
            n = sz * sz
            cfg = shape_cfg(sz, "border", tag)
            quads = make_quads(cfg, sz)
            native = 2 if bidir else 1
            by_ramp = {}
            eject_lbs = {}
            for rb in ramps:
                mks = []
                for m in msg_sizes:
                    t1 = time.time()
                    mk = best_makespan(sz, bidir, rb, quads, m)
                    mks.append(mk)
                    print(f"  {key} ramp={rb} m={m}: mk={mk} ({time.time()-t1:.1f}s)",
                          flush=True)
                by_ramp[str(rb)] = mks
                # eject lower bound for m-flit all-to-all: ceil((n-1)*m / ramp)
                eject_lbs[str(rb)] = [((n - 1) * m + rb - 1) // rb for m in msg_sizes]
            out["configs"][key] = {
                "size": sz,
                "bidir": bidir,
                "n": n,
                "native_ramp": native,
                "ring_shape": {"cfg": list(cfg), "cfg_str": cfg_str(cfg)},
                "by_ramp": by_ramp,
                "eject_lb": eject_lbs,
            }
    out["elapsed_s"] = time.time() - t0
    SIZE_OUT.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"Wrote {SIZE_OUT} ({out['elapsed_s']:.0f}s)")
    return out


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--sizes", type=int, nargs="+", default=list(SIZES))
    ap.add_argument("--only", choices=("ramp4", "size", "both"), default="both")
    args = ap.parse_args()
    sizes = tuple(args.sizes)
    if args.only in ("ramp4", "both"):
        run_ramp4(sizes)
    if args.only in ("size", "both"):
        run_size(sizes)


if __name__ == "__main__":
    main()
