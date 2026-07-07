#!/usr/bin/env python3
"""Strict zero-in-network-buffer allgather sweep (ground truth), reusing
sched_zerobuf_compare.py's rigid offset packer across all small/medium
mesh sizes at m=1.

Why m=1 only: the rigid packer's "find smallest feasible per-source offset"
search is a linear scan over a `forbidden` cycle set rebuilt from scratch per
source; its cost grows much faster than linearly with flits (measured at
16x16/multitree: m=1 -> 13.9s, m=2 -> 38.7s, ~2.8x per +1 flit), so a full
m=1..5 sweep at every size would take upwards of 10+ hours. m=1 alone is
fast (seconds-to-tens-of-seconds per scheme) and gives an exact, trustworthy
zero-buffer ranking to validate/calibrate the event-driven engine's optimism
against (see results/report_allgather_scale.html section on buffer depth).

Output: results/zerobuf_strict_m1.json
"""
import argparse
import json
import time
from pathlib import Path

import sched_zerobuf_compare as Z

ROOT = Path(__file__).resolve().parents[1]
OUT_JSON = ROOT / "results" / "zerobuf_strict_m1.json"

SIZES = [(4, 4), (6, 8), (8, 8), (12, 16), (16, 16)]
H, V = 4, 6
RAMP_BWS = [1, 2]


def study_json_m1(ramp_bw):
    """Same content as sched_zerobuf_compare.study_json but built directly so
    we can time/label it explicitly; flits is implicitly 1 (matches the
    existing results/zerobuf_16x16.json format)."""
    return Z.study_json(ramp_bw)


def sweep_size(mx, my, verbose=True):
    Z.cfg(mx, my, H, V)
    Z.init_ring()
    have_quad = True
    try:
        Z.init_quadrants()
    except AssertionError:
        have_quad = False

    out = {"mx": mx, "my": my, "h": H, "v": V, "n": mx * my, "quad_available": have_quad, "bw": {}}
    for rb in RAMP_BWS:
        t0 = time.time()
        d = Z.study(rb)
        dt = time.time() - t0
        flat = [
            ("multitree", d["multitree"][0], d["multitree"][3]),
            ("ring_uni", d["ring_uni"][0], d["ring_uni"][3]),
            ("ring_bi", d["ring_bi"][0], d["ring_bi"][3]),
        ]
        for fam in ("hybrid_uni", "hybrid_bi", "hybrid_v_uni", "hybrid_v_bi"):
            for B, r in d[fam].items():
                flat.append((f"{fam}_B{B}", r[0], r[3]))
        if have_quad:
            for fam in ("quad_uni", "quad_bi", "border_uni", "border_bi"):
                flat.append((fam, d[fam][0], d[fam][3]))
        best_name, best_mk, best_ok = min(
            (f for f in flat if f[2]), key=lambda f: f[1], default=(None, None, False))
        eject_lb = (mx * my - 1 + rb - 1) // rb
        out["bw"][rb] = {
            "eject_lb": eject_lb,
            "results": [{"name": n, "makespan": mk, "ok": ok} for n, mk, ok in flat],
            "best": {"name": best_name, "makespan": best_mk},
            "time_s": round(dt, 2),
        }
        if verbose:
            print(f"[{mx}x{my} bw={rb}] best={best_name} mk={best_mk} "
                  f"eject_lb={eject_lb} ({dt:.1f}s)")
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sizes", default=None)
    ap.add_argument("--json", default=str(OUT_JSON))
    args = ap.parse_args()

    sizes = SIZES
    if args.sizes:
        want = set(args.sizes.split(","))
        sizes = [(mx, my) for mx, my in SIZES if f"{mx}x{my}" in want]

    payload = {"h": H, "v": V, "m": 1, "sizes": [f"{mx}x{my}" for mx, my in SIZES], "data": {}}
    out_path = Path(args.json)
    if out_path.exists():
        payload = json.loads(out_path.read_text(encoding="utf-8"))

    for mx, my in sizes:
        key = f"{mx}x{my}"
        t0 = time.time()
        payload["data"][key] = sweep_size(mx, my)
        print(f"=== {key} done in {time.time()-t0:.1f}s ===")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    print(f"Wrote {out_path}")


if __name__ == "__main__":
    main()
