#!/usr/bin/env python3
"""Can I-tag's own threshold buy fairness, with no new hardware at all?

The per-direction fabric's unfairness turned out to be *structural*, not jitter.
Four cores -- 0, 8, 10, 18, the ones sitting at the exits of the two HA-less
gaps (nodes 9 and 19) -- must board the ring's two hottest hops, so in-ring
priority starves them in exactly one direction: board failures split 54k/22k for
them against a balanced 33k/33k for the other six. Their contention-window rate
is 0.42-0.46 against 0.69-0.72, and the routing's fixed 50/50 direction split
means they cannot route around it.

That is precisely the situation I-tag exists for: a node that has waited too long
for a slot reserves one upstream. And it is *self-targeting* -- the starving
nodes are the ones whose consecutive-failure counters run long (max DAT starve
59-89 cycles at the four laggards, 21-31 at the other six), so lowering `t_inj`
fires mostly at them without any per-core state, table, or bus.

So before spending S22's controller (a dedicated flow-control bus, deficit
counters, a 32-deep inject Q) on this, the honest baseline to beat is: just turn
the threshold down. `t_inj` is a single comparator constant already in the
design.

Forecast, written before running: `t_inj` = 1 or 2 lifts binned Jain well above
the shipped 0.879 at a throughput cost far under S22's -3.8%, because it moves
slots to the starved nodes instead of throttling the fast ones. It will *not*
reach 0.99 on its own -- I-tag equalises access to a hop, but the four laggards
are disadvantaged in *volume* on that hop, so a per-slot mechanism can only go
partway. If it does reach 0.99 under 1%, phase 3 needs no new hardware.

Usage:
    PYTHONHASHSEED=0 python3 probe_ring2_itag_fair.py [K]
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
       / "probe_ring2_itag_fair.json")

# `t_inj` alone, then paired with the two I-tag scoping knobs that decide how
# much of the ring one tag disturbs.
CASES = [
    ("t_inj 4（出厂）", {}),
    ("t_inj 1", {"t_inj": 1}),
    ("t_inj 2", {"t_inj": 2}),
    ("t_inj 3", {"t_inj": 3}),
    ("t_inj 8", {"t_inj": 8}),
    ("t_inj 16", {"t_inj": 16}),
    ("t_inj 1 + hold 2", {"t_inj": 1, "itag_hold": 2}),
    ("t_inj 1 + hold 4", {"t_inj": 1, "itag_hold": 4}),
    ("t_inj 2 + hold 2", {"t_inj": 2, "itag_hold": 2}),
    ("t_inj 1 + scope segment", {"t_inj": 1, "itag_scope": "segment"}),
    ("t_inj 2 + scope segment", {"t_inj": 2, "itag_scope": "segment"}),
]


def main() -> None:
    k = int(sys.argv[1]) if len(sys.argv) > 1 else 3000
    topo = Ring2Topology(n_planes=1, vcs=CHI_VCS_WRITE, route="latency")
    tx = build_pattern("uniform", k=k, W=W_FLITS, seed=0)
    fpc = k * W_FLITS
    print(f"K={k}\n", flush=True)

    rows = []
    base_thr = None
    for name, over in CASES:
        cfg = dict(FABRIC)
        cfg.update(over)
        r = run_scheme("S0", topo, tx, cfg=cfg, quiet=True)
        inj = {int(c): v for c, v in (r.get("wr_inject_by_core") or {}).items()}
        f = fairness_stats(inj, r["makespan"] or 1, fpc)
        jb = binned_jain(inj, BIN_W, f.get("t_fair") or 0)
        thr = f["throughput"]
        if base_thr is None:
            base_thr = thr
        rows.append({
            "case": name, "over": over, "thr": thr,
            "delta_pct": round(100 * (thr - base_thr) / base_thr, 2),
            "jain_bin": jb["jain_bin_mean"], "maxmin": f["max_min"],
            "bw_min": f["bw_min"], "bw_max": f["bw_max"],
            "n_itag_raised": r.get("n_itag_raised"),
            "n_itag_yield": r.get("n_itag_yield"),
            "defl": r.get("n_deflections"),
        })
        x = rows[-1]
        print(f"  {name:<26} Jbin={x['jain_bin']:<9} thr={thr:<8} "
              f"({x['delta_pct']:+.2f}%) mm={x['maxmin']:<8} "
              f"itag={x['n_itag_raised']:,}/{x['n_itag_yield']:,} "
              f"defl={x['defl']:,}", flush=True)

    OUT.write_text(json.dumps({"k": k, "rows": rows}, indent=2))
    print(f"\nwrote {OUT}")


if __name__ == "__main__":
    main()
