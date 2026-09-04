#!/usr/bin/env python3
"""One-dimensional knob sweeps for the 13 official deck write schemes.

Each scheme is walked along the scalar that actually moves it in the
(CoV, R) plane, at a common screening K, so slide 31 can put every
trajectory on the same axes. CoV is the 100-cycle window-mean conversion
used everywhere else: sqrt((1 - J_bin) / J_bin).

Usage:
    PYTHONHASHSEED=0 python3 utils/probe_ring2_knob13.py [K] [jobs]
"""
from __future__ import annotations

import json
import math
import os
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from deck_ring2_data import S22_STOCK
from dse_ring2_write_fair import (BIN_W, FABRIC, S1_CFG, S16_OVERCOMMIT,
                                  S26_CFG, S27_CFG, S28_CFG, S28S_CFG,
                                  S29_CFG, W_FLITS, binned_jain,
                                  build_pattern, fairness_stats, run_scheme)
from rg_ring2_topo import CHI_VCS_WRITE, Ring2Topology

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results" / "probe_ring2_knob13.json"


def j2cov(j: float) -> float:
    j = min(max(j, 1e-12), 1.0)
    return math.sqrt((1.0 - j) / j)


# (panel, scheme, base cfg, knob name, knob values, official value)
SWEEPS: list[tuple[str, str, dict[str, Any], str, list[Any], Any]] = [
    ("S0", "S0", {}, "t_inj", [1, 2, 4, 8, 16, 1_000_000_000], 4),
    ("S1", "S1", {}, "band·cap",
     ["gentle·1.0", "gentle·0.5", "gentle·0.25",
      "spec·1.0", "spec·0.5", "spec·0.25",
      "harsh·1.0", "harsh·0.5", "harsh·0.25"], "spec·1.0"),
    ("S1T", "S1T", dict(S1_CFG), "cap_scale", [1.0, 0.75, 0.5, 0.25], 0.5),
    ("S16", "S16", {"overcommit": S16_OVERCOMMIT}, "overcommit",
     [6, 8, 10, 12, 16, 20, 32, 64], S16_OVERCOMMIT),
    ("ITAG", "S0", {"t_inj": 2, "itag_hold": 2}, "t_inj",
     [1, 2, 4, 8, 16], 2),
    ("S19", "S19", {}, "swift_t_mult", [4.0, 6.0, 8.0, 12.0, 16.0], 8.0),
    ("S20", "S20", {}, "win_max", [16.0, 24.0, 32.0, 64.0, 128.0], 128.0),
    ("S22", "S22", dict(S22_STOCK), "dfc_margin",
     [0.0, 1.0, 2.0, 3.0, 4.0], 3.0),
    ("S26", "S26", dict(S26_CFG), "route_max_extra", [0, 2, 4, 8], 2),
    ("S27", "S27", dict(S27_CFG), "bp_xoff",
     [0.80, 0.85, 0.90, 0.95, 0.99], 0.90),
    ("S28", "S28", dict(S28_CFG), "rcp_alpha",
     [0.10, 0.25, 0.50, 1.00], 0.25),
    ("S28S", "S28S", dict(S28S_CFG), "rcp_target",
     [0.90, 0.94, 0.96, 0.98, 1.00], 0.98),
    ("S29", "S29", dict(S29_CFG), "tdma_slot", [1, 2, 3, 4, 8], 2),
]


def _cfg_for(scheme: str, base: dict[str, Any], knob: str, val: Any) -> dict[str, Any]:
    cfg = {**FABRIC, **base}
    if knob == "band·cap":
        band, cap = str(val).split("·")
        cfg["band"] = band
        cfg["cap_scale"] = float(cap)
    else:
        cfg[knob] = val
    if scheme == "S27" and knob == "bp_xoff":
        cfg["bp_xon"] = max(0.50, float(val) - 0.10)
    return cfg


def _one(job: tuple) -> dict[str, Any]:
    name, scheme, base, knob, val, k = job
    topo = Ring2Topology(n_planes=1, vcs=CHI_VCS_WRITE, route="latency")
    tx = build_pattern("uniform", k=k, W=W_FLITS, seed=0)
    cfg = _cfg_for(scheme, base, knob, val)
    r = run_scheme(scheme, topo, tx, cfg=cfg, quiet=True)
    inj = {int(c): v for c, v in (r.get("wr_inject_by_core") or {}).items()}
    f = fairness_stats(inj, r["makespan"] or 1, k * W_FLITS)
    jb = binned_jain(inj, BIN_W, f.get("t_fair") or 0)
    j = jb["jain_bin_mean"]
    return {
        "name": name, "scheme": scheme, "knob": knob, "val": val,
        "thr": f["throughput"], "jain_bin": j, "cov": round(j2cov(j), 5),
        "max_min": f["max_min"], "makespan": r["makespan"],
        "completed": r.get("completed"),
    }


def main() -> None:
    k = int(sys.argv[1]) if len(sys.argv) > 1 else 2000
    jobs_n = int(sys.argv[2]) if len(sys.argv) > 2 else max(1, (os.cpu_count() or 2) - 1)
    jobs = []
    for name, scheme, base, knob, vals, _anchor in SWEEPS:
        for val in vals:
            jobs.append((name, scheme, base, knob, val, k))
    print(f"K={k}  jobs={len(jobs)}  workers={jobs_n}", flush=True)
    with ProcessPoolExecutor(max_workers=jobs_n) as ex:
        rows = list(ex.map(_one, jobs, chunksize=1))
    by: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        by.setdefault(r["name"], []).append(r)
        print(f"  {r['name']:<5} {r['knob']}={r['val']!s:<16} "
              f"R={r['thr']:.4f} CoV={r['cov']:.4f}", flush=True)
    out = {
        "k": k, "bin_w": BIN_W,
        "sweeps": [
            {"name": name, "scheme": scheme, "knob": knob,
             "anchor": anchor,
             "rows": by[name]}
            for name, scheme, _b, knob, _v, anchor in SWEEPS
        ],
    }
    OUT.write_text(json.dumps(out, indent=1, ensure_ascii=False))
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
