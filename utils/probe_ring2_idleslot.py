#!/usr/bin/env python3
"""Why does the binding link idle? Classify every wasted slot on it.

`probe_ring2_hoputil.py` showed the busiest DAT hop carries exactly its routed
load but only occupies 90.8% of the run, so the entire shortfall against R* is
idle cycles on the bottleneck link itself. A slot on a directed hop out of node
i can only ever be filled by node i injecting or by a transit flit arriving at
i, so a wasted slot has exactly two causes:

  * `dry`  -- node i had nothing queued for that direction on that VC. No
    arbitration or tagging scheme can recover this slot; the flit that should
    have used it does not exist yet. On a write handshake this means the
    upstream phase (REQ -> DBIDResp) has not delivered.
  * `port` -- node i *did* have a flit for that direction but the slot still
    went empty, which on a one-flit-per-cycle inject port means the port was
    spent on the other direction (or an I-tag blocked the board).

The split decides whether I-tag can help at all: I-tag creates a bubble for a
node that has a flit and cannot board, so it addresses `port`-class waste and
starvation, never `dry`-class waste.

Usage:
    PYTHONHASHSEED=0 python3 probe_ring2_idleslot.py [K] [scheme]
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from dse_ring2_write_fair import (FABRIC, W_FLITS, build_pattern,
                                  fairness_stats, make_sim)
from rg_ring2_topo import CHI_VCS_WRITE, Ring2Topology, is_core

OUT = Path(__file__).resolve().parents[1] / "results" / "probe_ring2_idleslot.json"


def main() -> None:
    k = int(sys.argv[1]) if len(sys.argv) > 1 else 3000
    scheme = sys.argv[2] if len(sys.argv) > 2 else "S0"
    topo = Ring2Topology(n_planes=1, vcs=CHI_VCS_WRITE)
    tx = build_pattern("uniform", k=k, W=W_FLITS, seed=0)
    sim = make_sim(scheme, topo, seed=0, cfg=dict(FABRIC))
    sim.offer_batch(tx)

    vcs = ("req", "rsp", "dat")
    hops = [(n, d, vc) for n in range(topo.n) for d in (1, -1) for vc in vcs]
    free = defaultdict(int)   # hop was boardable at cycle start
    dry = defaultdict(int)    # ... and stayed empty, node had nothing for it
    port = defaultdict(int)   # ... and stayed empty, node had a flit for it
    # Of the `port` cases: did the node's one-per-VC board port go to the
    # opposite direction that cycle, or did it sit idle? Only the second is
    # unexplained by the port rate, and only the second could be an arbiter or
    # tagging bug.
    port_other = defaultdict(int)
    port_none = defaultdict(int)

    boards: dict[tuple[int, str], tuple[int, int]] = {}
    _orig_on_inject = sim._on_inject

    def _hook(f):
        boards[(f.src, f.vc)] = (sim.t, f.dir)
        return _orig_on_inject(f)

    sim._on_inject = _hook

    while sim.t < 5_000_000 and not sim.done():
        t = sim.t
        pre: list[tuple[Any, bool]] = []
        for n, d, vc in hops:
            seg = (0, d, n, vc)
            if sim.seg_free[seg] > t:
                continue
            if any((t + dt) in sim.arr_set[(0, d, n, vc)]
                   for dt in range(sim.sigma)):
                continue          # a transit flit already owns this slot
            free[(n, d, vc)] += 1
            # A flit's direction is fixed when it is admitted, so the shared
            # FIFO already knows where each entry is headed -- counting the
            # whole FIFO would credit node n with a CCW candidate when its only
            # queued flit is bound CW.
            has = bool(sim.srcq.get((n, 0, vc, d))) or \
                any(f.dir == d for f in (sim.srcq.get((n, 0, vc)) or ()))
            pre.append(((n, d, vc), has))
        before = {kk: sim.hop_use.get((0, kk[1], kk[0], kk[2]), 0)
                  for kk, _ in pre}
        sim.step()
        for kk, has in pre:
            if sim.hop_use.get((0, kk[1], kk[0], kk[2]), 0) > before[kk]:
                continue
            if not has:
                dry[kk] += 1
                continue
            port[kk] += 1
            bt, bd = boards.get((kk[0], kk[2]), (-1, 0))
            (port_other if bt == t and bd == -kk[1] else port_none)[kk] += 1

    r = sim.summary()
    f = fairness_stats(r["wr_inject_by_core"], r["makespan"], k * W_FLITS)
    mk = r["makespan"]
    hu = r["hop_use"]

    rows = []
    for vc in vcs:
        cand = [(kk, v) for kk, v in hu.items() if kk.endswith(f":{vc}")]
        cand.sort(key=lambda kv: -kv[1]["n"])
        for kk, v in cand[:3]:
            idx, d, _ = kk.split(":")
            key = (int(idx), int(d), vc)
            rows.append({
                "hop": kk, "role": "core" if is_core(int(idx)) else "mem",
                "n": v["n"], "util": v["util"],
                "free": free[key], "dry": dry[key], "port": port[key],
                "port_other": port_other[key], "port_none": port_none[key],
                "wasted_pct_of_span": round(100.0 * (dry[key] + port[key]) / mk, 2),
            })

    out = {"k": k, "scheme": scheme, "makespan": mk,
           "thr": f.get("throughput"), "rows": rows,
           # Aggregate over every hop of a VC: is the waste dry or port?
           "totals": {vc: {"dry": sum(v for kk, v in dry.items() if kk[2] == vc),
                           "port": sum(v for kk, v in port.items() if kk[2] == vc)}
                      for vc in vcs}}
    OUT.write_text(json.dumps(out, indent=1))
    print(f"K={k} {scheme} mk={mk} thr={f.get('throughput')}\n")
    print(f"{'hop':>10} {'who':>5} {'n':>7} {'util':>8} {'wasted':>8} "
          f"{'dry':>8} {'port':>7} {'->other':>8} {'->idle':>7} "
          f"{'waste%span':>11}")
    for r0 in rows:
        print(f"{r0['hop']:>10} {r0['role']:>5} {r0['n']:>7} {r0['util']:>8} "
              f"{r0['dry'] + r0['port']:>8} {r0['dry']:>8} {r0['port']:>7} "
              f"{r0['port_other']:>8} {r0['port_none']:>7} "
              f"{r0['wasted_pct_of_span']:>10}%")
    print(f"\nper-VC totals over all 40 directed hops: {out['totals']}")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
