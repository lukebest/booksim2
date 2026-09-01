#!/usr/bin/env python3
"""Second-round knob probe: the two families the first round found promising.

Round one (`probe_ring2_gapcc.py`) settled two verdicts and left two
questions.

Settled: adaptive routing (S26) is worse than S0 on both fairness axes at
every point in its grid, and hop-by-hop backpressure (S27) buys a smaller
long-run rate ratio only by paying 35-44% of the bandwidth. Neither needs a
finer grid to be judged.

Open: explicit-rate feedback (S28) reached the best fairness of any scheme in
the study -- binned Jain 0.988, max/min 1.06 -- while giving up a third of the
bandwidth, which the static `C/N` share explains: a core held down by one hop
leaves capacity at every other hop and nothing hands it back. Round two turns
on RCP's residual-capacity term, which is exactly the mechanism for handing it
back. And proactive scheduling (S29) improved as the slot got *shorter*, so
the bottom of that curve had not been found.

Usage:
    PYTHONHASHSEED=0 python3 utils/probe_ring2_gapcc2.py [K]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from dse_ring2_write_fair import (BIN_W, S28_CFG, S29_CFG, W_FLITS,
                                  build_pattern, digest, run_scheme)
from rg_ring2_topo import CHI_VCS_WRITE, Ring2Topology

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results" / "probe_ring2_gapcc2.json"


def grid() -> list[tuple[str, str, dict[str, Any]]]:
    rows: list[tuple[str, str, dict[str, Any]]] = [("S0", "S0", {}),
                                                   ("S1", "S1", {})]
    rows.append(("S28 static", "S28", {**S28_CFG, "rcp_mode": "static",
                                       "rcp_pace_burst": 2.0}))
    for al in (0.25, 0.5, 1.0):
        for burst in (1.0, 2.0, 4.0):
            rows.append((f"S28 a{al} b{burst}", "S28",
                         {**S28_CFG, "rcp_alpha": al, "rcp_pace_burst": burst,
                          "rcp_target": 1.0}))
    for g in (0.25, 1.0):
        rows.append((f"S28 g{g}", "S28", {**S28_CFG, "rcp_g": g,
                                         "rcp_pace_burst": 2.0,
                                         "rcp_target": 1.0}))
    for slot in (1, 2, 3, 4):
        for dodge in (8, 32):
            rows.append((f"S29 s{slot} d{dodge}", "S29",
                         {**S29_CFG, "tdma_slot": slot, "tdma_dodge": dodge}))
    for win in (16, 64):
        rows.append((f"S29 s2 w{win}", "S29",
                     {**S29_CFG, "tdma_slot": 2, "tdma_window": win,
                      "tdma_dodge": 32}))
    return rows


def main() -> None:
    k = int(sys.argv[1]) if len(sys.argv) > 1 else 1500
    topo = Ring2Topology(n_planes=1, vcs=CHI_VCS_WRITE, route="latency")
    txns = build_pattern("uniform", k=k, W=W_FLITS, seed=0)
    print(f"K={k}  bin={BIN_W}", flush=True)
    out, base = [], None
    for name, scheme, cfg in grid():
        raw = run_scheme(scheme, topo, txns, cfg=cfg, quiet=True)
        d = digest(raw, flits_per_core=k * W_FLITS, bin_w=BIN_W)
        f = d["fairness"]
        bw = f["throughput"]
        base = bw if base is None else base
        row = {
            "name": name, "scheme": scheme, "cfg": cfg,
            "thr": bw, "d_bw_pct": round(100.0 * (bw / base - 1), 2),
            "jain_bin": f["jain_bin"]["jain_bin_mean"],
            "max_min": f["max_min"], "makespan": d["makespan"],
            "completed": d["completed"],
            "fc": {kk: vv for kk, vv in (d.get("fc") or {}).items()
                   if kk != "trace"},
        }
        out.append(row)
        print(f"  {name:<18} bw={bw:.4f} ({row['d_bw_pct']:+.2f}%) "
              f"J={row['jain_bin']:.5f} mm={row['max_min']:.4f} "
              f"ok={d['completed']}", flush=True)
    OUT.write_text(json.dumps({"k": k, "bin_w": BIN_W, "rows": out},
                              indent=2, ensure_ascii=False))
    print(f"\nwrote {OUT}")


if __name__ == "__main__":
    main()
