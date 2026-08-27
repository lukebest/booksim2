#!/usr/bin/env python3
"""Knob probe: how close can S0 get to the hop-ideal write ceiling R*?

The previous round localised the gap to up-ring arbitration on a bufferless
ring, concentrated at the mem nodes that sit at the tail of the busiest RSP
segment. This script sweeps the knobs that could plausibly move that:

  * buffer depths (`inj_depth`, `dir_inj_depth`, `eject_depth`, `resv_ej`)
  * the I-tag starvation threshold `t_inj`
  * the E-tag deflection threshold `t_xfer`
  * `inj_sel`: whether the injection arbiter looks at which outgoing hop is
    actually free this cycle before committing the port to a direction

Ranking runs use a short batch; the winner has to be confirmed at the
official K, because the tracker round showed short batches never reach the
steady-state congestion the official run has.

Usage:
    PYTHONHASHSEED=0 python3 probe_ring2_ceiling.py [K] [group ...]
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from dse_ring2_write_fair import (BIN_W, FABRIC, M_REQ, M_RSP, W_FLITS,
                                  binned_jain, board_dir_from_inj,
                                  build_pattern, fairness_stats, run_scheme)
from rg_ring2_topo import (CHI_VCS_WRITE, Ring2Topology, write_bounds,
                           write_paths_for_txns)

OUT = Path(__file__).resolve().parents[1] / "results" / "probe_ring2_ceiling.json"

# The arbiter fix, carried into every combination probe below.
FS = {"inj_sel": "free_slot"}


def _groups(k: int) -> dict[str, list[dict[str, Any]]]:
    """Knob settings to try, grouped so each group answers one question."""
    return {
        # Does an output-aware inject arbiter recover the idle hop slots?
        "inj_sel": [{}, {"inj_sel": "free_slot"}],
        # I-tag: how fast should a starved injector lock out the others?
        "t_inj": [{"t_inj": v} for v in (2, 4, 8, 16, 32, 64, 256, 10**9)],
        # E-tag: expected inert, the leave buffers never fill.
        "t_xfer": [{"t_xfer": v} for v in (1, 2, 4, 16, 10**9)],
        "resv_ej": [{"resv_ej": v} for v in (0, 1, 4, 12)],
        # Buffers, one at a time and then all at once.
        "buffers": [{}, {"inj_depth": 32}, {"dir_inj_depth": 16},
                    {"eject_depth": 32}, {"eject_bw": 2},
                    {"inj_depth": 32, "dir_inj_depth": 16, "eject_depth": 32}],
        # Everything below assumes the fixed arbiter, which is where the
        # baseline is headed. `t_inj` low is the fairness/throughput knob:
        # it hits Jain > 0.99 on its own but costs a lot of bandwidth.
        "fs_t_inj": [dict(FS, t_inj=v) for v in
                     (2, 3, 4, 6, 8, 12, 16, 32, 10**9)],
        "fs_buffers": [dict(FS), dict(FS, inj_depth=32),
                       dict(FS, dir_inj_depth=2), dict(FS, dir_inj_depth=4),
                       dict(FS, dir_inj_depth=16), dict(FS, dir_inj_depth=32),
                       dict(FS, eject_depth=32), dict(FS, eject_bw=2),
                       dict(FS, inj_depth=32, dir_inj_depth=32)],
        "fs_etag": [dict(FS), dict(FS, t_xfer=1), dict(FS, t_xfer=10**9),
                    dict(FS, resv_ej=0), dict(FS, resv_ej=12)],
    }


def run_one(topo: Ring2Topology, txns, over: dict[str, Any], *, k: int,
            scheme: str = "S0", seed: int = 0) -> dict[str, Any]:
    cfg = dict(FABRIC)
    cfg.update(over)
    r = run_scheme(scheme, topo, txns, seed=seed, cfg=cfg, quiet=True)
    inj = r.get("wr_inject_by_core") or {}
    f = fairness_stats(inj, r["makespan"], k * W_FLITS)
    jb = binned_jain(inj, BIN_W, f["t_fair"]) if f else {}
    bd = board_dir_from_inj(r.get("inj_by_hop") or {},
                            sorted(int(c) for c in inj))
    return {
        "over": over, "scheme": scheme,
        "makespan": r["makespan"], "completed": r["completed"],
        "thr": f.get("throughput"),
        "jain_bin": jb.get("jain_bin_mean"),
        "jain_bin_null": jb.get("jain_bin_null"),
        "jain_bin_ratio": jb.get("jain_bin_ratio"),
        "jain_bin_min": jb.get("jain_bin_min"),
        "max_min": f.get("max_min"),
        "n_board_fail": r.get("n_board_fail"),
        "n_itag": r.get("n_itag_raised"), "n_etag": r.get("n_etag_raised"),
        "fail_ratio_max": (round(max(
            (max(r["fail_cw"], r["fail_ccw"]) / max(1, min(r["fail_cw"],
                                                           r["fail_ccw"])))
            for r in bd.values()), 3) if bd else None),
        "buffers": r.get("buffers") or {},
        "wall": r.get("wall_secs"),
    }


def main() -> None:
    k = int(sys.argv[1]) if len(sys.argv) > 1 else 3000
    want = sys.argv[2:]
    topo = Ring2Topology(n_planes=1, vcs=CHI_VCS_WRITE)
    txns = build_pattern("uniform", k=k, W=W_FLITS, seed=0)
    vp = write_paths_for_txns(topo, txns, strategy="least_occupied")
    b = write_bounds(topo, vp, m_req=M_REQ, m_rsp=M_RSP, m_wdata=W_FLITS,
                     merge_port_vcs=False)
    n_c = len(topo.cores)
    r_star = n_c * k * W_FLITS / max(1, b["bound"])
    print(f"K={k}  bound={b['bound']}  R*={r_star:.4f} flit/cycle", flush=True)

    t0 = time.perf_counter()
    out: dict[str, Any] = {"k": k, "bound": b["bound"], "r_star": round(r_star, 4),
                           "groups": {}}
    for name, cases in _groups(k).items():
        if want and name not in want:
            continue
        print(f"\n[{name}]", flush=True)
        rows = []
        for over in cases:
            row = run_one(topo, txns, over, k=k)
            row["pct_r_star"] = round(100.0 * (row["thr"] or 0) / r_star, 1)
            rows.append(row)
            print(f"  {str(over) or '(baseline)':<52} "
                  f"thr={row['thr']:<8} {row['pct_r_star']:>5}% R*  "
                  f"Jbin={row['jain_bin']} ratio={row['jain_bin_ratio']}  "
                  f"itag={row['n_itag']}  {row['wall']}s", flush=True)
        out["groups"][name] = rows
    out["wall_secs"] = round(time.perf_counter() - t0, 1)
    OUT.write_text(json.dumps(out, indent=1))
    print(f"\nwrote {OUT}  {out['wall_secs']}s")


if __name__ == "__main__":
    main()
