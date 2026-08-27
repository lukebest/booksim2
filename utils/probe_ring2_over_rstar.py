#!/usr/bin/env python3
"""Why the reported per-bin mean write bandwidth sits *above* R*.

The report shows S0 at 6.0207 flit/cycle and calls it "105.4% of R*", with
R* = 5.7143. Taken at face value that is a simulator that beats its own bound.
It is not, and this pins down which of the three possibilities holds:

  a) the bound is wrong and has to be recomputed;
  b) the simulator delivers more write flits than the workload asked for;
  c) the two numbers are different quantities and the *comparison* is wrong.

The whole-run figure is 5.4681 = 400000 flits / 73152 cycles, comfortably under
the bound, so any excess lives in how the 6.0207 is formed. `_inst_balance`
keeps only bins wholly inside the contention window [0, t_fair] -- past t_fair a
core has run out of quota, so its zeros are not unfairness -- which makes 6.0207
a mean over the *busy* part of the run and R* a bound on the *whole* run.

That alone does not make 6.0207 legal, though, and this is the part worth
testing. R* comes from one link: the busiest DAT hop must carry 70000 crossings
at 1 flit/cycle. Under the workload's nominal mix a share
    f = 70000 / 400000 = 0.175
of every write flit crosses that hop, so a *sustained* total rate obeys
    rate <= 1 / f = 5.714,
which is R* again. A window may exceed it only by running a mix skewed *away*
from the hot hop -- i.e. the flows that use it fall behind and settle up later,
in the drain tail. So the excess is a testable claim about which cores are being
served during the window, not a free lunch.

This script checks, at the official K on the shipped fabric:
  1. flit conservation -- no extra WriteData is invented (rules out (b));
  2. the window / whole-run split, and the rate in the tail;
  3. the nominal vs actual in-window share of the cores whose WriteData crosses
     the binding hop. If those cores are under-served in the window by roughly
     the amount the excess implies, (c) is confirmed and the bound stands.

Forecast, written before this ran: (c). Conservation holds exactly at 400000;
the contention window is ~55700 of 73152 cycles with a tail rate near 3.7; and
the cores routed across the hot hop take visibly less than their nominal share
during the window. Falsified if conservation fails (then it is a real
simulator bug) or if the hot-hop cores are served at or above their nominal
share while the window mean still exceeds R* (then the bound is wrong and the
link-counting derivation has to be redone).

Usage:
    PYTHONHASHSEED=0 python3 probe_ring2_over_rstar.py
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from dse_ring2_write_fair import (BIN_W, K_PER_CORE, M_REQ, M_RSP, W_FLITS,
                                  build_pattern)
from rg_ring2_topo import (CHI_VCS_WRITE, Ring2Topology, write_bounds,
                           write_paths_for_txns)

DATA = Path(__file__).resolve().parents[1] / "results" / "ring2_write_fair.json"
OUT = (Path(__file__).resolve().parents[1] / "results"
       / "probe_ring2_over_rstar.json")


def main() -> None:
    raw = json.loads(DATA.read_text())
    pat = raw["patterns"]["uniform"]
    rec = pat["schemes"]["S0"]
    f = rec["fairness"]
    mk, t_fair = rec["makespan"], int(f["t_fair"])
    k = int(pat["K"])

    topo = Ring2Topology(n_planes=1, vcs=CHI_VCS_WRITE, route="latency")
    tx = build_pattern("uniform", k=k, W=W_FLITS, seed=0)
    vp = write_paths_for_txns(topo, tx, strategy="least_occupied")
    b = write_bounds(topo, vp, m_req=M_REQ, m_rsp=M_RSP, m_wdata=W_FLITS,
                     merge_port_vcs=False)
    total = len(topo.cores) * k * W_FLITS
    r_star = total / b["bound"]

    # (1) Conservation. Does the run deliver exactly the WriteData asked for?
    per_core = {int(c): sum(v) for c, v in
                ((c, [int(round(r * BIN_W)) for r in rec["wr_binned"][c]["rate"]])
                 for c in rec["wr_binned"])}
    got = sum(per_core.values())
    print(f"R* = {r_star:.4f}   whole-run = {total / mk:.4f} "
          f"({100 * (total / mk) / r_star:.2f}% R*)   makespan = {mk}")
    print(f"(1) conservation: binned total {got} vs workload {total} "
          f"-> {'OK' if abs(got - total) <= len(per_core) else 'MISMATCH'}"
          f"  (rounding tolerance {len(per_core)})\n")

    # (2) The window / tail split, exactly as _inst_balance slices it.
    cs = sorted(rec["wr_binned"], key=int)
    t_all = rec["wr_binned"][cs[0]]["t"]
    idx = [i for i in range(len(t_all)) if t_all[i] + BIN_W <= t_fair]
    win = {int(c): sum(int(round(rec["wr_binned"][c]["rate"][i] * BIN_W))
                       for i in idx) for c in cs}
    n_win = sum(win.values())
    span = len(idx) * BIN_W
    tail_flits, tail_span = total - n_win, mk - span
    print(f"(2) contention window: {len(idx)} bins x {BIN_W} = {span} cycles "
          f"of {mk} ({100 * span / mk:.1f}%)")
    print(f"    in-window  {n_win} flits -> {n_win / span:.4f} flit/cycle "
          f"({100 * (n_win / span) / r_star:.1f}% R*)")
    print(f"    tail       {tail_flits} flits over {tail_span} cycles "
          f"-> {tail_flits / max(1, tail_span):.4f} flit/cycle\n")

    # (3) The binding hop, and who rides it. R* is set by this one link, so the
    # window can only beat R* by serving its users below their nominal share.
    dat_load: dict[Any, int] = defaultdict(int)
    for p in vp["dat"]:
        for e in p.links():
            dat_load[e] += W_FLITS
    hot, hot_n = max(dat_load.items(), key=lambda kv: kv[1])
    riders: dict[int, int] = defaultdict(int)
    for p in vp["dat"]:
        if hot in p.links():
            riders[p.src] += W_FLITS
    share_nom = hot_n / total
    print(f"(3) binding DAT hop {hot[1]}->{hot[2]}: carries {hot_n} crossings "
          f"= {100 * share_nom:.2f}% of all {total} write flits")
    print(f"    sustained ceiling under the nominal mix = 1/{share_nom:.5f} "
          f"= {1 / share_nom:.4f} flit/cycle  (== R*)")
    print(f"    cores riding it: {sorted(riders)}")
    nom_tot = sum(riders.values())
    win_riders = sum(win[c] for c in riders)
    all_riders = sum(per_core[c] for c in riders)
    print(f"    their WriteData through this hop, nominal {nom_tot} flits")
    print(f"    their share of delivered flits: whole run "
          f"{100 * all_riders / total:.2f}%, in window "
          f"{100 * win_riders / n_win:.2f}%")
    # How much hot-hop work the window actually consumed, vs its capacity.
    hot_in_win = win_riders * (nom_tot / max(1, all_riders))
    print(f"    hot-hop crossings consumed in window ~{hot_in_win:.0f} "
          f"of {span} available cycles -> util {hot_in_win / span:.4f}")
    print(f"    deferred to the tail: ~{hot_n - hot_in_win:.0f} crossings "
          f"in {tail_span} cycles -> util {(hot_n - hot_in_win) / max(1, tail_span):.4f}")

    verdict = ("(c) 比较口径错：窗内均值 vs 全程 makespan 界"
               if abs(got - total) <= len(per_core) and n_win / span > r_star
               else "需要进一步排查")
    print(f"\nverdict: {verdict}")

    OUT.write_text(json.dumps({
        "r_star": round(r_star, 4), "makespan": mk, "t_fair": t_fair,
        "whole_run": round(total / mk, 4),
        "conservation": {"binned": got, "workload": total},
        "window": {"bins": len(idx), "span": span,
                   "flits": n_win, "rate": round(n_win / span, 4),
                   "pct_r_star": round(100 * (n_win / span) / r_star, 2)},
        "tail": {"span": tail_span, "flits": tail_flits,
                 "rate": round(tail_flits / max(1, tail_span), 4)},
        "hot_dat_hop": {"edge": f"{hot[1]}->{hot[2]}", "crossings": hot_n,
                        "share_of_write_flits": round(share_nom, 5),
                        "sustained_ceiling": round(1 / share_nom, 4),
                        "riders": sorted(riders),
                        "rider_share_whole": round(100 * all_riders / total, 2),
                        "rider_share_window": round(100 * win_riders / n_win, 2),
                        "hot_util_window": round(hot_in_win / span, 4)},
        "verdict": verdict}, indent=2))
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
