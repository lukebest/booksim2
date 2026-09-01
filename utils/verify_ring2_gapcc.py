"""Prove the four gap-filling controllers actually engage.

The deck claims a mechanism for each of S26 / S27 / S28 / S28S / S29, and the
main results table only shows the three outcome axes. Outcome numbers cannot
distinguish "this family loses" from "this controller never fired", and those
two readings lead to opposite decisions. So this script runs each family at its
published operating point and dumps the controller's own activity counters --
detours taken, XOFF cycles asserted, bus words posted, slots yielded.

It runs at a screening K because the counters only have to be non-zero and
proportionate; the published numbers come from `deck_ring2_data.py` at the
official K.

Usage:
    PYTHONHASHSEED=0 python3 verify_ring2_gapcc.py [K]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from dse_ring2_write_fair import (BIN_W, FABRIC, S1_CFG, S26_CFG, S27_CFG,
                                  S28_CFG, S28S_CFG, S29_CFG, W_FLITS,
                                  binned_jain, build_pattern, fairness_stats,
                                  run_scheme)
from rg_ring2_topo import CHI_VCS_WRITE, Ring2Topology

OUT = (Path(__file__).resolve().parents[1] / "results"
       / "verify_ring2_gapcc.json")

CASES = [
    ("S0", "S0", {}),
    ("S1", "S1", S1_CFG),
    ("S26", "S26", S26_CFG),
    ("S27", "S27", S27_CFG),
    ("S28", "S28", S28_CFG),
    ("S28S", "S28S", S28S_CFG),
    ("S29", "S29", S29_CFG),
]

# Per family, the counters that have to be non-zero for the mechanism to have
# been exercised at all. Naming follows each module's own `fc_summary`.
LIVE_KEYS = {
    "S26": ("n_detour", "extra_hops"),
    "S27": ("n_bp_xoff", "n_bp_deny"),
    "S28": ("bus_posts", "n_rcp_deny"),
    "S28S": ("bus_posts", "n_rcp_deny"),
    "S29": ("bus_posts", "n_tdma_yield"),
}


def main() -> None:
    k = int(sys.argv[1]) if len(sys.argv) > 1 else 2000
    topo = Ring2Topology(n_planes=1, vcs=CHI_VCS_WRITE, route="latency")
    tx = build_pattern("uniform", k=k, W=W_FLITS, seed=0)
    rows = []
    print(f"K={k}  bin={BIN_W}")
    for name, scheme, cfg in CASES:
        r = run_scheme(scheme, topo, tx, cfg={**FABRIC, **cfg}, quiet=True)
        inj = {int(c): v for c, v in (r.get("wr_inject_by_core") or {}).items()}
        fair = fairness_stats(inj, r.get("makespan") or 1, k // len(inj) * W_FLITS)
        jb = binned_jain(inj, BIN_W, fair.get("t_fair") or 0)
        fc = r.get("fc") or {}
        row = {
            "name": name, "scheme": scheme, "cfg": cfg,
            "throughput": round(r["n_delivered_flits"] / r["makespan"], 4),
            "jain_bin": jb.get("jain_bin_mean"),
            "max_min": fair.get("max_min"),
            "fc": fc,
        }
        need = LIVE_KEYS.get(name, ())
        row["engaged"] = all(fc.get(x) for x in need) if need else None
        rows.append(row)
        live = "" if row["engaged"] is None else \
            ("  ENGAGED" if row["engaged"] else "  *** INERT ***")
        shown = {x: fc.get(x) for x in need}
        print(f"  {name:5s} bw={row['throughput']:.4f} "
              f"J={row['jain_bin']:.5f} max/min={row['max_min']:.4f} "
              f"{shown}{live}")
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps({"k": k, "bin_w": BIN_W, "rows": rows},
                              indent=1, ensure_ascii=False))
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
