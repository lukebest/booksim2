#!/usr/bin/env python3
"""Tune S23: fair-share deterministic pacing, in-band vs 30-cycle bus.

Everything is scored against the ideal controller (`ideal_ring2_cc.json`), on the
two axes kept separate: bandwidth against R*_fair = 40/7, and per-bin Jain
against what an ideal deterministic scheduler would score on the *same* delivered
flit count.

Forecast, written before running:
  * The in-band signal does at least as well as the bus. It carries the same
    fair-share reference with zero delay and zero wires, and the quantity is
    persistent so neither variant should be delay-limited at `window >= 64`.
  * S23 clears Jain 0.99 -- it is the first scheme here that both moves rate
    (fair-share trim, which 3.2.2 says is mandatory) and regularises timing
    (burst = 1 interval pacing, which the >0.99 line requires).
  * Bandwidth lands between S21's -26% and S22's -0.4%, and I expect much closer
    to S22: trimming only the cores that are *ahead* frees exactly the upstream
    slots the four starved cores are waiting for, so most of what the fast cores
    give up should be recovered rather than lost. A small net loss is still
    likely because the pacer refuses slots that a bufferless ring offers once.
  * `fair_tol` is the main lever: tight tol equalises harder and costs more.
  * Falsifier: if no setting clears Jain 0.97, then per-direction interval pacing
    is not enough regularity on its own and the trigger has to move to the
    arbitration layer (S22's yield) rather than the injection gate.

Usage:
    PYTHONHASHSEED=0 python3 probe_ring2_s23.py [K]
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
OUT = ROOT / "results" / "probe_ring2_s23.json"
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
    s0 = bf["throughput"]
    print(f"K={k}  ideal bw={r_ideal:.4f} U={u_ideal:.4f} | "
          f"S0 thr={s0} ({s0 / r_ideal:.4f} of ideal)\n", flush=True)
    print(f"{'case':<46}{'Jbin':>9}{'J/ideal':>9}{'bw/ideal':>10}"
          f"{'vs S0':>8}{'eta':>8}  lines")

    rows = []
    cases = []
    for sig, win, tol, step in product(("inband", "bus"), (64, 128),
                                       (0.02, 0.05, 0.10), (0.05,)):
        cases.append((f"{sig} win{win} tol{tol} step{step}",
                      {"fair_signal": sig, "fair_window": win,
                       "fair_tol": tol, "fair_step": step}))
    for name, over in cases:
        cfg = dict(FABRIC)
        cfg.update(over)
        r = run_scheme("S23", topo, tx, cfg=cfg, quiet=True)
        inj = {int(c): v for c, v in (r.get("wr_inject_by_core") or {}).items()}
        f = fairness_stats(inj, r["makespan"] or 1, fpc)
        jb = binned_jain(inj, BIN_W, f.get("t_fair") or 0)
        thr, jm = f["throughput"], jb["jain_bin_mean"]
        jrel = jm / (jb.get("jain_bin_ideal") or 1.0)
        bwrel = thr / r_ideal
        eta = thr * jm / u_ideal
        d0 = 100 * (thr - s0) / s0
        ok = jm > 0.99 and abs(d0) < 1.0
        rows.append({"case": name, "over": over, "thr": thr, "jain_bin": jm,
                     "jain_vs_ideal": round(jrel, 5),
                     "bw_vs_ideal": round(bwrel, 5),
                     "delta_vs_s0_pct": round(d0, 2), "eta": round(eta, 5),
                     "maxmin": f["max_min"], "pass": ok})
        print(f"{name:<46}{jm:>9.5f}{jrel:>9.4f}{bwrel:>10.4f}"
              f"{d0:>+8.2f}{eta:>8.4f}  "
              f"{'J' if jm > 0.99 else '-'}{'B' if abs(d0) < 1 else '-'}"
              f"{'   <== PASS' if ok else ''}", flush=True)

    rows.sort(key=lambda r: -r["eta"])
    print(f"\nbest eta: {rows[0]['case']}  eta={rows[0]['eta']}")
    npass = sum(1 for r in rows if r["pass"])
    print(f"{npass}/{len(rows)} pass both lines")
    OUT.write_text(json.dumps({"k": k, "s0_thr": s0, "ideal_bw": r_ideal,
                               "u_ideal": u_ideal, "rows": rows}, indent=2))
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
