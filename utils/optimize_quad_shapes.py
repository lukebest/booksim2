#!/usr/bin/env python3
"""Find ring-shape-optimized Hamilton configs per mesh size for 4-quadrant schemes.

Scans (base,rot) per quadrant (rect/vflip/vband × rotations), picks the cfg that
minimizes makespan under sched_ring_zerobuf for border (+ ringfollow on 16×16).

Output: results/optimal_quad_shapes.json
"""

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import sched_ring_zerobuf as S
from sweep_quad_ring_shapes import (all_configs, cfg_str, cfg_short, make_quads,
                                    run_one_cfg)

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results" / "optimal_quad_shapes.json"
BASES = ("rect", "vflip", "vband")
AFIFO_CAP = 5
SIZES = (4, 8, 16)


def sweep_cfg(sz, scheme, bidir, ramp_bw, progress=512):
    cfgs = all_configs(BASES, sz)
    best_any = None
    best_bal = None
    t0 = time.time()
    for i, cfg in enumerate(cfgs):
        for lb in (False, True):
            r = run_one_cfg(cfg, scheme, sz, bidir, ramp_bw, spread=0, lb_cross=lb)
            if not r.get("ok"):
                continue
            bal = r["afifo_balanced"]["peak"]
            rec = dict(cfg=cfg_short(cfg), cfg_str=cfg_str(cfg),
                       makespan=r["makespan"], afifo_depth=r["afifo_depth"],
                       afifo_balanced=bal, spread=0, lb_cross=lb)
            if best_any is None or rec["makespan"] < best_any["makespan"]:
                best_any = rec
            if bal <= AFIFO_CAP:
                if best_bal is None or rec["makespan"] < best_bal["makespan"]:
                    best_bal = rec
        if (i + 1) % progress == 0:
            print(f"    {sz}x{sz} {scheme} {'bi' if bidir else 'uni'}: "
                  f"{i+1}/{len(cfgs)} ({time.time()-t0:.0f}s)", flush=True)
    return best_any, best_bal, len(cfgs), time.time() - t0


def refine_spread(sz, scheme, bidir, ramp_bw, cfg_rec, deep=30):
    """Spread sweep on the winning shape cfg."""
    cfg = tuple(tuple(x) for x in cfg_rec["cfg"])
    best = dict(cfg_rec)
    for sp in range(1, deep):
        for lb in (False, True):
            r = run_one_cfg(cfg, scheme, sz, bidir, ramp_bw, spread=sp, lb_cross=lb)
            if not r.get("ok"):
                continue
            bal = r["afifo_balanced"]["peak"]
            if bal <= AFIFO_CAP and r["makespan"] < best["makespan"]:
                best = dict(cfg=cfg_short(cfg), cfg_str=cfg_str(cfg),
                            makespan=r["makespan"], afifo_depth=r["afifo_depth"],
                            afifo_balanced=bal, spread=sp, lb_cross=lb)
    return best


def optimize(sizes=SIZES, schemes=("border",), refine=True):
    out = {"updated": datetime.now(timezone.utc).isoformat(),
           "afifo_cap": AFIFO_CAP, "sizes": {}}
    for sz in sizes:
        out["sizes"][f"{sz}x{sz}"] = {}
        for scheme in schemes:
            out["sizes"][f"{sz}x{sz}"][scheme] = {}
            for bidir, tag, rb in ((False, "uni", 1), (True, "bi", 2)):
                print(f"== {sz}x{sz} {scheme} {tag} ==")
                any_, bal, n, el = sweep_cfg(sz, scheme, bidir, rb)
                pick = any_ if any_ else bal
                if refine and pick:
                    cfg = tuple(tuple(x) for x in pick["cfg"])
                    _, _, pick = best_spread(sz, scheme, bidir, rb, cfg, min_mk=True)
                out["sizes"][f"{sz}x{sz}"][scheme][tag] = {
                    "best_any": any_, "best_balanced": bal, "chosen": pick,
                    "n_configs": n, "sweep_s": el,
                }
                if pick:
                    print(f"  chosen mk={pick['makespan']} afifo={pick['afifo_depth']} "
                          f"bal={pick['afifo_balanced']} spread={pick['spread']}")
                    print(f"    {pick['cfg_str']}")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"Wrote {OUT}")
    return out


def load_optimal():
    if OUT.exists():
        return json.loads(OUT.read_text(encoding="utf-8"))
    return optimize()


def chosen_cfg(sz, scheme="border", tag="bi"):
    """Return 4-tuple (base,rot) — min-makespan shape from optimization."""
    data = load_optimal()
    block = data["sizes"].get(f"{sz}x{sz}", {}).get(scheme, {}).get(tag, {})
    rec = block.get("chosen") or block.get("best_any")
    if not rec:
        # fallback: default ham_cycle_rect
        return (("rect", 0), ("rect", 0), ("rect", 0), ("rect", 0))
    return tuple(tuple(x) for x in rec["cfg"])


def quads_for(sz, scheme="border", tag="bi"):
    data = load_optimal()
    if scheme not in data.get("sizes", {}).get(f"{sz}x{sz}", {}):
        scheme = "border"
    return make_quads(chosen_cfg(sz, scheme, tag), sz)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sizes", type=int, nargs="+", default=list(SIZES))
    ap.add_argument("--ringfollow", action="store_true",
                    help="also optimize ringfollow (16x16 only in practice)")
    ap.add_argument("--no-refine", action="store_true")
    args = ap.parse_args()
    schemes = ["border"]
    if args.ringfollow:
        schemes.append("ringfollow")
    optimize(tuple(args.sizes), tuple(schemes), refine=not args.no_refine)


if __name__ == "__main__":
    main()
