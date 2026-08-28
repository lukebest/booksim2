#!/usr/bin/env python3
"""S24: pin every core at the fair-share rate. The LP's central claim, tested.

The reasoning that produced this
--------------------------------
S23's two variants failed in opposite and informative ways. With the bus signal
the rate settles at 0.95-1.0 per direction, which is at or above what the fabric
offers, so `credit` is essentially always >= 1 and the pacer **never gates** --
no interval regularity, Jain stuck at 0.89-0.92. With the (buggy) in-band signal
the rate walked to the floor and the pacer gated constantly, which produced
Jain 0.982 at a third of the bandwidth.

Those two together say something sharp: on a bufferless ring, per-bin regularity
appears only when **the pacer, not the fabric, is the binding constraint**. A
controller cannot make slots arrive evenly; it can only decline the ones that
arrive early. So the operating point has to sit just *below* fabric capacity.

The LP says exactly where that is. `ideal_ring2_cc` shows the equal-rate flow is
feasible at any bandwidth up to R* = 40/7, and at S0's own 5.4681 flit/cycle
there is 4.31% slack on every hop -- so an equal-rate schedule at that bandwidth
needs no zero-slack miracle. Per core per direction that is

    lambda_dir = thr_target / (n_cores * 2 directions)

which for S0's bandwidth is 5.4681 / 20 = 0.2734 flit/cycle, and for the full R*
is 0.2857 = 2/7 / 1... i.e. 5.7143 / 20.

The striking part is what this costs to build: **the fair share is a constant.**
It follows from the topology and the destination mix, both fixed at design time,
so there is no signal to carry, no table to keep, no feedback loop and no bus --
therefore no 30-cycle penalty either. One rate register and one credit counter
per (core, VC, direction). That is strictly less hardware than S1, and less than
every adaptive scheme in the study.

The obvious objection is that a static allocation cannot adapt, which is true and
is a real limitation worth stating plainly rather than hiding: it is calibrated
for this offered load and would need the adaptive outer loop back if the
destination mix or the active-core set changed. The point of this probe is to
establish whether the *acceptance region is reachable at all*, and by what
mechanism, before paying for adaptivity.

Implementation note: no new code is needed. `fair_step = 0` freezes the slow
loop, so the rate stays at `fair_init` for the whole run and S23 degenerates
into exactly this static pacer.

Forecast, written before running:
  * There is a sweet spot near 0.27-0.28. Below it bandwidth falls roughly
    linearly with the pinned rate; above it the pacer stops binding and Jain
    collapses back toward S0's 0.88.
  * At the sweet spot Jain clears 0.99 -- strict interval pacing at a common rate
    is both perfectly fair in rate *and* sub-Poisson in timing, which is the
    combination the >0.99 line demands.
  * Bandwidth at the sweet spot lands within ~1% of S0. It cannot exceed S0 by
    much: the four gap-exit cores are starved by in-ring priority, and pacing the
    other six frees slots for them, but S0's own losses (deflection surcharge,
    binding-hop idle) are still there.
  * Falsifier: if no pinned rate clears Jain 0.99, then interval pacing at the
    injection port is not sufficient regularity on this fabric no matter how it
    is driven, and the mechanism has to move into the arbiter.

Usage:
    PYTHONHASHSEED=0 python3 probe_ring2_s24.py [K]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from dse_ring2_write_fair import (BIN_W, FABRIC, W_FLITS, binned_jain,
                                  build_pattern, fairness_stats,
                                  jain_ideal_bin, run_scheme)
from rg_ring2_topo import CHI_VCS_WRITE, Ring2Topology

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results" / "probe_ring2_s24.json"
IDEAL = json.loads((ROOT / "results" / "ideal_ring2_cc.json").read_text())


def main() -> None:
    k = int(sys.argv[1]) if len(sys.argv) > 1 else 2000
    topo = Ring2Topology(n_planes=1, vcs=CHI_VCS_WRITE, route="latency")
    tx = build_pattern("uniform", k=k, W=W_FLITS, seed=0)
    fpc = k * W_FLITS
    n = IDEAL["n_cores"]
    r_ideal = IDEAL["r_fair"]
    u_ideal = r_ideal * jain_ideal_bin(int(round(r_ideal * BIN_W)), n)

    base = run_scheme("S0", topo, tx, cfg=dict(FABRIC), quiet=True)
    binj = {int(c): v for c, v in (base.get("wr_inject_by_core") or {}).items()}
    bf = fairness_stats(binj, base["makespan"] or 1, fpc)
    s0, s0j = bf["throughput"], binned_jain(
        binj, BIN_W, bf.get("t_fair") or 0)["jain_bin_mean"]
    print(f"K={k}  ideal bw={r_ideal:.4f}  U_ideal={u_ideal:.4f}")
    print(f"S0: thr={s0} ({s0 / r_ideal:.4f} of ideal)  Jbin={s0j}")
    print(f"fair share at S0 bandwidth = {s0 / (n * 2):.4f} flit/cycle/dir, "
          f"at R* = {r_ideal / (n * 2):.4f}\n", flush=True)
    print(f"{'pinned rate/dir':>16}{'Jbin':>9}{'J/ideal':>9}{'bw/ideal':>10}"
          f"{'vs S0':>8}{'eta':>8}  lines")

    rows = []
    for pin in (0.24, 0.255, 0.265, 0.2734, 0.28, 0.2857, 0.295, 0.31, 0.34):
        cfg = dict(FABRIC)
        cfg.update({"fair_init": pin, "fair_floor": pin, "fair_step": 0.0,
                    "fair_burst": 1.0, "fair_signal": "inband",
                    "fair_window": 1_000_000_000})
        r = run_scheme("S23", topo, tx, cfg=cfg, quiet=True)
        inj = {int(c): v for c, v in (r.get("wr_inject_by_core") or {}).items()}
        f = fairness_stats(inj, r["makespan"] or 1, fpc)
        jb = binned_jain(inj, BIN_W, f.get("t_fair") or 0)
        thr, jm = f["throughput"], jb["jain_bin_mean"]
        jrel = jm / (jb.get("jain_bin_ideal") or 1.0)
        bwrel, eta = thr / r_ideal, thr * jm / u_ideal
        d0 = 100 * (thr - s0) / s0
        ok = jm > 0.99 and abs(d0) < 1.0
        rows.append({"pin": pin, "thr": thr, "jain_bin": jm,
                     "jain_vs_ideal": round(jrel, 5),
                     "bw_vs_ideal": round(bwrel, 5),
                     "delta_vs_s0_pct": round(d0, 2), "eta": round(eta, 5),
                     "maxmin": f["max_min"], "pass": ok})
        print(f"{pin:>16.4f}{jm:>9.5f}{jrel:>9.4f}{bwrel:>10.4f}"
              f"{d0:>+8.2f}{eta:>8.4f}  "
              f"{'J' if jm > 0.99 else '-'}{'B' if abs(d0) < 1 else '-'}"
              f"{'   <== PASS' if ok else ''}", flush=True)

    best = max(rows, key=lambda r: r["eta"])
    print(f"\nbest eta: pin={best['pin']} eta={best['eta']}")
    npass = sum(1 for r in rows if r["pass"])
    print(f"{npass}/{len(rows)} pass both lines")
    OUT.write_text(json.dumps({"k": k, "s0_thr": s0, "s0_jbin": s0j,
                               "ideal_bw": r_ideal, "u_ideal": u_ideal,
                               "rows": rows}, indent=2))
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
