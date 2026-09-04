#!/usr/bin/env python3
"""Re-pick S16's overcommit for the current `core_outstanding`.

S16 only acts by *withholding* a grant, so its budget has to sit below the
number of requests a completer actually keeps in flight. That occupancy is
RTT-limited, not cap-limited, once `core_outstanding` covers the worst-case
write RTT. Forecast: the eta peak stays near 16; bandwidth falls off below
~10 as the completer starves itself, and binned Jain collapses above ~24 as
the budget stops binding. Falsifier: no interior point beats S0 on both axes.

Usage:
    PYTHONHASHSEED=0 python3 probe_ring2_s16_oc.py [K]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from dse_ring2_write_fair import (BIN_W, CORE_OUTSTANDING_WR, FABRIC, W_FLITS,
                                  binned_jain, build_pattern, fairness_stats,
                                  jain_ideal_bin, run_scheme)
from rg_ring2_topo import CHI_VCS_WRITE, Ring2Topology

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results" / "probe_ring2_s16_oc.json"
IDEAL = json.loads((ROOT / "results" / "ideal_ring2_cc.json").read_text())
POINTS = (4, 6, 8, 10, 12, 16, 20, 24, 32, 48, 64)


def main() -> None:
    k = int(sys.argv[1]) if len(sys.argv) > 1 else 2000
    n, r_ideal = IDEAL["n_cores"], IDEAL["r_fair"]
    u_ideal = r_ideal * jain_ideal_bin(int(round(r_ideal * BIN_W)), n)
    topo = Ring2Topology(n_planes=1, vcs=CHI_VCS_WRITE, route="latency")
    tx = build_pattern("uniform", k=k, W=W_FLITS, seed=0)
    fpc = k * W_FLITS

    print(f"K={k}  core_outstanding={CORE_OUTSTANDING_WR}  bin={BIN_W}")
    print(f"{'overcommit':>11}{'thr':>9}{'bw/R*':>8}{'Jbin':>9}{'eta':>8}"
          f"{'max/min':>9}")
    rows = []
    for oc in (None,) + POINTS:
        cfg = dict(FABRIC)
        if oc is not None:
            cfg["overcommit"] = oc
        r = run_scheme("S0" if oc is None else "S16", topo, tx, cfg=cfg,
                       quiet=True)
        inj = {int(c): v for c, v in (r.get("wr_inject_by_core") or {}).items()}
        f = fairness_stats(inj, r["makespan"] or 1, fpc)
        jb = binned_jain(inj, BIN_W, f.get("t_fair") or 0)
        thr, jm = f["throughput"], jb["jain_bin_mean"]
        rows.append({"overcommit": oc, "thr": thr,
                     "bw_vs_ideal": round(thr / r_ideal, 5), "jain_bin": jm,
                     "eta": round(thr * jm / u_ideal, 5),
                     "max_min": f["max_min"], "makespan": r["makespan"]})
        print(f"{'S0' if oc is None else oc:>11}{thr:>9.4f}"
              f"{thr / r_ideal:>8.4f}{jm:>9.5f}{thr * jm / u_ideal:>8.4f}"
              f"{f['max_min']:>9.4f}", flush=True)

    best = max((r for r in rows if r["overcommit"]), key=lambda r: r["eta"])
    OUT.write_text(json.dumps(
        {"k": k, "core_outstanding": CORE_OUTSTANDING_WR, "bin_w": BIN_W,
         "rows": rows, "best_overcommit": best["overcommit"]},
        indent=2, ensure_ascii=False))
    print(f"\nbest overcommit = {best['overcommit']} (eta {best['eta']:.4f})")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
