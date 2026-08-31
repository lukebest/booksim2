#!/usr/bin/env python3
"""Pick between the two S22 finalists, and price how fragile the choice is.

Both cleared the acceptance lines at the official K, but with opposite
margins, and each one's thin margin is on a different line:

    w=2 margin=3   Jain 0.99147 (+0.00147 over the line), throughput -0.55%
    w=2 margin=4   Jain 0.99062 (+0.00062 over the line), throughput -0.04%

A seed sweep cannot break the tie: on the `uniform` pattern this study has no
stochastic component at all -- `build_pattern("uniform")` is a deterministic
tiled channel hash and `HA_RSP_JIT = 0`, so re-running seed 1 reproduces seed
0 bit for bit (checked). So the axes that can actually move the answer are the
two that change the offered load rather than the dice:

  * run length K -- a controller whose cost compounds in steady state will
    show a *worse* delta at larger K, and a controller that is only winning
    on the start-up transient will show a worse Jain.
  * traffic pattern -- `hot` funnels every write into one two-node memory
    cluster, so the deficits it has to equalise are much larger and the
    near-level swaps `dfc_margin` refuses are much rarer.

Forecast, written before this ran: the margin=4 point is the one at risk. Its
Jain headroom is 6e-4, and `hot` raises the spread of per-core deficits, so
predict margin=4 falls below 0.99 on `hot` while margin=3 holds; on K both
should hold, with the throughput delta shrinking slightly at larger K because
the start-up transient amortises. If that comes out, margin=3 is the operating
point to ship and the report should say the choice was made on pattern
robustness, not on the official-K number.

Falsified if: margin=4 holds Jain > 0.99 on every point (then it ships
instead, being 0.5% cheaper), or both fall below on `hot` (then the phase-3
claim has to be scoped to the uniform pattern explicitly).

Usage:
    PYTHONHASHSEED=0 python3 robust_ring2_s22.py
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from dse_ring2_write_fair import (BIN_W, FABRIC, K_PER_CORE, W_FLITS,
                                  binned_jain, build_pattern, fairness_stats,
                                  run_scheme)
from rg_ring2_topo import CHI_VCS_WRITE, Ring2Topology, write_paths_for_txns

OUT = (Path(__file__).resolve().parents[1] / "results"
       / "robust_ring2_s22.json")

DEEP = {"dfc_dodge": 32, "dir_inj_depth": 32, "inj_depth": 32}


def _s22(margin: float) -> dict[str, Any]:
    return {"dfc_window": 2, "dfc_bus_lat": 1, "dfc_thresh": 0.5,
            "dfc_hold": 16, "dfc_margin": margin, **DEEP}


CANDIDATES: list[tuple[str, dict[str, Any]]] = [
    ("m=3", _s22(3.0)),
    ("m=4", _s22(4.0)),
]

# (pattern, K). The official point is (uniform, K_PER_CORE); the others move
# one axis each so a failure can be attributed.
POINTS: list[tuple[str, int]] = [
    ("uniform", K_PER_CORE // 2),
    ("uniform", K_PER_CORE),
    ("hot", K_PER_CORE // 4),
]

JAIN_LINE = 0.99
THR_LINE = 1.0


def _measure(scheme: str, over: dict[str, Any], topo: Ring2Topology,
             txns: list[Any], k: int) -> dict[str, Any]:
    cfg = dict(FABRIC)
    cfg.update(over)
    r = run_scheme(scheme, topo, txns, seed=0, cfg=cfg, quiet=True)
    inj = r["wr_inject_by_core"]
    f = fairness_stats(inj, r["makespan"], k * W_FLITS)
    jb = binned_jain(inj, BIN_W, f["t_fair"])
    return {"thr": round(len(txns) * W_FLITS / max(1, r["makespan"]), 4),
            "jain_bin": round(jb["jain_bin_mean"], 5),
            "max_min": round(f["max_min"], 4), "makespan": r["makespan"]}


def main() -> None:
    topo = Ring2Topology(n_planes=1, vcs=CHI_VCS_WRITE)
    t0 = time.time()
    rows: list[dict[str, Any]] = []

    for pat, k in POINTS:
        txns = build_pattern(pat, k=k, W=W_FLITS, seed=0)
        write_paths_for_txns(topo, txns, strategy="least_occupied")
        base = _measure("S0", {}, topo, txns, k)
        print(f"\n{pat} K={k}  S0 thr={base['thr']:.4f} "
              f"Jbin={base['jain_bin']:.5f} mm={base['max_min']:.4f}",
              flush=True)
        rows.append({"pattern": pat, "k": k, "label": "S0", **base,
                     "thr_delta_pct": 0.0, "pass": None})
        for lab, over in CANDIDATES:
            m = _measure("S22", over, topo, txns, k)
            m["thr_delta_pct"] = round(
                100.0 * (m["thr"] - base["thr"]) / base["thr"], 2)
            m["pass"] = (m["jain_bin"] > JAIN_LINE
                         and abs(m["thr_delta_pct"]) < THR_LINE)
            rows.append({"pattern": pat, "k": k, "label": lab, **m})
            print(f"  S22 {lab}  thr={m['thr']:.4f} "
                  f"({m['thr_delta_pct']:+.2f}%) Jbin={m['jain_bin']:.5f} "
                  f"mm={m['max_min']:.4f} {'PASS' if m['pass'] else 'MISS'}",
                  flush=True)

    print("\nworst case per candidate, over all points")
    worst: dict[str, Any] = {}
    for lab, _o in CANDIDATES:
        rs = [r for r in rows if r["label"] == lab]
        jmin = min(r["jain_bin"] for r in rs)
        dmax = max(abs(r["thr_delta_pct"]) for r in rs)
        allp = all(r["pass"] for r in rs)
        worst[lab] = {"jain_min": jmin, "abs_thr_delta_max": dmax,
                      "pass_all": allp,
                      "jain_headroom": round(jmin - JAIN_LINE, 5),
                      "thr_headroom_pct": round(THR_LINE - dmax, 2)}
        print(f"  {lab}  Jbin_min={jmin:.5f} (+{jmin - JAIN_LINE:.5f})  "
              f"|dthr|_max={dmax:.2f}% (line 1.00%)  "
              f"{'PASS everywhere' if allp else 'MISS somewhere'}")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(
        {"points": POINTS, "bin_w": BIN_W, "rows": rows, "worst": worst,
         "note_no_seed_noise": (
             "uniform pattern is deterministic (tiled channel hash) and "
             "HA_RSP_JIT=0, so seed has no effect; verified seed 1 == seed 0"),
         "wall_secs": round(time.time() - t0, 1)}, indent=2))
    print(f"\nwrote {OUT}")


if __name__ == "__main__":
    main()
