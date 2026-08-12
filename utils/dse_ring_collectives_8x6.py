#!/usr/bin/env python3
"""Six collectives on the 8x6 bufferless ring: paper mechanism vs static calendar.

Three legs, same collective, same payload, same barrier semantics:

  ring_base    the HPCA'22 mechanism -- E-tag/I-tag, deflection, bridge
               transfer FIFOs, reassembly at the destination. Reactive: offer
               everything and let it sort itself out.
  ring_islip2d centralized request-grant arbitration under D-R. Same T0
               capability as `ring_base`, different scheduler, so the gap
               between these two is the price of being distributed.
  calendar     the static slot table from `rg_ring_calendar`. Same capability
               at T0; T1 adds copy-and-continue.

Phased collectives are run phase by phase with a hard barrier on every leg,
because phase k+1 consumes what phase k produced. That is pessimistic for all
three legs equally, which is what keeps the comparison fair -- the alternative
(offer everything at t=0) would let the reactive mechanism deliver phase-2
traffic before its input exists.

Four lower bounds are reported for every row, with the binding one named:

  arc load   busiest directed segment's occupancy in cycles
  port       busiest insert/extract point, divided by its port count
  ramp       busiest node's L1 injection or ejection, at RAMP_BW per cycle
  latency    zero-contention critical path through the phases

The last one matters more than expected here. At m=1 nearly every collective on
this fabric is latency bound, not bandwidth bound, so quoting only the traffic
bounds would make a schedule that is provably optimal look 30x off.
"""

from __future__ import annotations

import json
import math
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable, Sequence

from rg_ring_base import RingBaseParams, RingBaseSim
from rg_ring_calendar import build_calendar
from rg_ring_collectives import (
    ALGOS, PATTERNS, RingCollective, all_configs, build_ring_collective,
    mcast_applicable, replay,
)
from rg_ring_sched import islip2d_ring_schedule
from rg_ring_topo import RingTopology, build_ring_plan, verify_dr
from rg_topo import RAMP, RAMP_BW, coord

OUT = Path(__file__).resolve().parent.parent / "results"
ROUNDS: tuple[int, ...] = (1, 13)
ROOT = 27                     # (x=3, y=3): interior, so no root is favoured by
#                               sitting on a wrap segment


# ---------------------------------------------------------------------------
# 1. Offers: (src, dst, flits) triples, one list per phase
# ---------------------------------------------------------------------------

def phase_offers(col: RingCollective) -> list[list[tuple[int, int, int]]]:
    """Flatten each phase to unicast deliveries with their own flit counts.

    A multicast transfer flattens to one delivery per member: that IS what a
    unicast fabric has to do, and it is the honest way to charge T1 algorithms
    to a T0 mechanism.
    """
    out: list[list[tuple[int, int, int]]] = []
    for ph in col.phases:
        offers: list[tuple[int, int, int]] = []
        for x in ph.xfers:
            for d in x.dsts:
                offers.append((x.src, d, x.nflit))
        out.append(offers)
    return out


# ---------------------------------------------------------------------------
# 2. Leg A: the paper mechanism, phase by phase
# ---------------------------------------------------------------------------

def run_base_phase(topo: RingTopology, offers: Sequence[tuple[int, int, int]],
                   params: RingBaseParams | None, seed: int,
                   t_max: int = 400_000) -> dict[str, Any]:
    """`run_batch` with a per-pair flit count instead of one global m."""
    sim = RingBaseSim(topo, params, seed=seed)
    total = 0
    for s, d, nf in offers:
        sim.offer(s, d, nf)
        total += nf
    last_t = last_n = 0
    while sim.t < t_max and sim.st["n_delivered_flits"] < total:
        sim.step()
        if sim.st["n_delivered_flits"] != last_n:
            last_n = sim.st["n_delivered_flits"]
            last_t = sim.t
        elif sim.t - last_t > 20_000:
            break
    out = sim.summary()
    out["makespan"] = sim.t
    out["n_target_flits"] = total
    out["completed"] = sim.st["n_delivered_flits"] >= total
    lat = sorted(t - f.t_gen for f, t in sim.delivered)
    if lat:
        out["lat_p50"] = lat[len(lat) // 2]
        out["lat_p99"] = lat[min(len(lat) - 1, int(0.99 * len(lat)))]
        out["lat_max"] = lat[-1]
    return out


def run_base_collective(topo: RingTopology, col: RingCollective, *,
                        params: RingBaseParams | None = None, seed: int = 0
                        ) -> dict[str, Any]:
    mk = 0
    agg = defaultdict(int)
    p99 = 0
    done = True
    per_phase: list[int] = []
    for offers in phase_offers(col):
        if not offers:
            per_phase.append(0)
            continue
        r = run_base_phase(topo, offers, params, seed)
        mk += r["makespan"]
        per_phase.append(r["makespan"])
        done = done and r["completed"]
        p99 = max(p99, r.get("lat_p99", 0))
        for k in ("n_deflections", "n_out_of_order", "n_etag_raised",
                  "n_itag_raised", "n_inring_blocked", "n_reasm_overflow",
                  "n_swaps", "n_deadlock_detected", "n_delivered_flits"):
            agg[k] += r.get(k, 0) or 0
        agg["max_reasm_occupancy"] = max(agg["max_reasm_occupancy"],
                                         r.get("max_reasm_occupancy", 0) or 0)
        agg["max_deflections"] = max(agg["max_deflections"],
                                     r.get("max_deflections", 0) or 0)
    out = {"leg": "ring_base", "makespan": mk, "completed": done,
           "lat_p99": p99, "per_phase_makespan": per_phase}
    out.update(agg)
    out["deflect_per_flit"] = (round(agg["n_deflections"]
                                    / max(1, agg["n_delivered_flits"]), 5))
    return out


# ---------------------------------------------------------------------------
# 3. Leg B: centralized arbitration, same capability
# ---------------------------------------------------------------------------

def run_islip_collective(topo: RingTopology, col: RingCollective, *,
                         t_rtt: int = 16, seed: int = 0) -> dict[str, Any]:
    mk = 0
    rounds = 0
    cf = True
    per_phase: list[int] = []
    for offers in phase_offers(col):
        if not offers:
            per_phase.append(0)
            continue
        # one VOQ per (src,dst) per phase; duplicates would violate R5, and a
        # collective phase never repeats a pair, so collapse defensively
        by_pair: dict[tuple[int, int], int] = {}
        for s, d, nf in offers:
            by_pair[(s, d)] = by_pair.get((s, d), 0) + nf
        pl = sorted(by_pair)
        plan = build_ring_plan(topo, pl, "balanced")
        fps = [topo.footprint(i, plan.paths[k], by_pair[k], release=t_rtt)
               for i, k in enumerate(pl)]
        load = topo.link_load(plan.paths[k] for k in pl)
        for fp in fps:
            fp.pressure = sum(load[e] for e, _ in fp.links)
        r = islip2d_ring_schedule(topo, fps, grants_per_src=2, t_rtt=t_rtt,
                                  pipeline_depth=1 << 20, seed=seed)
        by_id = {fp.flow_id: fp for fp in fps}
        v = verify_dr(topo, [(by_id[f], t) for f, t in r["starts"].items()])
        cf = cf and v["conflict_free"]
        mk += r["makespan_sched"]
        per_phase.append(r["makespan_sched"])
        rounds += r["n_rounds"]
    return {"leg": "ring_islip2d", "makespan": mk, "n_rounds": rounds,
            "conflict_free": cf, "per_phase_makespan": per_phase}


# ---------------------------------------------------------------------------
# 4. Bounds the calendar's own accounting does not see
# ---------------------------------------------------------------------------

def bisection_bound(topo: RingTopology, col: RingCollective) -> dict[str, Any]:
    """Flits that must cross the vertical bisection, over the cut width.

    On this ring every row ring crosses a vertical cut TWICE -- once on a
    regular segment and once on its wrap -- so the cut is 2*my directed
    segments per direction against the mesh's my. That doubled cut is the one
    place the ring's extra metal shows up as extra bisection bandwidth rather
    than as extra latency.
    """
    half = topo.mx // 2
    side = lambda n: coord(n, topo.mx)[0] < half        # noqa: E731
    cut_pos = [e for e in topo.directed_links if side(e[0]) and not side(e[1])]
    cut_neg = [e for e in topo.directed_links if not side(e[0]) and side(e[1])]
    f_pos = f_neg = 0
    for x in col.xfers:
        for d in x.dsts:
            if side(x.src) and not side(d):
                f_pos += x.nflit
            elif not side(x.src) and side(d):
                f_neg += x.nflit
    w = max(1, len(cut_pos))
    return {
        "cut_width_directed": len(cut_pos),
        "flits_crossing_pos": f_pos, "flits_crossing_neg": f_neg,
        "bisection_lb": max(math.ceil(f_pos * topo.sigma / w),
                            math.ceil(f_neg * topo.sigma / max(1, len(cut_neg)))),
    }


def ramp_eject_bound(topo: RingTopology, col: RingCollective) -> dict[str, Any]:
    """The hard floor multicast cannot touch.

    Every flit a node must END UP holding has to cross that node's L1 ramp at
    RAMP_BW per cycle, however cleverly it got there. For allgather and
    alltoall that is (N-1)*m flits into every node, which is why no amount of
    in-network copying moves their makespan once m is large: the bottleneck is
    the last two centimetres, not the fabric.
    """
    inj: dict[int, int] = defaultdict(int)
    ej: dict[int, int] = defaultdict(int)
    for x in col.xfers:
        inj[x.src] += x.nflit
        for d in x.dsts:
            ej[d] += x.nflit
    cap = max(1, RAMP_BW * topo.sigma)
    mi = max(inj.values()) if inj else 0
    me = max(ej.values()) if ej else 0
    return {"max_inject_flits": mi, "max_eject_flits": me,
            "ramp_bw": cap,
            "ramp_inject_lb": math.ceil(mi / cap),
            "ramp_eject_lb": math.ceil(me / cap),
            "ramp_lb": max(math.ceil(mi / cap), math.ceil(me / cap))}


# ---------------------------------------------------------------------------
# 5. The sweep
# ---------------------------------------------------------------------------

def one_config(topo: RingTopology, pattern: str, algo: str, tier: str, m: int,
               *, do_base: bool = True, do_islip: bool = True,
               bidir: bool = True) -> dict[str, Any]:
    col = build_ring_collective(topo, pattern, m=m, tier=tier, algo=algo,
                                root=ROOT, bidir=bidir)
    rp = replay(col)
    cal = build_calendar(topo, col)
    cs = cal.summary(topo)
    v = verify_dr(topo, cal.items)
    row: dict[str, Any] = {
        "pattern": pattern, "algo": algo, "tier": tier, "m": m,
        "bidir": bidir,
        "shape": col.summary(),
        "replay_ok": rp["ok"],
        "calendar": cs,
        "verify": {k: v[k] for k in (
            "R1_link_violations", "R2_board_violations", "R3_leave_violations",
            "R4_turn_violations", "R5_voq_violations", "MC_shape_violations",
            "max_turn_residency", "n_mcast_grants", "n_mcast_copies",
            "conflict_free")},
        "bounds": {**ramp_eject_bound(topo, col),
                   **bisection_bound(topo, col),
                   "arc_load_lb": cs["arc_load_lb"],
                   "port_lb": cs["port_lb"],
                   "latency_lb": cs["latency_lb"],
                   "makespan_lb": cs["makespan_lb"],
                   "binding_lb": cs["binding_lb"]},
    }
    if do_base:
        if tier == "T1":
            row["ring_base"] = {
                "leg": "ring_base", "makespan": None,
                "skipped": "the paper mechanism has no multicast; a T1 "
                           "algorithm charged to it degenerates to its T0 form"}
        else:
            t0 = time.perf_counter()
            row["ring_base"] = run_base_collective(topo, col)
            row["ring_base"]["wall_s"] = round(time.perf_counter() - t0, 2)
    if do_islip:
        if tier == "T1":
            row["ring_islip2d"] = {"leg": "ring_islip2d", "makespan": None,
                                   "skipped": "same reason as ring_base"}
        else:
            t0 = time.perf_counter()
            row["ring_islip2d"] = run_islip_collective(topo, col)
            row["ring_islip2d"]["wall_s"] = round(time.perf_counter() - t0, 2)

    lb = row["bounds"]["makespan_lb"]
    row["ratios"] = {
        "calendar_over_lb": round(cs["makespan"] / max(1, lb), 3),
        "base_over_lb": (round(row["ring_base"]["makespan"] / max(1, lb), 3)
                         if row.get("ring_base", {}).get("makespan") else None),
        "islip_over_lb": (round(row["ring_islip2d"]["makespan"] / max(1, lb), 3)
                          if row.get("ring_islip2d", {}).get("makespan")
                          else None),
        "base_over_calendar": (
            round(row["ring_base"]["makespan"] / max(1, cs["makespan"]), 3)
            if row.get("ring_base", {}).get("makespan") else None),
    }
    return row


def sweep(topo: RingTopology, rounds: Iterable[int] = ROUNDS) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    cfgs = all_configs()
    for m in rounds:
        for pattern, algo, tier in cfgs:
            t0 = time.perf_counter()
            row = one_config(topo, pattern, algo, tier, m)
            rows.append(row)
            b = row.get("ring_base", {}).get("makespan")
            i = row.get("ring_islip2d", {}).get("makespan")
            print(f"  m={m:<3} {pattern:10} {algo:17} {tier} "
                  f"cal={row['calendar']['makespan']:>6} "
                  f"base={b if b is not None else '-':>7} "
                  f"islip={i if i is not None else '-':>7} "
                  f"lb={row['bounds']['makespan_lb']:>6} "
                  f"({row['bounds']['binding_lb']:>8}) "
                  f"{time.perf_counter()-t0:.1f}s", flush=True)
    return {"rows": rows}


def bidir_lever(topo: RingTopology) -> list[dict[str, Any]]:
    """Price walking both ways round the ring, separately for unicast and
    multicast fan-out. The two answers are not the same and the difference is
    structural, not a tuning artefact."""
    out: list[dict[str, Any]] = []
    for m in ROUNDS:
        for pattern, algo, tier in (("broadcast", "dim_2phase", "T1"),
                                    ("broadcast", "dim_2phase", "T0"),
                                    ("allgather", "dim_2phase", "T1"),
                                    ("allgather", "dim_2phase", "T0")):
            rec: dict[str, Any] = {"pattern": pattern, "algo": algo,
                                   "tier": tier, "m": m}
            for bd in (True, False):
                col = build_ring_collective(topo, pattern, m=m, tier=tier,
                                            algo=algo, root=ROOT, bidir=bd)
                cal = build_calendar(topo, col)
                s = cal.summary(topo)
                rec["bi" if bd else "uni"] = {
                    "makespan": s["makespan"],
                    "makespan_lb": s["makespan_lb"],
                    "total_link_cycles": s["util"]["total_link_cycles"],
                    "critical_arc_util": s["util"]["critical_arc_util"]}
            rec["makespan_ratio_uni_over_bi"] = round(
                rec["uni"]["makespan"] / max(1, rec["bi"]["makespan"]), 3)
            rec["traffic_ratio_uni_over_bi"] = round(
                rec["uni"]["total_link_cycles"]
                / max(1, rec["bi"]["total_link_cycles"]), 3)
            out.append(rec)
    return out


def port_sensitivity(topo: RingTopology) -> list[dict[str, Any]]:
    """Why `ring_base` sometimes beats a provably optimal calendar.

    D-R charges an extract point to ONE transfer for its whole burst: seven
    senders funnelling 13 flits each into one node serialize into 7*13 = 91
    cycles on that node's extract point. The paper mechanism does not work that
    way -- a station pulls flits off the ring one at a time and does not care
    which sender they came from, so it interleaves all seven and drains at the
    ramp rate instead. That is not the calendar losing, it is the two models
    charging different hardware, and on the funnel-shaped collectives (gather,
    reduce) it is worth up to 1.8x.

    Giving the ring station two insert/extract points per ring is the closest
    D-R-legal approximation of per-flit interleaving, so this sweep prices the
    extra port and shows how much of the gap it closes.
    """
    out: list[dict[str, Any]] = []
    picks = (("reduce", "dim_2phase", "T0"), ("gather", "dim_2phase", "T0"),
             ("allgather", "dim_2phase", "T1"), ("broadcast", "dim_2phase", "T1"),
             ("allgather", "flat", "T0"), ("alltoall", "flat", "T0"))
    for m in ROUNDS:
        for pattern, algo, tier in picks:
            rec: dict[str, Any] = {"pattern": pattern, "algo": algo,
                                   "tier": tier, "m": m, "by_ports": {}}
            for p in (1, 2):
                tp = RingTopology(board_ports=p, leave_ports=p)
                col = build_ring_collective(tp, pattern, m=m, tier=tier,
                                            algo=algo, root=ROOT)
                cal = build_calendar(tp, col)
                v = verify_dr(tp, cal.items)
                rec["by_ports"][p] = {
                    "makespan": cal.makespan,
                    "makespan_lb": cal.bounds["makespan_lb"],
                    "binding_lb": cal.bounds["binding_lb"],
                    "conflict_free": v["conflict_free"]}
            rec["speedup_ports2"] = round(
                rec["by_ports"][1]["makespan"]
                / max(1, rec["by_ports"][2]["makespan"]), 3)
            out.append(rec)
    return out


def fill_order_lever(topo: RingTopology) -> list[dict[str, Any]]:
    """Does the pack order matter? Reported because a static calendar's only
    free variable is the order transfers are offered to the packer."""
    from rg_ring_calendar import FILL_ORDERS
    out: list[dict[str, Any]] = []
    for pattern, algo, tier in (("allgather", "dim_2phase", "T1"),
                                ("allgather", "flat", "T0"),
                                ("alltoall", "flat", "T0")):
        col = build_ring_collective(topo, pattern, m=13, tier=tier, algo=algo,
                                    root=ROOT)
        rec: dict[str, Any] = {"pattern": pattern, "algo": algo, "tier": tier,
                               "m": 13, "by_fill": {}}
        for f in FILL_ORDERS:
            cal = build_calendar(topo, col, fill=f)
            rec["by_fill"][f] = cal.makespan
        vals = list(rec["by_fill"].values())
        rec["spread"] = max(vals) - min(vals)
        rec["best_fill"] = min(rec["by_fill"], key=lambda k: rec["by_fill"][k])
        out.append(rec)
    return out


def main() -> None:
    topo = RingTopology()
    print("=== ring collectives 8x6: three legs, six patterns ===")
    print(f"audit: {json.dumps(topo.audit())}\n")
    t0 = time.perf_counter()
    res = sweep(topo)
    print("\n--- bidirectional lever ---")
    res["bidir_lever"] = bidir_lever(topo)
    for r in res["bidir_lever"]:
        print(f"  m={r['m']:<3} {r['pattern']:10} {r['tier']} "
              f"bi={r['bi']['makespan']:>6} uni={r['uni']['makespan']:>6} "
              f"mk_ratio={r['makespan_ratio_uni_over_bi']:<6} "
              f"traffic_ratio={r['traffic_ratio_uni_over_bi']}")
    print("\n--- ring-station port count (why ring_base can win) ---")
    res["port_sensitivity"] = port_sensitivity(topo)
    for r in res["port_sensitivity"]:
        print(f"  m={r['m']:<3} {r['pattern']:10} {r['algo']:12} {r['tier']} "
              f"ports1={r['by_ports'][1]['makespan']:>6} "
              f"ports2={r['by_ports'][2]['makespan']:>6} "
              f"speedup={r['speedup_ports2']}")
    print("\n--- fill order lever (m=13) ---")
    res["fill_lever"] = fill_order_lever(topo)
    for r in res["fill_lever"]:
        print(f"  {r['pattern']:10} {r['algo']:12} {r['tier']} "
              f"spread={r['spread']:<6} best={r['best_fill']:<10} "
              f"{r['by_fill']}")
    res["audit"] = topo.audit()
    res["root"] = ROOT
    res["rounds"] = list(ROUNDS)
    res["wall_s"] = round(time.perf_counter() - t0, 1)
    OUT.mkdir(exist_ok=True)
    (OUT / "ring_collectives_8x6.json").write_text(json.dumps(res, indent=1))
    print(f"\nwrote {OUT / 'ring_collectives_8x6.json'} "
          f"({len(res['rows'])} rows, {res['wall_s']}s)")


if __name__ == "__main__":
    main()
