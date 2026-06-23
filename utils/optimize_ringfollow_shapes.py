#!/usr/bin/env python3
"""Find ring-shape-optimized Hamilton configs for the RING-FOLLOW scheme.

Mirrors optimize_quad_shapes.sweep_cfg but only for scheme="ringfollow", and
MERGES the result into results/optimal_quad_shapes.json under
  sizes[<sz>]["ringfollow"][<tag>] = {best_any, best_balanced, ...}
so sweep_afifo_depth.shape_cfg(sz, "ringfollow", tag) returns a good shape.

Output: updates results/optimal_quad_shapes.json in place.
"""

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path

from optimize_quad_shapes import OUT, AFIFO_CAP, sweep_cfg

SIZES = (4, 8, 16)


def run(sizes=SIZES):
    data = json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() else {
        "updated": datetime.now(timezone.utc).isoformat(),
        "afifo_cap": AFIFO_CAP, "sizes": {},
    }
    t0 = time.time()
    for sz in sizes:
        key = f"{sz}x{sz}"
        data["sizes"].setdefault(key, {})
        data["sizes"][key]["ringfollow"] = {}
        for bidir, tag, rb in ((False, "uni", 1), (True, "bi", 2)):
            print(f"== {key} ringfollow {tag} ==", flush=True)
            any_, bal, n, el = sweep_cfg(sz, "ringfollow", bidir, rb)
            data["sizes"][key]["ringfollow"][tag] = {
                "best_any": any_, "best_balanced": bal, "chosen": any_,
                "n_configs": n, "sweep_s": el,
            }
            if any_:
                print(f"  best_any mk={any_['makespan']} afifo={any_['afifo_depth']} "
                      f"bal={any_['afifo_balanced']}  {any_['cfg_str']}", flush=True)
    data["updated"] = datetime.now(timezone.utc).isoformat()
    OUT.write_text(json.dumps(data, indent=2), encoding="utf-8")
    print(f"Merged ringfollow shapes into {OUT} ({time.time()-t0:.0f}s)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sizes", type=int, nargs="+", default=list(SIZES))
    args = ap.parse_args()
    run(tuple(args.sizes))


if __name__ == "__main__":
    main()
