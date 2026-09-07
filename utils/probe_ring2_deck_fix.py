#!/usr/bin/env python3
"""One-shot data for the deck edit: hot S16 oc=32 @ outstanding=128,
and uniform read-only CompData=1 for S0 / S1-R / S16-R."""
from __future__ import annotations

import json
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from deck_ring2_data import READ_CASES, _read_case
from dse_ring2_write_fair import (BIN_W, FABRIC, W_FLITS, binned_jain,
                                  build_pattern, fairness_stats, run_scheme)
from rg_ring2_topo import CHI_VCS_WRITE, Ring2Topology

ROOT = Path(__file__).resolve().parents[1]
HOT_OUT = ROOT / "results" / "probe_ring2_hot_s16_oc32.json"
READ_OUT = ROOT / "results" / "probe_ring2_read_m1.json"


def run_hot_s16() -> dict:
    k = 2000
    cap, oc = 128, 32
    topo = Ring2Topology(n_planes=1, vcs=CHI_VCS_WRITE, route="latency")
    tx = build_pattern("hot", k=k, W=W_FLITS, seed=0)
    cfg = dict(FABRIC)
    cfg["core_outstanding"] = cap
    cfg["overcommit"] = oc
    r = run_scheme("S16", topo, tx, cfg=cfg, quiet=False)
    inj = {int(c): v for c, v in (r.get("wr_inject_by_core") or {}).items()}
    f = fairness_stats(inj, r["makespan"] or 1, k * W_FLITS)
    jb = binned_jain(inj, BIN_W, f.get("t_fair") or 0)
    r_star = 2.0
    row = {
        "name": "S16 grant withhold oc32",
        "scheme": "S16",
        "cap": cap,
        "overcommit": oc,
        "thr": f["throughput"],
        "bw_vs_ideal": round(f["throughput"] / r_star, 5),
        "jain_bin": jb["jain_bin_mean"],
        "makespan": r["makespan"],
        "n_etag": r.get("n_etag_raised", 0),
        "completed": r.get("completed"),
        "wall_secs": r.get("wall_secs"),
    }
    HOT_OUT.write_text(json.dumps({"k": k, "load": "hot", "row": row},
                                  indent=2, ensure_ascii=False))
    print("hot S16", row, flush=True)
    return row


def run_read_m1() -> dict:
    k, m = 5000, 1
    jobs = [(nm, sc, ov, k, m) for nm, sc, ov in READ_CASES]
    with ProcessPoolExecutor(max_workers=3) as ex:
        rows = dict(ex.map(_read_case, jobs, chunksize=1))
    out = {"k": k, "m_resp": m, "rows": rows}
    READ_OUT.write_text(json.dumps(out, indent=2, ensure_ascii=False))
    for nm, r in rows.items():
        print(f"read-m1 {nm} bw={r['throughput']:.4f} "
              f"mm={r['max_min']:.4f} J={r['jain_bin']['jain_bin_mean']:.5f}",
              flush=True)
    return out


def main() -> None:
    run_hot_s16()
    run_read_m1()
    print(f"wrote {HOT_OUT}")
    print(f"wrote {READ_OUT}")


if __name__ == "__main__":
    main()
