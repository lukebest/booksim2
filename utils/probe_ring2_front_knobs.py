#!/usr/bin/env python3
"""Single-knob (R, CoV) sweeps for the page-28/29 first and second fronts.

The two Pareto pages' union is S0, S16, S26, I-tag, S22(w32), S29, S21,
S21+eq, S28S. Nothing on that list hard-codes λ* (S24/S25 were already
withdrawn), so the filter for a strong traffic-pattern prior removes no
scheme. I-tag stays because it is on the uniform second front; it is still
an S0 t_inj/hold retune, not a new mechanism.

Reuse `probe_ring2_knob13.json` for the six official-scheme knobs already
walked at K=2000. Run only the three missing families: S21, S21+eq, S22(w32).

Usage:
    PYTHONHASHSEED=0 python3 utils/probe_ring2_front_knobs.py [K] [jobs]
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

from dse_ring2_write_fair import (BIN_W, FABRIC, S16_OVERCOMMIT, S22_CFG,
                                  S26_CFG, S28S_CFG, S29_CFG, W_FLITS,
                                  binned_jain, build_pattern, fairness_stats,
                                  run_scheme)
from rg_ring2_topo import CHI_VCS_WRITE, Ring2Topology

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results" / "probe_ring2_front_knobs.json"
KNOB13 = ROOT / "results" / "probe_ring2_knob13.json"


def j2cov(j: float) -> float:
    j = min(max(j, 1e-12), 1.0)
    return math.sqrt((1.0 - j) / j)


# (panel, scheme, base cfg, knob, values, published/Pareto anchor)
SWEEPS: list[tuple[str, str, dict[str, Any], str, list[Any], Any]] = [
    ("S0", "S0", {}, "t_inj", [1, 2, 4, 8, 16, 1_000_000_000], 16),
    ("S16", "S16", {"overcommit": S16_OVERCOMMIT}, "overcommit",
     [6, 8, 10, 12, 16, 20, 32, 64], S16_OVERCOMMIT),
    ("S26", "S26", dict(S26_CFG), "route_max_extra", [0, 2, 4, 8], 2),
    ("ITAG", "S0", {"t_inj": 2, "itag_hold": 2}, "t_inj",
     [1, 2, 4, 8, 16], 2),
    ("S22w32", "S22",
     {**S22_CFG, "dfc_window": 32, "dfc_bus_lat": 30, "dfc_margin": 3.0},
     "dfc_margin", [0.0, 1.0, 2.0, 3.0, 4.0], 3.0),
    ("S29", "S29", dict(S29_CFG), "tdma_slot", [1, 2, 3, 4, 8], 2),
    ("S21", "S21",
     {"pace_burst": 1.0, "pace_headroom": 1.5, "pace_gain": 0.05,
      "pace_equalise": False},
     "pace_headroom", [1.0, 1.15, 1.3, 1.5, 1.8, 2.0], 1.5),
    ("S21eq", "S21",
     {"pace_burst": 1.0, "pace_headroom": 1.5, "pace_gain": 0.25,
      "pace_equalise": True, "pace_tol": 0.02, "pace_window": 64,
      "pace_bus_lat": 30},
     "pace_headroom", [1.0, 1.15, 1.3, 1.5, 1.8, 2.0], 1.5),
    ("S28S", "S28S", dict(S28S_CFG), "rcp_target",
     [0.90, 0.94, 0.96, 0.98, 1.00], 0.98),
]

# knob13 panel name -> this file's panel name (same scheme+knob+values)
REUSE = {"S0": "S0", "S16": "S16", "S26": "S26", "ITAG": "ITAG",
         "S29": "S29", "S28S": "S28S"}


def _cfg(scheme: str, base: dict[str, Any], knob: str, val: Any) -> dict[str, Any]:
    cfg = {**FABRIC, **base, knob: val}
    return cfg


def _one(job: tuple) -> dict[str, Any]:
    name, scheme, base, knob, val, k = job
    topo = Ring2Topology(n_planes=1, vcs=CHI_VCS_WRITE, route="latency")
    tx = build_pattern("uniform", k=k, W=W_FLITS, seed=0)
    r = run_scheme(scheme, topo, tx, cfg=_cfg(scheme, base, knob, val),
                   quiet=True)
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


def _reuse_knob13() -> dict[str, list[dict[str, Any]]]:
    if not KNOB13.is_file():
        return {}
    raw = json.loads(KNOB13.read_text())
    out: dict[str, list[dict[str, Any]]] = {}
    for swp in raw["sweeps"]:
        dst = REUSE.get(swp["name"])
        if not dst:
            continue
        rows = []
        for r in swp["rows"]:
            row = dict(r)
            row["name"] = dst
            rows.append(row)
        out[dst] = rows
    return out


def main() -> None:
    k = int(sys.argv[1]) if len(sys.argv) > 1 else 2000
    jobs_n = int(sys.argv[2]) if len(sys.argv) > 2 else max(1, (os.cpu_count() or 2) - 1)
    have = _reuse_knob13() if k == 2000 else {}
    jobs = []
    for name, scheme, base, knob, vals, _anchor in SWEEPS:
        if name in have:
            continue
        for val in vals:
            jobs.append((name, scheme, base, knob, val, k))
    print(f"K={k}  reuse={sorted(have)}  new={len(jobs)}  workers={jobs_n}",
          flush=True)
    rows: list[dict[str, Any]] = []
    if jobs:
        with ProcessPoolExecutor(max_workers=jobs_n) as ex:
            rows = list(ex.map(_one, jobs, chunksize=1))
    by: dict[str, list[dict[str, Any]]] = {**have}
    for r in rows:
        by.setdefault(r["name"], []).append(r)
        print(f"  {r['name']:<7} {r['knob']}={r['val']!s:<12} "
              f"R={r['thr']:.4f} CoV={r['cov']:.4f}", flush=True)
    out = {
        "k": k, "bin_w": BIN_W,
        "note": "Page 28/29 first+second fronts; no λ*-pinned scheme on those "
                "fronts (S24/S25 already withdrawn).",
        "sweeps": [
            {"name": name, "scheme": scheme, "knob": knob, "anchor": anchor,
             "rows": by[name]}
            for name, scheme, _b, knob, _v, anchor in SWEEPS
        ],
    }
    OUT.write_text(json.dumps(out, indent=1, ensure_ascii=False))
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
