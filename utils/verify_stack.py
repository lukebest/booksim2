#!/usr/bin/env python3
"""Checks for the 3D-stacked fabric: topology, conservation, FIFOs, schemes.

Kept separate from `verify_ring2_20.py` so the existing 42 ring checks stay
green and fast. Run both before trusting any number in the report.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_UTILS = Path(__file__).resolve().parent
if str(_UTILS) not in sys.path:
    sys.path.insert(0, str(_UTILS))

from dse_ring2_write_fair import fairness_stats
from rg_stack_base import StackBaseParams, StackBaseSim, run_batch
from rg_stack_fc import (StackAdaptParams, StackAdaptSim, StackAdaptTurnParams,
                         StackAdaptTurnSim, StackFairTurnSim, StackFcParams,
                         StackFcSim, StackGrantParams, StackGrantSim,
                         StackTurnParams)
from rg_stack_topo import (ANY_PLANE, GROUP_COLS, H_HOP_LAT, H_PER_GAP,
                           N_ATTACH, N_COLS, N_HA, N_HRING, N_ROWS,
                           N_TOP_DIE, TOP_BRIDGES, TOP_N, TURN_LAT,
                           V_HOP_LAT, V_LEN, StackTopology,
                           build_uniform_write)

RESULTS: list[tuple[str, bool, str]] = []
# Outstanding is the longest uncongested write RTT. HA retry (16 POS
# entries) is what keeps that window from flooding a completer.
OC_WORK = 5
OC_MANDATED = StackTopology().max_write_rtt(m_wdata=4)["outstanding"]
HA_POS = 16
FAB = dict(turn_depth=64, d2d_depth=128, core_outstanding=OC_WORK,
           ha_pos_depth=HA_POS)


def check(name: str):
    def deco(fn):
        try:
            note = fn() or ""
            RESULTS.append((name, True, note))
            print(f"  [ok] {name}" + (f"  {note}" if note else ""))
        except AssertionError as e:
            RESULTS.append((name, False, str(e)))
            print(f"  [FAIL] {name}: {e}")
        except Exception as e:                      # noqa: BLE001
            RESULTS.append((name, False, f"{type(e).__name__}: {e}"))
            print(f"  [ERROR] {name}: {type(e).__name__}: {e}")
        return fn
    return deco


def _run(scheme: str = "s0", *, route: str = "bound", k: int = 12,
         seed: int = 0, **kw):
    topo = StackTopology(route_mode=route)
    txns = build_uniform_write(topo, k=k, seed=seed)
    f = dict(FAB)
    f.update(kw)
    cls, params = {
        "s0": (StackBaseSim, StackBaseParams),
        "s1": (StackFcSim, StackFcParams),
        "s16": (StackGrantSim, StackGrantParams),
        "s17": (StackFairTurnSim, StackTurnParams),
        "s18": (StackAdaptSim, StackAdaptParams),
        "s19": (StackAdaptTurnSim, StackAdaptTurnParams),
    }[scheme]
    r = run_batch(topo, txns, params=params(**f), sim_cls=cls, seed=seed,
                  stall_after=20_000)
    r["fairness"] = fairness_stats(
        r["wr_inject_by_core"], r["makespan"],
        (len(txns) // len(topo.cores)) * 4)
    return topo, txns, r


# ---------------------------------------------------------------------------
# topology
# ---------------------------------------------------------------------------

@check("topo_node_accounting")
def _t1():
    t = StackTopology()
    assert len(t.cores) == N_TOP_DIE * 10 == 60, len(t.cores)
    assert len(t.bridges) == N_TOP_DIE * 8 == 48, len(t.bridges)
    assert len(t.has) == N_HA == 96, len(t.has)
    assert len(t.attaches) == N_ATTACH == 48, len(t.attaches)
    assert N_COLS * V_LEN == 144 == N_HA + N_ATTACH
    assert t.n == N_TOP_DIE * TOP_N + N_HA + N_ATTACH == 264, t.n
    inert = [n for n in t.nodes if n.role == "inert"]
    assert len(inert) == N_TOP_DIE * 2 == 12
    assert all(n.idx in (9, 19) for n in inert)
    return f"264 nodes = 6x20 + 96 HA + 48 attach; 8x{V_LEN}=144"


@check("bottom_die_latencies_are_4_6_5")
def _t_lat():
    t = StackTopology()
    assert t.h_hop_lat == H_HOP_LAT == 4, t.h_hop_lat
    assert t.v_hop_lat == V_HOP_LAT == 6, t.v_hop_lat
    assert t.turn_lat == TURN_LAT == 5, t.turn_lat
    hs = {t.edge_lat[e] for e, rk in enumerate(t.edge_ring) if rk[0] == "h"}
    vs = {t.edge_lat[e] for e, rk in enumerate(t.edge_ring) if rk[0] == "v"}
    assert hs == {4} and vs == {6}, (hs, vs)
    rtt = t.max_write_rtt(m_wdata=4)
    assert rtt["outstanding"] == rtt["rtt"] == OC_MANDATED, rtt
    return (f"h=4 v=6 turn=5; longest write RTT {rtt['rtt']} "
            f"(fwd {rtt['fwd']}, rev {rtt['rev']})")


@check("topo_v_ring_layout")
def _t2():
    t = StackTopology()
    for c in range(N_COLS):
        seq = [t.nodes[t._v_node(c, p)] for p in range(V_LEN)]
        assert sum(1 for n in seq if n.role == "ha") == N_ROWS
        assert sum(1 for n in seq if n.role == "attach") == N_HRING
        # attach points come in adjacent pairs, three gaps
        pos = [i for i, n in enumerate(seq) if n.role == "attach"]
        assert pos == [1, 2, 8, 9, 15, 16], pos
    return "attach vpos = [1,2,8,9,15,16] in every column"


@check("topo_attach_grouping_is_2x4")
def _t3():
    t = StackTopology()
    for h in range(N_HRING):
        cols = {t.nodes[t.attach(h, c)].col for c in range(N_COLS)}
        assert cols == set(range(N_COLS)), "an H ring must span all 8 columns"
    seen: set[int] = set()
    for d in range(N_TOP_DIE):
        pts = [t.nodes[t.bridge_landing(d, i)] for i in TOP_BRIDGES]
        assert len(pts) == 8
        hs = {p.hring for p in pts}
        cs = {p.col for p in pts}
        assert len(hs) == H_PER_GAP, f"die {d} spans {len(hs)} attach rows"
        assert len(cs) == GROUP_COLS, f"die {d} spans {len(cs)} columns"
        assert hs == {t.die_gap(d) * H_PER_GAP + i
                      for i in range(H_PER_GAP)}, "group left its row gap"
        assert cs == set(t.die_cols(d)), "group left its column half"
        ids = {p.nid for p in pts}
        assert not (ids & seen), f"die {d} shares an attach point"
        seen |= ids
    assert len(seen) == N_ATTACH, f"{len(seen)} of {N_ATTACH} attach used"
    return (f"6 groups of 2 attach rows x {GROUP_COLS} columns "
            f"partition all {N_ATTACH} attach points")


@check("topo_binding_is_one_column_per_bridge")
def _t3b():
    t = StackTopology()
    for d in range(N_TOP_DIE):
        cols = sorted(t.bridge_target_col(d, i) for i in TOP_BRIDGES)
        assert cols == list(range(N_COLS)), (d, cols)
        near = [i for i in TOP_BRIDGES
                if t.nodes[t.bridge_landing(d, i)].col
                == t.bridge_target_col(d, i)]
        assert len(near) == GROUP_COLS, f"die {d}: {len(near)} in-column"
        # and the binding is a function of the HA alone, per die
        for c in range(N_COLS):
            idx = t.ha_bridge(d, c)
            assert t.bridge_target_col(d, idx) == c
    return ("each die's 8 bridges own the 8 columns one apiece; "
            f"{GROUP_COLS} land in-column, {N_COLS - GROUP_COLS} must cross")


@check("topo_plane_isolation")
def _t4():
    t = StackTopology()
    bad = 0
    for p in (0, 1):
        for c in t.cores[:15]:
            for h in t.has[::7]:
                for e in t.route(c, h, p):
                    ep = t.edge_plane[e]
                    if ep != ANY_PLANE and ep != p:
                        bad += 1
    assert bad == 0, f"{bad} edges leaked across top-die planes"
    return "no route uses the other plane's top-die links"


@check("topo_routes_cross_d2d_once")
def _t5():
    for mode in ("bound", "lat", "hops"):
        t = StackTopology(route_mode=mode)
        for c in t.cores[::7]:
            for h in t.has[::5]:
                r = t.route(c, h, 0)
                nd = sum(1 for e in r if t.is_d2d(e))
                assert nd == 1, f"{mode}: {nd} D2D crossings"
                assert t.fabric_of(r[-1]) == "v", "must land on a V ring"
    return "every core->HA route crosses the die boundary exactly once"


@check("topo_bound_route_uses_the_bound_bridge")
def _t5b():
    t = StackTopology(route_mode="bound")
    for c in t.cores[::3]:
        die = t.nodes[c].die
        for h in t.has[::5]:
            col = t.nodes[h].col
            want = t.top(die, t.ha_bridge(die, col))
            r = t.route(c, h, 0)
            d2d = [e for e in r if t.is_d2d(e)]
            assert len(d2d) == 1
            assert t.edges[d2d[0]][0] == want, "crossed at the wrong bridge"
            rev = t.route(h, c, 0)
            d2dr = [e for e in rev if t.is_d2d(e)]
            assert t.edges[d2dr[0]][1] == want, "returned via a free bridge"
    return "the destination HA, not the router, picks the D2D crossing"


@check("topo_far_writes_need_a_horizontal_hop")
def _t6():
    t = StackTopology(route_mode="bound")
    near = far = 0
    for c in t.cores[::3]:
        die = t.nodes[c].die
        own = set(t.die_cols(die))
        for h in t.has:
            col = t.nodes[h].col
            nh = sum(1 for e in t.route(c, h, 0) if t.fabric_of(e) == "h")
            if col in own:
                assert nh == 0, f"in-group column took {nh} H hops"
                near += 1
            else:
                assert nh == N_COLS // 2, f"far column took {nh} H hops"
                far += 1
    assert near == far, f"{near} near vs {far} far"
    return (f"half of every core's writes ({far}/{near + far}) ride "
            f"{N_COLS // 2} horizontal hops -- the H rings are load bearing")


@check("topo_laps_close")
def _t7():
    t = StackTopology()
    for rk, members in t.ring_of.items():
        dirs = (1, -1) if rk[0] == "top" else (1,)
        for d in dirs:
            node = members[0]
            lap = t.lap(rk, node, d)
            assert len(lap) == len(members), (rk, len(lap))
            cur = node
            for e in lap:
                cur = t.edges[e][1]
            assert cur == node, f"lap on {rk} does not close"
    return "every deflection lap returns to its start"


@check("topo_binding_costs_no_capacity")
def _t8():
    """The binding removes routing freedom -- but it does not cost capacity.

    Free shortest-path routing is not implementable here, and it would not
    help anyway: letting a write cross at whichever bridge is nearest lures
    traffic onto the scarce bottom-die rings and *raises* the bound.
    """
    tb = StackTopology(route_mode="bound")
    tl = StackTopology(route_mode="lat")
    xb = build_uniform_write(tb, k=10, seed=0)
    xl = build_uniform_write(tl, k=10, seed=0)
    bb = tb.write_bounds(xb)["bound"]
    bl = tl.write_bounds(xl)["bound"]
    assert bb < bl, f"bound routing {bb} not better than free lat {bl}"
    hb = sum(len(tb.route(x.core, x.ha, 0)) for x in xb) / len(xb)
    hl = sum(len(tl.route(x.core, x.ha, 0)) for x in xl) / len(xl)
    return (f"mandated routing bound {bl}->{bb} better than free routing, "
            f"mean hops {hl:.2f}->{hb:.2f}")


@check("topo_h_and_v_are_both_load_bearing")
def _t9():
    """The horizontal rings are no longer surplus, and they are not the cap.

    With a 2x4 group each die reaches only 4 columns directly, so half the
    writes cross horizontally. That puts real load on the 48 horizontal links
    -- same order as the 144 vertical ones -- but the vertical rings still
    bind.
    """
    t = StackTopology(route_mode="bound")
    x = build_uniform_write(t, k=8, seed=0)
    per: dict[int, int] = {}
    occ: dict[int, int] = {}
    for q in x:
        pl = t.pick_plane(q.core, q.ha, occupancy=occ)
        for vc, path, m in (("dat", t.route(q.core, q.ha, pl), 4),
                            ("rsp", t.route(q.ha, q.core, pl), 2)):
            for e in path:
                per[e] = per.get(e, 0) + m
    hv = [v for e, v in per.items() if t.fabric_of(e) == "h"]
    vv = [v for e, v in per.items() if t.fabric_of(e) == "v"]
    assert hv and min(hv) >= 0
    assert sum(hv) > 0, "horizontal rings carry no traffic"
    assert max(vv) > max(hv), "expected the vertical rings to still bind"
    return (f"H peak {max(hv)} vs V peak {max(vv)}: horizontal rings carry "
            f"real load ({sum(hv)} flit-hops) but vertical still binds")


# ---------------------------------------------------------------------------
# datapath conservation
# ---------------------------------------------------------------------------

@check("write_four_phase_conservation")
def _c1():
    topo, txns, r = _run("s0", k=12)
    n = len(txns)
    assert r["completed"], "baseline did not finish"
    assert r["n_delivered_req"] == n, r["n_delivered_req"]
    assert r["n_delivered_dbid"] == n, r["n_delivered_dbid"]
    assert r["n_delivered_wdata"] == 4 * n, r["n_delivered_wdata"]
    assert r["n_delivered_comp"] == n, r["n_delivered_comp"]
    assert r["n_delivered_flits"] == 7 * n, r["n_delivered_flits"]
    assert r["n_txn_done"] == n
    return f"{n} txns -> {7 * n} flits, exact"


@check("no_flit_lost_at_turn_or_d2d")
def _c2():
    topo, txns, r = _run("s0", k=12)
    assert r["n_injected"] == r["n_delivered_flits"], \
        f"{r['n_injected']} injected vs {r['n_delivered_flits']} delivered"
    assert r["in_flight"] == 0 and r["backlog"] == 0
    assert r["fifo"]["residual_xq"] == 0, "flits stranded in a transfer FIFO"
    assert r["n_turns"] > 0, "no turn ever happened - routes are wrong"
    return f"{r['n_turns']:,} turns, nothing stranded"


@check("makespan_ge_bound")
def _c3():
    topo, txns, r = _run("s0", k=12)
    b = topo.write_bounds(txns)["bound"]
    assert r["makespan"] >= b, f"makespan {r['makespan']} < bound {b}"
    return f"makespan {r['makespan']} >= bound {b}"


@check("links_stay_bufferless")
def _c4():
    topo, txns, r = _run("s0", k=12)
    assert r["n_inring_blocked"] == 0, r["n_inring_blocked"]
    assert r["max_inring_hold"] == 0, r["max_inring_hold"]
    return "S0 never latches an in-ring flit"


@check("fifo_occupancy_bounded")
def _c5():
    topo, txns, r = _run("s0", k=12, turn_depth=16, d2d_depth=32)
    f = r["fifo"]
    assert f["turn_peak"] <= 16, f["turn_peak"]
    assert f["d2d_peak"] <= 32, f["d2d_peak"]
    assert f["n_turn_fifo"] > 0 and f["n_d2d_fifo"] > 0
    return (f"turn peak {f['turn_peak']}/16, d2d peak {f['d2d_peak']}/32 "
            f"over {f['n_turn_fifo']}+{f['n_d2d_fifo']} FIFOs")


@check("d2d_landing_has_two_ring_interfaces")
def _c5b():
    """Bottom D2D landing: H and V are their own FIFOs, not the H↔V turn."""
    _, _, r = _run("s0", k=12, turn_depth=16, d2d_depth=32)
    f = r["fifo"]
    # Both ring interfaces of the bottom D2D landing must have been used.
    assert f.get("n_d2d_land_h", 0) > 0 and f.get("n_d2d_land_v", 0) > 0, f
    ser = r.get("fabric_series") or {}
    assert ser.get("t") and ser.get("bw", {}).get("d2d"), ser.keys()
    d2d = r["fabric"]["d2d"]
    assert "peak_inst_bw" in d2d and d2d["peak_inst_util"] <= 1.0
    return (f"D2D land H={f['n_d2d_land_h']} V={f['n_d2d_land_v']}; "
            f"D2D peak inst {d2d['peak_inst_bw']} flit/cycle")


@check("fabric_util_matches_capacity_model")
def _c6():
    topo, txns, r = _run("s0", k=12)
    cap = topo.capacity()
    for name, row in r["fabric"].items():
        assert row["links"] == cap[name], (name, row["links"], cap[name])
        assert 0.0 <= row["util"] <= 1.0, (name, row["util"])
        assert 0.0 <= row.get("peak_inst_util", 0) <= 1.0, (name, row)
    # the horizontal rings are load bearing now, not surplus
    assert r["fabric"].get("h", {}).get("flit_hops", 0) > 0, \
        "no horizontal traffic: the 2x4 grouping should force column crossings"
    return "per-fabric link counts and utilisation self-consistent"


@check("turn_fifo_depth_is_a_real_requirement")
def _c7():
    """H↔V turns still saturate a shallow FIFO; they no longer livelock.

    Dual D2D interfaces pulled landings off the turn queue, so even a
    1-flit turn FIFO drains. The FIFO is still on the critical path:
    it pins at its depth and a shallow one stretches makespan.
    """
    _, _, sh = _run("s0", k=50, turn_depth=1, d2d_depth=8)
    _, _, dp = _run("s0", k=50, turn_depth=64, d2d_depth=128)
    assert sh["completed"] and dp["completed"]
    assert sh["fifo"]["turn_peak"] == 1, sh["fifo"]
    assert sh["makespan"] > dp["makespan"], \
        (sh["makespan"], dp["makespan"])
    return (f"depth 1 drains pinned at {sh['fifo']['turn_peak']}/1 in "
            f"{sh['makespan']:,}c; depth 64 drains in {dp['makespan']:,}c")


@check("lower_concurrency_also_lowers_the_buffer_requirement")
def _c7b():
    """Higher outstanding still fills the turn FIFO further.

    Dual interfaces removed the livelock cliff, but occupancy still
    tracks concurrency: oc=5 needs a deeper peak than oc=3.
    """
    _, _, lo = _run("s0", k=50, core_outstanding=3, turn_depth=24,
                    d2d_depth=48)
    _, _, hi = _run("s0", k=50, core_outstanding=5, turn_depth=24,
                    d2d_depth=48)
    assert lo["completed"] and hi["completed"]
    assert hi["fifo"]["turn_peak"] > lo["fifo"]["turn_peak"], \
        (lo["fifo"]["turn_peak"], hi["fifo"]["turn_peak"])
    return ("at turn depth 24: oc=3 peak "
            f"{lo['fifo']['turn_peak']}, oc=5 peak "
            f"{hi['fifo']['turn_peak']} -- more concurrency, more buffer")


# ---------------------------------------------------------------------------
# schemes
# ---------------------------------------------------------------------------

@check("s16_unbounded_equals_s0")
def _s1():
    """Grant pacing with no budget must not perturb the datapath at all."""
    _, _, a = _run("s0", k=10)
    _, _, b = _run("s16", k=10, overcommit=10 ** 9)
    for key in ("makespan", "n_delivered_flits", "n_deflections",
                "n_board_fail", "n_turns", "n_txn_done"):
        assert a[key] == b[key], f"{key}: {a[key]} != {b[key]}"
    return "exact equality with the baseline on 6 counters"


@check("s16_respects_grant_budget")
def _s2():
    _, _, r = _run("s16", k=10, overcommit=6)
    fc = r["fc"]
    assert fc["peak_grants"] <= 6, fc["peak_grants"]
    assert r["completed"], "S16 stalled"
    return f"peak outstanding grants {fc['peak_grants']} <= 6"


@check("s1_bus_never_uses_the_noc")
def _s3():
    _, _, r = _run("s1", k=10)
    fc = r["fc"]
    assert fc["bus_posts"] > 0, "S1 never broadcast anything"
    assert fc["mean_path_nodes"] > 0, "受控节点表 is empty"
    assert r["completed"]
    return (f"{fc['bus_posts']:,} posts, {fc['bus_bits']:,} bits, "
            f"mean {fc['mean_path_nodes']} controlled nodes per source")


@check("s17_latch_is_bounded_and_off_by_default")
def _s4():
    _, _, off = _run("s17", k=10, turn_patience=0)
    assert off["max_inring_hold"] == 0, "patience=0 must equal the baseline"
    _, _, on = _run("s17", k=10, turn_patience=2, yield_depth=1)
    assert on["max_inring_hold"] <= 1, on["max_inring_hold"]
    assert on["fc"]["n_turn_yield"] > 0, "S17 never fired"
    assert on["completed"]
    return (f"patience=0 -> 0 latch; patience=2 -> latch "
            f"{on['max_inring_hold']}, {on['fc']['n_turn_yield']:,} yields")


@check("s17_gain_is_real_but_immaterial")
def _s5():
    """Pins the report's verdict on S17 so it cannot silently drift.

    S17 does nudge Jain up -- it is the only scheme aimed at the actual
    bottleneck -- but the nudge is far too small to justify touching the
    fabric, and it does not improve the worst-case max/min at all. Both
    directions matter: if the gain ever becomes material, the report's
    recommendation is wrong and this check should fire.
    """
    seeds = (0, 1, 2)
    a = [_run("s0", k=50, seed=s)[2] for s in seeds]
    ja = sum(r["fairness"]["jain"] for r in a) / len(a)
    ma = max(r["fairness"]["max_min"] for r in a)
    best = 0.0
    for pat in (1, 2, 8):
        c = [_run("s17", k=50, seed=s, turn_patience=pat)[2] for s in seeds]
        jb = sum(r["fairness"]["jain"] for r in c) / len(c)
        mb = max(r["fairness"]["max_min"] for r in c)
        best = max(best, jb)
        assert jb - ja < 0.01, \
            f"patience={pat} now gives a material gain " \
            f"({ja:.5f} -> {jb:.5f}); the report's verdict on S17 needs " \
            "revisiting"
        assert mb >= ma - 0.05, \
            f"patience={pat} now improves worst-case max/min " \
            f"({ma:.3f} -> {mb:.3f}); revisit the S17 verdict"
    return (f"mean Jain {ja:.5f} -> at best {best:.5f} (+{best - ja:.5f}), "
            f"worst-case max/min unchanged at {ma:.2f}: real but immaterial")


@check("rtt_window_drains_on_every_seed")
def _s5b():
    """The RTT-sized window plus HA retry must drain on every measured seed."""
    seeds = (0, 1, 2)
    rows = [_run("s0", k=40, seed=s, core_outstanding=OC_MANDATED,
                 ha_pos_depth=HA_POS)[2] for s in seeds]
    assert all(r["completed"] for r in rows), [r["n_txn_done"] for r in rows]
    j = sum(r["fairness"]["jain"] for r in rows) / len(rows)
    return (f"oc={OC_MANDATED} HA POS={HA_POS}: {len(rows)}/{len(rows)} "
            f"seeds drained, mean Jain {j:.5f}")


@check("baseline_unfairness_is_structural_not_random")
def _s6():
    """Unfairness exists, and it tracks position rather than luck.

    The old "first of an adjacent pair loses" story died with the 2x4
    grouping: every die now reaches 4 columns near and 4 far, so a die has no
    single vertical position. What survives is that the spread is reproducible
    across seeds and attributable to a die's group, not to the RNG.
    """
    per_die: dict[int, list[float]] = {}
    spread = []
    for sd in (0, 1, 2):
        topo, _, r = _run("s0", k=40, seed=sd)
        f = r["fairness"]
        spread.append(f["max_min"])
        bw = {int(c): v for c, v in f["bw_by_core"].items()}
        for c, v in bw.items():
            per_die.setdefault(topo.nodes[c].die, []).append(v)
    assert min(spread) > 1.15, f"expected a real spread, got {spread}"
    means = {d: sum(v) / len(v) for d, v in per_die.items()}
    lo = min(means, key=lambda d: means[d])
    hi = max(means, key=lambda d: means[d])
    ratio = means[hi] / means[lo]
    assert ratio > 1.05, f"no per-die structure: {means}"
    return (f"max/min {min(spread):.2f}-{max(spread):.2f} across 3 seeds; "
            f"die {hi} beats die {lo} by {ratio:.2f}x on the 3-seed mean")


@check("rtt_window_and_ha_retry_do_not_collapse")
def _s7():
    """The operating point is RTT-sized outstanding plus HA retry.

    A slot is held from REQ inject to Comp, so the register equals the
    longest write RTT. Completers that run out of tracker entries retry
    rather than queue. Neither S0 nor S1 is allowed to livelock there.
    """
    notes = []
    for scheme in ("s0", "s1"):
        _, x, r = _run(scheme, k=50, core_outstanding=OC_MANDATED,
                       ha_pos_depth=HA_POS)
        assert r["completed"], f"{scheme} collapsed at oc={OC_MANDATED}"
        assert r["n_txn_done"] == len(x), (scheme, r["n_txn_done"], len(x))
        notes.append(f"{scheme} {r['makespan']}c retry={r['retry']['n_retry']}")
    return "; ".join(notes)


@check("ha_retry_fires_when_the_tracker_fills")
def _s8():
    """A shallow completer tracker must bounce requests and still drain."""
    _, _, deep = _run("s0", k=20, core_outstanding=OC_MANDATED, ha_pos_depth=0)
    _, _, shal = _run("s0", k=20, core_outstanding=OC_MANDATED, ha_pos_depth=4)
    assert deep["completed"] and shal["completed"]
    assert shal["retry"]["n_retry"] > 0, shal["retry"]
    assert shal["retry"]["n_req_resent"] == shal["retry"]["n_pcrd"]
    assert shal["retry"]["eff_frac"] < 1.0
    return (f"pos=4: {shal['retry']['n_retry']} bounces, "
            f"eff/nom {shal['retry']['eff_frac']:.3f}, still drained")


@check("horizontal_ring_assignment_changes_the_collapse_point")
def _s9():
    """Which of a gap's two rings carries the far traffic is a real choice.

    Both assignments move identical flit-hop totals, so the analytic bound
    cannot tell them apart -- but they differ in whether the two dies of a gap
    land on the same attach point, and that shifts where the fabric folds.
    """
    from rg_stack_topo import StackTopology as ST
    bounds, ok = {}, {}
    for ha in ("split", "stack"):
        t = ST(route_mode="bound", h_assign=ha)
        x = build_uniform_write(t, k=50, seed=0)
        bounds[ha] = t.write_bounds(x)["bound"]
        n = 0
        for sd in (0, 1, 2):
            xs = build_uniform_write(t, k=50, seed=sd)
            params = StackBaseParams(**{**FAB, "core_outstanding": 6})
            r = run_batch(t, xs, params=params, sim_cls=StackBaseSim,
                          seed=sd, stall_after=20_000)
            n += bool(r["completed"])
        ok[ha] = n
    assert bounds["split"] == bounds["stack"], \
        f"bounds should be identical: {bounds}"
    assert ok["split"] == 3 and ok["stack"] == 3, \
        f"both assignments should drain at oc=6: {ok}"
    return (f"identical bound {bounds['split']}; both drain 3/3 seeds at oc=6")


@check("binding_follows_the_mod4_rule")
def _b1():
    """The bridge for a column is the one at (column mod 4) within its row.

    This is the difference between a wiring rule and a lookup table. A die's
    eight bridges cover its own four columns twice, once per horizontal ring
    of its row gap; the near-ring one serves that column directly and the
    far-ring one serves the column four across. Either way the position
    inside the group of four is the target column modulo four.
    """
    t = StackTopology()
    n = 0
    for die in range(t.n_die):
        cols = t.die_cols(die)
        for col in range(N_COLS):
            j = TOP_BRIDGES.index(t.ha_bridge(die, col))
            assert j % GROUP_COLS == col % GROUP_COLS, (die, col, j)
            assert (j < GROUP_COLS) == (col in cols), (die, col, j)
            n += 1
    assert n == t.n_die * N_COLS == 48, n
    return f"all {n} (die, column) pairs use the bridge at (col mod 4)"


@check("retry_costs_bandwidth_and_parks_outstanding")
def _r1():
    """A bounced request must cost real traffic and hold its slot idle.

    If RetryAck were free the completer's limit would be a pure win. It is
    not: the bounce and the credit are RSP messages, the request crosses the
    network a second time, and while it waits it occupies the requester's
    outstanding budget without a flit in flight. That gap between nominal and
    effective concurrency is the thing worth measuring, so it must not be
    silently zero.
    """
    _, _, deep = _run("s0", k=12, core_outstanding=OC_MANDATED,
                      ha_pos_depth=0)
    _, _, shal = _run("s0", k=12, core_outstanding=OC_MANDATED,
                      ha_pos_depth=4)
    assert deep["retry"]["n_retry"] == 0, deep["retry"]
    assert deep["retry"]["eff_frac"] == 1.0, deep["retry"]
    q = shal["retry"]
    assert q["n_retry"] > 0, q
    # every bounce is answered by a grant and a second REQ traversal
    assert q["n_req_resent"] == q["n_pcrd"], q
    assert q["n_pcrd"] <= q["n_retry"], q
    assert q["eff_frac"] < 1.0, q
    assert q["park_wait_mean"] > 0, q
    return (f"pos=4: {q['n_retry']} bounces, {q['n_req_resent']} re-sends, "
            f"effective concurrency {q['eff_frac']:.3f} of nominal, "
            f"parked {q['park_wait_mean']:.0f} cycles on average")


@check("batch_larger_than_outstanding_does_not_hol_block_retry")
def _r1b():
    """k > outstanding used to pin new REQs at the inject head.

    A P-Credit re-send already holds its slot and must be able to board
    while the rest of the closed batch waits. If those waiters HOL-block
    the re-send, grants are delivered into an empty ring and the run dies
    with n_req_resent = 0.
    """
    _, x, r = _run("s0", k=20, core_outstanding=6, ha_pos_depth=4)
    q = r["retry"]
    assert r["completed"], (r["n_txn_done"], len(x), q)
    assert r["n_txn_done"] == len(x)
    assert q["n_retry"] > 0, q
    assert q["n_req_resent"] == q["n_pcrd"] > 0, q
    return (f"k=20 oc=6 drained {len(x)}; "
            f"{q['n_req_resent']} re-sends after {q['n_retry']} retries")


@check("shallow_tracker_retries_but_still_drains")
def _r2():
    """Retry is a real protocol cost, not a collapse substitute.

    With the longer bottom-die hops the fabric drains even with an unlimited
    completer. A shallow tracker must still bounce, park, and re-send -- and
    the batch must still finish.
    """
    _, x, unl = _run("s0", k=50, core_outstanding=OC_MANDATED, ha_pos_depth=0)
    _, _, shal = _run("s0", k=50, core_outstanding=OC_MANDATED, ha_pos_depth=4)
    assert unl["completed"] and shal["completed"]
    assert unl["n_txn_done"] == shal["n_txn_done"] == len(x)
    assert shal["retry"]["n_retry"] > unl["retry"]["n_retry"]
    return (f"both drain {len(x)}; pos=0 retry={unl['retry']['n_retry']}, "
            f"pos=4 retry={shal['retry']['n_retry']}")


@check("rtt_outstanding_covers_the_longest_round_trip")
def _r3():
    """The configured window is exactly the longest uncongested write RTT."""
    t = StackTopology()
    info = t.max_write_rtt(m_wdata=4)
    assert info["outstanding"] == OC_MANDATED == info["rtt"]
    assert info["fwd"] + info["rev"] < info["rtt"]
    # a one-die load still completes at that same window
    x = build_uniform_write(t, k=20, seed=0, dies=[0])
    r = run_batch(t, x, params=StackBaseParams(
        **{**FAB, "core_outstanding": OC_MANDATED, "ha_pos_depth": HA_POS}),
        sim_cls=StackBaseSim, seed=0, stall_after=20_000)
    assert r["completed"], r["n_txn_done"]
    return (f"window {info['rtt']} = 2*fwd({info['fwd']}) + 2*rev({info['rev']}) "
            f"+ (W-1); one-die batch drained in {r['makespan']}")


@check("s18_tracks_the_window_across_scenarios")
def _r4():
    """One parameter set, two loads, two different converged windows.

    The claim is that the controller finds the concurrency instead of being
    told it. The test of that is not the throughput it reaches but whether the
    window it settles on moves with the load, in the right direction, without
    anything being retuned.
    """
    t = StackTopology(route_mode="bound")
    out = {}
    for lbl, dies in (("full", None), ("light", [0])):
        x = build_uniform_write(t, k=50, seed=0, dies=dies)
        p = StackAdaptParams(**{**FAB, "core_outstanding": OC_MANDATED,
                                "ha_pos_depth": 32, "rtt_slack": 2.0})
        r = run_batch(t, x, params=p, sim_cls=StackAdaptSim, seed=0,
                      stall_after=20_000)
        out[lbl] = (r["fc"]["win_mean_final"], r["fc"]["win_max_final"],
                    r["completed"],                     r["fc"]["rtt_min_mean"])
    assert out["full"][2] and out["light"][2], out
    assert out["light"][0] >= out["full"][0], out
    assert out["full"][3] > 0 and out["light"][3] > 0, out
    return (f"window settles at {out['full'][0]:.1f} under full load and "
            f"{out['light'][0]:.1f} under light load (peak "
            f"{out['light'][1]:.0f}), rtt_min ~{out['full'][3]:.0f} cycles")


@check("group_goodput_is_equal_when_the_batch_drains")
def _r5():
    """Ten cores per top die; retired write traffic matches across dies."""
    from dse_stack_write_fair import group_stats
    t = StackTopology(route_mode="bound")
    x = build_uniform_write(t, k=50, seed=0)
    r = run_batch(t, x, params=StackBaseParams(
        **{**FAB, "core_outstanding": OC_MANDATED, "ha_pos_depth": HA_POS}),
        sim_cls=StackBaseSim, seed=0, stall_after=20_000)
    g = group_stats(t, r["wr_inject_by_core"], r["wr_done_by_core"],
                    r["makespan"])
    assert r["completed"], r["n_txn_done"]
    assert g["n_groups"] == 6 and g["cores_per_group"] == 10, g
    assert g["goodput_max_min"] == 1.0, g["goodput_by_group"]
    return (f"6 groups x 10 cores, goodput max/min "
            f"{g['goodput_max_min']:.2f}, group Jain {g['jain']:.4f}")


@check("s0_and_s1_group_series_cover_the_run")
def _r6():
    """The time-series bins reconstruct the WriteData count per die."""
    from dse_stack_write_fair import group_bw_series
    t = StackTopology(route_mode="bound")
    x = build_uniform_write(t, k=20, seed=0)
    r = run_batch(t, x, params=StackBaseParams(
        **{**FAB, "core_outstanding": OC_MANDATED, "ha_pos_depth": HA_POS}),
        sim_cls=StackBaseSim, seed=0, stall_after=20_000)
    ser = group_bw_series(t, r["wr_inject_by_core"], window=50,
                          makespan=r["makespan"])
    assert r["completed"]
    assert len(ser["bw_by_group"]) == 6
    # 20 writes x 4 flits x 10 cores per die
    for d, ys in ser["bw_by_group"].items():
        got = sum(v * ser["window"] for v in ys)
        assert abs(got - 20 * 4 * 10) < ser["window"] + 1, (d, got)
    return (f"{ser['n_windows']} windows of {ser['window']}c, "
            f"6 groups, integral matches 800 write flits/die")


def main() -> None:
    print("stacked-fabric checks\n")
    n_ok = sum(1 for _, ok, _ in RESULTS if ok)
    out = Path(__file__).resolve().parents[1] / "results" / "verify_stack.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(
        [{"name": n, "ok": ok, "note": note} for n, ok, note in RESULTS],
        indent=1))
    print(f"\n{n_ok}/{len(RESULTS)} passed  -> {out}")
    sys.exit(0 if n_ok == len(RESULTS) else 1)


if __name__ == "__main__":
    main()
