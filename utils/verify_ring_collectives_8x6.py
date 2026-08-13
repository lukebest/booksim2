#!/usr/bin/env python3
"""Executable checks for the 8x6 ring collective calendars.

Every check is an assertion with the measured quantity printed next to it, so a
failure names the number that broke rather than just the property. Checks that
encode a PREDICTION are labelled as such, and two of them fail the prediction
the plan started from -- those are kept, inverted, and marked, because a check
that has been quietly relaxed to keep passing is worse than no check.

Groups:
    topology and footprint model
    collective semantics
    D-R legality of every calendar
    lower bounds (calendar model, then ring_base's own model)
    structural floors vs measured makespan / II / utilization, both legs
    core attachment: why 2 ports and full rings, not half rings
    rotation and utilization
    faults
    jitter
    calendar export
"""

from __future__ import annotations

import json
import math
from collections import Counter
from pathlib import Path
from typing import Any, Callable

from dse_ring_collectives_8x6 import (
    bisection_bound, ramp_eject_bound, run_base_phase,
)
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
from rg_ring_topo import (
    RingMcastFootprint, RingTopology, build_ring_plan, route_delay_spread,
    verify_dr,
)
from rg_topo import MX, MY, PITCH_H, PITCH_V, RAMP, RAMP_BW, coord, nid

ROOT_DIR = Path(__file__).resolve().parents[1]
OUT = ROOT_DIR / "results" / "verify_ring_collectives_8x6.json"
COLL = ROOT_DIR / "results" / "ring_collectives_8x6.json"
THR = ROOT_DIR / "results" / "ring_throughput_8x6.json"
BRIDGE = ROOT_DIR / "results" / "ring_bridge_8x6.json"
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
        check(f"every calendar makespan is at or above the {label}",
              not fails[k], f"{len(fails[k])} below {fails[k][:2]}")


def load_json(p: Path) -> dict[str, Any] | None:
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def group_base_bounds() -> None:
    """`ring_base` against a bound valid in ring_base's OWN machine model.

    The suite used to check only the calendar against the bounds, which is how
    ring_base came to be printed at 0.73x of a "lower bound" without anything
    failing. Both halves are asserted here: base never beats its own model's
    bound, and the cross-model comparison that produced the nonsense ratio is
    pinned to its three named causes so it cannot be mistaken for physics again.
    """
    c = load_json(COLL)
    if not c:
        check("ring_collectives_8x6.json is present for the base-bound check",
              False, "missing")
        return
    rows = [r for r in c["rows"] if r["tier"] == "T0" and r["bidir"]
            and (r.get("ring_base") or {}).get("makespan")]
    below = [(r["pattern"], r["algo"], r["m"],
              r["ring_base"]["makespan"], r["bounds_base"]["makespan_lb"])
             for r in rows
             if r["ring_base"]["makespan"] < r["bounds_base"]["makespan_lb"]]
    check("ring_base never finishes below its OWN model's lower bound",
          not below, f"{len(below)} below over {len(rows)} rows {below[:2]}")

    rot = [r for r in rows if r["algo"] == "ring_rotate"]
    tight = [r for r in rot
             if r["ring_base"]["makespan"] == r["bounds_base"]["makespan_lb"]]
    check("ring_base on rotation is EXACTLY tight in its own model",
          len(tight) == len(rot),
          f"{len(tight)}/{len(rot)} rows at ratio 1.000")

    cross = [r for r in rows
             if r["ring_base"]["makespan"] < r["bounds"]["makespan_lb"]]
    worst = min(cross, key=lambda r: r["ratios"]["base_over_cal_model_lb"],
                default=None)
    check("the calendar-model bound is NOT a bound on ring_base "
          "(documented cross-model artefact, not a violation)",
          bool(cross),
          f"{len(cross)}/{len(rows)} rows appear below it, worst "
          f"{worst['pattern']}/{worst['algo']} m={worst['m']} at "
          f"{worst['ratios']['base_over_cal_model_lb']}x"
          if worst else "none",
          prediction="reporting one bound for both legs: REFUTED")

    # cause 1: station exit 1 flit/cycle vs node ejection RAMP_BW per cycle
    g = next((r for r in rows if r["pattern"] == "gather"
              and r["algo"] == "dim_2phase" and r["m"] == 13), None)
    if g:
        check("cause 1 -- the fan-in gap is the station-exit port model",
              g["bounds"]["port_lb"] > g["bounds_base"]["ramp_lb"]
              and g["bounds"]["binding_lb"] == "port",
              f"port_lb={g['bounds']['port_lb']} (leave_ports=1) vs "
              f"eject bound {g['bounds_base']['ramp_lb']} "
              f"(RAMP_BW={g['bounds']['ramp_bw']}/cycle), base="
              f"{g['ring_base']['makespan']} sits between")

    # cause 2: +RAMP per phase, which multiplies by the phase count
    rr = next((r for r in rot if r["pattern"] == "allgather" and r["m"] == 1),
              None)
    if rr:
        gap = rr["bounds"]["latency_lb"] - rr["bounds_base"]["latency_lb"]
        n_ph = rr["shape"]["n_phases"]
        check("cause 2 -- the latency floor charges +RAMP per phase, "
              "so a 47-phase schedule inherits 47 of them",
              gap == RAMP * n_ph,
              f"gap={gap} = RAMP({RAMP}) x {n_ph} phases")

    # what used to be cause 3: the bridge. Under the folded-pitch wire setup
    # both legs pay t_turn, so an uncontended transfer must now agree to the
    # cycle -- turning or not. If this ever fails, the two models have drifted
    # apart again and the cross-model ratios above stop meaning anything.
    topo = RingTopology()
    same = []
    for pair, kind in (((0, 9), "转环"), ((0, 1), "同环")):
        plan = build_ring_plan(topo, [pair], "balanced")
        fp = topo.footprint(0, plan.paths[pair], 1)
        sim = run_base_phase(topo, [(*pair, 1)], None, 0)
        same.append((kind, fp.wire + fp.dur, sim["makespan"]))
    check("the bridge is no longer a model difference: one uncontended "
          "transfer costs the same in both legs",
          all(cal == s for _, cal, s in same),
          "; ".join(f"{k} calendar {c} vs sim {s}" for k, c, s in same)
          + f", t_turn={topo.t_turn}",
          prediction="cause 3 (free bridge in the sim) is retired")


def group_throughput() -> None:
    """The structural floors, and the measurements that must respect them.

    This group exists because the interesting failure mode is not "a schedule is
    slow", it is "a number was printed below a floor and nobody noticed". The
    floors here are the weak ones (relaying and local combining allowed, so they
    hold for T0 and T1 alike and for every algorithm), which is exactly what
    makes a violation meaningful.
    """
    d = load_json(THR)
    if not d:
        check("ring_throughput_8x6.json is present", False,
              "missing -- run dse_ring_throughput_8x6.py")
        return
    th = {(t["pattern"], t["m"]): t for t in d["theory"]}
    rows = d["rows"]

    legs = [(r, leg, L) for r in rows for leg, L in
            (("calendar", r["calendar"]), ("ring_base", r["ring_base"]))
            if L["T1"] is not None]
    bad = [(leg, r["pattern"], r["algo"], r["tier"], r["m"], L["T1"])
           for r, leg, L in legs
           if L["T1"] < th[(r["pattern"], r["m"])]["makespan_lb"]]
    check("no measured makespan is below the structural floor, either leg",
          not bad, f"{len(legs)} leg-rows checked, {len(bad)} below {bad[:2]}")

    bad = [(leg, r["pattern"], r["algo"], r["m"], R, v["per_round"])
           for r, leg, L in legs for R, v in L["by_rounds"].items()
           if v["per_round"] < th[(r["pattern"], r["m"])]["II_lb"] - 1e-9]
    check("no amortised per-round time T_R/R is below the capacity floor",
          not bad, f"{len(bad)} below {bad[:2]}")

    # the same is NOT true of the marginal II estimate, and that is a property of
    # the estimator, not of the hardware -- pin it so the two never get conflated
    def dips(rounds: str) -> list[tuple[Any, ...]]:
        return [(r["pattern"], r["algo"], leg, v["II_eff"],
                 th[(r["pattern"], r["m"])]["II_lb"])
                for r, leg, L in legs for R, v in L["by_rounds"].items()
                if R == rounds and v["II_eff"]
                and v["II_eff"] < th[(r["pattern"], r["m"])]["II_lb"]]
    d5, d13 = dips("5"), dips("13")
    w5 = min(d5, key=lambda x: x[3] / x[4], default=None)
    w13 = min(d13, key=lambda x: x[3] / x[4], default=None)
    check("II_eff=(T_R-T1)/(R-1) may dip below the capacity floor at finite R "
          "(estimator artefact: the first instance already did some of the "
          "work being amortised away), and the dip shrinks with R",
          bool(d5) and bool(d13) and w13[3] / w13[4] > w5[3] / w5[4],
          f"R=5: {len(d5)} dips, worst {w5[3]} vs floor {w5[4]} = "
          f"{w5[3] / w5[4]:.3f}x ({w5[0]}/{w5[1]}/{w5[2]}); "
          f"R=13: {len(d13)} dips, worst "
          + (f"{w13[3]} vs {w13[4]} = {w13[3] / w13[4]:.3f}x "
             f"({w13[0]}/{w13[1]}/{w13[2]})" if w13 else "none"),
          prediction="II_eff is a safe throughput bound to check: REFUTED")

    bad = [(leg, r["pattern"], r["m"], R, u["global_util"],
            u["critical_arc_util"])
           for r, leg, L in legs for R, v in L["by_rounds"].items()
           for u in [v["util"]]
           if u["global_util"] > 1 + 1e-6 or u["critical_arc_util"] > 1 + 1e-6]
    check("no utilization exceeds 100% (measured over each run's own span)",
          not bad, f"{len(bad)} over {bad[:2]}")

    bad = [(leg, r["pattern"], r["m"], R)
           for r, leg, L in legs for R, v in L["by_rounds"].items()
           if v["util"]["useful_global"] > v["util"]["global_util"] + 1e-6]
    check("useful (minimum-hop) utilization never exceeds occupied utilization",
          not bad, f"{len(bad)} over {bad[:2]}")

    taxes = {r["cal_hop_tax"] for r in rows}
    check("the calendar spends EXACTLY the minimum-hop arc budget: hop tax 1.00",
          taxes == {1.0}, f"distinct calendar hop taxes: {sorted(taxes)}")

    fanin = max((r for r in rows if r["pattern"] in ("gather", "reduce")
                 and r.get("base_hop_tax")),
                key=lambda r: r["base_hop_tax"], default=None)
    if fanin:
        u = fanin["ring_base"]["by_rounds"]["13"]["util"]
        check("ring_base pays for its fan-in makespan in wasted arc bandwidth, "
              "not in useful work",
              fanin["base_hop_tax"] > 3
              and u["useful_global"] < u["global_util"] / 3,
              f'{fanin["pattern"]}/{fanin["algo"]} m={fanin["m"]}: hop tax '
              f'{fanin["base_hop_tax"]}x, occupied {u["global_util"]} vs '
              f'useful {u["useful_global"]}; the calendar delivers the same '
              f'flow set at '
              f'{fanin["calendar"]["by_rounds"]["13"]["util"]["global_util"]} '
              f'with hop tax {fanin["cal_hop_tax"]}x')

    at1 = th[("alltoall", 1)]
    check("alltoall is the one pattern whose II floor is the CUT, and relaying "
          "cannot help it (distinct payload per src-dst pair)",
          at1["binding_capacity"] == "cut" and at1["cut_lb"] == 48,
          f'cut={at1["cut_lb"]} port={at1["port_lb"]} ramp={at1["ramp_lb"]}: '
          f'{at1["cut_witness"]}')

    lat = {t["lat_distance_cy"] for t in d["theory"]}
    check("every m=1 collective on this fabric is LATENCY bound, not bandwidth "
          "bound: the floor is pure wire delay",
          all(t["binding"] == "latency" for t in d["theory"] if t["m"] == 1),
          f"distance floors {sorted(lat)} cy vs capacity floors "
          f'{sorted({t["capacity_lb"] for t in d["theory"] if t["m"] == 1})}')

    m13 = [t for t in d["theory"] if t["m"] == 13]
    check("at m=13 the binding floor moves off latency for the three "
          "many-item patterns",
          {t["pattern"] for t in m13 if t["binding"] != "latency"}
          == {"gather", "allgather", "alltoall"},
          ", ".join(f'{t["pattern"]}:{t["binding"]}' for t in m13))

    # the floors here must be weaker than the attachment study's flat-demand
    # convention, otherwise one of the two documents is wrong about relaying
    att = load_json(ROOT_DIR / "results" / "ring_attach_8x6.json")
    if att:
        a = next(s for s in att["schemes"] if s["key"] == "A_full_2port")
        worse = [p for p in ("broadcast", "reduce", "gather", "allreduce",
                             "allgather", "alltoall")
                 if th[(p, 1)]["II_lb"] > a["bounds"][f"{p}/T0"]["lb"]]
        check("the relay-aware floors are never stronger than the attachment "
              "study's no-relay convention (the two models are consistent)",
              not worse, f"stronger on: {worse or 'none'}")

    hl = {(h["pattern"], h["m"]): h for h in d["headline"]}
    g = hl[("gather", 1)]
    check("the calendar is within a few percent of the gather throughput floor",
          g["cal_T0"]["per_round_over_lb"] <= 1.10,
          f'gather m=1: {g["cal_T0"]["best_II"]["per_round"]} cy/round vs floor '
          f'{g["bound"]["II_lb"]} = {g["cal_T0"]["per_round_over_lb"]}x')

    a = hl[("alltoall", 13)]
    check("under pipelining the calendar beats ring_base on throughput for the "
          "bandwidth-bound patterns",
          a["base_over_cal_T0"]["per_round"] > 1.2,
          f'alltoall m=13: base {a["base"]["best_II"]["per_round"]} vs calendar '
          f'{a["cal_T0"]["best_II"]["per_round"]} cy/round = '
          f'{a["base_over_cal_T0"]["per_round"]}x')

    reasm = max((v["max_reasm_occupancy"] for r, leg, L in legs
                 if leg == "ring_base" for v in L["by_rounds"].values()),
                default=0)
    check("ring_base's pipelined throughput assumes a reorder buffer far larger "
          "than the 64 flits it is provisioned with",
          reasm > 64,
          f"peak reassembly occupancy {reasm} flits over all pipelined runs")


def group_attach() -> None:
    """Why the fabric is attached the way it is (scheme A), not by assumption.

    These checks re-derive the attachment from structure only -- port budget,
    connectivity, cut capacity, wire length -- with an independent model
    (`rg_ring_attach`), and then confront that model with the numbers the main
    pipeline produced. Two independent computations agreeing on 12 / 576 / 48 /
    56 is the reason to trust either.
    """
    import rg_ring_attach as at

    rows = {s.key: at.analyse(s) for s in at.schemes()}
    at.rank(rows.values())
    A = rows["A_full_2port"]

    check("recommended attachment: every core spends its 2 ports on "
          "1 row ring + 1 column ring",
          A["ports_min"] == A["ports_max"] == 2 and A["ports_ok"],
          f"ports/core = {A['ports_min']}, both dims tappable = "
          f"{A['dims_directly_reachable']}")
    check("2 ring ports exactly match the L1 ramp (RAMP_BW=2), so neither "
          "side starves the other",
          A["core_rate"] == RAMP_BW and A["ramp_match"],
          f"core rate {A['core_rate']} flit/cy vs ramp {RAMP_BW}")
    check("co-located bridge: turning needs no extra ring tap",
          A["structure"]["n_extra_tap_bridges"] == 0
          and A["structure"]["n_bridges"] == 48,
          f"{A['structure']['n_bridges']} bridges, "
          f"{A['structure']['n_extra_tap_bridges']} extra taps")

    # the independent model must reproduce the main pipeline's bounds
    coll = json.loads(COLL.read_text())
    a2a = next(r for r in coll["rows"] if r["pattern"] == "alltoall"
               and r["tier"] == "T0" and r["m"] == 1 and r["algo"] == "flat")
    b = a2a["bounds"]
    ax = A["cuts"]["x"]
    cut_row = next(r for r in ax["per_cut"] if r["at"] == 4)
    a2a_lb = A["bounds"]["alltoall/T0"]
    check("independent attachment model reproduces the pipeline's bisection "
          "numbers",
          cut_row["cap_per_dir"] == b["cut_width_directed"]
          and a2a_lb["cut_lb"] == b["bisection_lb"],
          f"cut width {cut_row['cap_per_dir']} vs {b['cut_width_directed']}, "
          f"bisection LB {a2a_lb['cut_lb']} vs {b['bisection_lb']}")
    # flat alltoall at m=1 has one phase and no relaying, so its latency floor in
    # ring_base's own model is exactly "worst pair's wire delay + one flit time"
    check("independent attachment model reproduces the pipeline's latency "
          "floor (diameter + one flit time)",
          A["distance"]["diameter_cy"] + a2a["m"]
          == a2a["bounds_base"]["latency_lb"],
          f"diameter {A['distance']['diameter_cy']} cy + m={a2a['m']} vs "
          f"base-model latency LB {a2a['bounds_base']['latency_lb']}")
    check("folded full rings cost exactly 2x mesh wire length, which is where "
          "the repo's sigma=2 yardstick comes from",
          A["structure"]["wire_vs_mesh"] == 2.0,
          f"{A['structure']['wire_pitches']} pitches vs mesh "
          f"{A['structure']['mesh_wire_pitches']} = "
          f"{A['structure']['wire_vs_mesh']}x")

    # half-SPAN rings: the seam is the whole story
    c0 = rows["C0_rowhalf_noseam"]
    half = (MX // 2) * MY
    expect = 2 * half * (half - 1)
    check("half-span rings with <=2 ports/core DISCONNECT the fabric unless "
          "the seam is repaired",
          not c0["distance"]["connected"]
          and c0["distance"]["reachable_pairs"] == expect
          and c0["cuts"]["x"]["min_cap_per_dir"] == 0,
          f"{c0['distance']['reachable_pairs']}/"
          f"{c0['distance']['total_pairs']} pairs reachable "
          f"(two {half}-core halves), x-cut capacity "
          f"{c0['cuts']['x']['min_cap_per_dir']}",
          prediction="CONFIRMED: a column ring never changes x and a split "
                     "row ring never spans the seam")
    c = rows["C_rowhalf_seam"]
    check("seam bridges restore connectivity but halve the crossing capacity, "
          "and the bandwidth-bound collectives inherit it",
          c["distance"]["connected"]
          and c["cuts"]["x"]["min_cap_per_dir"] * 2
          == A["cuts"]["x"]["min_cap_per_dir"]
          and c["bounds"]["alltoall/T0"]["lb"]
          == 2 * A["bounds"]["alltoall/T0"]["lb"],
          f"x-cut {c['cuts']['x']['min_cap_per_dir']} vs "
          f"{A['cuts']['x']['min_cap_per_dir']} flit/cy; alltoall LB "
          f"{c['bounds']['alltoall/T0']['lb']} vs "
          f"{A['bounds']['alltoall/T0']['lb']} cy")
    e = rows["E_bothhalf_seam"]
    check("splitting both dimensions saves wire but loses both cuts",
          e["structure"]["wire_pitches"] < A["structure"]["wire_pitches"]
          and e["bounds"]["alltoall/T0"]["lb"]
          > A["bounds"]["alltoall/T0"]["lb"],
          f"wire {e['structure']['wire_pitches']} vs "
          f"{A['structure']['wire_pitches']} pitches, alltoall LB "
          f"{e['bounds']['alltoall/T0']['lb']} vs "
          f"{A['bounds']['alltoall/T0']['lb']} cy")
    f = rows["F_rowhalf_stagger"]
    check("staggered half rings keep the cut and need no seam bridge, but a "
          "wrap-around loop cannot be folded under 2 pitches",
          f["cuts"]["x"]["min_cap_per_dir"] == A["cuts"]["x"]["min_cap_per_dir"]
          and f["structure"]["max_link_pitches"] > 2
          and f["structure"]["wire_pitches"] > A["structure"]["wire_pitches"],
          f"x-cut {f['cuts']['x']['min_cap_per_dir']}, longest wire "
          f"{f['structure']['max_link_pitches']} pitches (A: "
          f"{A['structure']['max_link_pitches']}), wire "
          f"{f['structure']['wire_pitches']} vs "
          f"{A['structure']['wire_pitches']}",
          prediction="REFUTED the cheap reading of 'half rings save metal': "
                     "a staggered half ring costs MORE (352 vs 328)")

    # half-LANE rings: metal-constant, so the loss is latency not bandwidth
    bl = rows["B_full_1lane"]
    lat_ratio = bl["distance"]["avg_lat_cy"] / A["distance"]["avg_lat_cy"]
    same = all(bl["bounds"][k]["lb"] == A["bounds"][k]["lb"]
               for k in A["bounds"])
    check("one-lane rings at metal parity (half the wires, 2x width) tie every "
          "bound and pay only in latency",
          same and bl["structure"]["wire_pitches_x_width"]
          == A["structure"]["wire_pitches_x_width"] and lat_ratio > 1.5,
          f"all 12 bounds equal, metal {bl['structure']['wire_pitches']}x"
          f"{bl['structure']['segment_width']} = "
          f"{bl['structure']['wire_pitches_x_width']}, average latency "
          f"{lat_ratio:.2f}x, diameter {bl['distance']['diameter_cy']} vs "
          f"{A['distance']['diameter_cy']} cy")

    # port budget: 1 port is the one choice that hurts every collective
    g = rows["G_row_only_1port"]
    check("spending only 1 port per core doubles every port-bound collective",
          g["core_rate"] == 1
          and g["bounds"]["allgather/T1"]["lb"]
          == 2 * A["bounds"]["allgather/T1"]["lb"] - 1,
          f"allgather/T1 LB {g['bounds']['allgather/T1']['lb']} vs "
          f"{A['bounds']['allgather/T1']['lb']} cy, gather/T0 "
          f"{g['bounds']['gather/T0']['lb']} vs "
          f"{A['bounds']['gather/T0']['lb']} cy")
    h = rows["H_two_on_row"]
    check("spending both ports on one ring keeps the bounds but pays 48 extra "
          "ring taps and longer paths",
          h["bounds"]["alltoall/T0"]["lb"] == A["bounds"]["alltoall/T0"]["lb"]
          and h["structure"]["n_extra_tap_bridges"] == 48
          and h["distance"]["avg_hops"] > A["distance"]["avg_hops"],
          f"extra taps {h['structure']['n_extra_tap_bridges']}, average hops "
          f"{h['distance']['avg_hops']} vs {A['distance']['avg_hops']}")

    gates = {k: [r["key"] for r in rows.values() if not r["gates"][k]]
             for k in A["gates"]}
    beats_a = [r["key"] for r in rows.values()
               if r["best_vs_A"] is not None and r["best_vs_A"] < 1.0]
    check("scheme A passes all four physical gates and no other candidate "
          "beats it on a single one of the 12 bounds",
          not A["fails"] and A["worst_vs_A"] == 1.0 and not beats_a,
          f"schemes with any bound below A: {beats_a or 'none'}; "
          + "; ".join(f"{k}: {v or 'all pass'}" for k, v in gates.items()))


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


def group_wire(topo: RingTopology) -> None:
    """The link-delay setup itself, and the two things it must not break."""
    rows = [topo.link_lat(("row", 0), i) for i in range(topo.mx)]
    cols = [topo.link_lat(("col", 0), i) for i in range(topo.my)]
    check("folded segments: 2 core pitches each except the two fold ends, "
          "which are 1",
          sorted(set(rows)) == [PITCH_H, 2 * PITCH_H]
          and sorted(set(cols)) == [PITCH_V, 2 * PITCH_V]
          and rows.count(PITCH_H) == 2 and cols.count(PITCH_V) == 2,
          f"row {rows} (pitch {PITCH_H}), col {cols} (pitch {PITCH_V})")
    check("one lane of a ring is 2(k-1) pitches of wire, i.e. exactly the "
          "2x-mesh metal the attachment study charges",
          topo.ring_wire(("row", 0)) == (2 * topo.mx - 2) * PITCH_H
          and topo.ring_wire(("col", 0)) == (2 * topo.my - 2) * PITCH_V,
          f"row {topo.ring_wire(('row', 0))} cy = "
          f"{2 * topo.mx - 2} x {PITCH_H}, col "
          f"{topo.ring_wire(('col', 0))} cy = {2 * topo.my - 2} x {PITCH_V}")

    # the two short segments sit half a ring apart, so a tie in hops stays a tie
    # in delay and hop-minimal routing is still latency-minimal
    spread = route_delay_spread(topo, [(s, d) for s in range(topo.n)
                                      for d in range(topo.n) if s != d])
    check("uneven segments do NOT break latency invariance of the minimal "
          "route set (the two short segments are half a ring apart)",
          spread["latency_invariant"],
          f"{spread['n_pairs']} pairs, worst spread "
          f"{spread['max_wire_spread']} cy")

    # the two-phase dimension-order route is delay-optimal, so the calendars are
    # not paying for their routing discipline
    worst = (0, None)
    for s in range(topo.n):
        for d in range(topo.n):
            if s == d:
                continue
            best = min(topo.footprint(0, p, 1).wire
                       for p in topo.candidates(s, d))
            dij = topo.wire_distance(s, d)
            if best - dij > worst[0]:
                worst = (best - dij, (s, d, best, dij))
    check("the two-phase RC/CR route set is delay-optimal over all routes "
          "(Dijkstra over (core, ring) states finds nothing shorter)",
          worst[0] == 0, f"worst excess {worst[0]} cy {worst[1] or ''}")

    turning = topo.wire_distance(0, 9)
    straight = topo.wire_distance(0, 1) + topo.wire_distance(1, 9)
    row_hop = topo.link_lat(("row", 0), 0)
    col_hop = topo.link_lat(("col", 1), 0)
    check("changing rings costs t_turn, and it is charged once per ring "
          "change, not per hop",
          turning == row_hop + topo.t_turn + col_hop
          and turning == straight + topo.t_turn,
          f"0->9 = {turning} cy = {row_hop} (row hop) + {topo.t_turn} "
          f"(bridge) + {col_hop} (col hop); diameter "
          f"{max(topo.wire_distance(0, d) for d in range(topo.n))} cy")

    # with a 10-cycle bridge, relaying through L1 is CHEAPER than turning, which
    # is why the floor charges min(t_turn, RAMP) -- see dse_ring_throughput
    check("a 10-cycle bridge makes the L1 relay the cheaper way to change "
          "dimension, so charging a turn would NOT be a floor",
          topo.t_turn > RAMP
          and topo.wire_distance(0, 9, turn_cost=RAMP) < topo.wire_distance(0, 9),
          f"turn {topo.t_turn} cy vs L1 relay {RAMP} cy; 0->9 "
          f"{topo.wire_distance(0, 9, turn_cost=RAMP)} vs "
          f"{topo.wire_distance(0, 9)} cy",
          prediction="REFUTED 「转维必须过桥」：按维分解的拍图靠落 L1 中继"
                     "绕开了桥，这也是它们能压过 flat 的原因")


def group_bridge() -> None:
    """The 48 transfer FIFOs: occupancy, what fills them, what it costs.

    This is the buffer the rings do not have. Every check here is about the same
    claim: with a 10-cycle bridge the transfer FIFO stops being an
    implementation detail and becomes the baseline's binding resource.
    """
    b = load_json(BRIDGE)
    if not b:
        check("ring_bridge_8x6.json is present for the bridge census", False,
              "missing; run dse_ring_bridge_8x6.py")
        return
    per = {(r["pattern"], r["m"]): r for r in b["per_pattern"]}
    a2a1, a2a13 = per[("alltoall", 1)], per[("alltoall", 13)]
    depth = b["params"]["fifo_depth"]
    resv = b["params"]["resv_tx"]

    check("every core is a bridge and every bridge is used by flat alltoall",
          a2a1["n_bridges_touched"] == MX * MY,
          f"{a2a1['n_bridges_touched']} of {MX * MY} bridges hold entries")
    over = [(r["pattern"], r["m"], r["peak_max"]) for r in b["per_pattern"]
            if r["peak_max"] > depth + resv]
    check("no bridge ever holds more than fifo_depth + resv_tx entries",
          not over, f"peak <= {depth}+{resv} on all "
          f"{len(b['per_pattern'])} runs, worst "
          f"{max(r['peak_max'] for r in b['per_pattern'])}")
    check("the mean depth in use is far below the peak, so the depth is paid "
          "for a transient",
          a2a13["mean_max"] < a2a13["peak_max"],
          f"alltoall m=13: peak {a2a13['peak_max']} entries vs mean "
          f"{a2a13['mean_max']} (max over bridges), "
          f"{a2a13['full_frac_max']:.1%} of cycles at capacity")

    # the control: a dimension-decomposed schedule relays instead of turning
    ctl = [r for r in b["no_turn_control"] if r["n_bridges_touched"] == 0]
    check("a dimension-decomposed calendar needs ZERO bridge buffer: it "
          "relays through L1 instead of turning in flight",
          len(ctl) == len(b["no_turn_control"]),
          f"{len(ctl)}/{len(b['no_turn_control'])} control runs touch no "
          "bridge at all",
          prediction="CONFIRMED: 拍图不是把桥用得更好，是根本不用桥")

    # depth is a first-order performance knob, not a detail
    d13 = next(d for d in b["depth_sweep"]
               if d["pattern"] == "alltoall" and d["m"] == 13)
    mono = all(d13["rows"][i]["makespan"] >= d13["rows"][i + 1]["makespan"]
               for i in range(len(d13["rows"]) - 1))
    check("a full bridge deflects instead of blocking, so FIFO depth buys "
          "makespan monotonically",
          mono, "depth " + " -> ".join(f"{r['fifo_depth']}:{r['makespan']}"
                                       for r in d13["rows"]))
    check("depth 1 is not a design point under a 10-cycle bridge",
          d13["cost_of_depth1"] > 2.0,
          f"alltoall m=13: depth 1 costs {d13['cost_of_depth1']}x the best "
          f"depth, knee at {d13['knee_depth']} entries")

    # and the requirement is created by the turn latency, not just by load
    t13 = next(t for t in b["turn_sweep"]
               if t["pattern"] == "alltoall" and t["m"] == 13)
    check("occupancy and deflection are functions of the turn latency: the "
          "10-cycle bridge is what fills the FIFOs",
          t13["mean_10_over_1"] > 1.5 and t13["deflect_10_over_1"] > 2.0
          and t13["makespan_10_over_1"] > 1.2,
          f"t_turn 1 -> 10 at m=13: mean depth x{t13['mean_10_over_1']}, "
          f"bridge deflections x{t13['deflect_10_over_1']}, makespan "
          f"x{t13['makespan_10_over_1']}")

    # the fan-in shapes are the ones with a hotspot
    g13 = per[("gather", 13)]
    check("fan-in loads the bridges unevenly, all-to-all does not",
          g13["mean_max"] / max(1e-9, g13["mean_avg"])
          > a2a13["mean_max"] / max(1e-9, a2a13["mean_avg"]),
          f"gather m=13 hottest/average = "
          f"{g13['mean_max'] / g13['mean_avg']:.2f} (node "
          f"{g13['hot_node']['node']}), alltoall = "
          f"{a2a13['mean_max'] / a2a13['mean_avg']:.2f}")

    # The calendar has no FIFO, but that does not make its bridges free: what
    # it needs instead is a bridge that can PIPELINE, because the slot table
    # deliberately overlaps crossings. Measuring both the same way is the only
    # way the comparison is not rigged.
    t_turn = b["wire"]["t_turn"]
    cal = {(c["pattern"], c["algo"], c["m"]): c for c in b["calendar"]}
    cflat = cal[("alltoall", "flat", 1)]
    cdim = cal[("alltoall", "dim_2phase", 1)]
    check("the calendar pays for the bridge in pipelining, not in queueing: a "
          "unicast slot table overlaps crossings, a dimension-decomposed one "
          "has none",
          cflat["peak_max"] > 1 and cdim["peak_max"] == 0,
          f"alltoall m=1: flat calendar {cflat['peak_max']} crossings at once "
          f"per bridge (mean {cflat['mean_max']}), dim_2phase "
          f"{cdim['peak_max']}; a crossing lasts t_turn+m*sigma = "
          f"{t_turn + 1} cy so at most "
          f"{2 * (t_turn + 1)} can overlap",
          prediction="「拍图不需要桥 buffer」只对按维分解成立，"
                     "flat 拍图要求桥是流水的")

    # Depth 4 vs 17 concurrent crossings is not a fair fight, so re-run the
    # comparison at matched bridge capacity. The calendar still wins, by less.
    d13 = next(d for d in b["depth_sweep"]
               if d["pattern"] == "alltoall" and d["m"] == 13)
    at16 = next(r for r in d13["rows"] if r["fifo_depth"] == 16)
    at4 = next(r for r in d13["rows"] if r["fifo_depth"] == 4)
    cal13 = cal[("alltoall", "flat", 13)]
    check("the calendar still wins at MATCHED bridge capacity, so the "
          "baseline's gap is not an artefact of an under-provisioned FIFO",
          at16["makespan"] > cal13["makespan"],
          f"alltoall m=13: ring_base {at4['makespan']} at depth 4 and "
          f"{at16['makespan']} at depth 16 vs calendar {cal13['makespan']} "
          f"= {at16['makespan'] / cal13['makespan']:.2f}x "
          f"(default depth exaggerates the gap by "
          f"{at4['makespan'] / at16['makespan']:.2f}x)")

    # and the sim's own accounting has to add up
    thr = load_json(THR)
    rows = [r for r in (thr or {}).get("rows", [])
            if r["pattern"] == "alltoall" and r["algo"] == "flat"
            and r["m"] == 13]
    br = rows[0]["ring_base"]["by_rounds"]["1"]["bridge"] if rows else None
    check("the throughput sweep and the bridge census agree on the peak",
          br is not None and br["bridge_peak_max"] == a2a13["peak_max"],
          f"throughput leg {br and br['bridge_peak_max']} vs census "
          f"{a2a13['peak_max']} entries")


def main() -> None:
    topo = RingTopology()
    print("=== verify: 8x6 bufferless ring collectives ===\n")
    print("-- topology and footprint model --")
    group_topology(topo)
    print("\n-- link delay setup --")
    group_wire(topo)
    print("\n-- collective semantics --")
    group_semantics(topo)
    cals = _all_calendars(topo)
    print(f"\n-- D-R legality ({len(cals)} calendars) --")
    group_dr(topo, cals)
    print("\n-- lower bounds --")
    group_bounds(topo, cals)
    print("\n-- ring_base against its own model's bounds --")
    group_base_bounds()
    print("\n-- structural floors, II and bandwidth utilization --")
    group_throughput()
    print("\n-- bridge transfer FIFOs --")
    group_bridge()
    print("\n-- core attachment: why 2 ports, full rings --")
    group_attach()
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
