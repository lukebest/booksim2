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
   makespan with the border AFIFO depth constrained to <= 5 FLITS, for ramp in
   {1,2,4}.  Output: results/msg_size_sweep.json
"""

import json
import time
from datetime import datetime, timezone
from pathlib import Path

import sched_ring_zerobuf as S
import sim_fused_rings as fr
from sweep_afifo_depth import CAPS, SIZES, collect_atomic, shape_cfg, sweep_config
from sweep_quad_ring_shapes import cfg_str, make_quads

ROOT = Path(__file__).resolve().parents[1]
RAMP4_OUT = ROOT / "results" / "ramp4_afifo_depth_sweep.json"
SIZE_OUT = ROOT / "results" / "msg_size_sweep.json"

RAMP4 = 4
FLIT_BYTES = 64
BUS_WIDTH_BYTES = 64
MSG_SIZES = tuple(range(1, 6))
SIZE_RAMPS = (1, 2)
SIZE_CROSS_LAT = 6       # border AFIFO link latency (cy); H=4, V=6
SIZE_CAP = 5             # border AFIFO depth constrained to <= 5 FLITS
# Atomic-cap pool for the size study: atomic runs paced at afifo_cap 0..5 yield
# the depth<=5 candidates; merged with the (rarely feasible) spread=0 schedule
# and filtered at depth<=SIZE_CAP.  Verified to reproduce
# border_afifo_depth_sweep cap=5 for m=1 on every size/direction.
SIZE_ATOMIC_CAPS = (0, 1, 2, 3, 4, 5)


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
    """Minimum makespan at AFIFO depth <= cap (flit-accurate), cross_lat=6."""
    fr.cfg(sz, sz, 4, 6, cross=SIZE_CROSS_LAT)
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
                  f"AFIFO cap={SIZE_CAP}, cross_lat={SIZE_CROSS_LAT}, "
                  f"flit={FLIT_BYTES}B, bus={BUS_WIDTH_BYTES}B"),
        "flit_bytes": FLIT_BYTES,
        "bus_width_bytes": BUS_WIDTH_BYTES,
        "cross_lat": SIZE_CROSS_LAT,
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
            SIZE_OUT.write_text(json.dumps(out, indent=2), encoding="utf-8")
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
