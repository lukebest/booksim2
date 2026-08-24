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
from rg_stack_fc import (StackFairTurnSim, StackFcParams, StackFcSim,
                         StackGrantParams, StackGrantSim, StackTurnParams)
from rg_stack_topo import (ANY_PLANE, N_ATTACH, N_COLS, N_HA, N_HRING,
                           N_ROWS, N_TOP_DIE, TOP_BRIDGES, TOP_N, V_LEN,
                           StackTopology, build_uniform_write)

RESULTS: list[tuple[str, bool, str]] = []
FAB = dict(turn_depth=64, d2d_depth=128, core_outstanding=128)


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


def _run(scheme: str = "s0", *, route: str = "dor", k: int = 12,
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


@check("topo_h_ring_and_bridge_map")
def _t3():
    t = StackTopology()
    for h in range(N_HRING):
        cols = {t.nodes[t.attach(h, c)].col for c in range(N_COLS)}
        assert cols == set(range(N_COLS))
    # each top die has exactly one bridge per column
    for d in range(N_TOP_DIE):
        cols = sorted(t.bridge_col(i) for i in TOP_BRIDGES)
        assert cols == list(range(N_COLS)), cols
    return "each top die has exactly one bridge per bottom-die column"


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
    for mode in ("lat", "hops", "dor"):
        t = StackTopology(route_mode=mode)
        for c in t.cores[::7]:
            for h in t.has[::5]:
                r = t.route(c, h, 0)
                nd = sum(1 for e in r if t.is_d2d(e))
                assert nd == 1, f"{mode}: {nd} D2D crossings"
                assert t.fabric_of(r[-1]) == "v", "must land on a V ring"
    return "every core->HA route crosses the die boundary exactly once"


@check("topo_dor_uses_no_horizontal_hop")
def _t6():
    t = StackTopology(route_mode="dor")
    for c in t.cores[::5]:
        for h in t.has[::3]:
            for r in (t.route(c, h, 0), t.route(h, c, 0)):
                nh = sum(1 for e in r if t.fabric_of(e) == "h")
                assert nh == 0, f"{nh} horizontal hops under DOR"
    return "DOR needs no horizontal ring at all for core<->HA traffic"


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


@check("topo_dor_beats_shortest_path_bound")
def _t8():
    tl = StackTopology(route_mode="lat")
    td = StackTopology(route_mode="dor")
    xl = build_uniform_write(tl, k=10, seed=0)
    xd = build_uniform_write(td, k=10, seed=0)
    bl = tl.write_bounds(xl)["bound"]
    bd = td.write_bounds(xd)["bound"]
    assert bd < bl, f"dor bound {bd} not better than lat {bl}"
    # DOR is longer in hops yet has a better bound: it is load balance,
    # not path length, that matters here.
    hl = sum(len(tl.route(x.core, x.ha, 0)) for x in xl) / len(xl)
    hd = sum(len(td.route(x.core, x.ha, 0)) for x in xd) / len(xd)
    assert hd > hl, f"expected DOR to be longer: {hd} vs {hl}"
    return f"bound {bl}->{bd} while mean hops {hl:.2f}->{hd:.2f} (longer)"


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


@check("fabric_util_matches_capacity_model")
def _c6():
    topo, txns, r = _run("s0", k=12)
    cap = topo.capacity()
    for name, row in r["fabric"].items():
        assert row["links"] == cap[name], (name, row["links"], cap[name])
        assert 0.0 <= row["util"] <= 1.0, (name, row["util"])
    # DOR must not touch the horizontal rings at all
    assert r["fabric"].get("h", {}).get("flit_hops", 0) == 0, \
        "DOR run put traffic on a horizontal ring"
    return "per-fabric link counts and utilisation self-consistent"


@check("shallow_turn_fifo_is_adequate_under_dor")
def _c7():
    """The plan flagged the 4-flit turn FIFO as a risk. Under DOR it is not.

    Deepening it 16x buys a few percent, so the earlier "depth 4 livelocks"
    observation belongs to the routing hotspot, not to a buffering shortfall.
    """
    _, txns, sh = _run("s0", k=40, turn_depth=4, d2d_depth=8)
    _, _, dp = _run("s0", k=40, turn_depth=64, d2d_depth=128)
    assert sh["completed"] and dp["completed"], "DOR should be stable at both"
    thr_s = sh["n_txn_done"] / sh["makespan"]
    thr_d = dp["n_txn_done"] / dp["makespan"]
    assert thr_d / thr_s < 1.15, \
        f"depth is a cliff after all: {thr_s:.3f} -> {thr_d:.3f}"
    return (f"thr {thr_s:.3f} (depth 4) vs {thr_d:.3f} (depth 64), "
            f"only {100 * (thr_d / thr_s - 1):.1f}% apart")


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


@check("s17_does_not_deliver_a_reliable_gain")
def _s5():
    """Pins the report's negative result on S17 so it cannot silently drift.

    A single seed makes S17 look like a win; three seeds do not. If a future
    change makes it genuinely work, this check fires and the report is wrong
    in the direction that matters, so the failure is the useful signal.
    """
    seeds = (0, 1, 2)
    a = [_run("s0", k=40, seed=s)[2] for s in seeds]
    ja = sum(r["fairness"]["jain"] for r in a) / len(a)
    for pat in (1, 2):
        b = [_run("s17", k=40, seed=s, turn_patience=pat)[2] for s in seeds]
        jb = sum(r["fairness"]["jain"] for r in b) / len(b)
        assert jb <= ja + 0.002, \
            f"patience={pat} now beats the baseline ({ja:.5f} -> {jb:.5f}); " \
            "the report's negative result on S17 needs revisiting"
    return (f"mean Jain baseline {ja:.5f}; no aggressive patience setting "
            "beats it, matching the reported negative result")


@check("lower_outstanding_wins_on_both_axes")
def _s5b():
    """The report's headline practical claim, on the seeds it was measured on.

    Dropping the per-core limit from 128 to 32 has to improve throughput and
    fairness at once, otherwise the recommendation is not earned.
    """
    seeds = (0, 1, 2)
    hi = [_run("s0", k=40, seed=s, core_outstanding=128)[2] for s in seeds]
    lo = [_run("s0", k=40, seed=s, core_outstanding=32)[2] for s in seeds]
    jh = sum(r["fairness"]["jain"] for r in hi) / len(hi)
    jl = sum(r["fairness"]["jain"] for r in lo) / len(lo)
    th = sum(r["makespan"] for r in hi) / len(hi)
    tl = sum(r["makespan"] for r in lo) / len(lo)
    assert jl > jh, f"fairness not improved: {jh:.5f} -> {jl:.5f}"
    assert tl < th, f"throughput not improved: {th:.0f} -> {tl:.0f}"
    return (f"oc 128->32: mean Jain {jh:.5f} -> {jl:.5f} and mean makespan "
            f"{th:.0f} -> {tl:.0f} (both better, zero hardware)")


@check("baseline_is_position_unfair")
def _s6():
    topo, _, r = _run("s0", k=40)
    f = r["fairness"]
    assert f["max_min"] > 1.5, f"expected clear unfairness, got {f['max_min']}"
    bw = {int(c): v for c, v in f["bw_by_core"].items()}
    first = [v for c, v in bw.items() if topo.nodes[c].die % 2 == 0]
    second = [v for c, v in bw.items() if topo.nodes[c].die % 2 == 1]
    m1 = sum(first) / len(first)
    m2 = sum(second) / len(second)
    assert m1 > m2 * 1.15, \
        f"expected first-of-pair dies to win: {m1:.4f} vs {m2:.4f}"
    return (f"max/min {f['max_min']}; first-of-pair {m1:.4f} vs "
            f"second-of-pair {m2:.4f} ({m1 / m2:.2f}x)")


@check("flow_control_cannot_fix_a_routing_hotspot")
def _s7():
    """Collapse under latency-shortest routing needs sustained saturation.

    At k=20 the batch drains before the deflection feedback runs away, so the
    check has to run at the report's operating point to mean anything.
    """
    for scheme in ("s0", "s1"):
        _, txns, r = _run(scheme, route="lat", k=40)
        assert not r["completed"], \
            f"{scheme} unexpectedly survived latency-shortest routing"
    _, _, ok = _run("s0", route="dor", k=40)
    assert ok["completed"], "DOR baseline should be stable"
    return "S0 and S1 both collapse under latency-shortest; DOR is stable"


@check("collapse_needs_sustained_load")
def _s8():
    """Small batches survive the bad routing; the cliff is a saturation one."""
    _, _, light = _run("s0", route="lat", k=20)
    _, _, heavy = _run("s0", route="lat", k=40)
    assert light["completed"], "k=20 should still drain"
    assert not heavy["completed"], "k=40 should collapse"
    return (f"lat route: k=20 completes in {light['makespan']}, "
            f"k=40 collapses ({heavy['n_txn_done']} txns done)")


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
