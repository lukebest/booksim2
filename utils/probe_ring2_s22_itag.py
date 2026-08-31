#!/usr/bin/env python3
"""Last shot at phase 3's two lines: S22's controller plus I-tag's own knobs.

The re-tuned S22 grid on the per-direction fabric passes **0 of 36** configs:
the frontier runs from Jain 0.99063 at -3.78% bandwidth down to Jain 0.98494 at
-0.23%, and the two acceptance lines (Jain > 0.99, bandwidth within 1%) sit on
opposite sides of it. The reason is now understood -- the unfairness is a
*persistent* rate split, not jitter. Four cores (0, 8, 10, 18) sit at the exits
of the two HA-less gaps and must board the ring's two hottest hops, so in-ring
priority starves them in one direction (board failures 54k/22k against a balanced
33k/33k elsewhere). S22 equalises by throttling the advantaged six, which is why
every step toward Jain costs total bandwidth.

I-tag attacks the same problem from the other end: it moves a *slot* to the
starved node instead of taking rate from the fast ones, and it self-targets,
because the starving nodes are the ones whose consecutive-failure counters run
long. On its own it reached only Jain 0.924 (`probe_ring2_itag_fair.py`), but it
did so at -0.20%, i.e. essentially free -- and `itag_hold` also cut deflections
by an order of magnitude, which buys back binding-hop capacity.

So the two mechanisms are plausibly complementary rather than redundant: S22
removes the persistent rate difference, I-tag pays for part of it by recovering
slots that in-ring priority was wasting. This probe crosses them.

Forecast, written before running: the cross helps but still misses. Expect the
best combination to land near Jain 0.990-0.992 at -1.5% to -2.5%, i.e. it will
clear the Jain line but not the bandwidth line. Reasoning: `itag_hold`'s gain at
S0 was ~+0.03 Jain and ~0.2% bandwidth, and gains of this kind do not simply add
-- once S22 has already equalised the rates, the slots I-tag redirects are worth
less. If the best point *does* clear both lines, the deciding ingredient will be
the deflection reduction rather than the fairness mechanism itself, and that
should show up as a lower `defl` in the passing row.

Usage:
    PYTHONHASHSEED=0 python3 probe_ring2_s22_itag.py [K]
"""
from __future__ import annotations

import json
import sys
from itertools import product
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from dse_ring2_write_fair import (BIN_W, FABRIC, W_FLITS, binned_jain,
                                  build_pattern, fairness_stats, run_scheme)
from rg_ring2_topo import CHI_VCS_WRITE, Ring2Topology

OUT = (Path(__file__).resolve().parents[1] / "results"
       / "probe_ring2_s22_itag.json")

DEEP = {"dfc_window": 2, "dfc_bus_lat": 1, "dfc_thresh": 0.5,
        "dfc_hold": 16, "dfc_dodge": 32, "inj_depth": 32,
        "dir_inj_depth": 32}


def main() -> None:
    k = int(sys.argv[1]) if len(sys.argv) > 1 else 3000
    topo = Ring2Topology(n_planes=1, vcs=CHI_VCS_WRITE, route="latency")
    tx = build_pattern("uniform", k=k, W=W_FLITS, seed=0)
    fpc = k * W_FLITS

    base = run_scheme("S0", topo, tx, cfg=dict(FABRIC), quiet=True)
    binj = {int(c): v for c, v in (base.get("wr_inject_by_core") or {}).items()}
    bf = fairness_stats(binj, base["makespan"] or 1, fpc)
    s0 = bf["throughput"]
    print(f"K={k}  S0 thr={s0}  "
          f"Jbin={binned_jain(binj, BIN_W, bf.get('t_fair') or 0)['jain_bin_mean']}\n",
          flush=True)

    rows = []
    grid = list(product((1.0, 2.0, 3.0, 4.0), (0, 2, 4), (2, 4)))
    for margin, hold, t_inj in grid:
        cfg = dict(FABRIC)
        cfg.update(DEEP)
        cfg["dfc_margin"] = margin
        cfg["t_inj"] = t_inj
        if hold:
            cfg["itag_hold"] = hold
        r = run_scheme("S22", topo, tx, cfg=cfg, quiet=True)
        inj = {int(c): v for c, v in (r.get("wr_inject_by_core") or {}).items()}
        f = fairness_stats(inj, r["makespan"] or 1, fpc)
        jb = binned_jain(inj, BIN_W, f.get("t_fair") or 0)
        thr = f["throughput"]
        d = round(100 * (thr - s0) / s0, 2)
        jm = jb["jain_bin_mean"]
        ok = jm > 0.99 and abs(d) < 1.0
        rows.append({"margin": margin, "itag_hold": hold, "t_inj": t_inj,
                     "thr": thr, "delta_pct": d, "jain_bin": jm,
                     "maxmin": f["max_min"], "defl": r.get("n_deflections"),
                     "n_itag_yield": r.get("n_itag_yield"), "pass": ok})
        print(f"  margin={margin} hold={hold} t_inj={t_inj}  "
              f"Jbin={jm:<9} thr={thr:<8} ({d:+.2f}%) "
              f"defl={r.get('n_deflections'):,}"
              f"{'   <== PASS' if ok else ''}", flush=True)

    npass = sum(1 for r in rows if r["pass"])
    print(f"\n{npass}/{len(rows)} pass both lines")
    OUT.write_text(json.dumps({"k": k, "s0_thr": s0, "rows": rows}, indent=2))
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
