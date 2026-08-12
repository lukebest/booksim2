#!/usr/bin/env python3
"""Injection-rate sweep for all four configurations: bisection use + latency.

Records, per (config, lambda), the bisection utilization and the mean / p50 /
p99 packet latency for both distributed baselines and both centralized
arbiters. Feeds utils/gen_bisect_lat_plots.py and section 9.3-9.5 of the report.

`buf_depth=20` matches the main sweep's head-to-head group, so these curves can
be read next to the throughput curves; it is the only knob that separates a
credit-limited mesh_base from a topology-limited one.

The lambda grid is deliberately non-uniform: dense across 0.38..0.52 where the
mesh saturates (anchor 0.490) and across 0.68..0.82 where the ring does (anchor
0.783), coarse elsewhere. A uniform grid either misses both knees or wastes most
of its samples deep in the flat overload region.

Bisection is counted per HOP on both baselines, so a deflected ring flit that
rides past its turn and crosses the cut again is charged again. That is the
intended accounting: it is how wasted cut bandwidth becomes visible.

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

CONFIGS = ("mesh_base", "mesh_islip2d", "ring_base", "ring_islip2d")
BUF_DEPTH = 20                       # mesh_base only; matches the main sweep


def fabric(config: str) -> str:
    return "mesh" if config.startswith("mesh") else "ring"

LAMS = (
    0.01, 0.02, 0.04, 0.06, 0.08, 0.10, 0.15, 0.20, 0.25, 0.30, 0.35,
    0.38, 0.41, 0.44, 0.47, 0.50,          # mesh_base and mesh_islip2d knees
    0.52, 0.55, 0.58, 0.60, 0.62, 0.65,    # ring_base knee
    0.68, 0.71, 0.74, 0.77, 0.80,          # ring_islip2d knee
    0.85, 0.90, 1.00,
)
QUICK = (0.05, 0.20, 0.44, 0.60, 0.80, 1.00)


def one(job: tuple[str, float, int, int]) -> dict[str, Any]:
    config, lam, warmup, measure = job
    t0 = time.time()
    r = run_steady(config, SteadyParams(lam=lam, warmup=warmup,
                                        measure=measure,
                                        buf_depth=BUF_DEPTH))
    r["secs"] = round(time.time() - t0, 2)
    return r


def crossing_fraction(n: int = 48) -> float:
    """P(a uniform (src,dst) pair straddles the X bisection).

    Three of the four configurations route minimally and never re-cross, so a
    straddling pair crosses the cut exactly once and this turns the offered rate
    into an analytic bisection demand -- the measured curve gets checked rather
    than merely plotted. `ring_base` deflects, so for it the same number is a
    FLOOR, and the measured excess is what deflection costs the cut.
    """
    half = n // 2
    return 2.0 * (half / n) * (half / (n - 1))


ACCEPTING = 0.999      # accept_ratio above which nothing is queueing up yet


def run_grid(lams: tuple[float, ...], warmup: int, measure: int,
             jobs: int) -> list[dict[str, Any]]:
    grid = [(c, lam, warmup, measure) for c in CONFIGS for lam in lams]
    rows: list[dict[str, Any]] = []
    with ProcessPoolExecutor(max_workers=jobs) as ex:
        for r in ex.map(one, grid):
            rows.append(r)
            print(f"  {r['config']:13} lam={r['lam']:<5} "
                  f"acc={r['accepted']:.4f} bis={r['bisect_util']:.4f} "
                  f"mean={r['mean_lat']:>7} p99={r['p99']:>6} "
                  f"stable={int(bool(r['stable']))} {r['secs']}s", flush=True)
    return rows


def summarize(rows: list[dict[str, Any]], lams: Any, warmup: int,
              measure: int) -> dict[str, Any]:
    """Reduce raw sweep rows to the numbers the report quotes.

    Split out from the sweep so the reported quantities can be revised without
    re-simulating: `--from-json` re-runs just this step.
    """
    frac = crossing_fraction()
    summary: dict[str, Any] = {"crossing_fraction": round(frac, 5)}
    for c in CONFIGS:
        rs = sorted([r for r in rows if r["config"] == c], key=lambda r: r["lam"])
        st = [r for r in rs if r["stable"]]
        peak = max(rs, key=lambda r: r["bisect_util"])
        nb = rs[0]["bisect_links"]
        # Analytic: at accepted rate a, flits crossing per cycle = 48*a*frac*m,
        # spread over nb links each busy sigma cycles per flit.
        #
        # The prediction is driven by DELIVERED packets while the measurement is
        # link-busy time, so it is only exact while essentially everything
        # offered is also delivered inside the window. Two separate things break
        # that, and they are reported separately rather than lumped into one
        # error bar:
        #   - approaching lambda*, packets are injected but not yet delivered;
        #     their cut cycles are counted with no packet to divide by, so the
        #     measurement drifts ABOVE the prediction (worst on ring_base).
        #   - past saturation the accepted mix itself biases away from the cut,
        #     pushing the measurement BELOW it (overload_mix_skew).
        # Hence the headline check runs where accept_ratio >= ACCEPTING.
        def pred(r: dict) -> float:
            return 48 * r["accepted"] * frac * r["m"] * r["sigma"] / nb

        def cross(r: dict) -> float:
            """Cut crossings per accepted packet: cut width divided out."""
            return (r["bisect_flits_per_cy"] / (r["accepted"] * 48)
                    if r["accepted"] else 0.0)
        err = max((abs(pred(r) - r["bisect_util"]) for r in st), default=0.0)
        acc = [r for r in st if r["accept_ratio"] >= ACCEPTING]
        err_acc = max((abs(pred(r) - r["bisect_util"]) for r in acc),
                      default=0.0)
        top = rs[-1]
        lstar = max((r["lam"] for r in st), default=None)
        summary[c] = {
            "bisect_links": nb,
            "lam_star": lstar,
            "anchor": anchors()["mesh_xy" if fabric(c) == "mesh"
                                else "ring_fixed"],
            "peak_bisect_util": peak["bisect_util"],
            "peak_bisect_util_at_lam": peak["lam"],
            "peak_accepted": max(r["accepted"] for r in rs),
            "bisect_util_at_lam_star": next(
                (r["bisect_util"] for r in reversed(st)), None),
            "analytic_max_abs_err_stable": round(err, 4),
            "analytic_agrees_stable": bool(err < 0.03),
            "analytic_max_abs_err_accepting": round(err_acc, 4),
            "analytic_agrees_accepting": bool(err_acc < 0.01),
            "accepting_lam_max": max((r["lam"] for r in acc), default=None),
            "overload_mix_skew": round(pred(top) - top["bisect_util"], 4),
            "cross_per_pkt_accepting": round(
                next((cross(r) for r in reversed(acc)), 0.0), 4),
            "cross_per_pkt_at_lam_star": round(
                next((cross(r) for r in reversed(st)), 0.0), 4),
            "cross_per_pkt_max": round(max(cross(r) for r in rs), 4),
            "cross_per_pkt_max_at_lam": max(rs, key=cross)["lam"],
            "mean_lat_unloaded": st[0]["mean_lat"] if st else None,
            "mean_lat_at_lam_star": st[-1]["mean_lat"] if st else None,
            "p99_at_lam_star": st[-1]["p99"] if st else None,
            "worst_p99_over_mean_stable": round(
                max((r["p99"] / r["mean_lat"] for r in st if r["mean_lat"]),
                    default=0.0), 3),
        }
    return {
        "meta": {
            "generated": time.strftime("%Y-%m-%d %H:%M:%S"),
            "configs": list(CONFIGS), "lams": list(lams),
            "warmup": warmup, "measure": measure, "buf_depth": BUF_DEPTH,
            "bisection": {c: len(bisection_links(fabric(c)))
                          for c in CONFIGS},
            "note": "bisect_util = fraction of time the cut's directed links "
                    "are busy; the ring's cut has twice as many links because "
                    "a row ring must be severed in two places",
        },
        "rows": rows,
        "summary": summary,
    }


def sweep(quick: bool = False, jobs: int = 6) -> dict[str, Any]:
    lams = QUICK if quick else LAMS
    warmup, measure = (1500, 4000) if quick else (3000, 12000)
    return summarize(run_grid(lams, warmup, measure, jobs),
                     lams, warmup, measure)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--jobs", type=int, default=6)
    ap.add_argument("--out", default=str(OUT))
    ap.add_argument("--from-json", default=None,
                    help="recompute the summary from existing rows, no sim")
    a = ap.parse_args()
    t0 = time.time()
    if a.from_json:
        old = json.loads(Path(a.from_json).read_text())
        m = old["meta"]
        data = summarize(old["rows"], m["lams"], m["warmup"], m["measure"])
        data["meta"]["resummarized_from"] = a.from_json
    else:
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
              f"analytic_err(lam<={s['accepting_lam_max']})="
              f"{s['analytic_max_abs_err_accepting']:.4f} "
              f"[{'ok' if s['analytic_agrees_accepting'] else 'MISMATCH'}]  "
              f"(to lam* {s['analytic_max_abs_err_stable']:.4f})  "
              f"overload_skew={s['overload_mix_skew']:+.3f}")
        print(f"  {'':13} cross/pkt: at lam*={s['cross_per_pkt_at_lam_star']} "
              f"max={s['cross_per_pkt_max']}@lam={s['cross_per_pkt_max_at_lam']}"
              f" (analytic {data['summary']['crossing_fraction']})  "
              f"mean {s['mean_lat_unloaded']}->{s['mean_lat_at_lam_star']}  "
              f"p99@lam*={s['p99_at_lam_star']}  "
              f"worst p99/mean={s['worst_p99_over_mean_stable']}")


if __name__ == "__main__":
    main()
