#!/usr/bin/env python3
"""Where does S0's missing write bandwidth go? Measure the binding link.

R* is a link-capacity bound: it says the run cannot be shorter than the time
the busiest directed hop needs to carry every flit routed over it. Quoting a
percentage of R* therefore does not localise anything -- the shortfall could be
idle slots on that link, slots spent on flits riding an extra lap after a
failed eject, or the link not being the binding resource at all.

This probe measures the occupancy of every directed hop per VC and reports:

  * the busiest DAT hop's measured utilisation, against the per-VC link bound
    that R* is computed from;
  * how much of that occupancy went to re-circulating (deflected) flits, which
    is the bandwidth E-tag exists to bound;
  * the longest run of *consecutive* failed boards per node, which is the
    quantity an I-tag threshold is compared against -- and hence whether the
    shipped `t_inj` can fire at all.

Usage:
    PYTHONHASHSEED=0 python3 probe_ring2_hoputil.py [K] [scheme ...]
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from dse_ring2_write_fair import (BIN_W, FABRIC, M_REQ, M_RSP, W_FLITS,
                                  binned_jain, build_pattern, fairness_stats,
                                  run_scheme)
from rg_ring2_topo import (CHI_VCS_WRITE, Ring2Topology, write_bounds,
                           write_paths_for_txns)

OUT = Path(__file__).resolve().parents[1] / "results" / "probe_ring2_hoputil.json"


def _routed_load(topo: Ring2Topology, vp: dict) -> dict[str, dict[str, int]]:
    """'idx:dir' -> flits the route table sends over that hop, per VC.

    This is the numerator of the link bound: what R* assumes the hop must
    carry. Comparing it against measured occupancy separates "the link is the
    bottleneck and it is saturated" from "the link is idle part of the time".
    """
    mult = {"req": M_REQ, "rsp": M_RSP, "dat": W_FLITS}
    out: dict[str, dict[str, int]] = {}
    for vc, paths in vp.items():
        acc: dict[str, int] = defaultdict(int)
        for p in paths:
            for _plane, u, v in p.links():
                d = 1 if (v - u) % topo.n == 1 else -1
                acc[f"{u}:{d}"] += mult[vc]
        out[vc] = dict(acc)
    return out


def run_one(topo: Ring2Topology, txns, vp, *, scheme: str, k: int,
            over: dict[str, Any] | None = None, seed: int = 0) -> dict[str, Any]:
    cfg = {**FABRIC, **(over or {})}
    r = run_scheme(scheme, topo, txns, seed=seed, cfg=cfg, quiet=True)
    inj = r.get("wr_inject_by_core") or {}
    f = fairness_stats(inj, r["makespan"], k * W_FLITS)
    jb = binned_jain(inj, BIN_W, f["t_fair"]) if f else {}
    hu = r.get("hop_use") or {}
    routed = _routed_load(topo, vp)

    per_vc: dict[str, Any] = {}
    for vc in ("req", "rsp", "dat"):
        rows = [(kk, v) for kk, v in hu.items() if kk.endswith(f":{vc}")]
        if not rows:
            continue
        rows.sort(key=lambda kv: -kv[1]["n"])
        top = rows[0]
        idx, d, _ = top[0].split(":")
        per_vc[vc] = {
            "hot_hop": f"{idx}:{d}",
            "util": top[1]["util"],
            "n": top[1]["n"],
            "defl": top[1]["defl"],
            "routed": routed[vc].get(f"{idx}:{d}"),
            "lat": top[1]["lat"],
            # The hop the *route table* says is busiest, and its utilisation --
            # if it differs from the measured hot hop the bound is being set by
            # a link that is not in fact the busiest one at run time.
            "routed_hot": max(routed[vc], key=lambda kk: routed[vc][kk]),
            "top5": [[kk, v["n"], v["util"], v["defl"]] for kk, v in rows[:5]],
        }
    st = r.get("starve") or {}
    return {
        "scheme": scheme, "over": over or {},
        "makespan": r["makespan"], "completed": r["completed"],
        "thr": f.get("throughput"), "jain_bin": jb.get("jain_bin_mean"),
        "max_min": f.get("max_min"),
        "n_itag": r.get("n_itag_raised"), "n_etag": r.get("n_etag_raised"),
        "n_defl": r.get("n_deflections"),
        "n_board_fail": r.get("n_board_fail"),
        "per_vc": per_vc,
        # Longest consecutive board-failure run, which is what t_inj sees.
        "starve_max": max(st.values()) if st else None,
        "starve_top": sorted(st.items(), key=lambda kv: -kv[1])[:6],
    }


def main() -> None:
    k = int(sys.argv[1]) if len(sys.argv) > 1 else 3000
    schemes = sys.argv[2:] or ["S0"]
    topo = Ring2Topology(n_planes=1, vcs=CHI_VCS_WRITE)
    txns = build_pattern("uniform", k=k, W=W_FLITS, seed=0)
    vp = write_paths_for_txns(topo, txns, strategy="least_occupied")
    b = write_bounds(topo, vp, m_req=M_REQ, m_rsp=M_RSP, m_wdata=W_FLITS,
                     merge_port_vcs=False)
    n_c = len(topo.cores)
    r_star = n_c * k * W_FLITS / max(1, b["bound"])
    print(f"K={k}  bound={b['bound']} ({b.get('binding')})  "
          f"link_lb={b.get('link_lb')} port_lb={b.get('port_lb')}  "
          f"R*={r_star:.4f} flit/cycle", flush=True)

    out: dict[str, Any] = {"k": k, "bound": b["bound"],
                           "binding": b.get("binding"),
                           "link_lb": b.get("link_lb"),
                           "port_lb": b.get("port_lb"),
                           "r_star": round(r_star, 4), "rows": []}
    for s in schemes:
        row = run_one(topo, txns, vp, scheme=s, k=k)
        row["pct_r_star"] = round(100.0 * (row["thr"] or 0) / r_star, 2)
        out["rows"].append(row)
        print(f"\n[{s}] thr={row['thr']} ({row['pct_r_star']}% R*)  "
              f"Jbin={row['jain_bin']}  itag={row['n_itag']} "
              f"etag={row['n_etag']} defl={row['n_defl']}", flush=True)
        print(f"  longest consecutive board-fail run = {row['starve_max']} "
              f"(t_inj={FABRIC.get('t_inj', 64)})  top={row['starve_top'][:3]}")
        for vc, v in row["per_vc"].items():
            print(f"  {vc}: hot hop {v['hot_hop']} util={v['util']} "
                  f"n={v['n']} defl={v['defl']} routed={v['routed']} "
                  f"lat={v['lat']}  routed_hot={v['routed_hot']}")
    OUT.write_text(json.dumps(out, indent=1))
    print(f"\nwrote {OUT}")


if __name__ == "__main__":
    main()
