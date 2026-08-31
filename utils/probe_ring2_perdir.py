#!/usr/bin/env python3
"""Re-derive the ceiling and re-measure S0 with per-direction board ports.

The fabric was wrong. This is a full ring, and each node's up-ring port is
natively *two* port groups -- one per direction -- each carrying REQ / RSP /
DAT. So a node boards up to 1 flit/cycle/VC *per direction*, six inject ports
in total, not three shared between the directions.

That invalidates the previous ceiling analysis rather than adjusting it:

  * The 9.6% gap to R* was attributed to port coupling -- one port serving two
    directions, so a bubble on the other direction's hop went unused. On the
    real fabric that coupling does not exist, so the attribution is void and
    the gap has to be re-measured from scratch.
  * `inj_sel="free_slot"` only reorders a port group with more than one queue
    (`_board_one`). With per-direction ports every group is a singleton, so the
    arbiter is a *no-op*. Phase 1's headline win (+11% from rr -> free_slot)
    was a workaround for the shared port and should now vanish.

This probe answers three things at the study's own K:
  1. Does R* move? The port term in `write_bounds` stacks both directions onto
     one (node, plane, VC) key, so it is now too tight. Recomputed here split
     by direction, alongside the link term, to see which binds.
  2. What are S0's total write bandwidth and 50-cycle binned Jain now?
  3. Is `free_slot` really equivalent to `rr` on this fabric? If the two rows
     are not bit-identical the singleton-group reasoning above is wrong.

Forecast, written before this ran: R* stays 5.7143 flit/cycle because the port
term was already slack (50000 against the link's 70000) and splitting it can
only loosen it further. S0's throughput goes to roughly 5.42 flit/cycle
(94.8% of R*), from the earlier `per_dir_ports` diagnostic. The binned Jain
gets *worse*, near 0.85 versus 0.968, because that diagnostic showed 0.85136 --
decoupling the ports lets every node board on both directions at once, so the
cores next to a memory node pull further ahead within any 50-cycle window.
`rr` and `free_slot` come out identical to the last digit.

Falsified if: the link bound is no longer the binding one (then R* itself has
to be restated), or free_slot still differs from rr (then port groups are not
singletons and `_port_groups` needs re-reading).

Usage:
    PYTHONHASHSEED=0 python3 probe_ring2_perdir.py [K]
"""
from __future__ import annotations

import json
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from dse_ring2_write_fair import (BIN_W, FABRIC, K_PER_CORE, M_REQ, M_RSP,
                                  W_FLITS, binned_jain, board_dir_from_inj,
                                  build_pattern, fairness_stats, run_scheme)
from rg_ring2_topo import (CHI_VCS_WRITE, Ring2Topology, write_bounds,
                           write_paths_for_txns)

OUT = (Path(__file__).resolve().parents[1] / "results"
       / "probe_ring2_perdir.json")

MULT = {"req": M_REQ, "rsp": M_RSP, "dat": W_FLITS}


def _split_port_bounds(vp: dict[str, list[Any]]) -> dict[str, Any]:
    """Board / leave port floors under the real port structure.

    A board port is per (node, VC, direction); a leave port stays per
    (node, VC) because the down-ring side is unchanged -- one two-write-one-read
    buffer per node draining 1 flit/cycle.
    """
    board: dict[Any, int] = defaultdict(int)
    board_merged: dict[Any, int] = defaultdict(int)
    leave: dict[Any, int] = defaultdict(int)
    for vc, paths in vp.items():
        m = MULT[vc]
        for p in paths:
            board[(p.src, vc, p.dir)] += m
            board_merged[(p.src, vc)] += m
            leave[(p.dst, vc)] += m
    hot_b = max(board.items(), key=lambda kv: kv[1])
    hot_bm = max(board_merged.items(), key=lambda kv: kv[1])
    hot_l = max(leave.items(), key=lambda kv: kv[1])
    return {
        "board_per_dir_lb": hot_b[1],
        "board_per_dir_at": {"node": hot_b[0][0], "vc": hot_b[0][1],
                             "dir": hot_b[0][2]},
        "board_shared_lb": hot_bm[1],
        "board_shared_at": {"node": hot_bm[0][0], "vc": hot_bm[0][1]},
        "leave_lb": hot_l[1],
        "leave_at": {"node": hot_l[0][0], "vc": hot_l[0][1]},
    }


def _hot_hop(r: dict[str, Any], makespan: int) -> list[dict[str, Any]]:
    """Utilisation of the busiest directed hop per VC.

    `hop_use_table` keys are "idx:dir:vc" and its values carry the flit count
    and the share of that count that was riding an extra lap.
    """
    per_vc: dict[str, tuple[str, dict[str, Any]]] = {}
    for key, v in (r.get("hop_use") or {}).items():
        vc = key.rsplit(":", 1)[-1]
        if vc not in per_vc or v["n"] > per_vc[vc][1]["n"]:
            per_vc[vc] = (key, v)
    return [{"vc": vc, "hop": k, "flits": v["n"], "util": v["util"],
             "defl": v["defl"]} for vc, (k, v) in sorted(per_vc.items())]


def _measure(over: dict[str, Any], topo: Ring2Topology, txns: list[Any],
             k: int, r_star: float) -> dict[str, Any]:
    cfg = dict(FABRIC)
    cfg.update(over)
    r = run_scheme("S0", topo, txns, seed=0, cfg=cfg, quiet=True)
    inj = r["wr_inject_by_core"]
    f = fairness_stats(inj, r["makespan"], k * W_FLITS)
    jb = binned_jain(inj, BIN_W, f["t_fair"])
    bd = board_dir_from_inj(r.get("inj_by_hop") or {},
                            sorted(int(c) for c in inj))
    fr = max(max(v["fail_cw"], v["fail_ccw"])
             / max(1, min(v["fail_cw"], v["fail_ccw"]))
             for v in bd.values())
    thr = len(txns) * W_FLITS / max(1, r["makespan"])
    starve = r.get("starve") or {}
    return {
        "thr": round(thr, 4), "pct_r_star": round(100.0 * thr / r_star, 2),
        "jain_bin": round(jb["jain_bin_mean"], 5),
        "jain_bin_ideal": round(jb["jain_bin_ideal"], 5),
        "jain_vs_ideal": round(jb["jain_vs_ideal"], 5),
        "max_min": round(f["max_min"], 4),
        "bw_min": f["bw_min"], "bw_max": f["bw_max"],
        "failmax": round(fr, 3), "makespan": r["makespan"],
        "n_board_fail": r.get("n_board_fail"),
        "n_itag_raised": r.get("n_itag_raised"),
        "n_itag_yield": r.get("n_itag_yield"),
        "n_etag_raised": r.get("n_etag_raised"),
        "n_deflections": r.get("n_deflections"),
        "max_ejectq": r.get("max_ejectq"),
        "starve_max": max(starve.values()) if starve else 0,
        "hot_hops": _hot_hop(r, r["makespan"]),
    }


CASES: list[tuple[str, dict[str, Any]]] = [
    ("shared port, free_slot（旧 fabric）", {}),
    ("shared port, rr", {"inj_sel": "rr"}),
    ("per-dir ports, free_slot", {"per_dir_ports": True}),
    ("per-dir ports, rr", {"per_dir_ports": True, "inj_sel": "rr"}),
]


def main() -> None:
    k = int(sys.argv[1]) if len(sys.argv) > 1 else K_PER_CORE
    topo = Ring2Topology(n_planes=1, vcs=CHI_VCS_WRITE, route="latency")
    txns = build_pattern("uniform", k=k, W=W_FLITS, seed=0)
    vp = write_paths_for_txns(topo, txns, strategy="least_occupied")
    b = write_bounds(topo, vp, m_req=M_REQ, m_rsp=M_RSP, m_wdata=W_FLITS,
                     merge_port_vcs=False)
    sp = _split_port_bounds(vp)
    tot = len(topo.cores) * k * W_FLITS
    # The bound the ring actually imposes: the max of every resource floor.
    binding = max(b["link_lb"], sp["board_per_dir_lb"], sp["leave_lb"])
    r_star = tot / max(1, binding)
    r_star_old = tot / max(1, b["bound"])

    print(f"K={k}  flits={tot}")
    print(f"  link_lb            {b['link_lb']}")
    print(f"  board_lb  shared   {sp['board_shared_lb']} "
          f"@ {sp['board_shared_at']}   (old model: both dirs on one port)")
    print(f"  board_lb  per-dir  {sp['board_per_dir_lb']} "
          f"@ {sp['board_per_dir_at']}  (real fabric)")
    print(f"  leave_lb           {sp['leave_lb']} @ {sp['leave_at']}")
    print(f"  binding floor      {binding}  ->  R* = {r_star:.4f} flit/cycle")
    print(f"  (previous R* was {r_star_old:.4f}, bound={b['bound']})\n",
          flush=True)

    rows = []
    for lab, over in CASES:
        m = _measure(over, topo, txns, k, r_star)
        rows.append({"label": lab, "over": over, **m})
        print(f"  {lab:<32} thr={m['thr']:.4f} ({m['pct_r_star']:.2f}% R*) "
              f"Jbin={m['jain_bin']:.5f} mm={m['max_min']:.4f} "
              f"failmax={m['failmax']:.3f} starve_max={m['starve_max']} "
              f"itagY={m['n_itag_yield']}", flush=True)
        for h in m["hot_hops"]:
            print(f"      hot {h['vc']:>3} {h['hop']:<14} util={h['util']:.5f}",
                  flush=True)

    same = (rows[2]["thr"] == rows[3]["thr"]
            and rows[2]["jain_bin"] == rows[3]["jain_bin"])
    print(f"\nfree_slot == rr on per-dir ports: {same}"
          f"  (expected True: every port group is a singleton)")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(
        {"k": k, "bin_w": BIN_W, "flits": tot, "bounds": b,
         "split_port_bounds": sp, "binding_floor": binding,
         "r_star": round(r_star, 4), "r_star_prev": round(r_star_old, 4),
         "rows": rows, "free_slot_equals_rr": same}, indent=2))
    print(f"\nwrote {OUT}")


if __name__ == "__main__":
    main()
