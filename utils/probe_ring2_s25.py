#!/usr/bin/env python3
"""S25: the yield actuator driven by a constant local target. No bus at all.

Everything measured so far says the two halves of the acceptance test are solved
by two *different* pieces of a controller, and that no scheme has yet held both:

  * The **target** is what fixes fairness, and it does not need to be discovered.
    The equal-rate share is a topology constant, lambda* = 2/7 txn/cycle/core, so
    4/7 DAT flits/cycle/core, 2/7 per direction. S24 pins exactly that with no
    signalling and reaches Jbin 0.9903 -- past the 0.99 line -- so the reference
    rate is demonstrably right.
  * The **actuator** is what costs bandwidth. S24 gates: a core with no credit
    declines a free slot, the slot goes empty down a hop that is already ~91%
    loaded, and nothing recovers it. That is why S24 pays 23.55% for its Jain.
    S22 yields instead: the node stands aside only when a needier node is
    starving, and `dfc_dodge` lets it send a flit that leaves the ring *before*
    the requester so its own hop stays busy. S22 keeps bandwidth (-0.15% at stock
    depth) but its deficit comes off the flow-control bus, which costs 30 cycles,
    and 30-cycle-stale means cannot regulate a 50-cycle bin: Jbin caps at 0.94.

S25 is the obvious cross: **S22's yield actuator, S24's constant target.** Each
node accrues `dfc_target` of entitlement per cycle and spends 1.0 per boarded
flit, so the deficit is a local counter, exact and zero-latency. The bus
disappears -- with it the 30-cycle rule and the 6-bit broadcast, the 10-entry
table, and the adder tree -- leaving one counter and two comparators per core.
If the diagnosis is right, this should carry S24's fairness at S22's bandwidth.

Forecast, written before running:
  * At `target = 2/7` per direction the deficit hovers near zero and requests
    fire only when a node genuinely falls behind, so I expect Jbin >= 0.97 with
    bandwidth within ~1% of S0 -- eta above 0.92, better than anything buildable
    so far.
  * `dfc_thresh` is the sensitive knob: too low and every node requests all the
    time (requests cancel, degenerating to S0), too high and it never fires.
  * The falsifier I care about: if bandwidth holds but Jbin stays near 0.94, then
    the 30-cycle bus was never the reason S22 could not regulate a 50-cycle bin,
    and the limit is the yield actuator's reach -- a yielded bubble only helps
    nodes downstream of the yielder, which is a geometric constraint no amount of
    signal freshness can fix.

Usage:
    PYTHONHASHSEED=0 python3 probe_ring2_s25.py [K]
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
OUT = ROOT / "results" / "probe_ring2_s25.json"
IDEAL = json.loads((ROOT / "results" / "ideal_ring2_cc.json").read_text())

# lambda* = 2/7 txn/cycle/core, W = 2 DAT flits per txn, split over 2 directions.
FAIR_PER_DIR = 2 / 7


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
    s0j = binned_jain(binj, BIN_W, bf.get("t_fair") or 0)["jain_bin_mean"]
    print(f"K={k}  ideal bw={r_ideal:.4f}  U={u_ideal:.4f}  "
          f"S0 thr={s0} Jbin={s0j}\n")
    print(f"{'case':<46}{'Jbin':>9}{'bw/id':>8}{'vsS0%':>8}{'eta':>8}  lines")

    rows = []
    # The target is per node per cycle, and a node feeds both directions, so the
    # whole-node entitlement is 2 x the per-direction share.
    tgt = 2 * FAIR_PER_DIR
    cases = []
    for thresh, dodge, cap in product((0.5, 1.0, 2.0, 4.0), (8, 32), (8.0,)):
        cases.append((f"S25 target{tgt:.3f} thresh{thresh} dodge{dodge}",
                      {"dfc_target": tgt, "dfc_thresh": thresh,
                       "dfc_dodge": dodge, "dfc_cap": cap}))
    # Does over-provisioning the target help, as it did for the pacer? A target
    # above the achievable rate makes every node permanently in deficit, which
    # should degenerate to S0 -- a useful control.
    for scale in (1.05, 1.15):
        cases.append((f"S25 target x{scale} thresh1.0 dodge8",
                      {"dfc_target": tgt * scale, "dfc_thresh": 1.0,
                       "dfc_dodge": 8, "dfc_cap": 8.0}))

    for name, over in cases:
        cfg = dict(FABRIC)
        # Stock queue depth: `probe_ring2_cheap` showed the deep inject queues buy
        # +0.014 eta for 56x the hardware, so S25 is measured without them.
        cfg.update({"dfc_margin": 1.0, "dfc_hold": 16, "dfc_clear": 0.0,
                    "dfc_window": 64, "itag_hold": 2})
        cfg.update(over)
        r = run_scheme("S22", topo, tx, cfg=cfg, quiet=True)
        inj = {int(c): v for c, v in (r.get("wr_inject_by_core") or {}).items()}
        f = fairness_stats(inj, r["makespan"] or 1, fpc)
        jb = binned_jain(inj, BIN_W, f.get("t_fair") or 0)
        thr, jm = f["throughput"], jb["jain_bin_mean"]
        d0 = 100 * (thr - s0) / s0
        eta = thr * jm / u_ideal
        ok = jm > 0.99 and abs(d0) < 1.0
        fc = r.get("fc") or {}
        rows.append({"case": name, "over": over, "thr": thr, "jain_bin": jm,
                     "bw_vs_ideal": round(thr / r_ideal, 5),
                     "delta_vs_s0_pct": round(d0, 2), "eta": round(eta, 5),
                     "bus_posts": fc.get("bus_posts"), "pass": ok})
        print(f"{name:<46}{jm:>9.5f}{thr / r_ideal:>8.4f}{d0:>+8.2f}{eta:>8.4f}"
              f"  {'J' if jm > 0.99 else '-'}{'B' if abs(d0) < 1 else '-'}"
              f"{'   <== PASS' if ok else ''}", flush=True)

    best = max(rows, key=lambda x: x["eta"])
    OUT.write_text(json.dumps({"k": k, "s0_thr": s0, "s0_jbin": s0j,
                               "ideal_bw": r_ideal, "u_ideal": u_ideal,
                               "fair_per_dir": FAIR_PER_DIR, "best": best,
                               "rows": rows}, indent=2))
    print(f"\nbest by eta: {best['case']}  eta={best['eta']}  "
          f"bus_posts={best['bus_posts']}")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
