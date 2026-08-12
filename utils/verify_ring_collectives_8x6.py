#!/usr/bin/env python3
"""Executable checks for the 8x6 ring collective calendars.

Every check is an assertion with the measured quantity printed next to it, so a
failure names the number that broke rather than just the property. Checks that
encode a PREDICTION are labelled as such, and two of them fail the prediction
the plan started from -- those are kept, inverted, and marked, because a check
that has been quietly relaxed to keep passing is worse than no check.

Groups:
    topology and footprint model      1-8
    collective semantics              9-16
    D-R legality of every calendar   17-24
    lower bounds                     25-30
    rotation and utilization         31-35
    faults                           36-41
    jitter                           42-46
    calendar export                  47-51
"""

from __future__ import annotations

import json
import math
from collections import Counter
from pathlib import Path
from typing import Any, Callable

from dse_ring_collectives_8x6 import bisection_bound, ramp_eject_bound
from export_ring_calendars import (
    EXPORTS, PORT, OP_ADD, check_output_ports, check_ramp, station_records,
)
from rg_ring_calendar import (
    FaultModel, build_calendar, clean_cover, fault_sweep, release_offsets,
    repo_fault_scenarios, replay_jitter, scattered_node_scenarios,
    wrap_link_scenarios,
)
from rg_ring_collectives import (
    ALGOS, PATTERNS, all_configs, build_ring_collective, hamilton_cycle,
    mcast_applicable, multiround, replay,
)
from rg_ring_topo import RingMcastFootprint, RingTopology, verify_dr
from rg_topo import RAMP_BW, coord, nid

ROOT_DIR = Path(__file__).resolve().parents[1]
OUT = ROOT_DIR / "results" / "verify_ring_collectives_8x6.json"
ROOT_NODE = 27

RESULTS: list[dict[str, Any]] = []
_n = 0


def check(name: str, ok: bool, detail: Any = "", *, prediction: str = "") -> None:
    global _n
    _n += 1
    RESULTS.append({"id": _n, "name": name, "pass": bool(ok),
                    "detail": str(detail), "prediction": prediction})
    tag = "PASS" if ok else "FAIL"
    pred = f"  [{prediction}]" if prediction else ""
    print(f"{_n:3} {tag} {name}: {detail}{pred}")


# ---------------------------------------------------------------------------

def group_topology(topo: RingTopology) -> None:
    a = topo.audit()
    check("192 directed / 96 undirected segments",
          a["n_directed_links"] == 192 and a["n_undirected_links"] == 96,
          f"{a['n_directed_links']}/{a['n_undirected_links']}")
    t = topo.assert_same_links_as_torus()
    check("segment set equals the folded 2D torus", t["equal"],
          f"mine={t['n_mine']} torus={t['n_torus']} "
          f"only_mine={t['only_mine']}")
    check("every node bridges its two rings", a["n_bridges"] == topo.n,
          f"{a['n_bridges']} of {topo.n}")

    cyc = hamilton_cycle(topo)
    ok_len = len(cyc) == topo.n and len(set(cyc)) == topo.n
    ok_adj = True
    n_wrap = 0
    for i in range(len(cyc)):
        u, v = cyc[i], cyc[(i + 1) % len(cyc)]
        ux, uy = coord(u, topo.mx)
        vx, vy = coord(v, topo.mx)
        ring = ("row", uy) if uy == vy else ("col", ux)
        k = topo.ring_size(ring)
        du = (topo.index_on(ring, v) - topo.index_on(ring, u)) % k
        dv = (topo.index_on(ring, u) - topo.index_on(ring, v)) % k
        if min(du, dv) != 1:
            ok_adj = False
        if {topo.index_on(ring, u), topo.index_on(ring, v)} == {0, k - 1}:
            n_wrap += 1
    check("Hamiltonian cycle visits all 48 nodes once", ok_len, len(set(cyc)))
    check("every cycle step is one ring segment", ok_adj, "all 48 steps")
    check("the cycle needs a wrap segment to close (a mesh cannot)",
          n_wrap >= 1, f"{n_wrap} wrap segments used")

    row = ("row", 0)
    members = [n for n in topo.ring_nodes(row) if n != 0]
    cover = topo.mcast_cover(row, 0, members)
    covered = [n for _, ms in cover for n in ms]
    check("bidirectional cover partitions the members exactly",
          sorted(covered) == sorted(members) and len(covered) == len(set(covered)),
          f"{len(cover)} arcs, {len(covered)} members")
    fps = [topo.mcast_footprint(i, row, 0, list(ms), d, 1)
           for i, (d, ms) in enumerate(cover)]
    check("multicast charges one board and one leave per member",
          all(len(f.boards) == 1 and len(f.leaves) == len(f.dsts)
              for f in fps),
          f"boards={[len(f.boards) for f in fps]} "
          f"leaves={[len(f.leaves) for f in fps]}")


def group_semantics(topo: RingTopology) -> None:
    bad = []
    for pat, algo, tier in all_configs():
        col = build_ring_collective(topo, pat, m=1, tier=tier, algo=algo,
                                    root=ROOT_NODE)
        if not replay(col)["ok"]:
            bad.append(f"{pat}/{algo}/{tier}")
    check("all defined collectives deliver their own goal set (m=1)",
          not bad, f"{len(all_configs())} configs, {len(bad)} bad {bad[:3]}")

    ag = build_ring_collective(topo, "allgather", m=1, algo="flat")
    a2a = build_ring_collective(topo, "alltoall", m=1, algo="flat")
    check("flat allgather and flat alltoall are the SAME unicast flow set",
          Counter(ag.pairs) == Counter(a2a.pairs),
          f"{len(ag.pairs)} vs {len(a2a.pairs)} deliveries")

    g = build_ring_collective(topo, "gather", m=13, algo="dim_2phase",
                              root=ROOT_NODE)
    r = build_ring_collective(topo, "reduce", m=13, algo="dim_2phase",
                              root=ROOT_NODE)
    check("gather and reduce share the traffic shape",
          Counter(g.pairs) == Counter(r.pairs), f"{len(g.pairs)} deliveries")
    check("folding is size-preserving, so reduce boards fewer flits",
          r.n_flits < g.n_flits,
          f"reduce={r.n_flits} gather={g.n_flits} "
          f"ratio={round(g.n_flits / r.n_flits, 2)}x")

    same = []
    for pat in PATTERNS:
        for algo in ALGOS[pat]:
            if mcast_applicable(pat, algo):
                continue
            c0 = build_ring_collective(topo, pat, m=1, tier="T0", algo=algo,
                                       root=ROOT_NODE)
            c1 = build_ring_collective(topo, pat, m=1, tier="T1", algo=algo,
                                       root=ROOT_NODE)
            same.append(len(c0.xfers) == len(c1.xfers)
                        and c0.n_flits == c1.n_flits)
    check("where multicast cannot apply, T1 is identical to T0",
          all(same), f"{len(same)} (pattern,algo) pairs checked",
          prediction="multicast is a fan-out primitive only")

    bc0 = build_ring_collective(topo, "broadcast", m=1, tier="T0",
                                algo="dim_2phase", root=ROOT_NODE)
    bc1 = build_ring_collective(topo, "broadcast", m=1, tier="T1",
                                algo="dim_2phase", root=ROOT_NODE)
    check("multicast cuts boarded flits on broadcast",
          bc1.n_flits < bc0.n_flits,
          f"T1={bc1.n_flits} T0={bc0.n_flits} "
          f"ratio={round(bc0.n_flits / bc1.n_flits, 2)}x")

    base = build_ring_collective(topo, "allgather", m=1, algo="dim_2phase",
                                 tier="T1")
    mr = multiround(base, 5)
    check("R rounds contain exactly R times the transfers",
          len(mr.xfers) == 5 * len(base.xfers)
          and len(mr.phases) == len(base.phases),
          f"{len(mr.xfers)} = 5 x {len(base.xfers)}, "
          f"{len(mr.phases)} phases")

    red = build_ring_collective(topo, "reduce", m=7, algo="ring_rotate",
                                root=ROOT_NODE)
    check("a reduction payload never grows along the chain",
          all(x.nflit == 7 for x in red.xfers),
          f"nflit set = {sorted({x.nflit for x in red.xfers})}")


def _all_calendars(topo: RingTopology) -> list[tuple[str, Any, Any]]:
    out = []
    for m in (1, 13):
        for pat, algo, tier, _ in EXPORTS:
            col = build_ring_collective(topo, pat, m=m, tier=tier, algo=algo,
                                        root=ROOT_NODE)
            out.append((f"{pat}/{algo}/{tier}/m{m}", col,
                        build_calendar(topo, col)))
    return out


def group_dr(topo: RingTopology, cals: list[tuple[str, Any, Any]]) -> None:
    tot = {k: 0 for k in ("R1_link_violations", "R2_board_violations",
                          "R3_leave_violations", "R4_turn_violations",
                          "R5_voq_violations", "MC_shape_violations")}
    worst_turn = 0
    n_mcast = 0
    for name, col, cal in cals:
        v = verify_dr(topo, cal.items)
        for k in tot:
            tot[k] += v[k]
        worst_turn = max(worst_turn, v["max_turn_residency"])
        n_mcast += v["n_mcast_grants"]
    for k, label in (("R1_link_violations", "R1 segment mutual exclusion"),
                     ("R2_board_violations", "R2 boarding mutual exclusion"),
                     ("R3_leave_violations", "R3 leaving mutual exclusion"),
                     ("R4_turn_violations", "R4 turn atomicity"),
                     ("R5_voq_violations", "R5 VOQ order + static route"),
                     ("MC_shape_violations", "multicast shape")):
        check(f"{label}: zero violations across all calendars", tot[k] == 0,
              f"{tot[k]} over {len(cals)} calendars")
    check("zero turn residency (no in-ring buffering anywhere)",
          worst_turn == 0, f"max={worst_turn}")
    check("multicast grants are actually present to be checked",
          n_mcast > 0, f"{n_mcast} copy-and-continue arcs")


def group_bounds(topo: RingTopology, cals: list[tuple[str, Any, Any]]) -> None:
    fails = {"makespan_lb": [], "latency": [], "ramp": [], "bisection": [],
             "arc": [], "port": []}
    for name, col, cal in cals:
        b = cal.bounds
        if cal.makespan < b["makespan_lb"]:
            fails["makespan_lb"].append(name)
        if cal.makespan < b["latency_lb"]:
            fails["latency"].append(name)
        if cal.makespan < ramp_eject_bound(topo, col)["ramp_lb"]:
            fails["ramp"].append(name)
        if cal.makespan < bisection_bound(topo, col)["bisection_lb"]:
            fails["bisection"].append(name)
        if cal.makespan < b["arc_load_lb"]:
            fails["arc"].append(name)
        if cal.makespan < b["port_lb"]:
            fails["port"].append(name)
    for k, label in (("makespan_lb", "combined makespan bound"),
                     ("latency", "zero-contention latency bound"),
                     ("ramp", "L1 ramp ejection bound"),
                     ("bisection", "vertical bisection bound"),
                     ("arc", "busiest-segment bound"),
                     ("port", "insert/extract port bound")):
        check(f"every makespan is at or above the {label}",
              not fails[k], f"{len(fails[k])} below {fails[k][:2]}")


def group_rotation(topo: RingTopology) -> None:
    base = build_ring_collective(topo, "allgather", m=1, algo="ring_rotate")
    t1 = build_calendar(topo, base).makespan
    rows = []
    for R in (2, 5, 13, 26, 47):
        cal = build_calendar(topo, multiround(base, R))
        rows.append({"R": R, "mk": cal.makespan,
                     "ii": (cal.makespan - t1) / (R - 1),
                     "util": cal.utilization(topo)["critical_arc_util"],
                     "arc_lb": cal.bounds["arc_load_lb"]})
    iis = {r["ii"] for r in rows}
    check("rotation II_eff is constant across R", len(iis) == 1, sorted(iis))
    per_round = rows[0]["arc_lb"] / 2
    check("rotation II_eff equals the per-round busiest-segment load",
          abs(rows[0]["ii"] - per_round) < 1e-9,
          f"II_eff={rows[0]['ii']} arc load per round={per_round}",
          prediction="rotation is throughput-optimal: CONFIRMED")
    utils = [r["util"] for r in rows]
    check("rotation utilization rises monotonically with R",
          all(b > a for a, b in zip(utils, utils[1:])),
          f"{utils}")
    check("rotation utilization does NOT reach 1.0 at any practical R",
          max(utils) < 0.99,
          f"best={max(utils)} at R={rows[-1]['R']}; the fill T1={t1} dilutes it",
          prediction="plan predicted util=1.0: REFUTED")
    ii = rows[0]["ii"]
    closed = [round(ii * r["R"] / (t1 + ii * (r["R"] - 1)), 4) for r in rows]
    check("utilization matches II*R/(T1+II*(R-1)) closed form",
          all(abs(a - b) < 0.02 for a, b in zip(utils, closed)),
          f"measured={utils} closed_form={closed}")


def group_faults(topo: RingTopology) -> None:
    n_scen = (len(wrap_link_scenarios(topo)) + len(scattered_node_scenarios(topo))
              + len(repo_fault_scenarios(topo)))
    check("fault scenario set covers wrap, scattered, link, node, quadrant",
          n_scen >= 27, f"{n_scen} scenarios")

    rob = fault_sweep(topo, "broadcast", "dim_2phase", "T1", 13,
                      root=ROOT_NODE)
    immune_ok = True
    for row in rob["rows"]:
        if row["outcome"] != "immune":
            continue
        if not row["recompile_free"]:
            immune_ok = False
    check("every immune scenario is genuinely recompile free", immune_ok,
          f"{rob['n_immune']} immune of {rob['n_scenarios']}")

    legal = True
    delivers = True
    n_repair = 0
    n_recomp = 0
    for pat, algo, tier in (("broadcast", "dim_2phase", "T1"),
                            ("allgather", "dim_2phase", "T1"),
                            ("reduce", "dim_2phase", "T0"),
                            ("allreduce", "dim_2phase", "T1")):
        r = fault_sweep(topo, pat, algo, tier, 13, root=ROOT_NODE)
        n_repair += r["n_needing_repair_phase"]
        n_recomp += r["n_recompile"]
        for row in r["rows"]:
            if row["outcome"] == "recompile" and not row.get(
                    "delivers_survivor_goal"):
                delivers = False
    # one explicit recompile, re-verified from scratch
    fm = wrap_link_scenarios(topo)[0]
    from rg_ring_calendar import _fault_aware_collective
    col = _fault_aware_collective(topo, "broadcast", "dim_2phase", "T1", 13,
                                  fm, ROOT_NODE)
    cal = build_calendar(topo, col, forbidden=fm.forbidden_links(topo))
    v = verify_dr(topo, cal.items)
    used = {e for fp in cal.fps.values() for e, _ in fp.links}
    legal = v["conflict_free"] and not (used & fm.forbidden_links(topo))
    check("a recompiled calendar is D-R legal and avoids the dead segments",
          legal, f"{fm.name}: cf={v['conflict_free']} "
                 f"dead_used={len(used & fm.forbidden_links(topo))}")
    check("every recompiled calendar still delivers the survivor goal",
          delivers, "4 schemes x all scenarios")
    check("a dead node forces an EXTRA phase, not just a reschedule",
          n_repair > 0,
          f"{n_repair} of {n_recomp} recompiles need a repair phase: the dead "
          f"node was the only row-column meeting point feeding a whole ring",
          prediction="plan assumed rerouting suffices: REFUTED")

    rot = fault_sweep(topo, "allgather", "ring_rotate", "T0", 13)
    dim = fault_sweep(topo, "allgather", "dim_2phase", "T1", 13,
                      root=ROOT_NODE)
    check("rotation is infeasible under most faults, dimension-phase is not",
          rot["n_infeasible"] > 10 and dim["n_infeasible"] <= 1,
          f"rotation {rot['n_infeasible']}/{rot['n_scenarios']} infeasible, "
          f"dim_2phase {dim['n_infeasible']}/{dim['n_scenarios']}",
          prediction="rotation trades fault tolerance for throughput")

    # bypass mux: only the scattered scenarios can show a difference
    contiguous = [s for s in repo_fault_scenarios(topo)
                  if s.fault_class in ("node", "quadrant")]
    scattered = scattered_node_scenarios(topo)
    deltas = {}
    for tag, scen in (("contiguous", contiguous), ("scattered", scattered)):
        n = {}
        for bp in (True, False):
            sc = [FaultModel(s.name, s.dead_nodes, s.dead_links, bp,
                             s.fault_class, s.desc) for s in scen]
            n[bp] = fault_sweep(topo, "allgather", "dim_2phase", "T1", 13,
                                root=ROOT_NODE, scenarios=sc)["n_infeasible"]
        deltas[tag] = n[False] - n[True]
    check("a contiguous node hole needs NO ring-station bypass mux",
          deltas["contiguous"] == 0,
          f"extra infeasible without bypass = {deltas['contiguous']}",
          prediction="plan assumed a dead node always cuts the ring: REFUTED "
                     "for contiguous holes")
    check("scattered dead nodes DO need a bypass mux",
          deltas["scattered"] > 0,
          f"extra infeasible without bypass = {deltas['scattered']} "
          f"of {len(scattered)} scenarios")


def group_jitter(topo: RingTopology) -> None:
    col = build_ring_collective(topo, "allgather", m=13, tier="T1",
                                algo="dim_2phase", root=ROOT_NODE)
    cal = build_calendar(topo, col)
    exact = True
    never_worse = True
    for model in ("uniform_jitter", "distance_skew", "burst"):
        for J in (8, 64, 256):
            rel = release_offsets(topo, J, model, seed=3)
            g = replay_jitter(cal, rel, "global_shift")
            p = replay_jitter(cal, rel, "phase_shift")
            if g["makespan"] != cal.makespan + max(rel.values()):
                exact = False
            if p["makespan"] > g["makespan"]:
                never_worse = False
    check("a global shift inflates the makespan by exactly the worst lateness",
          exact, "3 models x 3 jitter levels",
          prediction="a rigid replay absorbs nothing")
    check("per-phase resynchronization is never worse than a global shift",
          never_worse, "3 models x 3 jitter levels")

    rel = release_offsets(topo, 256, "burst", seed=3)
    rp = build_calendar(topo, col, release=rel)
    v = verify_dr(topo, rp.items)
    check("a jitter-aware repack is still D-R legal", v["conflict_free"],
          f"makespan {cal.makespan} -> {rp.makespan}")
    check("repacking beats both rigid replays, so the slack is real",
          rp.makespan < cal.makespan + max(rel.values()),
          f"repack={rp.makespan} vs rigid="
          f"{cal.makespan + max(rel.values())} "
          f"(absorbed {cal.makespan + max(rel.values()) - rp.makespan})")

    delivered_base = {n: t for n, t in cal.node_done.items()}
    delivered_rp = {n: t for n, t in rp.node_done.items()}
    check("the set of nodes that receive data is unchanged under jitter",
          set(delivered_base) == set(delivered_rp)
          and replay(col)["ok"],
          f"{len(delivered_base)} nodes both ways")


def group_export(topo: RingTopology) -> None:
    idx = ROOT_DIR / "results" / "calendars" / "ring_index.json"
    check("exported calendar index exists", idx.exists(), str(idx.name))
    if not idx.exists():
        return
    entries = json.loads(idx.read_text())["entries"]
    check("every exported calendar is marked conflict free",
          all(e["conflict_free"] for e in entries), f"{len(entries)} files")
    check("every exported makespan is at or above its own bound",
          all(e["makespan"] >= e["makespan_lb"] for e in entries),
          f"{len(entries)} files")

    n_port = n_ramp = 0
    has_copy = False
    add_ok = True
    smaller = None
    for m in (1,):
        for pat, algo, tier, _ in EXPORTS:
            col = build_ring_collective(topo, pat, m=m, tier=tier, algo=algo,
                                        root=ROOT_NODE)
            cal = build_calendar(topo, col)
            recs = station_records(topo, cal)
            n_port += len(check_output_ports(recs))
            n_ramp += len(check_ramp(recs))
            for r in recs:
                leaves = [o for o in r["outs"] if o.endswith("_leave")]
                rings = [o for o in r["outs"] if "_out_" in o]
                if leaves and rings:
                    has_copy = True
                if r["opcode"] == OP_ADD and pat not in ("reduce", "allreduce"):
                    add_ok = False
            if pat == "allgather":
                key = f"{algo}/{tier}"
                if smaller is None:
                    smaller = {}
                smaller[key] = len(recs)
    check("no exported calendar drives one output port twice in a cycle",
          n_port == 0, f"{n_port} collisions")
    check("no exported calendar overruns the L1 ramp", n_ramp == 0,
          f"{n_ramp} overruns (ramp_bw={RAMP_BW})")
    check("copy-and-continue appears as ring_out + leave in one mask",
          has_copy, "at least one multicast station record")
    check("OP_ADD appears only on reducing collectives", add_ok,
          "reduce/allreduce only")
    if smaller and len(smaller) >= 2:
        mc = smaller.get("dim_2phase/T1")
        flat = smaller.get("flat/T0")
        check("the multicast calendar is far smaller in slot-table entries",
              mc is not None and flat is not None and mc < flat,
              f"dim_2phase/T1={mc} records vs flat/T0={flat} "
              f"({round(flat / mc, 1)}x smaller)" if mc and flat else smaller)


def main() -> None:
    topo = RingTopology()
    print("=== verify: 8x6 bufferless ring collectives ===\n")
    print("-- topology and footprint model --")
    group_topology(topo)
    print("\n-- collective semantics --")
    group_semantics(topo)
    cals = _all_calendars(topo)
    print(f"\n-- D-R legality ({len(cals)} calendars) --")
    group_dr(topo, cals)
    print("\n-- lower bounds --")
    group_bounds(topo, cals)
    print("\n-- rotation and utilization --")
    group_rotation(topo)
    print("\n-- faults --")
    group_faults(topo)
    print("\n-- jitter --")
    group_jitter(topo)
    print("\n-- calendar export --")
    group_export(topo)

    n_pass = sum(1 for r in RESULTS if r["pass"])
    preds = [r for r in RESULTS if r["prediction"]]
    print(f"\n{n_pass}/{len(RESULTS)} checks pass")
    print(f"{len(preds)} checks carry a labelled prediction; "
          f"{sum(1 for p in preds if 'REFUTED' in p['prediction'])} of those "
          f"record a refuted prediction")
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps({
        "n_checks": len(RESULTS), "n_pass": n_pass,
        "all_pass": n_pass == len(RESULTS),
        "audit": topo.audit(), "root": ROOT_NODE,
        "checks": RESULTS}, indent=1))
    print(f"wrote {OUT}")
    failed = [r for r in RESULTS if not r["pass"]]
    if failed:
        raise SystemExit(f"{len(failed)} checks FAILED: "
                         f"{[r['name'] for r in failed]}")


if __name__ == "__main__":
    main()
