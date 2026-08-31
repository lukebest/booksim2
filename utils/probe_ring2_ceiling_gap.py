#!/usr/bin/env python3
"""Has S0 reached the reachable ceiling, and if not, what holds the last few %?

On the shipped fabric (per-direction up-ring ports, ha_track=512) S0 runs at
5.4681 flit/cycle, 95.69% of R* = 5.7143. The makespan decomposes exactly, which
is what makes the question answerable rather than rhetorical:

    73152 = 70000  (the routing's load on the binding hop, at 1 flit/cycle)
          +  1056  (extra crossings of that hop by flits riding another lap)
          +  2096  (cycles the binding hop sat idle)

and the binding hop is **RSP**, not DAT: hops 1->0, 11->10, 7->8, 17->18 carry
71056 crossings at 97.14% occupancy, while the busiest DAT hops sit at 95.83%.
The earlier rounds looked at DAT because that was the binding VC on the shared
port; it no longer is.

So the remaining 4.31% is two separate things, and they need different remedies:

  * 1.44% is a **deflection surcharge** -- 1056 RSP crossings of the binding hop
    that would not exist if no flit ever had to take a second lap.
  * 2.87% is **idle** on the binding hop.

This probe attacks the second. A slot out of node i can only be filled by i
boarding or by transit, so an idle slot means i did not board. `_may_inject`
only ever refuses REQ from a core, so on an RSP hop it cannot be the cause,
leaving three candidates:

  * `dry`   -- nothing queued for that direction. Unrecoverable by arbitration.
  * `itag`  -- an I-tag reservation refused the board (`_itag_blocks`).
  * `hol`   -- the flit exists in the *shared* FIFO but never reached the
    per-direction inject Q. This is the mechanism the fabric makes possible:
    the two directions share one 12-deep FIFO, and `_xfer_shared` walks it in
    order, so a flit bound for a free direction can sit behind one bound for a
    direction whose 8-deep inject Q is full.

Forecast, written before this ran: `hol` dominates, because the RSP inject
queues at exactly these nodes are the ones the report already shows full
(62-78% of samples), and a full dir Q is precisely what makes the shared FIFO
head block the other direction. `dry` should be small on RSP, since the HA has
a standing backlog of DBIDResp/Comp. If instead `dry` dominates, the limit is
upstream supply (the REQ -> DBIDResp phase) and no inject-side change helps.

Usage:
    PYTHONHASHSEED=0 python3 probe_ring2_ceiling_gap.py [K]
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from dse_ring2_write_fair import (FABRIC, K_PER_CORE, M_REQ, M_RSP, W_FLITS,
                                  build_pattern, make_sim)
from rg_ring2_topo import (CHI_VCS_WRITE, Ring2Topology, write_bounds,
                           write_paths_for_txns)

OUT = (Path(__file__).resolve().parents[1] / "results"
       / "probe_ring2_ceiling_gap.json")


def main() -> None:
    k = int(sys.argv[1]) if len(sys.argv) > 1 else K_PER_CORE
    topo = Ring2Topology(n_planes=1, vcs=CHI_VCS_WRITE, route="latency")
    tx = build_pattern("uniform", k=k, W=W_FLITS, seed=0)
    vp = write_paths_for_txns(topo, tx, strategy="least_occupied")
    b = write_bounds(topo, vp, m_req=M_REQ, m_rsp=M_RSP, m_wdata=W_FLITS,
                     merge_port_vcs=False)
    total = len(topo.cores) * k * W_FLITS
    r_star = total / b["bound"]

    # Locate the hops the bound is built on, per VC, from the same routing.
    mult = {"req": M_REQ, "rsp": M_RSP, "dat": W_FLITS}
    load: dict[str, dict[Any, int]] = {vc: defaultdict(int) for vc in mult}
    for vc, paths in vp.items():
        for p in paths:
            for e in p.links():
                load[vc][e] += mult[vc]
    hot: dict[str, list[tuple[int, int]]] = {}
    for vc in ("rsp", "dat"):
        mx = max(load[vc].values())
        hot[vc] = [(u, 1 if (u + 1) % topo.n == v else -1)
                   for (_, u, v), n in sorted(load[vc].items()) if n == mx]
    watch = [(n, d, vc) for vc in ("rsp", "dat") for n, d in hot[vc]]
    print(f"K={k}  R*={r_star:.4f}  assigned floor={b['bound']}")
    print(f"watching binding hops: {watch}\n", flush=True)

    sim = make_sim("S0", topo, seed=0, cfg=dict(FABRIC))
    sim.offer_batch(tx)
    dir_cap = int(FABRIC.get("dir_inj_depth") or 1)

    free: dict[Any, int] = defaultdict(int)
    cls: dict[Any, dict[str, int]] = defaultdict(lambda: defaultdict(int))

    while sim.t < 5_000_000 and not sim.done():
        t = sim.t
        pre = []
        for n, d, vc in watch:
            seg = (0, d, n, vc)
            if sim.seg_free[seg] > t:
                continue
            if any((t + dt) in sim.arr_set[seg] for dt in range(sim.sigma)):
                continue                    # transit already owns this slot
            free[(n, d, vc)] += 1
            dq = sim.srcq.get((n, 0, vc, d)) or ()
            sh = sim.srcq.get((n, 0, vc)) or ()
            cand = dq[0] if dq else next((f for f in sh if f.dir == d), None)
            pre.append(((n, d, vc), cand, bool(dq)))
        before = {kk: sim.hop_use.get((0, kk[1], kk[0], kk[2]), 0)
                  for kk, _, _ in pre}
        # The simulator attributes its own board failures, so read them off it
        # rather than re-deriving the predicate chain: `_finish_board` records
        # only "itag" or "hop_busy", and it re-tests `_can_board` at the moment
        # the flit actually tries, which is after other traffic in the same
        # cycle has moved.
        fc_before = {kk: dict(sim.board_fail_cause.get((kk[0], kk[2]), {}))
                     for kk, _, _ in pre}
        sim.step()
        for kk, cand, had_dq in pre:
            if sim.hop_use.get((0, kk[1], kk[0], kk[2]), 0) > before[kk]:
                continue                    # the slot got used after all
            c = cls[kk]
            aft = sim.board_fail_cause.get((kk[0], kk[2]), {})
            bef = fc_before[kk]
            grew = {r: aft.get(r, 0) - bef.get(r, 0) for r in
                    set(aft) | set(bef)}
            if grew.get("itag", 0) > 0:
                c["itag"] += 1
            elif grew.get("hop_busy", 0) > 0:
                # It tried and the segment was gone by then: in-ring priority
                # took it inside the same cycle.
                c["raced"] += 1
            elif cand is None:
                c["dry"] += 1
            elif not had_dq:
                # It exists, but only in the FIFO the two directions share.
                c["hol"] += 1
            else:
                c["other"] += 1

    s = sim.summary()
    mk = s["makespan"]
    print(f"makespan={mk}  thr={total / mk:.4f} "
          f"({100 * (total / mk) / r_star:.2f}% R*)  "
          f"defl={s['n_deflections']}\n")

    rows = []
    for (n, d, vc), nf in sorted(free.items()):
        used = sim.hop_use.get((0, d, n, vc), 0)
        c = cls[(n, d, vc)]
        idle = sum(c.values())
        rows.append({
            "hop": f"{n}->{(n + d) % topo.n}", "vc": vc, "dir": d,
            "crossings": used, "util": round(used / mk, 5),
            "assigned": b["link_by_vc"][vc],
            "surcharge": used - b["link_by_vc"][vc],
            "idle_cycles": mk - used, "boardable_idle": idle,
            "dry": c["dry"], "itag": c["itag"], "hol": c["hol"],
            "raced": c["raced"], "other": c["other"],
        })
        print(f"  {vc} {n}->{(n + d) % topo.n:<3} used={used} "
              f"util={used / mk:.4f} surcharge={used - b['link_by_vc'][vc]:+d} "
              f"idle={mk - used}  | boardable-idle {idle}: "
              f"dry={c['dry']} itag={c['itag']} raced={c['raced']} "
              f"hol={c['hol']} other={c['other']}", flush=True)

    agg: dict[str, int] = defaultdict(int)
    for r in rows:
        if r["vc"] == "rsp":
            for kk in ("dry", "itag", "raced", "hol", "other",
                       "boardable_idle"):
                agg[kk] += r[kk]
    tot = max(1, agg["boardable_idle"])
    print(f"\nRSP binding hops, boardable-idle mix: "
          f"dry {100 * agg['dry'] / tot:.1f}%  "
          f"itag {100 * agg['itag'] / tot:.1f}%  "
          f"raced {100 * agg['raced'] / tot:.1f}%  "
          f"hol {100 * agg['hol'] / tot:.1f}%  "
          f"other {100 * agg['other'] / tot:.1f}%")

    OUT.write_text(json.dumps({
        "k": k, "r_star": round(r_star, 4), "makespan": mk,
        "thr": round(total / mk, 4), "assigned_floor": b["bound"],
        "n_deflections": s["n_deflections"],
        "dir_inj_depth": dir_cap, "inj_depth": FABRIC.get("inj_depth"),
        "rows": rows, "rsp_mix": dict(agg)}, indent=2))
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
