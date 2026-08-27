#!/usr/bin/env python3
"""Measure the binding DAT hop's occupancy over time, in 50-cycle bins.

This settles whether the report's "6.0207 flit/cycle = 105.4% of R*" is a real
violation. R* is set by one link -- the busiest DAT hop, which the routing loads
with 70000 crossings at 1 flit/cycle -- so it bounds the *whole-run* average.
A shorter window may exceed it only if that hop's traffic is under-served during
the window and settles up in the drain tail.

A first attempt to test that from stored per-core data was too coarse: it scaled
each core's *entire* WriteData by the hot hop's share, but only some of a core's
transactions cross that hop, and the estimate came out at 105.5% hop utilisation
-- physically impossible, and easy to mistake for evidence of a simulator bug.

So measure the hop directly. `_launch` is the single place a flit takes a
segment, so counting there per bin gives the hop's exact occupancy timeline.

The arithmetic to check: the contention window is 55700 cycles and carries
335353 write flits. If the hot hop saw its nominal 17.50% share of those, it
would need 58687 crossings in 55700 cycles -- impossible. Feasibility therefore
requires the in-window share to be at most 55700/335353 = 16.61%, i.e. hot-hop
traffic running about 5% below its nominal share during the window.

Forecast, written before this ran: the hot hop's in-window utilisation comes out
just under 1.0 (0.96 - 1.00), its in-window share of write flits lands near
16.6% against a 17.50% nominal, and its tail utilisation is well below 1.0 while
still clearing the deferred crossings. Falsified if in-window utilisation
exceeds 1.0 -- that would mean the hop carries more than 1 flit/cycle and the
simulator really is broken -- or if the hop's total crossings differ materially
from the 70000 the bound assumes, which would instead mean the bound is
computed for a routing the simulator does not follow.

Usage:
    PYTHONHASHSEED=0 python3 probe_ring2_hotslot_time.py [K]
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from dse_ring2_write_fair import (BIN_W, FABRIC, K_PER_CORE, M_REQ, M_RSP,
                                  W_FLITS, binned_jain, build_pattern,
                                  fairness_stats, make_sim)
from rg_ring2_topo import (CHI_VCS_WRITE, Ring2Topology, write_bounds,
                           write_paths_for_txns)

OUT = (Path(__file__).resolve().parents[1] / "results"
       / "probe_ring2_hotslot_time.json")


def main() -> None:
    k = int(sys.argv[1]) if len(sys.argv) > 1 else K_PER_CORE
    topo = Ring2Topology(n_planes=1, vcs=CHI_VCS_WRITE, route="latency")
    tx = build_pattern("uniform", k=k, W=W_FLITS, seed=0)
    vp = write_paths_for_txns(topo, tx, strategy="least_occupied")
    b = write_bounds(topo, vp, m_req=M_REQ, m_rsp=M_RSP, m_wdata=W_FLITS,
                     merge_port_vcs=False)
    total = len(topo.cores) * k * W_FLITS
    r_star = total / b["bound"]

    # The hop the bound is built on, from the same routing the bound used.
    load: dict[Any, int] = defaultdict(int)
    for p in vp["dat"]:
        for e in p.links():
            load[e] += W_FLITS
    hot, hot_nominal = max(load.items(), key=lambda kv: kv[1])
    _, hot_u, hot_v = hot
    hot_dir = 1 if (hot_u + 1) % topo.n == hot_v else -1

    sim = make_sim("S0", topo, seed=0, cfg=dict(FABRIC))
    # Count DAT crossings of that one directed hop, per bin, plus write
    # deliveries per bin so the two timelines are directly comparable.
    hop_bins: dict[int, int] = defaultdict(int)
    raw_launch = sim._launch

    def launch(f, *, inring):
        ok = raw_launch(f, inring=inring)
        if ok and f.vc == "dat" and f.dir == hot_dir:
            # `_launch` advances f.idx, so the segment just taken started at
            # the previous index.
            if (f.idx - f.dir) % topo.n == hot_u:
                hop_bins[sim.t // BIN_W] += 1
        return ok

    sim._launch = launch
    sim.offer_batch(tx)
    while sim.t < 2_000_000 and not sim.done():
        sim.step()
    s = sim.summary()
    mk = s["makespan"]
    inj = s["wr_inject_by_core"]
    fs = fairness_stats(inj, mk, k * W_FLITS)
    t_fair = int(fs["t_fair"])
    jb = binned_jain(inj, BIN_W, t_fair)

    n_bins_win = t_fair // BIN_W
    hot_total = sum(hop_bins.values())
    hot_win = sum(n for bi, n in hop_bins.items() if bi < n_bins_win)
    span = n_bins_win * BIN_W
    tail_span = mk - span
    hot_tail = hot_total - hot_win
    peak_bin = max(hop_bins.items(), key=lambda kv: kv[1])

    # Write flits delivered inside the same window, from the same run.
    wb = {int(c): v for c, v in inj.items()}
    win_flits = sum(sum(1 for t in ts if t < span) for ts in wb.values())

    print(f"K={k}  R*={r_star:.4f}  makespan={mk}  "
          f"whole-run={total / mk:.4f} ({100 * (total / mk) / r_star:.2f}% R*)")
    print(f"binding DAT hop {hot_u}->{hot_v} (dir={hot_dir:+d})")
    print(f"  crossings: measured {hot_total}  vs bound assumes "
          f"{hot_nominal}  -> {'match' if hot_total == hot_nominal else 'DIFFER'}")
    print(f"  busiest single {BIN_W}-cycle bin: {peak_bin[1]} crossings "
          f"-> util {peak_bin[1] / BIN_W:.4f}"
          f"{'  !! >1 IMPOSSIBLE' if peak_bin[1] > BIN_W else ''}")
    print(f"\ncontention window: {n_bins_win} bins = {span} cycles "
          f"({100 * span / mk:.1f}% of makespan)")
    print(f"  write flits in window {win_flits} -> "
          f"{win_flits / span:.4f} flit/cycle "
          f"({100 * (win_flits / span) / r_star:.1f}% R*)")
    print(f"  hot hop in window     {hot_win} crossings -> "
          f"util {hot_win / span:.4f}")
    print(f"  hot hop share of in-window write flits "
          f"{100 * hot_win / max(1, win_flits):.2f}%  "
          f"(nominal {100 * hot_nominal / total:.2f}%)")
    print(f"  feasibility ceiling on that share = {span}/{win_flits} = "
          f"{100 * span / max(1, win_flits):.2f}%")
    print(f"\ntail: {tail_span} cycles, hot hop {hot_tail} crossings -> "
          f"util {hot_tail / max(1, tail_span):.4f}")
    print(f"\nJbin(window) = {jb['jain_bin_mean']:.5f}")

    ok_phys = peak_bin[1] <= BIN_W and hot_win <= span
    print(f"\nphysically consistent: {ok_phys}")

    OUT.write_text(json.dumps({
        "k": k, "bin_w": BIN_W, "r_star": round(r_star, 4), "makespan": mk,
        "whole_run_rate": round(total / mk, 4),
        "hot_hop": {"edge": f"{hot_u}->{hot_v}", "dir": hot_dir,
                    "measured_crossings": hot_total,
                    "bound_assumes": hot_nominal,
                    "peak_bin_crossings": peak_bin[1],
                    "peak_bin_util": round(peak_bin[1] / BIN_W, 4)},
        "window": {"bins": n_bins_win, "span": span, "write_flits": win_flits,
                   "rate": round(win_flits / span, 4),
                   "pct_r_star": round(100 * (win_flits / span) / r_star, 2),
                   "hot_crossings": hot_win,
                   "hot_util": round(hot_win / span, 4),
                   "hot_share_pct": round(100 * hot_win / max(1, win_flits), 2),
                   "nominal_share_pct": round(100 * hot_nominal / total, 2),
                   "share_ceiling_pct": round(100 * span / max(1, win_flits), 2)},
        "tail": {"span": tail_span, "hot_crossings": hot_tail,
                 "hot_util": round(hot_tail / max(1, tail_span), 4)},
        "physically_consistent": ok_phys}, indent=2))
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
