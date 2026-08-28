#!/usr/bin/env python3
"""Robustness check: is the `hot` conclusion an artefact of total collapse?

On `hot` the answer came out unusually clean -- the free outstanding-cap change
delivers +0.2474 of R* and only S16 adds anything on top of it (+0.0110, reaching
99.85% of the bound). But `hot` is the extreme: all ten cores funnel into two HAs,
so the fabric's binding resource collapses onto a single ejection port and the
equal-rate bound coincides with the max-total bound. A conclusion drawn only there
could easily be a property of that collapse rather than of non-uniform traffic.

So re-run the load-bearing schemes on an *intermediate* skew: a fraction `f` of
each core's writes is redirected to the hot pair and the rest keeps the balanced
tiled walk. `f = 0` is `uniform`, `f = 1` is `hot`; this sweeps the middle. The
ideal bound is re-solved at every `f` from that mix, so `bw/R*` stays comparable
across the sweep.

What would overturn the `hot` conclusion:
  * the best cap moving with `f`, which would make cap 32 another pattern prior
    rather than the pattern-robust choice it looked like across uniform/hot;
  * S16's gain vanishing or inverting at intermediate `f`, which would mean it
    exploits full collapse rather than destination-side congestion in general;
  * some scheme that lost badly on `hot` turning positive in the middle, which
    would mean the ranking is not monotone in skew and no single fixed
    configuration can be recommended.

Usage:
    PYTHONHASHSEED=0 python3 probe_ring2_midskew.py [K]
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from dse_ring2_write_fair import (BIN_W, FABRIC, S22_CFG, W_FLITS, binned_jain,
                                  build_pattern, fairness_stats, run_scheme)
from ideal_ring2_cc import coefficients, solve_theta
from rg_ring2_topo import CHI_VCS_WRITE, Ring2Topology

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results" / "probe_ring2_midskew.json"
HOT_PAIR = (11, 13)
STOCK = {"inj_depth": FABRIC["inj_depth"],
         "dir_inj_depth": FABRIC["dir_inj_depth"]}

# The schemes the `hot` conclusion actually rests on, plus the two that lost worst
# there -- if the ranking is skew-dependent, this is where it shows.
SCHEMES: list[tuple] = [
    ("S0 (free baseline)", "S0", {}),
    ("S16 grant withhold", "S16", {}),
    ("S20 DCTCP", "S20", {}),
    ("S22 STOCK bus30 w64", "S22",
     {**S22_CFG, **STOCK, "dfc_bus_lat": 30, "dfc_window": 64,
      "dfc_dodge": 8, "dfc_margin": 3.0}),
    ("S23 fair-share pacer", "S23",
     {"fair_signal": "bus", "fair_window": 64, "fair_tol": 0.05,
      "fair_step": 0.05, "fair_bus_lat": 30}),
    ("S15 fair-share+resv", "S15", {}),
]


def build_skew(k: int, f: float) -> list:
    """Fraction `f` of every core's writes redirected to the hot HA pair."""
    tx = build_pattern("uniform", k=k, W=W_FLITS, seed=0)
    seen: dict[int, int] = defaultdict(int)
    out = []
    for t in tx:
        i = seen[t.core]
        seen[t.core] += 1
        # Deterministic stride rather than sampling, so the mix is exact.
        if f > 0 and (i % 1000) < int(round(1000 * f)):
            out.append(replace(t, ha=HOT_PAIR[i % 2]))
        else:
            out.append(t)
    return out


def r_star(topo, txns) -> float:
    cnt: dict[int, dict[int, int]] = defaultdict(lambda: defaultdict(int))
    for t in txns:
        cnt[t.core][t.ha] += 1
    mix = {c: {h: v / sum(row.values()) for h, v in sorted(row.items())}
           for c, row in sorted(cnt.items())}
    _, _, a = coefficients(topo, mix)
    return W_FLITS * float(solve_theta(a, 1.0).sum())


def main() -> None:
    k = int(sys.argv[1]) if len(sys.argv) > 1 else 1200
    topo = Ring2Topology(n_planes=1, vcs=CHI_VCS_WRITE, route="latency")
    out = {"k": k, "hot_pair": list(HOT_PAIR), "sweeps": {}}

    # (1) Is cap 32 still the right free choice at intermediate skew?
    print("=== cap sweep vs skew (S0 only), bw/R* ===")
    print(f"{'f':>6}{'R*':>8}" + "".join(f"{c:>9}" for c in (128, 64, 32, 16)))
    cap_tbl = {}
    for f in (0.0, 0.25, 0.5, 0.75, 1.0):
        tx = build_skew(k, f)
        rs = r_star(topo, tx)
        row = {}
        for cap in (128, 64, 32, 16):
            cfg = dict(FABRIC)
            cfg["core_outstanding"] = cap
            r = run_scheme("S0", topo, tx, cfg=cfg, quiet=True)
            inj = {int(c): v
                   for c, v in (r.get("wr_inject_by_core") or {}).items()}
            fs = fairness_stats(inj, r["makespan"] or 1, k * W_FLITS)
            row[cap] = round(fs["throughput"] / rs, 5)
        cap_tbl[f] = {"r_star": rs, "by_cap": row}
        print(f"{f:>6.2f}{rs:>8.4f}" + "".join(f"{row[c]:>9.4f}"
                                               for c in (128, 64, 32, 16)))
    out["sweeps"]["cap"] = cap_tbl

    # (2) Does S16's edge survive at intermediate skew, at the free cap?
    print("\n=== schemes vs skew at cap 32, bw/R* (Jbin) ===")
    print(f"{'scheme':<24}" + "".join(f"{f:>16.2f}" for f in
                                      (0.0, 0.25, 0.5, 0.75, 1.0)))
    sch_tbl: dict[str, dict] = {}
    loads = {f: (build_skew(k, f), r_star(topo, build_skew(k, f)))
             for f in (0.0, 0.25, 0.5, 0.75, 1.0)}
    for name, sch, over in SCHEMES:
        cells, row = [], {}
        for f, (tx, rs) in loads.items():
            cfg = dict(FABRIC)
            cfg.update(over)
            cfg["core_outstanding"] = 32
            try:
                r = run_scheme(sch, topo, tx, cfg=cfg, quiet=True)
            except Exception as e:                       # noqa: BLE001
                cells.append(f"{'ERR':>16}")
                row[f] = {"err": f"{type(e).__name__}: {e}"}
                continue
            inj = {int(c): v
                   for c, v in (r.get("wr_inject_by_core") or {}).items()}
            fs = fairness_stats(inj, r["makespan"] or 1, k * W_FLITS)
            jb = binned_jain(inj, BIN_W, fs.get("t_fair") or 0)
            bw = fs["throughput"] / rs
            row[f] = {"bw_vs_ideal": round(bw, 5),
                      "jain_bin": jb["jain_bin_mean"],
                      "thr": fs["throughput"]}
            cells.append(f"{bw:>10.4f}({jb['jain_bin_mean']:.2f})")
        sch_tbl[name] = row
        print(f"{name:<24}" + "".join(cells), flush=True)
    out["sweeps"]["schemes"] = sch_tbl

    OUT.write_text(json.dumps(out, indent=2, ensure_ascii=False))
    print(f"\nwrote {OUT}")


if __name__ == "__main__":
    main()
