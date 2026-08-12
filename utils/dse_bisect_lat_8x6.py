#!/usr/bin/env python3
"""Injection-rate sweep for the two centralized arbiters: bisection use + latency.

Sweeps lambda for `mesh_islip2d` and `ring_islip2d` and records, per point, the
bisection utilization and the mean / p50 / p99 packet latency. Feeds
utils/gen_bisect_lat_plots.py.

The lambda grid is deliberately non-uniform: dense across 0.38..0.52 where the
mesh saturates (anchor 0.490) and across 0.68..0.82 where the ring does (anchor
0.783), coarse elsewhere. A uniform grid either misses both knees or wastes most
of its samples deep in the flat overload region.

    python3 utils/dse_bisect_lat_8x6.py [--quick] [--jobs 6]

Writes results/bisect_lat_8x6.json.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from rg_steady_des import (SteadyParams, anchors, bisection_links,  # noqa: E402
                          run_steady)

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results" / "bisect_lat_8x6.json"

CONFIGS = ("mesh_islip2d", "ring_islip2d")

LAMS = (
    0.01, 0.02, 0.04, 0.06, 0.08, 0.10, 0.15, 0.20, 0.25, 0.30, 0.35,
    0.38, 0.41, 0.44, 0.47, 0.50,                      # mesh knee
    0.55, 0.60, 0.65,
    0.68, 0.71, 0.74, 0.77, 0.80,                      # ring knee
    0.85, 0.90, 1.00,
)
QUICK = (0.05, 0.20, 0.44, 0.60, 0.80, 1.00)


def one(job: tuple[str, float, int, int]) -> dict[str, Any]:
    config, lam, warmup, measure = job
    t0 = time.time()
    r = run_steady(config, SteadyParams(lam=lam, warmup=warmup,
                                        measure=measure))
    r["secs"] = round(time.time() - t0, 2)
    return r


def crossing_fraction(n: int = 48) -> float:
    """P(a uniform (src,dst) pair straddles the X bisection).

    Both fabrics route minimally, so a straddling pair crosses the cut exactly
    once. This turns the offered rate into an analytic bisection demand and lets
    the measured curve be checked rather than merely plotted.
    """
    half = n // 2
    return 2.0 * (half / n) * (half / (n - 1))


def sweep(quick: bool = False, jobs: int = 6) -> dict[str, Any]:
    lams = QUICK if quick else LAMS
    warmup, measure = (1500, 4000) if quick else (3000, 12000)
    grid = [(c, lam, warmup, measure) for c in CONFIGS for lam in lams]
    rows: list[dict[str, Any]] = []
    with ProcessPoolExecutor(max_workers=jobs) as ex:
        for r in ex.map(one, grid):
            rows.append(r)
            print(f"  {r['config']:13} lam={r['lam']:<5} "
                  f"acc={r['accepted']:.4f} bis={r['bisect_util']:.4f} "
                  f"mean={r['mean_lat']:>7} p99={r['p99']:>6} "
                  f"stable={int(bool(r['stable']))} {r['secs']}s", flush=True)

    frac = crossing_fraction()
    summary: dict[str, Any] = {"crossing_fraction": round(frac, 5)}
    for c in CONFIGS:
        rs = sorted([r for r in rows if r["config"] == c], key=lambda r: r["lam"])
        st = [r for r in rs if r["stable"]]
        peak = max(rs, key=lambda r: r["bisect_util"])
        nb = rs[0]["bisect_links"]
        # Analytic: at accepted rate a, flits crossing per cycle = 48*a*frac*m,
        # spread over nb links each busy sigma cycles per flit. This holds only
        # while the accepted MIX is still uniform, i.e. in the stable region --
        # past saturation the arbiter accepts whatever is unblocked, which biases
        # acceptance toward pairs that do NOT cross the cut. So the check runs on
        # stable rows and the overload deviation is reported as its own quantity.
        def pred(r: dict) -> float:
            return 48 * r["accepted"] * frac * r["m"] * r["sigma"] / nb
        err = max((abs(pred(r) - r["bisect_util"]) for r in st), default=0.0)
        top = rs[-1]
        summary[c] = {
            "bisect_links": nb,
            "lam_star": max((r["lam"] for r in st), default=None),
            "anchor": anchors()["mesh_xy" if c == "mesh_islip2d"
                                else "ring_fixed"],
            "peak_bisect_util": peak["bisect_util"],
            "peak_bisect_util_at_lam": peak["lam"],
            "peak_accepted": max(r["accepted"] for r in rs),
            "bisect_util_at_lam_star": next(
                (r["bisect_util"] for r in reversed(st)), None),
            "analytic_max_abs_err_stable": round(err, 4),
            "analytic_agrees_stable": bool(err < 0.03),
            "overload_mix_skew": round(pred(top) - top["bisect_util"], 4),
        }
    return {
        "meta": {
            "generated": time.strftime("%Y-%m-%d %H:%M:%S"),
            "configs": list(CONFIGS), "lams": list(lams),
            "warmup": warmup, "measure": measure,
            "bisection": {c: len(bisection_links(
                "mesh" if c == "mesh_islip2d" else "ring")) for c in CONFIGS},
            "note": "bisect_util = fraction of time the cut's directed links "
                    "are busy; the ring's cut has twice as many links because "
                    "a row ring must be severed in two places",
        },
        "rows": rows,
        "summary": summary,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--jobs", type=int, default=6)
    ap.add_argument("--out", default=str(OUT))
    a = ap.parse_args()
    t0 = time.time()
    data = sweep(quick=a.quick, jobs=a.jobs)
    data["wall_secs"] = round(time.time() - t0, 1)
    p = Path(a.out)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2))
    print(f"\nwrote {p}  ({len(data['rows'])} rows, {data['wall_secs']}s)")
    for c in CONFIGS:
        s = data["summary"][c]
        print(f"  {c:13} cut={s['bisect_links']:>2} links  "
              f"lam*={s['lam_star']}  anchor={s['anchor']:.3f}  "
              f"bisect@lam*={s['bisect_util_at_lam_star']}  "
              f"peak_bisect={s['peak_bisect_util']:.3f}"
              f"@lam={s['peak_bisect_util_at_lam']}  "
              f"analytic_err(stable)={s['analytic_max_abs_err_stable']:.4f} "
              f"[{'ok' if s['analytic_agrees_stable'] else 'MISMATCH'}]  "
              f"overload_skew={s['overload_mix_skew']:+.3f}")


if __name__ == "__main__":
    main()
