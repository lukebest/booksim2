#!/usr/bin/env python3
"""Total write bandwidth on a fixed non-uniform load, for every prior-free scheme.

The study so far graded schemes on `uniform`, where the binding resource is a ring
hop and S0 already reaches 94% of the ideal -- so there was only ~4.3% of bandwidth
to argue about and fairness was the whole story. That is the easy regime. This
probe moves to a fixed *non-uniform* load and asks the bandwidth question there.

The load is `hot`: all ten cores write into the two-node memory cluster at HAs
11/13, deterministic and fixed (no seed, no sampling). Its ideal bound is re-solved
from its own destination mix rather than reused from `uniform`, which matters
because the bottleneck moves: on `hot` the two hot HAs' *ejection* ports cap the
whole fabric at 2 DAT flit/cycle, so the equal-rate bound and the max-total bound
coincide -- there is no fairness/bandwidth exchange rate left to trade, and any
shortfall against R* is pure waste rather than a fairness premium. That makes it
the cleanest possible setting for a bandwidth-only comparison.

**Every knob is frozen at the value tuned on `uniform`. Nothing is re-tuned.**
That is the point, not a shortcut: a scheme qualifies as prior-free only if one
fixed configuration keeps working when the traffic changes. Re-tuning per pattern
would smuggle the pattern prior back in through the parameter file, which is what
disqualified S24/S25 in the first place.

Roster: every scheme still in the registry, i.e. the `sweep_ring2_cc_family` cases
plus the two extra registry points (S22 at stock queue depth, S23), and minus the
withdrawn rate-pinned pair.

Forecast, written before running:
  * S0 leaves a lot on the table here -- far more than the 4.3% it leaves on
    `uniform` -- because a full ejection port makes flits fail to leave, take an
    E-tag, and circulate, and every extra lap consumes hop bandwidth that a fresh
    flit could have used. Congestion control should be worth *more* in this regime,
    not less, and the ranking should not resemble the `uniform` ranking.
  * The schemes that should win are the ones whose signal comes from the actually
    congested resource: DCTCP/DCQCN mark at the full ejection buffer, so they cut
    the injections that were going to fail. Fairness-driven schemes (S22, S23, S15,
    S21+eq) equalise *sources*, which is the wrong control target here -- the
    imbalance is at the destination, and every source is equally entitled.
  * Falsifier: if the `hot` ranking simply reproduces the `uniform` ranking, then
    pattern robustness is not a discriminating axis and one plot would have done
    for both.

Usage:
    PYTHONHASHSEED=0 python3 probe_ring2_hotbw.py [K]
"""
from __future__ import annotations

import json
import sys
import traceback
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from dse_ring2_write_fair import (BIN_W, FABRIC, S1_CFG, S22_CFG, W_FLITS,
                                  binned_jain, build_pattern, fairness_stats,
                                  run_scheme)
from ideal_ring2_cc import coefficients, solve_max_total, solve_theta
from pareto_ring2_cc import hw_cost
from rg_ring2_topo import CHI_VCS_WRITE, Ring2Topology
from sweep_ring2_cc_family import CASES

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results" / "probe_ring2_hotbw.json"

# S22 at stock queue depth and S23, the two registry points that live outside
# `CASES`. Specs copied from `pareto_seed.HW` so the two plots use one cost model.
HW_S22STOCK = {"bus_bits": 6, "table_entries": 10, "table_bits": 8,
               "counter_bits": 10,
               "arith": {"addtree10": 1, "add": 2, "cmp": 8}}
HW_S23 = {"bus_bits": 6, "table_entries": 10, "table_bits": 8,
          "counter_bits": 24, "counter_scope": 10,
          "arith": {"addtree10": 1, "add": 2, "cmp": 3}, "arith_scope": 10}
EXTRA: list[tuple] = [
    ("S22 deficit-yield STOCK depth bus30 w64", "S22",
     {**S22_CFG, "inj_depth": FABRIC["inj_depth"],
      "dir_inj_depth": FABRIC["dir_inj_depth"], "dfc_bus_lat": 30,
      "dfc_window": 64, "dfc_dodge": 8, "dfc_margin": 3.0},
     ("recv", "arb", "bus"), HW_S22STOCK, True),
    ("S23 fair-share pacer (bus win64 tol0.05 step0.05)", "S23",
     {"fair_signal": "bus", "fair_window": 64, "fair_tol": 0.05,
      "fair_step": 0.05, "fair_bus_lat": 30},
     ("sender", "rate", "bus"), HW_S23, True),
]


def mix_of(txns) -> dict[int, dict[int, float]]:
    cnt: dict[int, dict[int, int]] = defaultdict(lambda: defaultdict(int))
    for t in txns:
        cnt[t.core][t.ha] += 1
    return {c: {h: v / sum(row.values()) for h, v in sorted(row.items())}
            for c, row in sorted(cnt.items())}


def ideal_for(topo: Ring2Topology, txns) -> dict:
    """Equal-rate and max-total bounds for this exact workload."""
    cores, names, a = coefficients(topo, mix_of(txns))
    lam_f = solve_theta(a, 1.0)
    lam_m = solve_max_total(a)
    load = a.T @ lam_f
    return {"r_fair": W_FLITS * float(lam_f.sum()),
            "r_max": W_FLITS * float(lam_m.sum()),
            "lam_star": float(lam_f.mean()),
            "binding": names[int(load.argmax())],
            "n_cores": len(cores)}


def run_pass(topo, tx, fpc, r_star: int | float, cap: int) -> list[dict]:
    """The whole roster at one value of the per-core outstanding cap."""
    print(f"\n=== core_outstanding = {cap} "
          f"{'(study default)' if cap == 128 else '(free pattern-robust fix)'} "
          f"===")
    print(f"{'scheme':<44}{'trig':>6}{'thr':>9}{'bw/R*':>8}{'vsS0':>9}"
          f"{'Jbin':>9}{'etag':>8}{'hw(FF-eq)':>11}  bus")
    rows, s0 = [], None
    for name, scheme, over, tax, hw, bus_ok in list(CASES) + EXTRA:
        cfg = dict(FABRIC)
        if scheme == "S1T":
            cfg.update(S1_CFG)
        cfg.update(over)
        cfg["core_outstanding"] = cap
        try:
            r = run_scheme(scheme, topo, tx, cfg=cfg, quiet=True)
        except Exception:
            print(f"{name:<44}  FAILED")
            traceback.print_exc(limit=1)
            continue
        inj = {int(c): v for c, v in (r.get("wr_inject_by_core") or {}).items()}
        f = fairness_stats(inj, r["makespan"] or 1, fpc)
        jb = binned_jain(inj, BIN_W, f.get("t_fair") or 0)
        thr, jm = f["throughput"], jb["jain_bin_mean"]
        if s0 is None:
            s0 = thr
        cost, brk = hw_cost(hw)
        rows.append({"name": name, "scheme": scheme, "cap": cap,
                     "driver": tax[0], "control": tax[1], "trigger": tax[2],
                     "thr": thr, "bw_vs_ideal": round(thr / r_star, 5),
                     "delta_vs_s0_pct": round(100 * (thr - s0) / s0, 2),
                     "jain_bin": jm, "hw_cost": cost, "hw_breakdown": brk,
                     "bus_rule_ok": bus_ok, "makespan": r["makespan"],
                     "n_etag": r.get("n_etag_raised", 0),
                     "n_mark": r.get("n_mark"), "n_win_down": r.get("n_win_down"),
                     "n_win_up": r.get("n_win_up")})
        print(f"{name:<44}{tax[2]:>6}{thr:>9.4f}{thr / r_star:>8.4f}"
              f"{100 * (thr - s0) / s0:>+8.2f}%{jm:>9.5f}"
              f"{r.get('n_etag_raised', 0):>8}{cost:>11,}"
              f"  {'Y' if bus_ok else 'N'}", flush=True)
    return rows


def main() -> None:
    k = int(sys.argv[1]) if len(sys.argv) > 1 else 2000
    topo = Ring2Topology(n_planes=1, vcs=CHI_VCS_WRITE, route="latency")
    tx = build_pattern("hot", k=k, W=W_FLITS, seed=0)
    fpc = k * W_FLITS

    idl = ideal_for(topo, tx)
    r_star = idl["r_fair"]
    print(f"K={k}  load=hot (all writes -> HAs 11/13)")
    print(f"ideal: R*_equal={r_star:.4f}  R_max={idl['r_max']:.4f}  "
          f"lambda*={idl['lam_star']:.4f}  binding={idl['binding']}")
    print(f"       equal-rate costs "
          f"{100 * (1 - r_star / idl['r_max']):.2f}% of max-total")

    # Two baselines, because the first pass showed the whole ranking is an
    # artefact of the cap rather than of the controllers. 128 is what the study
    # has been using; 32 is the best *single* cap across both patterns, chosen
    # without reference to either, and it costs no hardware at all.
    out = {"k": k, "load": "hot", "ideal": idl, "passes": {}}
    for cap in (128, 32):
        out["passes"][str(cap)] = run_pass(topo, tx, fpc, r_star, cap)

    OUT.write_text(json.dumps(out, indent=2, ensure_ascii=False))
    print(f"\nwrote {OUT}")


if __name__ == "__main__":
    main()
