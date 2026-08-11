#!/usr/bin/env python3
"""Steady-state injection-rate sweep across the four 8x6 configurations.

Companion to `dse_islip2d_8x6.py`. That one asks how few rounds drain a known
workload; this one asks what an open-loop stream of all-to-all traffic actually
gets, which is the number that decides whether centralization is worth its
control plane.

Axes
----
    lambda      0.01, 0.1, 0.2 ... 1.0 (packets per node per cycle)
    config      mesh_base / ring_base / mesh_islip2d / ring_islip2d
    buf_depth   4 / 8 / 20 -- mesh_base only, and it must be swept: the credit
                round trip on H=7/V=9 links is 15-19 cycles, so anything
                shallower than that throttles a link below its own capacity and
                would understate the baseline by 2-4x
    m           1 / 4 flits per packet (the flit rate is m*lambda, so the
                saturation point scales as 1/m)
    sigma       1 / 2 cycles per flit (metal-constant comparison: the ring
                carries 1.17x the mesh's wire, so sigma=2 is the fair column)

Reported per point: accepted throughput, the ratio of accepted to offered,
p50/p99 latency, the stability verdict, source backlog, per-node fairness, and
for the request-grant configurations the grant wait and control-plane load. Each
saturation point is checked against the analytic anchor from hottest-resource
load; a measurement above its anchor while still stable means the model is
wrong.

Writes results/load_sweep_8x6.json.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from rg_steady_des import CONFIGS, SteadyParams, anchors, run_steady

OUT = Path(__file__).resolve().parents[1] / "results" / "load_sweep_8x6.json"
LAMS = (0.01, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0)
FINE = (0.42, 0.45, 0.48, 0.52, 0.55, 0.65, 0.75)
WARM, MEAS = 1500, 6000


def point(config: str, **kw) -> dict[str, Any]:
    p = SteadyParams(warmup=WARM, measure=MEAS, **kw)
    t0 = time.perf_counter()
    r = run_steady(config, p)
    r["secs"] = round(time.perf_counter() - t0, 2)
    return r


def lam_star(rows: list[dict[str, Any]]) -> float | None:
    """Largest swept lambda that is still stable."""
    ok = [r["lam"] for r in rows if r["stable"]]
    return max(ok) if ok else None


def sweep() -> dict[str, Any]:
    out: dict[str, Any] = {"anchors": anchors(), "rows": []}
    rows = out["rows"]

    # -- main curve: four configurations, m=1, sigma=1, deep buffers --------
    for lam in LAMS + FINE:
        for cfg in CONFIGS:
            rows.append(point(cfg, lam=lam, buf_depth=20) | {"group": "main"})

    # -- mesh_base buffer depth --------------------------------------------
    for bd in (4, 8, 20):
        for lam in LAMS:
            rows.append(point("mesh_base", lam=lam, buf_depth=bd)
                        | {"group": "buf"})

    # -- packet length and metal constant ----------------------------------
    for m in (1, 4):
        for sigma in (1, 2):
            if m == 1 and sigma == 1:
                continue
            for lam in (0.01, 0.05, 0.1, 0.15, 0.2, 0.25, 0.3, 0.4, 0.6):
                for cfg in CONFIGS:
                    rows.append(point(cfg, lam=lam, m=m, sigma=sigma,
                                      buf_depth=20) | {"group": f"m{m}s{sigma}"})

    # -- conflict domain, in steady state ----------------------------------
    for dom in ("interval", "free_at"):
        for lam in (0.1, 0.3, 0.5, 0.7):
            for cfg in ("mesh_islip2d", "ring_islip2d"):
                rows.append(point(cfg, lam=lam, conflict_domain=dom)
                            | {"group": "domain"})

    # -- control-loop RTT ---------------------------------------------------
    for rtt in (8, 16, 32, 64):
        for lam in (0.01, 0.1, 0.3, 0.5):
            for cfg in ("mesh_islip2d", "ring_islip2d"):
                rows.append(point(cfg, lam=lam, t_rtt=rtt) | {"group": "rtt"})

    # -- ring_base knobs ----------------------------------------------------
    for fd in (1, 4, 8):
        for lam in (0.1, 0.3, 0.5, 0.7):
            rows.append(point("ring_base", lam=lam, fifo_depth=fd)
                        | {"group": "fifo"})
    for swap in (True, False):
        for lam in (0.3, 0.5, 0.7):
            rows.append(point("ring_base", lam=lam, swap_rule=swap,
                              dim_order="mixed") | {"group": "swap"})

    # -- ring board/leave ports --------------------------------------------
    for ports in (1, 2):
        for lam in (0.3, 0.5, 0.7, 0.8):
            rows.append(point("ring_islip2d", lam=lam, board_ports=ports,
                              leave_ports=ports) | {"group": "ports"})

    # -- summary -----------------------------------------------------------
    main = [r for r in rows if r.get("group") == "main"]
    out["lam_star"] = {c: lam_star([r for r in main if r["config"] == c])
                       for c in CONFIGS}
    out["peak_accepted"] = {
        c: max(r["accepted"] for r in main if r["config"] == c)
        for c in CONFIGS}
    out["anchor_check"] = {}
    for c, a in (("mesh_base", "mesh_xy"), ("mesh_islip2d", "mesh_xy"),
                 ("ring_base", "ring_fixed"), ("ring_islip2d", "ring_fixed")):
        ls = out["lam_star"][c]
        out["anchor_check"][c] = {
            "lam_star": ls, "anchor": out["anchors"][a],
            "ok": ls is None or ls <= out["anchors"][a] + 1e-9}
    return out


if __name__ == "__main__":
    t0 = time.perf_counter()
    res = sweep()
    res["wall_secs"] = round(time.perf_counter() - t0, 1)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(res, indent=1, default=str))
    print(f"wrote {OUT} ({len(res['rows'])} rows, {res['wall_secs']}s)")

    print("\n=== saturation ===")
    for c in CONFIGS:
        a = res["anchor_check"][c]
        print(f"  {c:13} lam*={a['lam_star']} peak={res['peak_accepted'][c]:.4f} "
              f"anchor={a['anchor']:.3f} ok={a['ok']}")

    print("\n=== main curve (m=1, sigma=1, buf=20) ===")
    print(f"{'lam':>5} " + " ".join(f"{c:>14}" for c in CONFIGS))
    main = [r for r in res["rows"] if r.get("group") == "main"]
    for lam in sorted({r["lam"] for r in main}):
        cells = []
        for c in CONFIGS:
            r = next(x for x in main if x["lam"] == lam and x["config"] == c)
            cells.append(f"{r['accepted']:>8.4f}/{int(r['stable'])}"
                         f"/{r['p99']:>4.0f}")
        print(f"{lam:>5.2f} " + " ".join(f"{c:>14}" for c in cells))
