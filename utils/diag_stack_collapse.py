#!/usr/bin/env python3
"""Isolate S0 congestion collapse: leftover snapshot + write-only vs mixed."""
from __future__ import annotations

import json
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from dse_stack_write_fair import FABRIC
from rg_stack_base import StackBaseParams, StackBaseSim
from rg_stack_fc import StackFcParams, StackFcSim
from rg_stack_topo import StackTopology, build_tiled_rw, build_tiled_write


def leftover(sim: StackBaseSim) -> dict:
    xq_kind: Counter[str] = Counter()
    xq_pair: Counter[str] = Counter()
    xq_occ: Counter[str] = Counter()
    for key, q in sim.xq.items():
        if not q:
            continue
        src = key[1]
        dst = key[2][0] if key[2] is not None else "?"
        pair = f"{src}->{dst}"
        xq_occ[pair] += len(q)
        xq_pair[pair] += 1
        for f in q:
            xq_kind[f"{pair}:{f.kind}:{f.vc}"] += 1

    buf_kind: Counter[str] = Counter()
    for key, q in sim.d2d_buf.items():
        for f in q:
            buf_kind[f"{f.kind}:{f.vc}"] += 1

    arr_kind: Counter[str] = Counter()
    arr_detour = 0
    arr_onring = 0
    arr_d2d = 0
    arr_held = 0
    for flits in sim.arrivals.values():
        for f in flits:
            fab = f.ring[0] if f.ring is not None else "none"
            arr_kind[f"{fab}:{f.kind}:{f.vc}"] += 1
            if f.detour:
                arr_detour += 1
            if f.held:
                arr_held += 1
            if f.ring is not None and f.ring[0] == "d2d":
                arr_d2d += 1
            elif f.ring is not None:
                arr_onring += 1

    src_kind: Counter[str] = Counter()
    for q in sim.srcq.values():
        for f in q:
            src_kind[f.kind] += 1

    return {
        "t": sim.t,
        "done": sim.st["n_txn_done"],
        "delivered": sim.st["n_delivered_flits"],
        "turn_full": sim.st["n_turn_full_deflect"],
        "turn_hold": sim.st.get("n_turn_hold", 0),
        "d2d_stall": sim.st["n_d2d_stall"],
        "swaps": sim.st["n_swaps"],
        "deflections": sim.st["n_deflections"],
        "in_flight": sim.in_flight() if hasattr(sim, "in_flight") else None,
        "core_outst_sum": int(sum(sim.core_outst.values())),
        "xq_flits": sum(xq_occ.values()),
        "xq_occ_by_pair": dict(xq_occ),
        "xq_queues_by_pair": dict(xq_pair),
        "xq_kind": dict(xq_kind),
        "d2d_buf": dict(buf_kind),
        "d2d_buf_n": sum(buf_kind.values()),
        "arr_n": sum(arr_kind.values()),
        "arr_kind": dict(arr_kind),
        "arr_detour": arr_detour,
        "arr_held": arr_held,
        "arr_onring": arr_onring,
        "arr_d2d": arr_d2d,
        "srcq": dict(src_kind),
        "max_turn_q": sim.st["max_turn_q"],
        "max_d2d_q": sim.st["max_d2d_q"],
        "max_d2d_landing": sim.st["max_d2d_landing"],
    }


def run_one(name: str, txns, stall_after: int, t_max: int,
            scheme: str = "s0") -> dict:
    topo = StackTopology()
    if scheme == "s1":
        sim = StackFcSim(topo, StackFcParams(**FABRIC), seed=0)
    else:
        sim = StackBaseSim(topo, StackBaseParams(**FABRIC), seed=0)
    sim.offer_batch(txns)
    last_progress, last_count = 0, 0
    t0 = time.time()
    while sim.t < t_max and not sim.done():
        sim.step()
        if sim.st["n_delivered_flits"] != last_count:
            last_count = sim.st["n_delivered_flits"]
            last_progress = sim.t
        elif sim.t - last_progress > stall_after:
            break
    snap = leftover(sim)
    snap["name"] = name
    snap["n_txn"] = len(txns)
    snap["completed"] = sim.done()
    snap["stall"] = not sim.done()
    snap["last_progress"] = last_progress
    snap["wall_s"] = round(time.time() - t0, 1)
    print(json.dumps(snap, indent=2), flush=True)
    return snap


def main() -> None:
    stall = int(sys.argv[1]) if len(sys.argv) > 1 else 4000
    t_max = int(sys.argv[2]) if len(sys.argv) > 2 else 20_000
    which = sys.argv[3] if len(sys.argv) > 3 else "both"
    n_tiles = int(sys.argv[4]) if len(sys.argv) > 4 else 1
    scheme = sys.argv[5] if len(sys.argv) > 5 else "s0"
    topo = StackTopology()
    out = {}
    if which in ("both", "wr"):
        tx = build_tiled_write(topo, n_tiles=n_tiles)
        out["write"] = run_one("write", tx, stall, t_max, scheme)
    if which in ("both", "rw"):
        tx = build_tiled_rw(topo, n_tiles=n_tiles)
        out["mixed"] = run_one("mixed", tx, stall, t_max, scheme)
    Path("results/diag_stack_collapse.json").write_text(
        json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
