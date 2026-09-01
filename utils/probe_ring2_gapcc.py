#!/usr/bin/env python3
"""Knob probe for the four congestion-control families the study was missing.

S26 adaptive routing, S27 hop-by-hop backpressure, S28 explicit-rate (RCP)
feedback and S29 proactive scheduled reservation each arrive with knobs that
the datacentre / interconnect literature specifies in units this fabric does
not have (microseconds, queue depths, RTTs). This probe picks each family's
operating point the same way the study picked S22's: a small grid at a
screening K, judged on the same three axes the deck reports, with the chosen
row required to be the family's own best trade rather than the best number on
any single axis.

The point is not to tune a family into looking good. It is to make sure a
family that loses does so because of its mechanism and not because a knob was
left at a datacentre default.

Usage:
    PYTHONHASHSEED=0 python3 utils/probe_ring2_gapcc.py [K]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from dse_ring2_write_fair import (BIN_W, S26_CFG, S27_CFG, S28_CFG, S29_CFG,
                                  W_FLITS, build_pattern, digest, run_scheme)
from rg_ring2_topo import CHI_VCS_WRITE, Ring2Topology

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results" / "probe_ring2_gapcc.json"


def grid() -> list[tuple[str, str, dict[str, Any]]]:
    rows: list[tuple[str, str, dict[str, Any]]] = [("S0", "S0", {}),
                                                   ("S1", "S1", {})]
    # S26: how aggressive the detour is, and how long it may be.
    for th in (0.05, 0.15, 0.30):
        for ex in (2, 4, 8):
            rows.append((f"S26 th{th} ex{ex}", "S26",
                         {**S26_CFG, "route_thresh": th,
                          "route_max_extra": ex}))
    # S27: where the XOFF sits, and how far it is forwarded.
    for off, on in ((0.90, 0.80), (0.95, 0.85), (0.99, 0.95)):
        for reach in (2, 8, 0):
            rows.append((f"S27 x{off} r{reach}", "S27",
                         {**S27_CFG, "bp_xoff": off, "bp_xon": on,
                          "bp_reach": reach}))
    # S28: bucket depth is the known failure mode of every pacer on this
    # fabric, so it is swept first; then the target and the damping.
    for burst in (1.0, 2.0, 4.0):
        for target in (0.98, 1.0):
            rows.append((f"S28 b{burst} t{target}", "S28",
                         {**S28_CFG, "rcp_pace_burst": burst,
                          "rcp_target": target}))
    for g in (0.25, 1.0):
        rows.append((f"S28 g{g}", "S28", {**S28_CFG, "rcp_g": g,
                                         "rcp_pace_burst": 2.0}))
    # S29: slot length sets the guarantee granularity; the demand bit and the
    # look-ahead each say how much of the reserved slot is recovered.
    for slot in (2, 4, 8, 16):
        rows.append((f"S29 s{slot}", "S29", {**S29_CFG, "tdma_slot": slot}))
    for mode in ("blind", "demand"):
        for dodge in (0, 8, 32):
            rows.append((f"S29 {mode} d{dodge}", "S29",
                         {**S29_CFG, "tdma_mode": mode, "tdma_dodge": dodge}))
    return rows


def main() -> None:
    k = int(sys.argv[1]) if len(sys.argv) > 1 else 2000
    topo = Ring2Topology(n_planes=1, vcs=CHI_VCS_WRITE, route="latency")
    txns = build_pattern("uniform", k=k, W=W_FLITS, seed=0)
    print(f"K={k}  bin={BIN_W}", flush=True)
    out = []
    base_bw = None
    for name, scheme, cfg in grid():
        raw = run_scheme(scheme, topo, txns, cfg=cfg, quiet=True)
        d = digest(raw, flits_per_core=k * W_FLITS, bin_w=BIN_W)
        f = d["fairness"]
        bw = f["throughput"]
        if base_bw is None:
            base_bw = bw
        row = {
            "name": name, "scheme": scheme, "cfg": cfg,
            "thr": bw, "d_bw_pct": round(100.0 * (bw / base_bw - 1), 2),
            "jain_bin": f["jain_bin"]["jain_bin_mean"],
            "max_min": f["max_min"], "makespan": d["makespan"],
            "completed": d["completed"],
            "fc": {kk: vv for kk, vv in (d.get("fc") or {}).items()
                   if kk != "trace"},
        }
        out.append(row)
        print(f"  {name:<20} bw={bw:.4f} ({row['d_bw_pct']:+.2f}%) "
              f"J={row['jain_bin']:.5f} mm={row['max_min']:.4f} "
              f"ok={d['completed']}", flush=True)
    OUT.write_text(json.dumps({"k": k, "bin_w": BIN_W, "rows": out},
                              indent=2, ensure_ascii=False))
    print(f"\nwrote {OUT}")


if __name__ == "__main__":
    main()
