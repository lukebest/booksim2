#!/usr/bin/env python3
"""Sweep S1's own AIMD knobs; record (CoV, total write bandwidth).

The grid is the unique subset of the 62-point retune
(`probe_ring2_s1_dirbal.py`): band, cap_scale, window, pace_burst,
dir_split, scope. Fabric, outstanding, bus latency and I-tag stay at
the official point. CoV matches the deck: j2cov of the 100-cycle
window-mean Jain.

Usage:
    PYTHONHASHSEED=0 python3 utils/probe_ring2_s1_covbw.py [K] [jobs]
"""
from __future__ import annotations

import itertools
import json
import math
import os
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from dse_ring2_write_fair import (BIN_W, CORE_OUTSTANDING_WR, FABRIC, S1_CFG,
                                  W_FLITS, binned_jain, build_pattern,
                                  fairness_stats, run_scheme)
from rg_ring2_topo import CHI_VCS_WRITE, Ring2Topology

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results" / "probe_ring2_s1_covbw.json"

DEFAULTS = dict(dir_split=False, band="spec", cap_scale=1.0,
                window=64, pace_burst=1, scope="core_only")
S1T_OVER = dict(S1_CFG)


def j2cov(j: float) -> float:
    j = min(max(j, 1e-12), 1.0)
    return math.sqrt((1.0 - j) / j)


def _combos(**axes: list) -> list[dict[str, Any]]:
    keys = list(axes)
    return [dict(zip(keys, vals))
            for vals in itertools.product(*(axes[k] for k in keys))]


def s1_grid() -> list[dict[str, Any]]:
    raw = (
        _combos(dir_split=[False, True],
                band=["spec", "harsh", "gentle"],
                cap_scale=[1.0, 0.5, 0.25])
        + _combos(dir_split=[True], band=["spec", "gentle"],
                  cap_scale=[0.25, 0.5],
                  window=[32, 64, 128],
                  pace_burst=[0, 1, 4])
        + _combos(dir_split=[True], band=["spec", "gentle"],
                  cap_scale=[0.5], scope=["core_only", "both"],
                  window=[32, 64], pace_burst=[1])
    )
    seen: set[tuple] = set()
    out: list[dict[str, Any]] = []
    for over in raw:
        key = tuple(sorted({**DEFAULTS, **over}.items()))
        if key in seen:
            continue
        seen.add(key)
        out.append(over)
    return out


def _lab(over: dict[str, Any]) -> str:
    c = {**DEFAULTS, **over}
    return (f"{c['band']} cap{c['cap_scale']:g} w{c['window']} "
            f"b{c['pace_burst']} "
            f"{'dir' if c['dir_split'] else 'node'} {c['scope']}")


def _one(job: tuple) -> dict[str, Any]:
    over, k = job
    topo = Ring2Topology(n_planes=1, vcs=CHI_VCS_WRITE, route="latency")
    tx = build_pattern("uniform", k=k, W=W_FLITS, seed=0)
    cfg = {**FABRIC, "core_outstanding": CORE_OUTSTANDING_WR, **over}
    r = run_scheme("S1", topo, tx, cfg=cfg, quiet=True)
    inj = {int(c): v for c, v in (r.get("wr_inject_by_core") or {}).items()}
    f = fairness_stats(inj, r["makespan"] or 1, k * W_FLITS)
    jb = binned_jain(inj, BIN_W, f.get("t_fair") or 0)
    jmean = jb.get("jain_bin_mean")
    full = {**DEFAULTS, **over}
    return {
        "over": over, "cfg": full, "lab": _lab(over),
        "thr": f["throughput"], "max_min": f["max_min"],
        "t_fair": f.get("t_fair"), "makespan": r["makespan"],
        "jain_bin": jmean,
        "cov": None if jmean is None else round(j2cov(jmean), 5),
        "n_board_fail": r.get("n_board_fail"),
        "n_deflections": r.get("n_deflections"),
        "n_leave_occ_gt1": r.get("n_leave_occ_gt1"),
        "n_down_fail": r.get("n_down_fail"),
        "completed": r.get("completed"),
        "wall_secs": r.get("wall_secs"),
        "is_s1": full == {**DEFAULTS},
        "is_s1t": full == {**DEFAULTS, **S1T_OVER},
    }


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    k = int(args[0]) if args else 20_000
    jobs_n = (int(args[1]) if len(args) > 1
              else min(5, max(1, (os.cpu_count() or 2) - 1)))
    grid = s1_grid()
    jobs = [(over, k) for over in grid]
    print(f"K={k}  outstanding={CORE_OUTSTANDING_WR}  "
          f"points={len(jobs)}  workers={jobs_n}", flush=True)
    rows: list[dict[str, Any]] = []
    with ProcessPoolExecutor(max_workers=jobs_n) as ex:
        futs = [ex.submit(_one, job) for job in jobs]
        for i, fut in enumerate(as_completed(futs), 1):
            row = fut.result()
            rows.append(row)
            mark = "  <-- S1" if row["is_s1"] else "  <-- S1T" if row["is_s1t"] else ""
            print(f"  [{i}/{len(jobs)}] {row['lab']:<42} "
                  f"R={row['thr']:.4f}  CoV={row['cov']}  "
                  f"mm={row['max_min']:.4f}{mark}", flush=True)
    rows.sort(key=lambda r: (-(r["thr"] or 0), r["cov"] or 0))
    data = {
        "k": k, "bin_w": BIN_W, "core_outstanding": CORE_OUTSTANDING_WR,
        "defaults": DEFAULTS, "n": len(rows), "rows": rows,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(data, indent=1, ensure_ascii=False))
    print(f"wrote {OUT}  n={len(rows)}")


if __name__ == "__main__":
    main()
