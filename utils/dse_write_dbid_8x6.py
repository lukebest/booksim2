#!/usr/bin/env python3
"""Steady-state goodput vs injection rate: baseline / centralized / HA-DBID.

Three schemes per fabric (8x6, all-to-all write traffic, open loop):

  mesh:  mesh_base    credit flow control + per-router iSLIP        (baseline)
         mesh_islip2d centralized request-grant, D-M, interval     (central)
         mesh_dbid    HA arbitrates when it returns DBIDResp         (dist)
  ring:  ring_base    E-tag / I-tag + deflection                     (baseline)
         ring_islip2d centralized request-grant, D-R, interval     (central)
         ring_dbid    HA arbitrates when it returns DBIDResp         (dist)

Phases
    A  K sweep   -- DBID pool size per HA, at a handful of lambdas, to choose
                    the distributed scheme's K per fabric (K=0 is unbounded)
    B  main      -- all three schemes over the full lambda grid, m=1
    C  control   -- K=0 distributed (must equal the baseline + a round trip)
    D  rtt       -- centralized with a distance-realistic T_rtt
    E  policy    -- least_served vs round_robin, grant pacing
    F  m=4       -- longer packets, lambda scaled by 1/m

K is chosen on the data, not by hand: the K with the highest mean goodput over
the phase-A lambdas (ties go to the smaller K, i.e. the smaller table).

Writes results/write_dbid_8x6.json. Run: python3 dse_write_dbid_8x6.py [--quick]
"""

from __future__ import annotations

import json
import sys
import time
from multiprocessing import Pool
from pathlib import Path
from typing import Any

from rg_steady_des import SteadyParams, anchors, run_steady

OUT = Path(__file__).resolve().parents[1] / "results" / "write_dbid_8x6.json"
LAMS = (0.01, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0)
FINE = (0.35, 0.42, 0.45, 0.48, 0.52, 0.55, 0.65, 0.75)
K_LAMS = (0.3, 0.4, 0.5, 0.6, 0.8, 1.0)
K_GRID = (8, 16, 32, 48, 64, 96, 128, 256, 0)
FABRICS = ("mesh", "ring")
WARM, MEAS = 1500, 6000


def _run(job: tuple[str, dict[str, Any]]) -> dict[str, Any]:
    cfg, kw = job
    group = kw.pop("_group")
    p = SteadyParams(warmup=WARM, measure=MEAS, buf_depth=20, **kw)
    t0 = time.perf_counter()
    r = run_steady(cfg, p)
    r["secs"] = round(time.perf_counter() - t0, 2)
    r["group"] = group
    return r


def _pool(jobs: list[tuple[str, dict[str, Any]]], n: int) -> list[dict]:
    with Pool(n) as pool:
        return pool.map(_run, jobs, chunksize=1)


def pick_k(rows: list[dict[str, Any]], fabric: str) -> int:
    score: dict[int, list[float]] = {}
    for r in rows:
        if r["config"] == f"{fabric}_dbid" and r["group"] == "ksweep" \
                and r["dbid_k"] != 0:
            score.setdefault(r["dbid_k"], []).append(r["goodput"])
    best = max(score, key=lambda k: (round(sum(score[k]) / len(score[k]), 4),
                                     -k))
    return best


def sweep(jobs_n: int, quick: bool) -> dict[str, Any]:
    lams = LAMS if quick else tuple(sorted(LAMS + FINE))
    out: dict[str, Any] = {"anchors": anchors(), "rows": [], "k_star": {}}
    rows = out["rows"]
    t_start = time.perf_counter()

    # -- A: K sweep ----------------------------------------------------------
    jobs = [(f"{f}_dbid", dict(lam=lam, dbid_k=k, _group="ksweep"))
            for f in FABRICS for lam in K_LAMS for k in K_GRID]
    rows += _pool(jobs, jobs_n)
    for f in FABRICS:
        out["k_star"][f] = pick_k(rows, f)
    print(f"[A] K*: {out['k_star']}  ({time.perf_counter() - t_start:.0f}s)",
          flush=True)

    # -- B/C: main curves + unbounded control -------------------------------
    jobs = []
    for f in FABRICS:
        ks = out["k_star"][f]
        for lam in lams:
            jobs += [(f"{f}_base", dict(lam=lam, _group="main")),
                     (f"{f}_islip2d", dict(lam=lam, _group="main")),
                     (f"{f}_dbid", dict(lam=lam, dbid_k=ks, _group="main")),
                     (f"{f}_dbid", dict(lam=lam, dbid_k=0, _group="control"))]
    rows += _pool(jobs, jobs_n)
    print(f"[B,C] done ({time.perf_counter() - t_start:.0f}s)", flush=True)

    # -- D: centralized with a distance-realistic round trip ----------------
    jobs = [(f"{f}_islip2d", dict(lam=lam, t_rtt=rtt, _group="rtt"))
            for f in FABRICS for lam in (0.3, 0.4, 0.5, 0.6, 0.75, 1.0)
            for rtt in (16, 64)]
    rows += _pool(jobs, jobs_n)

    # -- E: policy / pacing -------------------------------------------------
    jobs = []
    for f in FABRICS:
        ks = out["k_star"][f]
        for lam in (0.4, 0.6, 1.0):
            for pol in ("least_served", "round_robin"):
                jobs.append((f"{f}_dbid", dict(lam=lam, dbid_k=ks,
                                               dbid_policy=pol,
                                               _group="policy")))
            for pace in (2.0, 2.5):
                jobs.append((f"{f}_dbid", dict(lam=lam, dbid_k=ks,
                                               dbid_pace=pace,
                                               _group="pace")))
            jobs.append((f"{f}_dbid", dict(lam=lam, dbid_k=ks,
                                           ctrl_lat_mode="half",
                                           _group="halflat")))
    rows += _pool(jobs, jobs_n)
    print(f"[D,E] done ({time.perf_counter() - t_start:.0f}s)", flush=True)

    # -- F: m=4 --------------------------------------------------------------
    m4 = (0.03, 0.06, 0.09, 0.1, 0.12, 0.15, 0.2, 0.3)
    jobs = []
    for f in FABRICS:
        # K is a count of packets; keep the byte window the same as at m=1
        ks = max(8, out["k_star"][f] // 2) if out["k_star"][f] else 0
        for lam in m4:
            jobs += [(f"{f}_base", dict(lam=lam, m=4, _group="m4")),
                     (f"{f}_islip2d", dict(lam=lam, m=4, _group="m4")),
                     (f"{f}_dbid", dict(lam=lam, m=4, dbid_k=ks,
                                        _group="m4"))]
        out.setdefault("k_m4", {})[f] = ks
    rows += _pool(jobs, jobs_n)
    print(f"[F] done ({time.perf_counter() - t_start:.0f}s)", flush=True)

    out["meta"] = {"warmup": WARM, "measure": MEAS, "buf_depth": 20,
                   "lams": list(lams), "k_grid": list(K_GRID),
                   "secs": round(time.perf_counter() - t_start, 1)}
    return out


def lam_star(rows: list[dict[str, Any]], key: str = "stable_gp") -> float | None:
    ok = [r["lam"] for r in rows if r[key]]
    return max(ok) if ok else None


if __name__ == "__main__":
    quick = "--quick" in sys.argv
    res = sweep(6, quick)
    OUT.write_text(json.dumps(res, indent=1))
    print(f"wrote {OUT}  ({len(res['rows'])} rows)")
    for f in FABRICS:
        for sch in ("base", "islip2d", "dbid"):
            rs = [r for r in res["rows"] if r["config"] == f"{f}_{sch}"
                  and r["group"] == "main"]
            print(f"{f}_{sch:8} lam*={lam_star(rs)} "
                  f"peak_goodput={max(r['goodput'] for r in rs):.4f}")
