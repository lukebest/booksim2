#!/usr/bin/env python3
"""2D sweep: router per-port buffer cap × border AFIFO depth cap.

Model: border short-arc, ring shapes from optimal_quad_shapes.json (best_any).
Candidates:
  - strict schedules (spread + atomic): ring_buf=0, eject_buf=0 by construction
  - pipelined TDM (simulate_afifo): may use ring_buf / eject_buf > 0

Feasibility at (router_cap K, afifo_cap A):
  peak ring-internal link buffer <= K
  peak eject (down-ramp) buffer <= K
  peak per-link AFIFO <= A

Output: results/router_afifo_depth_sweep.json
"""

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import sim_fused_rings as fr
import sched_ring_zerobuf as S
from optimize_quad_shapes import load_optimal
from sweep_afifo_depth import CAPS, collect_atomic, cache_schedules, shape_cfg, eject_lb
from sweep_quad_ring_shapes import cfg_str, make_quads

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results" / "router_afifo_depth_sweep.json"
ROUTER_CAPS = (0, 1, 2, 3, 4)
SIZES = (4, 8, 16)


def pipelined_candidate(sz, bidir, ramp_bw, quads):
    fr.cfg(sz, sz, 4, 6)
    n = sz * sz
    deliveries = {s: S.deliv_border_quads(s, bidir, quads) for s in range(n)}
    r = fr.simulate_afifo(deliveries, ramp_bw)
    if not r.get("ok"):
        return None
    return {
        "makespan": r["makespan"],
        "afifo_depth": r["afifo_buf"],
        "afifo_balanced": r["afifo_buf"],
        "ring_buf": r["ring_buf"],
        "eject_buf": r["eject_buf"],
        "method": "pipelined",
    }


def stamp_strict(c):
    out = dict(c)
    out.setdefault("ring_buf", 0)
    out.setdefault("eject_buf", 0)
    return out


def min_at(candidates, router_cap, afifo_cap):
    feas = [
        c for c in candidates
        if c["afifo_depth"] <= afifo_cap
        and c.get("ring_buf", 0) <= router_cap
        and c.get("eject_buf", 0) <= router_cap
    ]
    if not feas:
        return None
    return min(feas, key=lambda x: x["makespan"])


def sweep_config(sz, bidir, ramp_bw, router_caps=ROUTER_CAPS, afifo_caps=CAPS):
    tag = "bi" if bidir else "uni"
    cfg = shape_cfg(sz, "border", tag)
    quads = make_quads(cfg, sz)
    shape = {"cfg": list(cfg), "cfg_str": cfg_str(cfg)}
    n = sz * sz
    spread_max = 30 if sz <= 4 else (50 if sz <= 8 else 80)
    print(f"  caching spread 0..{spread_max - 1}...", flush=True)
    t_cache = time.time()
    cached = [stamp_strict(c) for c in cache_schedules(sz, bidir, ramp_bw, quads, "border", spread_max)]
    print(f"  {len(cached)} spread schedules in {time.time() - t_cache:.1f}s", flush=True)
    atomic_pool = [stamp_strict(c) for c in collect_atomic(sz, bidir, ramp_bw, quads, afifo_caps, "border")]
    pip = pipelined_candidate(sz, bidir, ramp_bw, quads)
    candidates = cached + atomic_pool
    if pip:
        candidates.append(pip)
        print(f"  pipelined: mk={pip['makespan']} ring_buf={pip['ring_buf']} "
              f"eject_buf={pip['eject_buf']} afifo={pip['afifo_depth']}", flush=True)

    grid = {}
    for k in router_caps:
        row = []
        prev_mk = None
        for a in afifo_caps:
            rec = min_at(candidates, k, a)
            mk = rec["makespan"] if rec else None
            if prev_mk is not None and mk is not None and mk > prev_mk:
                print(f"  WARNING K={k} A={a}: mk={mk} > prev={prev_mk}", flush=True)
            if mk is not None:
                prev_mk = mk
            row.append({
                "router_cap": k,
                "afifo_cap": a,
                "makespan": mk,
                "feasible": rec is not None,
                "detail": rec,
            })
        grid[k] = row
        mk0 = row[0]["makespan"]
        mkl = row[-1]["makespan"]
        print(f"  K={k}: mk@A=0={mk0} mk@A={afifo_caps[-1]}={mkl}", flush=True)

    return {
        "size": sz,
        "bidir": bidir,
        "ramp_bw": ramp_bw,
        "n": n,
        "eject_lb": eject_lb(n, ramp_bw),
        "ring_shape": shape,
        "grid": grid,
        "pipelined": pip,
    }


def run(sizes=SIZES, router_caps=ROUTER_CAPS, afifo_caps=CAPS):
    out = {
        "updated": datetime.now(timezone.utc).isoformat(),
        "scheme": "border",
        "model": "border short-arc; per-port router buffer cap × per-link AFIFO cap; cross_lat=10",
        "router_caps": list(router_caps),
        "afifo_caps": list(afifo_caps),
        "configs": {},
    }
    t0 = time.time()
    for sz in sizes:
        for bidir, tag, rb in ((False, "uni", 1), (True, "bi", 2)):
            key = f"{sz}x{sz}_{tag}"
            print(f"== {key} ==", flush=True)
            out["configs"][key] = sweep_config(sz, bidir, rb, router_caps, afifo_caps)
    out["elapsed_s"] = time.time() - t0
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"Wrote {OUT} ({out['elapsed_s']:.0f}s)")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sizes", type=int, nargs="+", default=list(SIZES))
    ap.add_argument("--router-caps", type=int, nargs="+", default=list(ROUTER_CAPS))
    ap.add_argument("--afifo-caps", type=int, nargs="+", default=list(CAPS))
    args = ap.parse_args()
    run(tuple(args.sizes), tuple(args.router_caps), tuple(args.afifo_caps))


if __name__ == "__main__":
    main()
