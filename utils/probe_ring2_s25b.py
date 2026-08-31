#!/usr/bin/env python3
"""S25, second pass: close the 4% bandwidth gap without giving back the Jain.

The first sweep put S25 at Jbin 0.981 / -4.12%, which already settles the
actuator question -- at matched fairness the yield costs 4.1% where S24's gate
costs 10.4%, so standing aside for a named neighbour is ~2.5x cheaper than
declining a slot outright. It also showed `dfc_dodge` is inert at this operating
point: 8 and 32 gave bit-identical results, so the look-ahead and the 32-deep
inject queues it needs are both dead weight, and dodge is pinned to 0 here.

What is left is the 4%. Two suspects, both about a request being too blunt an
instrument rather than about the target being wrong:

  1. `dfc_hold` (16). A request blocks *every* upstream injector in scope until it
     is satisfied or expires. A requester starved by transit rather than by its
     neighbours therefore idles upstream nodes for up to 16 cycles for nothing.
     Shortening the hold caps that waste.
  2. `dfc_thresh` / `dfc_margin`. Monotone improvement down to 0.5 in the first
     sweep says requests were firing too late; by the time a node is 0.5 flits
     behind the bin is already skewed. Below 0.5 the risk flips -- everyone
     requests at once, the requests cancel, and it degenerates to S0.

Forecast: the best point lands near thresh 0.25, hold 4-8, and I expect roughly
-2% at Jbin ~0.98, eta ~0.91. I do not expect to reach both lines at once; the
reason is geometric and stated in the S25 write-up -- a yielded bubble only
reaches nodes *downstream* of the yielder, so rate cannot be moved to an
arbitrary starved core no matter how fresh or fine-grained the signal is.

Usage:
    PYTHONHASHSEED=0 python3 probe_ring2_s25b.py [K]
"""
from __future__ import annotations

import json
import sys
from itertools import product
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from dse_ring2_write_fair import (BIN_W, FABRIC, W_FLITS, binned_jain,
                                  build_pattern, fairness_stats,
                                  jain_ideal_bin, run_scheme)
from rg_ring2_topo import CHI_VCS_WRITE, Ring2Topology

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results" / "probe_ring2_s25b.json"
IDEAL = json.loads((ROOT / "results" / "ideal_ring2_cc.json").read_text())
TGT = 4 / 7          # 2 x lambda* x W / 2 directions, per node per cycle


def main() -> None:
    k = int(sys.argv[1]) if len(sys.argv) > 1 else 2000
    topo = Ring2Topology(n_planes=1, vcs=CHI_VCS_WRITE, route="latency")
    tx = build_pattern("uniform", k=k, W=W_FLITS, seed=0)
    fpc = k * W_FLITS
    n, r_ideal = IDEAL["n_cores"], IDEAL["r_fair"]
    u_ideal = r_ideal * jain_ideal_bin(int(round(r_ideal * BIN_W)), n)

    base = run_scheme("S0", topo, tx, cfg=dict(FABRIC), quiet=True)
    binj = {int(c): v for c, v in (base.get("wr_inject_by_core") or {}).items()}
    bf = fairness_stats(binj, base["makespan"] or 1, fpc)
    s0 = bf["throughput"]
    print(f"K={k}  ideal bw={r_ideal:.4f}  U={u_ideal:.4f}  S0 thr={s0}\n")
    print(f"{'case':<44}{'Jbin':>9}{'bw/id':>8}{'vsS0%':>8}{'eta':>8}  lines")

    rows = []
    cases = []
    for thresh, hold in product((0.1, 0.25, 0.5), (4, 8, 16)):
        cases.append((f"thresh{thresh} hold{hold} margin1.0",
                      {"dfc_thresh": thresh, "dfc_hold": hold,
                       "dfc_margin": 1.0}))
    # Margin decides whether a yield is worth it: only stand aside for someone
    # meaningfully further behind than you.
    for margin in (0.0, 2.0, 4.0):
        cases.append((f"thresh0.25 hold8 margin{margin}",
                      {"dfc_thresh": 0.25, "dfc_hold": 8,
                       "dfc_margin": margin}))
    # Backoff: after standing down, stay quiet a while so a node starved by
    # transit stops re-asserting every cycle.
    for bo in (8, 32):
        cases.append((f"thresh0.25 hold8 backoff{bo}",
                      {"dfc_thresh": 0.25, "dfc_hold": 8, "dfc_margin": 1.0,
                       "dfc_backoff": bo}))

    for name, over in cases:
        cfg = dict(FABRIC)
        cfg.update({"dfc_target": TGT, "dfc_dodge": 0, "dfc_cap": 8.0,
                    "dfc_clear": 0.0, "dfc_window": 64, "itag_hold": 2})
        cfg.update(over)
        r = run_scheme("S22", topo, tx, cfg=cfg, quiet=True)
        inj = {int(c): v for c, v in (r.get("wr_inject_by_core") or {}).items()}
        f = fairness_stats(inj, r["makespan"] or 1, fpc)
        jb = binned_jain(inj, BIN_W, f.get("t_fair") or 0)
        thr, jm = f["throughput"], jb["jain_bin_mean"]
        d0 = 100 * (thr - s0) / s0
        eta = thr * jm / u_ideal
        ok = jm > 0.99 and abs(d0) < 1.0
        rows.append({"case": name, "over": over, "thr": thr, "jain_bin": jm,
                     "bw_vs_ideal": round(thr / r_ideal, 5),
                     "delta_vs_s0_pct": round(d0, 2), "eta": round(eta, 5),
                     "pass": ok})
        print(f"{name:<44}{jm:>9.5f}{thr / r_ideal:>8.4f}{d0:>+8.2f}{eta:>8.4f}"
              f"  {'J' if jm > 0.99 else '-'}{'B' if abs(d0) < 1 else '-'}"
              f"{'   <== PASS' if ok else ''}", flush=True)

    best = max(rows, key=lambda x: x["eta"])
    OUT.write_text(json.dumps({"k": k, "s0_thr": s0, "ideal_bw": r_ideal,
                               "u_ideal": u_ideal, "target": TGT,
                               "best": best, "rows": rows}, indent=2))
    print(f"\nbest by eta: {best['case']}  eta={best['eta']}")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
