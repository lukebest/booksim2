#!/usr/bin/env python3
"""Goodput vs injection rate with a Homa-style distributed scheduler.

Workload: open-loop alltoall writes arriving as MESSAGES of 1 / 8 / 64 packets
(50% / 30% / 20% of messages, 1.5% / 15.3% / 81.5% of the load; 1 packet = 1
flit). lam is still packets per node per cycle. Homa needs messages longer than
its RTT window to have anything to schedule, which is why the single-packet
workload of the earlier DBID-pool study (`dse_write_dbid_8x6.py`) is only kept
here as the degenerate control.

Schemes per fabric
    base     mesh_base / ring_base        no request-grant
    central  mesh_islip2d / ring_islip2d  centralized iSLIP-style arbiter
    homa     mesh_homa / ring_homa        receiver-driven (rg_homa_dist)
    pool     mesh_dbid / ring_dbid        v1: per-packet DBID pool (ablation)

Phases
    A  (window, degree) grid for Homa at a few lambdas; (W*, D*) is the pair
       with the highest mean goodput (ties -> smaller W*D)
    B  main curves, 4 schemes
    C  single-packet workload: Homa must collapse onto the baseline
    D  ablation: SRPT vs FCFS, no unscheduled prefix, half control latency
    E  centralized T_rtt = 64

Writes results/write_homa_8x6.json.  Run: python3 dse_write_homa_8x6.py
"""

from __future__ import annotations

import json
import time
from multiprocessing import Pool
from pathlib import Path
from typing import Any

from rg_steady_des import SteadyParams, anchors, run_steady

OUT = Path(__file__).resolve().parents[1] / "results" / "write_homa_8x6.json"
MIX = "1:0.5,8:0.3,64:0.2"
LAMS = (0.05, 0.1, 0.15, 0.2, 0.25, 0.3, 0.35, 0.4, 0.45, 0.5, 0.6, 0.7,
        0.8, 1.0)
A_LAMS = (0.3, 0.5, 1.0)
W_GRID = (1, 2, 4, 8, 16)
D_GRID = (2, 4, 8, 16, 48)
FABRICS = ("mesh", "ring")
WARM, MEAS = 3000, 12000
POOL_K = 64


def _run(job: tuple[str, dict[str, Any]]) -> dict[str, Any]:
    cfg, kw = job
    group = kw.pop("_group")
    kw.setdefault("msg_mix", MIX)
    p = SteadyParams(warmup=WARM, measure=MEAS, buf_depth=20, **kw)
    t0 = time.perf_counter()
    r = run_steady(cfg, p)
    r["secs"] = round(time.perf_counter() - t0, 2)
    r["group"] = group
    return r


def _pool(jobs: list, n: int) -> list[dict]:
    with Pool(n) as pool:
        return pool.map(_run, jobs, chunksize=1)


GRID = OUT.with_name("write_homa_grid.json")


def pick(rows: list[dict[str, Any]], fabric: str, cap: float | None
         ) -> tuple[int, int]:
    """Best (window, degree) by mean goodput over the grid lambdas.

    With `cap`, only pairs whose 64-packet-message FCT at lam=0.3 is <= cap are
    eligible. The cap exists because pure goodput picks the corner W=1 (one
    packet per RTT per message), which paces a long message at 1/100 of line
    rate; see the report for why that is not a Homa operating point.
    """
    score: dict[tuple[int, int], list[float]] = {}
    fct: dict[tuple[int, int], float] = {}
    for r in rows:
        if r["config"] == f"{fabric}_homa" and r["group"] == "grid":
            k = (r["homa_window"], r["homa_degree"])
            score.setdefault(k, []).append(r["goodput"])
            if r["lam"] == 0.3:
                fct[k] = r.get("fct_mean_L64") or float("inf")
    ok = [k for k in score if cap is None or fct.get(k, float("inf")) <= cap]
    return max(ok, key=lambda k: (round(sum(score[k]) / len(score[k]), 3),
                                  -k[0] * k[1]))


def sweep(jobs_n: int) -> dict[str, Any]:
    out: dict[str, Any] = {"anchors": anchors(), "rows": [], "best": {},
                           "tp": {}, "mix": MIX}
    rows = out["rows"]
    t_start = time.perf_counter()

    if GRID.exists():
        rows += json.loads(GRID.read_text())["rows"]
        print(f"[A] grid reused from {GRID.name} ({len(rows)} rows)",
              flush=True)
    else:
        jobs = [(f"{f}_homa", dict(lam=lam, homa_window=w, homa_degree=d,
                                   _group="grid"))
                for f in FABRICS for lam in A_LAMS for w in W_GRID
                for d in D_GRID]
        rows += _pool(jobs, jobs_n)
        GRID.write_text(json.dumps({"rows": rows}))

    jobs = []
    for f in FABRICS:
        for lam in LAMS:
            jobs += [(f"{f}_base", dict(lam=lam, _group="main")),
                     (f"{f}_islip2d", dict(lam=lam, _group="main")),
                     (f"{f}_dbid", dict(lam=lam, dbid_k=POOL_K,
                                        _group="pool"))]
    rows += _pool(jobs, jobs_n)
    print(f"[B1] baselines done ({time.perf_counter() - t_start:.0f}s)",
          flush=True)

    for f in FABRICS:
        base03 = [r for r in rows if r["config"] == f"{f}_base"
                  and r["group"] == "main" and r["lam"] == 0.3][0]
        cap = 2.0 * base03["fct_mean_L64"]
        out["best"][f] = pick(rows, f, cap)
        out["tp"][f] = pick(rows, f, None)
    print(f"[A] balanced (W,D): {out['best']}  throughput-first: {out['tp']}",
          flush=True)

    jobs = []
    for f in FABRICS:
        for lam in LAMS:
            w, d = out["best"][f]
            jobs.append((f"{f}_homa", dict(lam=lam, homa_window=w,
                                           homa_degree=d, _group="main")))
            w, d = out["tp"][f]
            jobs.append((f"{f}_homa", dict(lam=lam, homa_window=w,
                                           homa_degree=d, _group="tp")))
    rows += _pool(jobs, jobs_n)
    OUT.write_text(json.dumps(out))
    print(f"[B2] homa done ({time.perf_counter() - t_start:.0f}s)", flush=True)

    jobs = []
    for f in FABRICS:
        w, d = out["best"][f]
        for lam in LAMS:
            jobs += [(f"{f}_base", dict(lam=lam, msg_mix="", _group="single")),
                     (f"{f}_homa", dict(lam=lam, msg_mix="", homa_window=w,
                                        homa_degree=d, _group="single"))]
    rows += _pool(jobs, jobs_n)
    print(f"[C] done ({time.perf_counter() - t_start:.0f}s)", flush=True)

    jobs = []
    for f in FABRICS:
        w, d = out["best"][f]
        for lam in (0.3, 0.5, 0.8):
            base = dict(lam=lam, homa_window=w, homa_degree=d)
            jobs += [(f"{f}_homa", dict(base, homa_sched="fcfs",
                                        _group="fcfs")),
                     (f"{f}_homa", dict(base, homa_unsched=0,
                                        _group="nounsched")),
                     (f"{f}_homa", dict(base, ctrl_lat_mode="half",
                                        _group="halflat")),
                     (f"{f}_homa", dict(base, homa_sched="srpt",
                                        _group="srpt"))]
    rows += _pool(jobs, jobs_n)

    # SRPT only has something to order when more than one size class is
    # scheduled: with U=W=8 a 8-packet message is entirely unscheduled, so the
    # balanced point schedules only 64-packet messages. W=2 schedules both.
    jobs = [(f"{f}_homa", dict(lam=lam, homa_window=2, homa_degree=4,
                               homa_sched=sch, _group=f"{sch}_w2"))
            for f in FABRICS for lam in (0.5, 0.8)
            for sch in ("srpt", "fcfs")]
    rows += _pool(jobs, jobs_n)

    jobs = [(f"{f}_islip2d", dict(lam=lam, t_rtt=64, _group="rtt64"))
            for f in FABRICS for lam in (0.2, 0.3, 0.5, 0.8)]
    rows += _pool(jobs, jobs_n)
    print(f"[D,E] done ({time.perf_counter() - t_start:.0f}s)", flush=True)

    out["meta"] = {"warmup": WARM, "measure": MEAS, "buf_depth": 20,
                   "lams": list(LAMS), "w_grid": list(W_GRID),
                   "d_grid": list(D_GRID), "a_lams": list(A_LAMS),
                   "pool_k": POOL_K,
                   "secs": round(time.perf_counter() - t_start, 1)}
    return out


def lam_star(rs: list[dict[str, Any]]) -> float | None:
    ok = [r["lam"] for r in rs if r["stable_gp"]]
    return max(ok) if ok else None


if __name__ == "__main__":
    res = sweep(6)
    OUT.write_text(json.dumps(res, indent=1))
    print(f"wrote {OUT}  ({len(res['rows'])} rows)")
    for f in FABRICS:
        for sch, cfg, grp in (("base", "base", "main"), ("central", "islip2d", "main"),
                              ("homa", "homa", "main"), ("homa-tp", "homa", "tp"),
                              ("pool", "dbid", "pool")):
            rs = [r for r in res["rows"] if r["config"] == f"{f}_{cfg}"
                  and r["group"] == grp]
            print(f"{f} {sch:8} lam*={lam_star(rs)} "
                  f"peak={max(r['goodput'] for r in rs):.4f}")
