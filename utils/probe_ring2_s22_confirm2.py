#!/usr/bin/env python3
"""Confirm the S22 + I-tag operating point at the official K, not at K=3000.

The cross grid found two configs clearing both phase-3 lines at K=3000
(`dfc_margin` 3 with `itag_hold` 2 or 4: Jain 0.99109 / 0.99035 at -0.94%). That
is not yet a result. This round has already produced one reversal from trusting a
short run -- the eject-side elimination held at K=6000 inside a retry storm and
flipped once the tracker stopped binding -- so a short-K pass is a candidate, not
a conclusion.

Two things can move with K. The drain tail is a fixed fraction of a longer run,
so the contention window that Jain is measured over grows; and S22's deficit
counters have longer to settle, which usually helps fairness and costs a little
bandwidth. Both acceptance lines are therefore live.

Forecast: `margin=3, hold=2` holds its Jain above 0.99 at the official K (the
deficit mechanism gets *more* accurate with more samples, not less) but its
bandwidth cost grows past 1%, because the -0.94% at K=3000 has no headroom and
every previous re-tune has cost slightly more bandwidth at larger K. If that
happens the `margin=4, hold=2` row is the fallback -- it was -0.04% with Jain
0.98934, so it has bandwidth headroom to trade.

Usage:
    PYTHONHASHSEED=0 python3 probe_ring2_s22_confirm2.py [K]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from dse_ring2_write_fair import (BIN_W, FABRIC, K_PER_CORE, W_FLITS,
                                  binned_jain, build_pattern, fairness_stats,
                                  run_scheme)
from rg_ring2_topo import CHI_VCS_WRITE, Ring2Topology

OUT = (Path(__file__).resolve().parents[1] / "results"
       / "probe_ring2_s22_confirm2.json")

DEEP = {"dfc_window": 2, "dfc_bus_lat": 1, "dfc_thresh": 0.5,
        "dfc_hold": 16, "dfc_dodge": 32, "inj_depth": 32,
        "dir_inj_depth": 32}

# The four rows worth spending official-K time on: the two that passed, the one
# with the most bandwidth headroom, and the incumbent.
CASES = [
    ("margin 3 + hold 2（K=3000 过线）", {"dfc_margin": 3.0, "itag_hold": 2}),
    ("margin 3 + hold 4（K=3000 过线）", {"dfc_margin": 3.0, "itag_hold": 4}),
    ("margin 4 + hold 2（带宽余量最大）", {"dfc_margin": 4.0, "itag_hold": 2}),
    ("margin 2 + hold 4", {"dfc_margin": 2.0, "itag_hold": 4}),
    ("margin 4（出厂，无 hold）", {"dfc_margin": 4.0}),
]


def main() -> None:
    k = int(sys.argv[1]) if len(sys.argv) > 1 else K_PER_CORE
    topo = Ring2Topology(n_planes=1, vcs=CHI_VCS_WRITE, route="latency")
    tx = build_pattern("uniform", k=k, W=W_FLITS, seed=0)
    fpc = k * W_FLITS

    base = run_scheme("S0", topo, tx, cfg=dict(FABRIC), quiet=True)
    binj = {int(c): v for c, v in (base.get("wr_inject_by_core") or {}).items()}
    bf = fairness_stats(binj, base["makespan"] or 1, fpc)
    s0 = bf["throughput"]
    s0j = binned_jain(binj, BIN_W, bf.get("t_fair") or 0)["jain_bin_mean"]
    print(f"K={k}  S0 thr={s0} Jbin={s0j}\n", flush=True)

    rows = []
    for name, over in CASES:
        cfg = dict(FABRIC)
        cfg.update(DEEP)
        cfg.update(over)
        r = run_scheme("S22", topo, tx, cfg=cfg, quiet=True)
        inj = {int(c): v for c, v in (r.get("wr_inject_by_core") or {}).items()}
        f = fairness_stats(inj, r["makespan"] or 1, fpc)
        jb = binned_jain(inj, BIN_W, f.get("t_fair") or 0)
        thr = f["throughput"]
        d = round(100 * (thr - s0) / s0, 2)
        jm = jb["jain_bin_mean"]
        ok = jm > 0.99 and abs(d) < 1.0
        rows.append({"case": name, "over": over, "thr": thr, "delta_pct": d,
                     "jain_bin": jm, "maxmin": f["max_min"],
                     "bw_min": f["bw_min"], "bw_max": f["bw_max"],
                     "defl": r.get("n_deflections"),
                     "n_itag_yield": r.get("n_itag_yield"),
                     "makespan": r["makespan"], "pass": ok})
        print(f"  {name:<34} Jbin={jm:<9} thr={thr:<8} ({d:+.2f}%) "
              f"mm={f['max_min']:<8} defl={r.get('n_deflections'):,}"
              f"{'   <== PASS' if ok else ''}", flush=True)

    OUT.write_text(json.dumps({"k": k, "s0_thr": s0, "s0_jbin": s0j,
                               "rows": rows}, indent=2))
    print(f"\nwrote {OUT}")


if __name__ == "__main__":
    main()
