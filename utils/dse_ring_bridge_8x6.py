#!/usr/bin/env python3
"""How much buffer does each bridge of the 8x6 bufferless folded 2D torus need?

The fabric is bufferless on the rings, not in the nodes: a flit that changes
rings has to sit in that node's transfer FIFO while the bridge carries it across,
and with the wire setup this study uses (`rg_ring_topo`: 2 core pitches per
typical segment = 10 / 14 cycles, and `t_turn = 10` cycles to change rings) the
crossing alone pins an entry for 10 cycles. Every one of the 48 cores is a
bridge, so this is 48 FIFOs whose depth is a real cost -- and the one buffer the
`ring_base` mechanism cannot argue away.

Three questions, three experiments:

1. WHERE does it pile up (`per_pattern`)? Per-bridge census for the six
   collectives at m=1 and m=13: peak entries, mean occupancy (flit-cycles over
   the makespan), cycles spent at capacity, and the turns that were deflected
   because the FIFO was full. Reported per node, not averaged, so a hotspot is
   visible -- the 8x6 map is what the report draws.

2. HOW DEEP does it have to be (`depth_sweep`)? fifo_depth is swept with
   everything else fixed. A bufferless ring cannot block, so a full FIFO does
   not stall: it deflects, and the flit pays another lap. The knee is therefore
   a makespan knee, and reporting the depth as "provisioned" without it is
   guessing.

3. WHO CREATED the requirement (`turn_sweep`)? t_turn is swept from 1 (the
   old free-bridge idealisation) to 20. Occupancy is roughly the arrival rate
   times the residency, so the depth a bridge needs is a function of the turn
   latency, and the 10-cycle bridge is what turns a 1-entry FIFO into a
   multi-entry one. This is the experiment that separates "the load is heavy"
   from "the crossing is slow".

Writes results/ring_bridge_8x6.json; rendered by gen_ring_collectives_report.py
and asserted by verify_ring_collectives_8x6.py.
"""

from __future__ import annotations

import json
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

from dse_ring_collectives_8x6 import ROOT, phase_offers, run_base_phase
from rg_ring_base import RingBaseParams
from rg_ring_collectives import build_ring_collective
from rg_ring_topo import RingTopology
from rg_topo import coord

OUT = Path(__file__).resolve().parents[1] / "results" / "ring_bridge_8x6.json"

# `flat` is the shape that actually uses the bridges: every delivery is one
# end-to-end unicast, so a pair differing in both dimensions changes rings in
# flight and occupies a transfer FIFO. It is also what the paper mechanism does
# when nobody schedules it, which is the case this baseline is here to price.
CASES: tuple[tuple[str, str], ...] = (
    ("alltoall", "flat"),
    ("allgather", "flat"),
    ("allreduce", "flat"),
    ("gather", "flat"),
    ("broadcast", "flat"),
    ("reduce", "flat"),
)
# The control that explains the numbers above: a dimension-decomposed schedule
# relays at the corner core instead of turning in flight, so its transfers never
# board a bridge. Same fabric, same collective, zero bridge buffer -- at the
# price of an extra L1 round trip per phase.
CONTROL: tuple[tuple[str, str], ...] = (
    ("alltoall", "dim_2phase"),
    ("allgather", "dim_2phase"),
)
M_LIST = (1, 13)
DEPTHS = (1, 2, 4, 8, 16, 32)
TURNS = (1, 2, 5, 10, 20)


def census(topo: RingTopology, pattern: str, algo: str, m: int, *,
           params: RingBaseParams | None = None) -> dict[str, Any]:
    """Run one collective phase by phase and merge the per-bridge tables.

    Peaks are maxima over phases (the FIFO has to hold the worst one), occupancy
    is a flit-cycle integral so it adds, and so do capacity cycles, entries and
    deflections. Phases run back to back, so dividing the integral by the summed
    makespan gives the mean depth in use over the whole collective.
    """
    col = build_ring_collective(topo, pattern, m=m, tier="T0", algo=algo,
                                root=ROOT, bidir=True)
    peak: dict[int, int] = defaultdict(int)
    occ: dict[int, float] = defaultdict(float)
    full: dict[int, int] = defaultdict(int)
    entries: dict[int, int] = defaultdict(int)
    defl: dict[int, int] = defaultdict(int)
    mk = 0
    wait_max = 0
    wait_sum = 0.0
    wait_n = 0
    done = True
    for offers in phase_offers(col):
        if not offers:
            continue
        r = run_base_phase(topo, offers, params, 0)
        mk += r["makespan"]
        done = done and r["completed"]
        wait_max = max(wait_max, r["bridge_wait_max"])
        wait_sum += r["bridge_wait_mean"] * r["bridge_entries"]
        wait_n += r["bridge_entries"]
        for node, v in r["_bridge_per_node"].items():
            peak[node] = max(peak[node], v["peak"])
            occ[node] += v["occ"]
            full[node] += v["full_cy"]
            entries[node] += v["entries"]
            defl[node] += v["deflect"]
    span = max(1, mk)
    nodes = sorted(set(peak) | set(entries))
    table = [{"node": n, "x": coord(n, topo.mx)[0], "y": coord(n, topo.mx)[1],
              "peak": peak[n], "mean": round(occ[n] / span, 3),
              "full_cy": full[n], "entries": entries[n],
              "deflect": defl[n]} for n in nodes]
    peaks = [t["peak"] for t in table] or [0]
    means = [t["mean"] for t in table] or [0.0]
    hot = max(table, key=lambda t: (t["peak"], t["mean"]), default=None)
    return {
        "pattern": pattern, "algo": algo, "m": m,
        "makespan": mk, "completed": done,
        "n_bridges_touched": len(table),
        "peak_max": max(peaks), "peak_min": min(peaks),
        "peak_at_cap": sum(1 for t in table
                           if t["peak"] >= (params or RingBaseParams()
                                            ).fifo_depth),
        "mean_max": max(means), "mean_avg": round(sum(means) / len(means), 3),
        "full_cy_total": sum(full.values()),
        "full_frac_max": round(max(full.values(), default=0) / span, 4),
        "entries_total": sum(entries.values()),
        "deflect_total": sum(defl.values()),
        "wait_max": wait_max,
        "wait_mean": round(wait_sum / wait_n, 2) if wait_n else 0.0,
        "hot_node": hot,
        "table": table,
    }


def depth_sweep(topo: RingTopology, pattern: str, algo: str, m: int
                ) -> dict[str, Any]:
    rows = []
    for d in DEPTHS:
        p = RingBaseParams(fifo_depth=d)
        t0 = time.perf_counter()
        c = census(topo, pattern, algo, m, params=p)
        rows.append({"fifo_depth": d, "makespan": c["makespan"],
                     "peak_max": c["peak_max"], "mean_max": c["mean_max"],
                     "full_frac_max": c["full_frac_max"],
                     "deflect_total": c["deflect_total"],
                     "wait_max": c["wait_max"],
                     "wall_s": round(time.perf_counter() - t0, 2)})
        print(f"  depth {d:2} mk {c['makespan']:7} peak {c['peak_max']:3} "
              f"mean {c['mean_max']:6.2f} defl {c['deflect_total']:7} "
              f"{rows[-1]['wall_s']:.1f}s", flush=True)
    best = min(r["makespan"] for r in rows)
    knee = next(r["fifo_depth"] for r in rows
                if r["makespan"] <= best * 1.01)
    return {"pattern": pattern, "algo": algo, "m": m, "rows": rows,
            "best_makespan": best, "knee_depth": knee,
            "cost_of_depth1": round(rows[0]["makespan"] / best, 3)}


def turn_sweep(topo: RingTopology, pattern: str, algo: str, m: int
               ) -> dict[str, Any]:
    rows = []
    for tt in TURNS:
        tp = RingTopology(t_turn=tt)
        t0 = time.perf_counter()
        c = census(tp, pattern, algo, m)
        rows.append({"t_turn": tt, "makespan": c["makespan"],
                     "peak_max": c["peak_max"], "mean_max": c["mean_max"],
                     "mean_avg": c["mean_avg"],
                     "full_frac_max": c["full_frac_max"],
                     "deflect_total": c["deflect_total"],
                     "wall_s": round(time.perf_counter() - t0, 2)})
        print(f"  t_turn {tt:2} mk {c['makespan']:7} peak {c['peak_max']:3} "
              f"mean {c['mean_avg']:6.2f} defl {c['deflect_total']:7} "
              f"{rows[-1]['wall_s']:.1f}s", flush=True)
    ref = next(r for r in rows if r["t_turn"] == 10)
    one = next(r for r in rows if r["t_turn"] == 1)
    return {"pattern": pattern, "algo": algo, "m": m, "rows": rows,
            "makespan_10_over_1": round(ref["makespan"] / one["makespan"], 3),
            "mean_10_over_1": round(ref["mean_avg"] / max(1e-9,
                                                          one["mean_avg"]), 2),
            "deflect_10_over_1": round(ref["deflect_total"]
                                       / max(1, one["deflect_total"]), 2)}


def main() -> None:
    topo = RingTopology()
    t_start = time.perf_counter()
    print("=== per-bridge transfer-FIFO census "
          f"(t_turn={topo.t_turn}, depth={RingBaseParams().fifo_depth}) ===")
    per_pattern = []
    for pattern, algo in CASES:
        for m in M_LIST:
            t0 = time.perf_counter()
            c = census(topo, pattern, algo, m)
            c["wall_s"] = round(time.perf_counter() - t0, 2)
            per_pattern.append(c)
            print(f"{pattern:10} m={m:<3} mk {c['makespan']:7} peak "
                  f"{c['peak_max']:2} (min {c['peak_min']:2}) mean_max "
                  f"{c['mean_max']:5.2f} avg {c['mean_avg']:5.2f} full "
                  f"{c['full_frac_max']:6.3f} defl {c['deflect_total']:7} "
                  f"wait {c['wait_max']:4} {c['wall_s']:.1f}s", flush=True)

    print("\n=== control: a dimension-decomposed schedule never turns ===")
    control = []
    for pattern, algo in CONTROL:
        for m in M_LIST:
            c = census(topo, pattern, algo, m)
            c.pop("table")
            control.append(c)
            print(f"{pattern:10}/{algo:11} m={m:<3} mk {c['makespan']:7} "
                  f"bridges touched {c['n_bridges_touched']:3} peak "
                  f"{c['peak_max']:2}", flush=True)

    print("\n=== depth sweep: alltoall m=1 ===")
    d1 = depth_sweep(topo, "alltoall", "flat", 1)
    print("=== depth sweep: alltoall m=13 ===")
    d13 = depth_sweep(topo, "alltoall", "flat", 13)
    print("=== depth sweep: allgather m=13 ===")
    dag = depth_sweep(topo, "allgather", "flat", 13)

    print("\n=== turn sweep: alltoall m=1 ===")
    t1 = turn_sweep(topo, "alltoall", "flat", 1)
    print("=== turn sweep: alltoall m=13 ===")
    t13 = turn_sweep(topo, "alltoall", "flat", 13)

    doc = {
        "wire": {"pitch_h": topo.pitch_h, "pitch_v": topo.pitch_v,
                 "folded": topo.folded, "t_turn": topo.t_turn,
                 "row_hop_cycles": sorted({topo.link_lat(("row", 0), i)
                                           for i in range(topo.mx)}),
                 "col_hop_cycles": sorted({topo.link_lat(("col", 0), i)
                                           for i in range(topo.my)})},
        "params": {k: getattr(RingBaseParams(), k) for k in
                   ("fifo_depth", "resv_tx", "eject_depth", "eject_bw",
                    "t_inj", "t_xfer")},
        "root": ROOT, "m_list": list(M_LIST),
        "definitions": {
            "peak": "该桥 transfer FIFO 在整个集合通信里到过的最深条目数，"
                    "取各 phase 的最大值；FIFO 必须按它来建",
            "mean": "flit·cycle 积分 / makespan，平均真正用到多深",
            "full_cy": "该桥处于满深度的拍数；满不会阻塞环，只会把要转环的 "
                       "flit 打偏（deflect），所以它的代价是绕一圈",
            "deflect": "因为该桥满而被打偏的转环次数",
            "wait": "在 t_turn 之外还多等的拍数（排队 + 抢不到插入点）",
        },
        "per_pattern": per_pattern,
        "no_turn_control": control,
        "depth_sweep": [d1, d13, dag],
        "turn_sweep": [t1, t13],
        "wall_s": round(time.perf_counter() - t_start, 1),
    }
    OUT.write_text(json.dumps(doc, indent=1, ensure_ascii=False),
                   encoding="utf-8")
    print(f"\nwrote {OUT}  ({doc['wall_s']}s)")


if __name__ == "__main__":
    main()
