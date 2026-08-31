#!/usr/bin/env python3
"""Is S22's 56x hardware actually load-bearing at a 30-cycle bus?

Two questions, both about whether the expensive parts of the design are still
buying anything now that the operating point has moved.

**1. S22's inject queues.** On the FF-equivalent accounting, S22 costs 1,198,560
against S1's 21,220 -- a factor of 56 -- and essentially all of it is queue SRAM:
`dir_inj_depth` 8 -> 32 and `inj_depth` 12 -> 32, at ~288 bits per entry. Those
depths exist for one reason, to give `dfc_dodge` candidates to overtake with. But
the justification was measured at `dfc_bus_lat = 1`, where the controller steers
on instantaneous deficits and needs a rich choice of flits every cycle. At the
mandated 30 cycles the controller runs on a 32-cycle window and a much smoother
signal, so the look-ahead may well be dead weight. If S22 holds up at stock
depth, it stops being the most expensive point on the plot and becomes one of the
cheapest, which would reshape the whole frontier.

**2. The gate actuator's real frontier.** S24 pins each core at the fair-share
rate. With `fair_burst = 1` it discards unused entitlement, which is what makes
injection a strict interval process (Jain 0.9903) and also what throws bandwidth
away (0.72 of ideal). Raising the burst lets a core bank missed slots and take
them later -- less waste, less regularity. Sweeping the burst out to 16 maps that
tradeoff to its end, which is the honest characterisation of what an
injection-gating actuator can and cannot do on a bufferless ring.

Forecast, written before running:
  * S22 at stock depth with `dodge = 0` loses noticeably less than the 2.18%
    measured at `bus_lat = 1` -- I expect under 1% -- because a 32-cycle window
    does not need per-cycle candidate choice.
  * Some intermediate `dodge` (8) at stock depth is within noise of the deep-queue
    point, making the deep queues unjustifiable under the 30-cycle rule.
  * The burst sweep is monotone in both directions and never passes both lines:
    bandwidth rises toward S0 as burst grows while Jain falls below 0.99 well
    before that. If it *did* pass, the gate actuator would be the answer and the
    yield actuator unnecessary.
  * Falsifier for (1): stock depth costing more than ~2% would mean the queue
    depth is genuinely load-bearing and S22 stays expensive.

Usage:
    PYTHONHASHSEED=0 python3 probe_ring2_cheap.py [K]
"""
from __future__ import annotations

import json
import sys
from itertools import product
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from dse_ring2_write_fair import (BIN_W, FABRIC, S22_CFG, W_FLITS, binned_jain,
                                  build_pattern, fairness_stats,
                                  jain_ideal_bin, run_scheme)
from rg_ring2_topo import CHI_VCS_WRITE, Ring2Topology

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results" / "probe_ring2_cheap.json"
IDEAL = json.loads((ROOT / "results" / "ideal_ring2_cc.json").read_text())
STOCK = {"inj_depth": FABRIC["inj_depth"],
         "dir_inj_depth": FABRIC["dir_inj_depth"]}


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
    print(f"K={k}  ideal bw={r_ideal:.4f} U={u_ideal:.4f}  S0 thr={s0}\n")
    print(f"{'case':<44}{'Jbin':>9}{'bw/id':>8}{'vsS0%':>8}{'eta':>8}  lines")

    rows = []

    def run(name: str, scheme: str, over: dict, group: str) -> None:
        cfg = dict(FABRIC)
        cfg.update(over)
        r = run_scheme(scheme, topo, tx, cfg=cfg, quiet=True)
        inj = {int(c): v for c, v in (r.get("wr_inject_by_core") or {}).items()}
        f = fairness_stats(inj, r["makespan"] or 1, fpc)
        jb = binned_jain(inj, BIN_W, f.get("t_fair") or 0)
        thr, jm = f["throughput"], jb["jain_bin_mean"]
        d0 = 100 * (thr - s0) / s0
        eta = thr * jm / u_ideal
        ok = jm > 0.99 and abs(d0) < 1.0
        rows.append({"case": name, "group": group, "over": over, "thr": thr,
                     "jain_bin": jm, "bw_vs_ideal": round(thr / r_ideal, 5),
                     "jain_vs_ideal": round(jm / (jb["jain_bin_ideal"] or 1), 5),
                     "delta_vs_s0_pct": round(d0, 2), "eta": round(eta, 5),
                     "pass": ok})
        print(f"{name:<44}{jm:>9.5f}{thr / r_ideal:>8.4f}{d0:>+8.2f}{eta:>8.4f}"
              f"  {'J' if jm > 0.99 else '-'}{'B' if abs(d0) < 1 else '-'}"
              f"{'   <== PASS' if ok else ''}", flush=True)

    # -- 1. S22 with the deep queues taken away ------------------------------
    b30 = {**S22_CFG, "dfc_bus_lat": 30, "dfc_margin": 3.0}
    run("S22 deep 32/32 dodge32 w32 (reference)", "S22",
        {**b30, "dfc_window": 32}, "s22")
    for dodge, win in product((0, 8), (32, 64)):
        run(f"S22 STOCK 12/8 dodge{dodge} w{win}", "S22",
            {**b30, **STOCK, "dfc_dodge": dodge, "dfc_window": win}, "s22")
    # Deep queues but no look-ahead: separates "the queue" from "the dodge".
    run("S22 deep 32/32 dodge0 w32", "S22",
        {**b30, "dfc_window": 32, "dfc_dodge": 0}, "s22")

    # -- 2. the gate actuator's frontier, out to a deep bank -----------------
    for burst in (1.0, 2.0, 4.0, 8.0, 16.0):
        run(f"S24 rate-pinned burst{burst}", "S23",
            {"fair_init": 0.2857, "fair_floor": 0.2857, "fair_step": 0.0,
             "fair_burst": burst, "fair_signal": "inband",
             "fair_window": 1_000_000_000}, "s24")

    OUT.write_text(json.dumps({"k": k, "s0_thr": s0, "ideal_bw": r_ideal,
                               "u_ideal": u_ideal, "rows": rows}, indent=2))
    print(f"\nwrote {OUT}")


if __name__ == "__main__":
    main()
