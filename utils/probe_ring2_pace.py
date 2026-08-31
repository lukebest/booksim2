#!/usr/bin/env python3
"""Phase 3: can a deterministic pacer hit Jain > 0.99 without losing 1%?

Acceptance is two-sided and both sides have to hold at once:

    mean Jain over 50-cycle bins  > 0.99
    total write bandwidth         within 1% of S0

The variance decomposition says this is possible in principle -- per-bin
unfairness on this fabric is 97-99% timing jitter around near-equal
long-run rates -- but the pacer has to convert bursts into an interval
process *without* ever missing a slot the ring offered. `pace_burst` and
`pace_headroom` are the two knobs that trade those against each other:
a strict bucket (burst 1, headroom 1.0) is maximally regular and most
likely to leave slots on the table, a loose one is the opposite.

Usage:
    PYTHONHASHSEED=0 python3 probe_ring2_pace.py [K] [grid ...]
"""
from __future__ import annotations

import itertools
import json
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from dse_ring2_write_fair import (BIN_W, FABRIC, W_FLITS, binned_jain,
                                  board_dir_from_inj, build_pattern,
                                  fairness_stats, run_scheme)
from rg_ring2_topo import CHI_VCS_WRITE, Ring2Topology

OUT = Path(__file__).resolve().parents[1] / "results" / "probe_ring2_pace.json"


def _grids() -> dict[str, list[dict[str, Any]]]:
    def combos(**axes):
        keys = list(axes)
        return [dict(zip(keys, vals))
                for vals in itertools.product(*(axes[k] for k in keys))]

    return {
        # The core trade: bucket depth against rate headroom.
        "shape": combos(pace_burst=[1.0, 2.0, 4.0],
                        pace_headroom=[1.0, 1.05, 1.15, 1.3]),
        # How fast to track the granted rate, and over what window.
        "track": combos(pace_burst=[1.0, 2.0], pace_headroom=[1.05, 1.15],
                        pace_window=[32, 64, 128], pace_gain=[0.1, 0.25, 0.5]),
        # Pace the responders too -- they are the binding injectors.
        "scope": combos(pace_burst=[1.0, 2.0], pace_headroom=[1.05, 1.15],
                        pace_scope=["core_only", "both"],
                        pace_vcs=[("dat",), ("dat", "rsp"),
                                  ("req", "rsp", "dat")]),
        # Bus-assisted equalisation on top of the best shape.
        "equalise": combos(pace_burst=[1.0], pace_headroom=[1.05, 1.15],
                           pace_equalise=[True], pace_tol=[0.02, 0.05, 0.1],
                           pace_trim=[0.01, 0.03]),
    }


def run_one(topo, txns, over: dict[str, Any], *, k: int, scheme: str = "S21"
            ) -> dict[str, Any]:
    cfg = dict(FABRIC)
    cfg.update(over)
    r = run_scheme(scheme, topo, txns, seed=0, cfg=cfg, quiet=True)
    inj = r.get("wr_inject_by_core") or {}
    f = fairness_stats(inj, r["makespan"], k * W_FLITS)
    jb = binned_jain(inj, BIN_W, f["t_fair"]) if f else {}
    bd = board_dir_from_inj(r.get("inj_by_hop") or {},
                            sorted(int(c) for c in inj))
    fr = [max(v["fail_cw"], v["fail_ccw"]) / max(1, min(v["fail_cw"],
                                                        v["fail_ccw"]))
          for v in bd.values()] or [0.0]
    return {
        "over": {k2: (list(v) if isinstance(v, tuple) else v)
                 for k2, v in over.items()},
        "scheme": scheme, "makespan": r["makespan"],
        "completed": r["completed"], "thr": f.get("throughput"),
        "max_min": f.get("max_min"), "bw_min": f.get("bw_min"),
        "jain_bin": jb.get("jain_bin_mean"),
        "jain_bin_ideal": jb.get("jain_bin_ideal"),
        "jain_vs_ideal": jb.get("jain_vs_ideal"),
        "jain_bin_p05": jb.get("jain_bin_p05"),
        "jain_bin_min": jb.get("jain_bin_min"),
        "fail_ratio_max": round(max(fr), 3),
        "fc": r.get("fc") or {}, "wall": r.get("wall_secs"),
    }


def main() -> None:
    k = int(sys.argv[1]) if len(sys.argv) > 1 else 2000
    want = sys.argv[2:]
    topo = Ring2Topology(n_planes=1, vcs=CHI_VCS_WRITE)
    txns = build_pattern("uniform", k=k, W=W_FLITS, seed=0)

    t0 = time.perf_counter()
    base = run_one(topo, txns, {}, k=k, scheme="S0")
    thr0 = base["thr"]
    print(f"K={k}\n  S0: thr={thr0} Jbin={base['jain_bin']} "
          f"ideal={base['jain_bin_ideal']} maxmin={base['max_min']}\n"
          f"  target: Jbin > 0.99 and thr >= {thr0 * 0.99:.4f}", flush=True)
    out: dict[str, Any] = {"k": k, "s0": base, "grids": {}}
    for name, cases in _grids().items():
        if want and name not in want:
            continue
        print(f"\n[{name}]  {len(cases)} runs", flush=True)
        rows = []
        for over in cases:
            row = run_one(topo, txns, over, k=k)
            row["thr_delta_pct"] = round(100.0 * (row["thr"] - thr0) / thr0, 2)
            row["pass"] = bool(row["jain_bin"] and row["jain_bin"] > 0.99
                               and row["thr_delta_pct"] > -1.0)
            rows.append(row)
            print(f"  {json.dumps(row['over'], sort_keys=True):<80} "
                  f"Jbin={row['jain_bin']:<9} thr={row['thr']:<8}"
                  f"({row['thr_delta_pct']:+.2f}%) mm={row['max_min']:<7} "
                  f"{'PASS' if row['pass'] else ''}", flush=True)
        rows.sort(key=lambda r: (not r["pass"], -(r["jain_bin"] or 0)))
        out["grids"][name] = rows
        print(f"  best: {rows[0]['over']} Jbin={rows[0]['jain_bin']} "
              f"thr={rows[0]['thr']} ({rows[0]['thr_delta_pct']:+.2f}%)",
              flush=True)
    out["wall_secs"] = round(time.perf_counter() - t0, 1)
    OUT.write_text(json.dumps(out, indent=1))
    print(f"\nwrote {OUT}  {out['wall_secs']}s")


if __name__ == "__main__":
    main()
