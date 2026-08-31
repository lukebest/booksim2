#!/usr/bin/env python3
"""Does the fair share survive a change of traffic pattern? No -- so S24/S25 go.

S24 and S25 were built on one observation: the equal-rate fair share on this
fabric is the topology constant lambda* = 2/7 txn/cycle/core, so a controller can
simply *be* correct without measuring anything, which is what let them drop the
flow-control bus entirely and put S24 on top of the buildable Pareto frontier at
1,520 FF-equivalents.

That observation has a hidden premise: lambda* is the solution of an LP whose
constraint matrix is built from the **destination mix** of the workload. It is a
constant of the fabric *and the traffic pattern together*, not of the fabric
alone. If the workload is not always balanced, the premise fails and any scheme
that hard-codes the share is mis-provisioned -- either throttling below what the
fabric could carry, or set so high that it stops regulating at all.

This probe measures that rather than asserting it. Three workloads:

  * `uniform` -- the tiled write whose channel hash balances the 8 HAs. The
    pattern lambda* = 2/7 was derived from.
  * `hot`     -- same roles, but every write funnels into the two-node cluster
    at HAs 11/13. Pure *destination* skew: cores still all want the same rate,
    but the resource loads change completely, so the LP's answer moves.
  * `skew`    -- *demand* skew: half the cores are thinned to a third of the
    transactions. Now equal rates is not even the right target for the whole run,
    only inside the contention window where every core still has work.

For each workload the ideal-CC bound is re-solved from that workload's own
destination mix, so every scheme is graded against the right reference. Then the
two prior-dependent schemes are run *with the uniform-derived constant still
hard-coded* -- which is exactly what shipping them would mean -- alongside the
adaptive survivors, which take their target from measured peer progress (S22/S15
off the bus, S23 from observed counts, S20/S16 from local congestion signals) and
so have no pattern premise to violate.

Forecast, written before running:
  * lambda* on `hot` differs from 2/7 by well over 10%: the hot cluster's
    ejection ports and the hops feeding them become the binding resources instead
    of hop:0:1:dat, and eight HAs' worth of load is folded onto two.
  * Consequently S24 pinned at 2/7 lands badly off on `hot`. I expect the error to
    be in the throttling direction (2/7 above what the hot fabric can absorb would
    make it stop regulating; below and it starves the ring), and either way its
    Jain advantage over S0 should shrink or invert.
  * The adaptive schemes should keep roughly the same *relative* standing on all
    three workloads, since none of them contains a constant derived from traffic.
  * Falsifier that would save S24/S25: if lambda* moves by only a percent or two
    across patterns, the constant is robust enough to ship with a margin and the
    schemes should stay. Then the right fix is a margin, not removal.

Usage:
    PYTHONHASHSEED=0 python3 probe_ring2_unbalanced.py [K]
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

from dse_ring2_write_fair import (BIN_W, FABRIC, S1_CFG, S22_CFG, W_FLITS,
                                  binned_jain, build_pattern, fairness_stats,
                                  jain_ideal_bin, run_scheme)
from ideal_ring2_cc import coefficients, jain, solve_theta
from rg_ring2_topo import CHI_VCS_WRITE, Ring2Topology

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results" / "probe_ring2_unbalanced.json"
# The constant S24/S25 were built on, derived from `uniform` only.
LAM_UNIFORM = 2 / 7
STOCK = {"inj_depth": FABRIC["inj_depth"],
         "dir_inj_depth": FABRIC["dir_inj_depth"]}
# Cores thinned in the `skew` workload, and by how much.
SKEW_CORES = (0, 4, 8, 12, 16)
SKEW_KEEP = 3


def mix_of(txns) -> dict[int, dict[int, float]]:
    """Destination distribution measured from an actual transaction list."""
    cnt: dict[int, dict[int, int]] = defaultdict(lambda: defaultdict(int))
    for t in txns:
        cnt[t.core][t.ha] += 1
    out = {}
    for c, row in cnt.items():
        tot = sum(row.values())
        out[c] = {h: v / tot for h, v in sorted(row.items())}
    return dict(sorted(out.items()))


def ideal_for(topo: Ring2Topology, txns) -> dict:
    """Equal-rate ideal bound for whatever pattern `txns` actually is."""
    cores, names, a = coefficients(topo, mix_of(txns))
    lam = solve_theta(a, 1.0)
    load = a.T @ lam
    return {"lam_star": float(lam.mean()),
            "r_fair": W_FLITS * float(lam.sum()),
            "binding": names[int(load.argmax())],
            "n_cores": len(cores)}


def make_skew(k: int) -> list:
    """Demand skew: half the cores keep only 1/SKEW_KEEP of their writes."""
    tx = build_pattern("uniform", k=k, W=W_FLITS, seed=0)
    seen: dict[int, int] = defaultdict(int)
    out = []
    for t in tx:
        i = seen[t.core]
        seen[t.core] += 1
        if t.core in SKEW_CORES and i % SKEW_KEEP:
            continue
        out.append(t)
    return out


def main() -> None:
    k = int(sys.argv[1]) if len(sys.argv) > 1 else 2000
    topo = Ring2Topology(n_planes=1, vcs=CHI_VCS_WRITE, route="latency")

    loads = {
        "uniform": build_pattern("uniform", k=k, W=W_FLITS, seed=0),
        "hot": build_pattern("hot", k=k, W=W_FLITS, seed=0),
        "skew": make_skew(k),
    }

    # The prior-dependent schemes carry the uniform constant, as shipping them
    # would. The adaptive ones carry no traffic constant at all.
    schemes = [
        ("S0 baseline", "S0", {}, "—"),
        ("S24 rate-pinned @uniform λ* (先验)", "S23",
         {"fair_init": LAM_UNIFORM, "fair_floor": LAM_UNIFORM,
          "fair_step": 0.0, "fair_burst": 8.0, "fair_signal": "inband",
          "fair_window": 1_000_000_000}, "静态先验"),
        ("S25 local-target @uniform λ* (先验)", "S22",
         {**S22_CFG, **STOCK, "dfc_target": 2 * LAM_UNIFORM, "dfc_dodge": 0,
          "dfc_thresh": 0.5, "dfc_cap": 8.0, "dfc_margin": 1.0,
          "dfc_hold": 16}, "静态先验"),
        ("S22 deficit-yield STOCK bus30 w64", "S22",
         {**S22_CFG, **STOCK, "dfc_bus_lat": 30, "dfc_window": 64,
          "dfc_dodge": 8, "dfc_margin": 3.0}, "自适应"),
        ("S23 fair-share pacer (bus)", "S23",
         {"fair_signal": "bus", "fair_window": 64, "fair_tol": 0.05,
          "fair_step": 0.05, "fair_bus_lat": 30}, "自适应"),
        ("S20 DCTCP", "S20", {}, "自适应"),
        ("S16 grant withhold", "S16", {}, "自适应"),
        ("S1T AIMD dir-split", "S1", dict(S1_CFG), "自适应"),
    ]

    result = {"k": k, "lam_uniform": LAM_UNIFORM, "workloads": {}}
    for wname, tx in loads.items():
        idl = ideal_for(topo, tx)
        fpc = {c: 0 for c in {t.core for t in tx}}
        for t in tx:
            fpc[t.core] += W_FLITS
        # `fairness_stats` wants one per-core flit quota; under skew they differ,
        # so pass the max and rely on t_fair to bound the contention window.
        fpc_max = max(fpc.values())
        print(f"\n=== {wname}: λ* = {idl['lam_star']:.6f} "
              f"({100 * (idl['lam_star'] / LAM_UNIFORM - 1):+.1f}% vs uniform), "
              f"R* = {idl['r_fair']:.4f}, 绑定 {idl['binding']} ===")
        print(f"{'scheme':<40}{'kind':<10}{'Jbin':>9}{'thr':>9}"
              f"{'bw/R*':>8}{'vsS0':>9}")
        rows, s0 = [], None
        for name, sch, over, kind in schemes:
            cfg = dict(FABRIC)
            cfg.update(over)
            try:
                r = run_scheme(sch, topo, tx, cfg=cfg, quiet=True)
            except Exception as e:                       # noqa: BLE001
                print(f"{name:<40}{kind:<10}  FAILED: {type(e).__name__}: {e}")
                continue
            inj = {int(c): v
                   for c, v in (r.get("wr_inject_by_core") or {}).items()}
            f = fairness_stats(inj, r["makespan"] or 1, fpc_max)
            jb = binned_jain(inj, BIN_W, f.get("t_fair") or 0)
            thr, jm = f["throughput"], jb["jain_bin_mean"]
            if s0 is None:
                s0 = thr
            d0 = 100 * (thr - s0) / s0
            rows.append({"name": name, "kind": kind, "thr": thr,
                         "jain_bin": jm, "bw_vs_ideal": round(thr / idl["r_fair"], 5),
                         "delta_vs_s0_pct": round(d0, 2)})
            print(f"{name:<40}{kind:<10}{jm:>9.5f}{thr:>9.4f}"
                  f"{thr / idl['r_fair']:>8.4f}{d0:>+8.2f}%", flush=True)
        result["workloads"][wname] = {"ideal": idl, "rows": rows,
                                      "n_txn": len(tx)}

    OUT.write_text(json.dumps(result, indent=2, ensure_ascii=False))
    print(f"\nwrote {OUT}")


if __name__ == "__main__":
    main()
