#!/usr/bin/env python3
"""Confirm the phase-2 and phase-3 winners at the official K.

Short batches ranked the knobs; the tracker round showed they also
understate steady-state congestion, so every candidate that matters gets
re-run at `K_PER_CORE` before anything is claimed.

Forecast, written before this ran (K=1500 numbers in brackets):

  S22 w=3 hold=12  -- Jain 0.991 [0.99165], throughput -1.2% [-1.16%].
      Longer runs raise everyone's per-bin index (S0 goes 0.942 -> 0.965
      from K=1500 to K=20000) because the start-up transient stops
      dominating, so predict Jain 0.991-0.995 and a *smaller* throughput
      loss, 0.6-1.4%. This is the candidate that either clears the two-sided
      acceptance or misses it by a few tenths of a point.
  S1 tuned (dir_split, cap_scale 0.5) -- direction failure ratio 4.8 [4.82]
      against S0's 4.43, throughput within +-0.5%.

Falsified if: S22's throughput loss at K=20000 is *larger* than at K=1500
(that would mean the yields compound in steady state rather than settle),
or its Jain comes out below the K=1500 value.

Usage:
    PYTHONHASHSEED=0 python3 confirm_ring2_s22.py [K]
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from dse_ring2_write_fair import (BIN_W, FABRIC, K_PER_CORE, M_REQ, M_RSP,
                                  S1_CFG, W_FLITS, binned_jain,
                                  board_dir_from_inj, build_pattern,
                                  fairness_stats, run_scheme)
from rg_ring2_topo import (CHI_VCS_WRITE, Ring2Topology, write_bounds,
                           write_paths_for_txns)

OUT = (Path(__file__).resolve().parents[1] / "results"
       / "confirm_ring2_s22.json")

# The look-ahead needs candidates to look at, so these carry a deeper
# per-direction inject Q than the S0 fabric. Priced as part of the scheme.
DEEP = {"dfc_dodge": 32, "dir_inj_depth": 32, "inj_depth": 32}

def _s22(w: int, thresh: float, margin: float, **over) -> dict[str, Any]:
    return {"dfc_window": w, "dfc_bus_lat": 1, "dfc_thresh": thresh,
            "dfc_hold": 16, "dfc_margin": margin, **DEEP, **over}


# Implementing I-tag as specified moved the baseline (per-bin Jain 0.957 ->
# 0.968, whole-window max/min 1.12 -> 1.03), so the operating point tuned
# against the old baseline now over-corrects: `m=2` costs 1.93% at the official
# K, outside the acceptance line. `dfc_margin` is the knob that matters --
# a fairer baseline means most deficit gaps are small, and refusing those
# near-level swaps is what stops the controller spending hops for no index
# movement. These are the survivors of the 48-point re-tune sweep.
CANDIDATES: list[tuple[str, str, dict[str, Any]]] = [
    ("S0", "S0", {}),
    ("S0 dirq=32", "S0", {"dir_inj_depth": 32, "inj_depth": 32}),
    ("S1 tuned", "S1", dict(S1_CFG)),
    # margin=0 yields to anyone behind at all; those near-level swaps cost a
    # hop and move the index almost not at all.
    ("S22 w=3 m=0", "S22", _s22(3, 0.5, 0.0)),
    ("S22 w=2 m=2", "S22", _s22(2, 0.5, 2.0)),
    ("S22 w=3 m=2", "S22", _s22(3, 0.5, 2.0)),
    ("S22 w=3 m=3", "S22", _s22(3, 0.5, 3.0)),
    ("S22 w=2 m=3", "S22", _s22(2, 0.5, 3.0)),
    ("S22 w=2 m=4", "S22", _s22(2, 0.5, 4.0)),
    ("S22 w=3 m=4 th=1", "S22", _s22(3, 1.0, 4.0)),
    ("S22 w=2 m=4 th=1", "S22", _s22(2, 1.0, 4.0)),
    # Same scheme on the stock 8-deep dir Q, to price the buffer separately.
    ("S22 dirq=8 m=3", "S22", {"dfc_window": 3, "dfc_bus_lat": 1,
                               "dfc_thresh": 0.5, "dfc_hold": 16,
                               "dfc_margin": 3.0, "dfc_dodge": 8}),
]


def main() -> None:
    k = int(sys.argv[1]) if len(sys.argv) > 1 else K_PER_CORE
    topo = Ring2Topology(n_planes=1, vcs=CHI_VCS_WRITE)
    txns = build_pattern("uniform", k=k, W=W_FLITS, seed=0)
    vp = write_paths_for_txns(topo, txns, strategy="least_occupied")
    b = write_bounds(topo, vp, m_req=M_REQ, m_rsp=M_RSP, m_wdata=W_FLITS,
                     merge_port_vcs=False)
    r_star = len(topo.cores) * k * W_FLITS / max(1, b["bound"])
    print(f"K={k}  R*={r_star:.4f} flit/cycle", flush=True)

    t0, base, rows = time.perf_counter(), None, []
    for lab, scheme, over in CANDIDATES:
        cfg = dict(FABRIC)
        cfg.update(over)
        r = run_scheme(scheme, topo, txns, seed=0, cfg=cfg, quiet=True)
        inj = r["wr_inject_by_core"]
        f = fairness_stats(inj, r["makespan"], k * W_FLITS)
        jb = binned_jain(inj, BIN_W, f["t_fair"])
        bd = board_dir_from_inj(r.get("inj_by_hop") or {},
                                sorted(int(c) for c in inj))
        fr = max(max(v["fail_cw"], v["fail_ccw"])
                 / max(1, min(v["fail_cw"], v["fail_ccw"]))
                 for v in bd.values())
        if base is None:
            base = f["throughput"]
        d = 100.0 * (f["throughput"] - base) / base
        row = {
            "label": lab, "scheme": scheme, "cfg": over,
            "makespan": r["makespan"], "completed": r["completed"],
            "thr": f["throughput"], "thr_delta_pct": round(d, 3),
            "pct_r_star": round(100.0 * f["throughput"] / r_star, 2),
            "jain_bin": jb["jain_bin_mean"],
            "jain_bin_null": jb["jain_bin_null"],
            "jain_bin_ratio": jb["jain_bin_ratio"],
            "jain_bin_p05": jb["jain_bin_p05"],
            "jain_bin_min": jb["jain_bin_min"],
            "flits_per_core_per_bin": jb["flits_per_core_per_bin"],
            "max_min": f["max_min"], "bw_min": f["bw_min"],
            "fail_ratio_max": round(fr, 3),
            "bw_by_core": f["bw_by_core"],
            "fc": r.get("fc") or {}, "wall_secs": r.get("wall_secs"),
            "pass_jain": jb["jain_bin_mean"] > 0.99,
            "pass_thr": d > -1.0,
        }
        rows.append(row)
        print(f"  {lab:<18} Jbin={row['jain_bin']:<9} "
              f"thr={row['thr']:<8}({d:+6.2f}%) {row['pct_r_star']:>5}% R*  "
              f"mm={row['max_min']:<7} failmax={row['fail_ratio_max']:<7} "
              f"{'PASS' if row['pass_jain'] and row['pass_thr'] else ''}"
              f"  {row['wall_secs']}s", flush=True)
    OUT.write_text(json.dumps(
        {"k": k, "r_star": round(r_star, 4), "bound": b["bound"],
         "rows": rows, "wall_secs": round(time.perf_counter() - t0, 1)},
        indent=1))
    print(f"\nwrote {OUT}")


if __name__ == "__main__":
    main()
