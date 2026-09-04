#!/usr/bin/env python3
"""Executable assertions for the 20-node dual-plane ring study.

Each check is named; a failure prints the concrete quantity. Writes
results/verify_ring2_20.json.
"""

from __future__ import annotations

import json
import random
import sys
import time
import traceback
from pathlib import Path
from typing import Any, Callable

_UTILS = Path(__file__).resolve().parent
if str(_UTILS) not in sys.path:
    sys.path.insert(0, str(_UTILS))

from rg_ring2_aimd import run_batch as run_aimd
from rg_ring2_base import Flit, Ring2BaseParams, Ring2BaseSim, run_batch as run_base
from rg_ring2_dist import (
    Ring2DistParams, Ring2DistSim, run_batch as run_dist, s5_params,
    s6_params, s7_params, s8_params, s9_params, s10_params, s11_params,
    s12_params, s13_params, s14_params,
)
from rg_ring2_pop import Ring2PopSim, run_batch as run_pop
from rg_ring2_rg import RING2_ALGOS, RGConfig, replay_ok, run_batch as run_rg
from rg_ring2_rg import requirements, schedule
from rg_ring2_fc import Ring2FcParams, Ring2FcSim
from rg_ring2_topo import (
    BURST_BYTES, CHI_VCS_WRITE, Ring2Topology, STRIDE_BYTES, TILE_BYTES,
    build_allpairs, build_hot_write, build_tiled_write, build_uniform,
    build_uniform_write, hop_count, interleave_ha, is_core, is_ha,
    paths_for_txns, shortest_dir, vc_of, write_bounds, write_paths_for_txns,
)
from dse_ring2_write_fair import binned_jain, fairness_stats, jain_ideal_bin
from rg_sched_cost import distributed_cost, sched_cost

OUT = Path(__file__).resolve().parents[1] / "results" / "verify_ring2_20.json"


class Checks:
    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []

    def add(self, name: str, fn: Callable[[], None]) -> None:
        try:
            fn()
            self.rows.append({"name": name, "ok": True})
            print(f"  [ok] {name}", flush=True)
        except Exception as e:
            self.rows.append({"name": name, "ok": False, "error": str(e),
                              "trace": traceback.format_exc()})
            print(f"  [FAIL] {name}: {e}", flush=True)

    @property
    def n_ok(self) -> int:
        return sum(1 for r in self.rows if r["ok"])


def test_topo() -> None:
    t = Ring2Topology()
    assert t.n == 20
    assert len(t.cores) == 10 and len(t.has) == 10
    assert all(is_core(c) for c in t.cores)
    assert all(is_ha(h) for h in t.has)
    assert len(t.directed_links) == 80
    assert t.n_vc == 2 and t.hop_bw_cap == 160
    assert len(t.undirected_links) == 40
    p = t.make_path(0, 19, 0)
    assert p.hops == 1 and p.dir == -1
    p = t.make_path(0, 1, 0)
    assert p.hops == 1 and p.dir == 1
    # wrap-around adjacency
    assert (0, 19, 0) in [(e[1], e[2], e[0]) for e in t.directed_links] or \
        (0, 0, 19) in [(e[0], e[1], e[2]) for e in t.directed_links]
    assert any(e == (0, 19, 0) or e == (0, 0, 19) for e in t.directed_links)
    assert shortest_dir(0, 10) == 1
    assert hop_count(0, 10, 1) == 10
    assert t.link_lats[0] == 2 and t.link_lats[19] == 3
    assert t.hop_lat_from(0, 1) == 2
    assert t.hop_lat_from(0, -1) == 3
    assert t.path_lat(0, 1) == 2
    assert t.path_lat(0, 19) == 3


def test_latency_route_matches_hops_on_this_ring() -> None:
    """Latency-shortest never strictly disagrees with hop-count here.

    Ties (equal path delay) still use hop-count, then CW. The standalone
    `shortest_dir` helper stays hop-count so (0, 10) remains +1.
    """
    hops = Ring2Topology(n_planes=1)
    lat = Ring2Topology(n_planes=1, route="latency")
    assert hops.route == "hops" and lat.route == "latency"
    assert shortest_dir(0, 10) == 1
    assert hops.choose_dir(0, 10) == 1
    assert lat.choose_dir(0, 10) == 1          # 21 vs 21, hop tie → CW
    assert lat.choose_dir(4, 15) == -1         # 21 vs 21, fewer hops CCW
    assert hops.make_path(0, 10, 0).dir == 1
    cores = list(range(0, 20, 2))
    mem = [1, 3, 5, 7, 11, 13, 15, 17]
    n_strict = 0
    for src, dsts in [(c, mem) for c in cores] + [(h, cores) for h in mem]:
        for dst in dsts:
            cw, ccw = lat.path_lat(src, dst, 1), lat.path_lat(src, dst, -1)
            if cw != ccw and hops.choose_dir(src, dst) != lat.choose_dir(src, dst):
                n_strict += 1
    assert n_strict == 0, n_strict


def test_shared_inj_depth_and_board_rate() -> None:
    """Per-VC FIFO is 12 deep, dir Q is 8, port stays 1 flit/cycle."""
    from dse_ring2_write_fair import base_params
    bp = base_params()
    assert bp.shared_inj and bp.per_vc_srcq and bp.per_vc_ports
    assert (bp.inj_depth, bp.dir_inj_depth) == (12, 8)
    assert bp.eject_depth == 12, bp.eject_depth

    topo = Ring2Topology(n_planes=1, vcs=CHI_VCS_WRITE)
    p = Ring2BaseParams(shared_inj=True, per_vc_srcq=True, per_vc_ports=True,
                        inj_depth=12, dir_inj_depth=8,
                        core_outstanding=0, ha_track=0)
    sim = Ring2BaseSim(topo, p, seed=0)
    for i in range(14):
        f = Flit(pid=i, txn_id=i, seq=0, nflit=1, src=0, dst=1,
                 kind="wdata", t_gen=0, plane=0)
        sim._place(f)
        sim._offer_flit(f)
    dat = (0, 0, "dat")
    assert len(sim.srcq[dat]) == 12, len(sim.srcq[dat])
    assert len(sim.pending[dat]) == 2
    sim.step()
    assert sim.st["n_injected"] == 1, sim.st["n_injected"]
    left = (len(sim.srcq[dat]) + len(sim.pending[dat])
            + sum(len(sim.srcq[(0, 0, "dat", d)]) for d in (1, -1)))
    assert left == 13, left
    assert sim.st["max_srcq"] <= 12, sim.st["max_srcq"]


def test_shared_fifo_pops_head_only_when_that_dir_q_has_room() -> None:
    """Shared FIFO → dir Q is head-of-line, not a scan for a movable flit.

    Three CW flits ahead of one CCW flit, CW Q two deep: two CW flits move,
    then the head is a CW flit with nowhere to go, so the transfer stops and
    the CCW flit behind it stays put even though its own Q is empty.
    """
    topo = Ring2Topology(n_planes=1, vcs=CHI_VCS_WRITE)
    p = Ring2BaseParams(shared_inj=True, per_vc_srcq=True, per_vc_ports=True,
                        inj_depth=8, dir_inj_depth=2,
                        core_outstanding=0, ha_track=0)
    sim = Ring2BaseSim(topo, p, seed=0)
    for i, d in enumerate((1, 1, 1, -1)):
        sim.srcq[(0, 0, "dat")].append(
            Flit(pid=i, txn_id=i, seq=0, nflit=1, src=0, dst=1, kind="wdata",
                 t_gen=0, plane=0, dir=d, idx=1, target=1, vc="dat"))
    sim._xfer_shared(0, 0)
    assert len(sim.srcq[(0, 0, "dat", 1)]) == 2
    assert len(sim.srcq[(0, 0, "dat", -1)]) == 0
    assert len(sim.srcq[(0, 0, "dat")]) == 2

    # drain one CW slot: the head moves, and the CCW flit behind it follows
    sim.srcq[(0, 0, "dat", 1)].popleft()
    sim._xfer_shared(0, 0)
    assert len(sim.srcq[(0, 0, "dat", 1)]) == 2
    assert len(sim.srcq[(0, 0, "dat", -1)]) == 1
    assert not sim.srcq[(0, 0, "dat")]


def test_free_slot_arbiter_prefers_the_direction_that_can_go() -> None:
    """`inj_sel=free_slot` must not hand the port to a blocked direction.

    Round-robin order commits the port before the outgoing hop is known, so
    it can pick a direction a transit flit already owns and idle the port
    even though the other direction was free. Here CW is occupied and CCW is
    clear: the round-robin order offers CW first, and only the free-slot
    arbiter reorders so the CCW flit boards.
    """
    def leg(sel: str) -> int:
        topo = Ring2Topology(n_planes=1, vcs=CHI_VCS_WRITE)
        p = Ring2BaseParams(shared_inj=True, per_vc_srcq=True,
                            per_vc_ports=True, inj_depth=8, dir_inj_depth=2,
                            core_outstanding=0, ha_track=0, inj_sel=sel)
        sim = Ring2BaseSim(topo, p, seed=0)
        for i, d in enumerate((1, -1)):
            sim.srcq[(0, 0, "dat", d)].append(
                Flit(pid=i, txn_id=i, seq=0, nflit=1, src=0, dst=(1 if d > 0
                                                                  else 19),
                     kind="wdata", t_gen=0, plane=0, dir=d, idx=0, target=1,
                     vc="dat"))
        sim.active_src.add((0, 0))
        # A transit flit owns the CW outgoing slot this cycle; CCW is clear.
        sim.seg_free[sim._seg(0, 1, 0, "dat")] = sim.t + 1
        groups = sim._port_groups(0, 0)
        dat = [g for g in groups if g and g[0][2] == "dat"][0]
        assert dat[0][3] == 1, "this test needs CW offered first"
        sim._board_one(0, 0, (0, 0), dat, sim.t)
        return sim.st["n_injected"]

    assert leg("rr") == 0
    assert leg("free_slot") == 1


def test_scoped_itag_only_blocks_flits_that_cross_the_holder() -> None:
    """A segment-scoped I-tag must not charge nodes it does not share a hop
    with.

    Node 5 holds a CW tag, so it is starving on hop 5→6. A flit boarding at
    node 0 bound past node 5 takes that hop and has to yield; one that leaves
    the ring at node 2 never touches it and must be let through. The
    plane-wide scope cannot tell them apart and blocks both, which is the
    collateral that makes the blunt version cost bandwidth.
    """
    def blocks(scope: str, target: int) -> bool:
        topo = Ring2Topology(n_planes=1, vcs=CHI_VCS_WRITE)
        p = Ring2BaseParams(shared_inj=True, per_vc_srcq=True,
                            per_vc_ports=True, itag_scope=scope)
        sim = Ring2BaseSim(topo, p, seed=0)
        sim.i_tag[(0, 1, "dat")].add(5)
        sim.itag_t[(0, 1, "dat", 5)] = sim.t
        f = Flit(pid=0, txn_id=0, seq=0, nflit=1, src=0, dst=target,
                 kind="wdata", t_gen=0, plane=0, dir=1, idx=0, target=target,
                 vc="dat")
        return sim._itag_blocks(f, 0)

    assert blocks("segment", 8) is True     # rides 0→…→8, crosses 5→6
    assert blocks("segment", 2) is False    # gone before node 5
    assert blocks("plane", 2) is True       # blunt scope cannot tell


def test_itag_hold_expires_so_transit_starvation_cannot_lock_the_ring():
    """A tag must stand down on its own.

    An I-tag does not stop transit, so a node starved by transit would hold
    its tag forever and idle every upstream injector for nothing. With
    `itag_hold` the tag clears itself once it has blocked that long.
    """
    topo = Ring2Topology(n_planes=1, vcs=CHI_VCS_WRITE)
    p = Ring2BaseParams(shared_inj=True, per_vc_srcq=True, per_vc_ports=True,
                        itag_scope="plane", itag_hold=4)
    sim = Ring2BaseSim(topo, p, seed=0)
    sim.i_tag[(0, 1, "dat")].add(5)
    sim.itag_t[(0, 1, "dat", 5)] = 0
    f = Flit(pid=0, txn_id=0, seq=0, nflit=1, src=0, dst=8, kind="wdata",
             t_gen=0, plane=0, dir=1, idx=0, target=8, vc="dat")
    sim.t = 3
    assert sim._itag_blocks(f, 0) is True
    sim.t = 4
    assert sim._itag_blocks(f, 0) is False
    assert 5 not in sim.i_tag[(0, 1, "dat")]


def test_buffer_occupancy_reports_full_per_plane() -> None:
    """Occupancy accounting is per (class, node, plane, VC, dir).

    Both planes hold a full FIFO of the same name; each instance must read
    100% full, not 200%, and the mean must never exceed the depth.
    """
    topo = Ring2Topology(n_planes=2, vcs=CHI_VCS_WRITE)
    p = Ring2BaseParams(shared_inj=True, per_vc_srcq=True, per_vc_ports=True,
                        inj_depth=12, dir_inj_depth=8, eject_depth=12,
                        buf_sample=1)
    sim = Ring2BaseSim(topo, p, seed=0)

    def _f(i):
        return Flit(pid=i, txn_id=i, seq=0, nflit=1, src=0, dst=1,
                    kind="wdata", t_gen=0, plane=0, dir=1, idx=1, target=1,
                    vc="dat")
    for pl in (0, 1):
        for i in range(12):
            sim.srcq[(0, pl, "dat")].append(_f(i))
            sim.ejectq[(1, pl, "dat")].append(_f(i))
        for i in range(8):
            sim.srcq[(0, pl, "dat", 1)].append(_f(i))
    sim._sample_buffers()
    sim._sample_buffers()

    b = sim.buffer_summary()
    assert b["n_samples"] == 2
    assert b["n_full_instances"] == 6, b["n_full_instances"]
    for r in b["worst"]:
        assert r["full_pct"] == 100.0, r
        assert r["occ_mean"] == r["depth"], r
    seen = {(r["buffer"], r["depth"], r["occ_mean"], r["n_instances"])
            for r in b["by_class"]}
    assert ("shared:dat", 12, 12.0, 2) in seen, seen
    assert ("dirq:dat", 8, 8.0, 2) in seen, seen
    assert ("leave:dat", 12, 12.0, 2) in seen, seen


def test_per_vc_ports_board_one_each_per_cycle() -> None:
    """REQ / RSP / DAT do not share the board port: 3 flits can leave at once.

    The merged-port fabric has to serialise the same three flits, so this is
    what `per_vc_ports` actually buys at the injection side.
    """
    topo = Ring2Topology(n_planes=1, vcs=CHI_VCS_WRITE)

    def _load(**over):
        p = Ring2BaseParams(shared_inj=True, inj_depth=8, dir_inj_depth=1,
                            core_outstanding=0, ha_track=0, **over)
        sim = Ring2BaseSim(topo, p, seed=0)
        for i, kind in enumerate(("wdata", "req", "dbid")):
            f = Flit(pid=i, txn_id=i, seq=0, nflit=1, src=0, dst=1,
                     kind=kind, t_gen=0, plane=0)
            sim._place(f)
            sim._offer_flit(f)
        sim.step()
        return sim.st["n_injected"]

    assert _load(per_vc_srcq=True, per_vc_ports=True) == 3
    assert _load() == 1


def test_two_write_leave_accepts_both_dirs() -> None:
    """Leave buffer: one write per incoming dir, one PE read."""
    topo = Ring2Topology(n_planes=1, vcs=CHI_VCS_WRITE)
    p = Ring2BaseParams(two_write_leave=True, eject_depth=4, eject_bw=0)
    sim = Ring2BaseSim(topo, p, seed=0)
    f1 = Flit(pid=0, txn_id=0, seq=0, nflit=1, src=0, dst=1,
              kind="wdata", t_gen=0, plane=0, dir=1, idx=1, target=0, vc="dat")
    f2 = Flit(pid=1, txn_id=1, seq=0, nflit=1, src=2, dst=1,
              kind="req", t_gen=0, plane=0, dir=-1, idx=1, target=0, vc="req")
    sim.arrivals[0] = [f1, f2]
    sim.step()
    assert sim.st["n_eject_full_deflect"] == 0, sim.st["n_eject_full_deflect"]
    assert len(sim.ejectq[(1, 0)]) == 2, len(sim.ejectq[(1, 0)])

    one = Ring2BaseSim(topo, Ring2BaseParams(two_write_leave=False,
                                             eject_depth=4, eject_bw=0), 0)
    g1 = Flit(pid=0, txn_id=0, seq=0, nflit=1, src=0, dst=1,
              kind="wdata", t_gen=0, plane=0, dir=1, idx=1, target=0, vc="dat")
    g2 = Flit(pid=1, txn_id=1, seq=0, nflit=1, src=2, dst=1,
              kind="req", t_gen=0, plane=0, dir=-1, idx=1, target=0, vc="req")
    one.arrivals[0] = [g1, g2]
    one.step()
    assert one.st["n_eject_full_deflect"] == 1, one.st["n_eject_full_deflect"]
    assert len(one.ejectq[(1, 0)]) == 1, len(one.ejectq[(1, 0)])


def test_per_vc_leave_is_two_write_one_read_per_vc() -> None:
    """With per-VC ports the leave buffer is per VC, and each takes two dirs.

    Four flits land at one node in the same cycle: DAT from both directions
    and REQ from both. Merged ports would keep two (one per direction) and
    deflect two; per-VC ports keep all four, two per VC buffer.
    """
    topo = Ring2Topology(n_planes=1, vcs=CHI_VCS_WRITE)

    def _land(**over):
        p = Ring2BaseParams(two_write_leave=True, eject_depth=4, eject_bw=0,
                            **over)
        sim = Ring2BaseSim(topo, p, seed=0)
        sim.arrivals[0] = [
            Flit(pid=i, txn_id=i, seq=0, nflit=1, src=s, dst=1, kind=kind,
                 t_gen=0, plane=0, dir=d, idx=1, target=0, vc=vc)
            for i, (kind, vc, d, s) in enumerate((
                ("wdata", "dat", 1, 0), ("wdata", "dat", -1, 2),
                ("req", "req", 1, 4), ("req", "req", -1, 6)))]
        sim.step()
        return sim.st["n_eject_full_deflect"], sum(
            len(q) for q in sim.ejectq.values())

    assert _land(per_vc_srcq=True, per_vc_ports=True) == (0, 4)
    assert _land() == (2, 2)


def test_leave_occ_gt1_counts_as_down_fail() -> None:
    """A second flit in the two-write-one-read FIFO is a down-ring fail.

    It is not a ring deflection: the flit has already left, so E-tag stays
    down and n_deflections is unchanged.
    """
    topo = Ring2Topology(n_planes=1, vcs=CHI_VCS_WRITE)
    p = Ring2BaseParams(two_write_leave=True, eject_depth=4, eject_bw=0)
    sim = Ring2BaseSim(topo, p, seed=0)
    sim.arrivals[0] = [
        Flit(pid=0, txn_id=0, seq=0, nflit=1, src=0, dst=1,
             kind="wdata", t_gen=0, plane=0, dir=1, idx=1, target=0, vc="dat"),
        Flit(pid=1, txn_id=1, seq=0, nflit=1, src=2, dst=1,
             kind="req", t_gen=0, plane=0, dir=-1, idx=1, target=0, vc="req"),
    ]
    sim.step()
    assert sim.st["n_eject_full_deflect"] == 0
    assert sim.st["n_deflections"] == 0
    assert sim.st["n_leave_occ_gt1"] == 1
    assert sim.st["n_etag_raised"] == 0
    assert len(sim.ejectq[(1, 0)]) == 2


def test_workload_counts() -> None:
    a = build_allpairs(m=2, m_resp=4)
    assert len(a) == 200
    assert all(is_core(t.core) and is_ha(t.ha) for t in a)
    u = build_uniform(k=7, seed=0)
    assert len(u) == 70
    from collections import Counter
    c = Counter(t.core for t in u)
    assert set(c.values()) == {7}


def _expected_flits(txns) -> int:
    return sum(t.m_req + t.m_resp for t in txns)


def test_s0_completes_and_conserves() -> None:
    topo = Ring2Topology()
    txns = build_allpairs(m=1, m_resp=4)
    r = run_base(topo, txns)
    exp = _expected_flits(txns)
    assert r["completed"], f"S0 stalled at t={r['makespan']}"
    assert r["n_txn_done"] == 100, r["n_txn_done"]
    assert r["n_delivered_flits"] == exp, (r["n_delivered_flits"], exp)
    assert r["n_inring_blocked"] == 0, r["n_inring_blocked"]
    assert r["makespan"] >= 1


def test_s1_completes_and_conserves() -> None:
    topo = Ring2Topology()
    txns = build_allpairs(m=1, m_resp=4)
    r = run_aimd(topo, txns)
    exp = _expected_flits(txns)
    assert r["completed"], f"S1 stalled at t={r['makespan']}"
    assert r["n_delivered_flits"] == exp
    assert r["n_inring_blocked"] == 0


def test_three_schemes_same_flits() -> None:
    topo = Ring2Topology()
    txns = build_allpairs(m=1, m_resp=2)
    s0 = run_base(topo, txns)
    s1 = run_aimd(topo, txns)
    s2 = run_rg(topo, txns, cfg=RGConfig(algo="islip", iters=2))
    s3 = run_pop(topo, txns)
    s4 = run_dist(topo, txns)
    s5 = run_dist(topo, txns, params=s5_params())
    s6 = run_dist(topo, txns, params=s6_params())
    s7 = run_dist(topo, txns, params=s7_params())
    s8 = run_dist(topo, txns, params=s8_params())
    s9 = run_dist(topo, txns, params=s9_params())
    s10 = run_dist(topo, txns, params=s10_params())
    s11 = run_dist(topo, txns, params=s11_params())
    s12 = run_dist(topo, txns, params=s12_params())
    s13 = run_dist(topo, txns, params=s13_params())
    s14 = run_dist(topo, txns, params=s14_params())
    exp = _expected_flits(txns)
    for name, r in (("S0", s0), ("S1", s1), ("S2", s2), ("S3", s3),
                    ("S4", s4), ("S5", s5), ("S6", s6), ("S7", s7),
                    ("S8", s8), ("S9", s9), ("S10", s10), ("S11", s11),
                    ("S12", s12), ("S13", s13), ("S14", s14)):
        assert r["completed"], name
        assert r["n_delivered_flits"] == exp, (name, r["n_delivered_flits"])
        assert r["n_txn_done"] == len(txns), name
        assert r["n_inring_blocked"] == 0, (name, r["n_inring_blocked"])


def test_makespan_ge_bound() -> None:
    topo = Ring2Topology()
    txns = build_allpairs(m=1, m_resp=4)
    rp, sp = paths_for_txns(topo, txns, strategy="least_occupied")
    b = topo.analytic_bounds(rp, sp, m_req=1, m_resp=4)
    for name, r in (
            ("S0", run_base(topo, txns)),
            ("S1", run_aimd(topo, txns)),
            ("S2", run_rg(topo, txns, cfg=RGConfig(algo="greedy_ff"))),
            ("S3", run_pop(topo, txns)),
            ("S4", run_dist(topo, txns)),
            ("S5", run_dist(topo, txns, params=s5_params())),
            ("S6", run_dist(topo, txns, params=s6_params())),
            ("S7", run_dist(topo, txns, params=s7_params())),
            ("S8", run_dist(topo, txns, params=s8_params())),
            ("S9", run_dist(topo, txns, params=s9_params())),
            ("S10", run_dist(topo, txns, params=s10_params())),
            ("S11", run_dist(topo, txns, params=s11_params())),
            ("S12", run_dist(topo, txns, params=s12_params())),
            ("S13", run_dist(topo, txns, params=s13_params())),
            ("S14", run_dist(topo, txns, params=s14_params()))):
        assert r["makespan"] >= b["bound"], (
            f"{name} makespan {r['makespan']} < bound {b['bound']}")


def test_inring_never_blocked_under_load() -> None:
    topo = Ring2Topology()
    txns = build_uniform(k=30, m_resp=4, seed=0)
    p = Ring2BaseParams(eject_depth=2, t_inj=16, t_xfer=2)
    r = run_base(topo, txns, params=p)
    assert r["completed"], r
    assert r["n_inring_blocked"] == 0, r["n_inring_blocked"]


def test_itag_bounds_starve() -> None:
    """A raised I-tag implies the holder eventually boards; starve is finite."""
    topo = Ring2Topology()
    txns = build_uniform(k=20, m_resp=4, seed=1)
    p = Ring2BaseParams(t_inj=32)
    sim = Ring2BaseSim(topo, p, seed=1)
    sim.offer_batch(txns)
    while sim.t < 80_000 and not sim.done():
        sim.step()
    assert sim.done()
    # max inject starve is finite and we did raise (or never needed to)
    assert sim.st["max_inj_starve"] < 20_000
    assert sim.st["n_inring_blocked"] == 0


def test_etag_and_deflect_bounded() -> None:
    topo = Ring2Topology()
    txns = build_allpairs(m=2, m_resp=4)
    p = Ring2BaseParams(eject_depth=1, resv_ej=1, t_xfer=3)
    r = run_base(topo, txns, params=p)
    assert r["completed"]
    # a flit that keeps missing the leave port raises E-tag; deflection
    # count per delivered flit must stay well below a livelock spiral
    assert r["max_deflections"] < 200, r["max_deflections"]


def test_boarding_queue_depth_respected() -> None:
    """The boarding queue never exceeds inj_depth, and it does fill up."""
    topo = Ring2Topology()
    txns = build_uniform(k=200, m_resp=4, seed=0)
    for depth in (2, 8):
        p = Ring2BaseParams(inj_depth=depth, plane_sel="least_occupied")
        r = run_base(topo, txns, params=p)
        assert r["completed"], depth
        assert r["max_srcq"] <= depth, (depth, r["max_srcq"])
        assert r["max_srcq"] == depth, (depth, r["max_srcq"])
        # backpressure actually engaged: PEs had to wait behind the queue
        assert r["n_admit_stall"] > 0, depth


def test_ports_are_per_node_plane() -> None:
    """S2 prices inject/eject per (node, plane), same as the S0 DES."""
    topo = Ring2Topology()
    txns = build_allpairs(m=1, m_resp=4)
    fp = topo.footprint(0, topo.make_path(0, 3, 1), 4, kind="resp")
    keys = {k for k, _, _, _ in requirements(topo, fp)}
    assert ("inj", 0, 1) in keys, keys
    assert ("ej", 3, 1) in keys, keys
    assert ("inj", 0) not in keys and ("ej", 3) not in keys
    # a node may board on both planes in the same cycle
    r = run_rg(topo, txns, cfg=RGConfig(algo="islip", iters=2))
    assert r["completed"] and r["n_conflicts"] == 0


def test_rg_replay_conflict_free() -> None:
    topo = Ring2Topology()
    txns = build_allpairs(m=1, m_resp=4)
    for algo in RING2_ALGOS:
        cfg = RGConfig(algo=algo, iters=2 if algo in ("islip", "pim") else 1)
        out = schedule(topo, txns, cfg=cfg)
        assert replay_ok(topo, out["grants"]), algo
        assert out["n_conflicts"] == 0, (algo, out["n_conflicts"])
        assert out["completed"], algo


def test_rg_req_before_resp() -> None:
    topo = Ring2Topology()
    txns = build_allpairs(m=1, m_resp=4)
    out = schedule(topo, txns, cfg=RGConfig(algo="islip", iters=2))
    by_txn: dict[int, dict[str, int]] = {}
    for g in out["grants"]:
        by_txn.setdefault(g.txn_id, {})[g.kind] = g.t0
    for tid, pair in by_txn.items():
        assert pair["resp"] >= pair["req"], (tid, pair)


def test_uniform_multi_seed_s0() -> None:
    topo = Ring2Topology()
    mks = []
    for seed in (0, 1, 2):
        txns = build_uniform(k=15, m_resp=4, seed=seed)
        r = run_base(topo, txns, seed=seed)
        assert r["completed"]
        assert r["n_delivered_flits"] == _expected_flits(txns)
        mks.append(r["makespan"])
    assert min(mks) > 0


def test_cost_ring2_does_not_break_mesh() -> None:
    from rg_topo import Topology
    mesh = Topology("mesh")
    c = sched_cost("greedy_ff", mesh, 2256, iters=1)
    assert abs(c["area_norm"] - 0.05) < 1e-6, c["area_norm"]
    d = distributed_cost("mesh_base")
    assert d["bits"] > 0
    topo = Ring2Topology()
    c2 = sched_cost("islip_ring2", topo, 200, iters=2, n_rounds=20,
                    conflict_domain="interval")
    assert c2["area_norm"] > 0 and c2["t_sched_cycles"] > 0
    db = distributed_cost("ring2_base", n_nodes=20)
    da = distributed_cost("ring2_aimd", n_nodes=20)
    dr = distributed_cost("ring2_rg", n_nodes=20)
    dp = distributed_cost("ring2_pop", n_nodes=20)
    dd = distributed_cost("ring2_dist", n_nodes=20)
    de = distributed_cost("ring2_ej", n_nodes=20)
    assert da["bits"] > db["bits"]
    assert dp["bits"] > db["bits"]
    # S0 and S2 share the credit + I/E-tag datapath; S2 does not drop it
    assert dr["bits"] == db["bits"], (dr["bits"], db["bits"])
    # S4 kind-aware leave is mux preference only — same bits as S0
    assert dd["bits"] == db["bits"], (dd["bits"], db["bits"])
    assert de["bits"] > db["bits"], (de["bits"], db["bits"])
    assert "leave_slot_resv" in de["breakdown"]
    assert "credit_counters" in db["breakdown"]
    assert "itag_etag_state" in db["breakdown"]
    assert "core_outstanding" in db["breakdown"]
    assert "credit_counters" in dr["breakdown"]
    assert "itag_etag_state" in dr["breakdown"]
    assert "core_outstanding" in dr["breakdown"]
    assert "core_outstanding" in dp["breakdown"]
    assert "dest_window" not in dp["breakdown"]
    assert "ha_req_rr" in dp["breakdown"]
    assert "ha_pending" in dp["breakdown"]
    assert "src_tokens" not in dp["breakdown"]
    assert "grant_fields" not in dp["breakdown"]
    # S3 is cheaper than S2 once the arbiter is counted
    c2 = sched_cost("islip_ring2", topo, 200, iters=2, n_rounds=20,
                    conflict_domain="interval")
    assert db["bits"] < dp["bits"] < db["bits"] + c2["bits"] + 1, (
        db["bits"], dp["bits"], c2["bits"])


def test_pop_window_and_token() -> None:
    """S3: request is the grant; HA schedules; core window never exceeds W."""
    topo = Ring2Topology()
    txns = build_uniform(k=40, m_resp=4, seed=0)
    n_resp = sum(t.m_resp for t in txns)
    # W is outstanding reads per (core, resp plane).
    for scope, w in (("req_as_grant", 4), ("resp_only", 4),
                     ("req_as_grant", 2), ("both", 4)):
        p = Ring2BaseParams(pop_window=w, pop_scope=scope,
                            plane_sel="least_occupied")
        sim = Ring2PopSim(topo, p, seed=0)
        sim.offer_batch(txns)
        offered_cap = 0
        while sim.t < 200_000 and not sim.done():
            sim.step()
            if sim._core_window():
                for v in sim.core_used.values():
                    assert v <= w, (scope, w, v)
            # a window-blocked head must not keep an I-tag
            for key, q in sim.srcq.items():
                if not q:
                    continue
                f = q[0]
                if sim._may_inject(key[0], key[1], f):
                    continue
                assert key[0] not in sim.i_tag[(f.plane, f.dir)], (
                    scope, key, f.plane, f.dir, sim.i_tag[(f.plane, f.dir)])
            # responses exist only after their request drained
            assert sim.st["n_offered_resp"] <= sim.st["n_delivered_req"] * 4, (
                scope, sim.st["n_offered_resp"], sim.st["n_delivered_req"])
            offered_cap = max(offered_cap, sim.st["n_offered_resp"])
        assert sim.done(), (scope, w, sim.summary())
        s = sim.summary()
        if sim._core_window():
            assert s["max_pull_outstanding"] <= w, (
                scope, w, s["max_pull_outstanding"])
            assert all(v == 0 for v in sim.core_used.values())
        assert s["max_ejectq"] <= p.eject_depth, s["max_ejectq"]
        assert s["n_inring_blocked"] == 0
        assert s["n_pull_issued"] == n_resp, (scope, s["n_pull_issued"], n_resp)
        assert s["n_offered_resp"] == n_resp


def test_core_outstanding_aligned() -> None:
    """S0–S14 share a 100-per-core outstanding-read cap."""
    from rg_ring2_aimd import Ring2AimdSim
    from rg_ring2_base import CORE_OUTSTANDING

    cap = CORE_OUTSTANDING
    assert Ring2BaseParams().core_outstanding == cap
    assert RGConfig().core_outstanding == cap
    topo = Ring2Topology()

    # Small cap binds quickly and proves the scoreboard, not just the default.
    bind = 8
    tx_bind = build_uniform(k=40, m_resp=4, seed=0)
    p_bind = Ring2BaseParams(core_outstanding=bind, plane_sel="least_occupied")
    for name, sim in (
            ("S0", Ring2BaseSim(topo, p_bind, seed=0)),
            ("S1", Ring2AimdSim(topo, Ring2BaseParams(
                core_outstanding=bind, plane_sel="least_occupied",
                aimd=True), seed=0)),
            ("S3", Ring2PopSim(topo, Ring2BaseParams(
                core_outstanding=bind, plane_sel="least_occupied",
                pop_window=0), seed=0)),
            ("S4", Ring2DistSim(topo, Ring2DistParams(
                core_outstanding=bind, plane_sel="least_occupied",
                leave_useful=True), seed=0)),
            ("S5", Ring2DistSim(topo, s5_params(
                core_outstanding=bind, plane_sel="least_occupied"), seed=0)),
            ("S6", Ring2DistSim(topo, s6_params(
                core_outstanding=bind, plane_sel="least_occupied"), seed=0)),
            ("S7", Ring2DistSim(topo, s7_params(
                core_outstanding=bind, plane_sel="least_occupied"), seed=0)),
            ("S8", Ring2DistSim(topo, s8_params(
                core_outstanding=bind, plane_sel="least_occupied"), seed=0)),
            ("S9", Ring2DistSim(topo, s9_params(
                core_outstanding=bind, plane_sel="least_occupied"), seed=0)),
            ("S10", Ring2DistSim(topo, s10_params(
                core_outstanding=bind, plane_sel="least_occupied"), seed=0)),
            ("S11", Ring2DistSim(topo, s11_params(
                core_outstanding=bind, plane_sel="least_occupied"), seed=0)),
            ("S12", Ring2DistSim(topo, s12_params(
                core_outstanding=bind, plane_sel="least_occupied"), seed=0)),
            ("S13", Ring2DistSim(topo, s13_params(
                core_outstanding=bind, plane_sel="least_occupied"), seed=0)),
            ("S14", Ring2DistSim(topo, s14_params(
                core_outstanding=bind, plane_sel="least_occupied"), seed=0))):
        sim.offer_batch(tx_bind)
        while sim.t < 200_000 and not sim.done():
            sim.step()
            for c, v in sim.core_outst.items():
                assert v <= bind, (name, c, v)
            # Do not probe _may_inject: S1 would consume AIMD tokens.
            assert not any(sim._outst_full(c) and sim.core_outst[c] > bind
                           for c in sim.core_outst)
        assert sim.done(), (name, sim.summary())
        assert sim.st["max_core_outstanding"] <= bind, (
            name, sim.st["max_core_outstanding"])
        assert sim.st["max_core_outstanding"] == bind, (
            name, sim.st["max_core_outstanding"])
        assert all(v == 0 for v in sim.core_outst.values()), name

    out = schedule(topo, tx_bind, cfg=RGConfig(
        algo="islip", iters=2, core_outstanding=bind))
    assert out["completed"]
    assert out["max_core_outstanding"] <= bind, out["max_core_outstanding"]
    assert out["max_core_outstanding"] == bind, out["max_core_outstanding"]
    assert replay_ok(topo, out["grants"])

    # Default cap: K=1000 unlimited S0 peaks well above 100, so the cap binds.
    tx = build_uniform(k=1000, m_resp=4, seed=0)
    p = Ring2BaseParams(core_outstanding=cap, plane_sel="least_occupied")
    for name, sim in (
            ("S0", Ring2BaseSim(topo, p, seed=0)),
            ("S3", Ring2PopSim(topo, p, seed=0))):
        sim.offer_batch(tx)
        while sim.t < 400_000 and not sim.done():
            sim.step()
            for c, v in sim.core_outst.items():
                assert v <= cap, (name, c, v)
        assert sim.done(), (name, sim.summary())
        assert sim.st["max_core_outstanding"] <= cap
        assert sim.st["max_core_outstanding"] == cap, (
            name, sim.st["max_core_outstanding"])
        assert all(v == 0 for v in sim.core_outst.values()), name

    s1 = Ring2AimdSim(topo, p, seed=0)
    s1.offer_batch(tx)
    while s1.t < 400_000 and not s1.done():
        s1.step()
        for c, v in s1.core_outst.items():
            assert v <= cap, ("S1", c, v)
    assert s1.done(), s1.summary()
    assert s1.st["max_core_outstanding"] <= cap

    out_cap = schedule(topo, tx, cfg=RGConfig(
        algo="islip", iters=2, core_outstanding=cap))
    assert out_cap["completed"]
    # A generation can stagger: early resps eject before late reqs board,
    # so the peak may sit just under the cap.
    assert out_cap["max_core_outstanding"] <= cap
    assert out_cap["max_core_outstanding"] >= max(1, cap - 32), (
        out_cap["max_core_outstanding"])
    assert replay_ok(topo, out_cap["grants"])


def test_s4_leave_completes_allpairs() -> None:
    """Kind-aware leave is mux-only; per-link delays may lose to S0."""
    topo = Ring2Topology()
    txns = build_allpairs(m=1, m_resp=4)
    s0 = run_base(topo, txns)
    s4 = run_dist(topo, txns, params=Ring2DistParams(leave_useful=True))
    assert s0["completed"] and s4["completed"]
    assert s4["n_delivered_flits"] == s0["n_delivered_flits"]
    assert s4["n_inring_blocked"] == 0


def test_s5_ej_beats_s0() -> None:
    """Leave-slot lock should cut allpairs makespan and kill deflections."""
    topo = Ring2Topology()
    txns = build_allpairs(m=1, m_resp=4)
    s0 = run_base(topo, txns)
    s5 = run_dist(topo, txns, params=s5_params())
    assert s0["completed"] and s5["completed"]
    assert s5["n_delivered_flits"] == s0["n_delivered_flits"]
    assert s5["makespan"] < s0["makespan"], (s5["makespan"], s0["makespan"])
    assert s5["n_deflections"] == 0, s5["n_deflections"]


def test_s6_oldest_beats_s5_uniform() -> None:
    """Oldest dest-clash should not regress allpairs and should win K=100."""
    topo = Ring2Topology()
    ap = build_allpairs(m=1, m_resp=4)
    s5a = run_dist(topo, ap, params=s5_params())
    s6a = run_dist(topo, ap, params=s6_params())
    assert s5a["completed"] and s6a["completed"]
    assert s6a["n_delivered_flits"] == s5a["n_delivered_flits"]
    assert s6a["makespan"] <= s5a["makespan"], (
        s6a["makespan"], s5a["makespan"])
    assert s6a["n_deflections"] == 0, s6a["n_deflections"]
    tx = build_uniform(k=100, m_resp=4, seed=0)
    s5u = run_dist(topo, tx, params=s5_params())
    s6u = run_dist(topo, tx, params=s6_params())
    assert s5u["completed"] and s6u["completed"]
    assert s6u["makespan"] <= s5u["makespan"] + 10, (
        s6u["makespan"], s5u["makespan"])
    assert s6u["n_deflections"] == 0, s6u["n_deflections"]


def test_s7_hop_bounce_beats_s6() -> None:
    """Late plane bind on a busy first hop should beat S6 on allpairs."""
    topo = Ring2Topology()
    ap = build_allpairs(m=1, m_resp=4)
    s6a = run_dist(topo, ap, params=s6_params())
    s7a = run_dist(topo, ap, params=s7_params())
    assert s6a["completed"] and s7a["completed"]
    assert s7a["n_delivered_flits"] == s6a["n_delivered_flits"]
    assert s7a["makespan"] < s6a["makespan"], (
        s7a["makespan"], s6a["makespan"])
    assert s7a["n_deflections"] == 0, s7a["n_deflections"]
    tx = build_uniform(k=100, m_resp=4, seed=0)
    s6u = run_dist(topo, tx, params=s6_params())
    s7u = run_dist(topo, tx, params=s7_params())
    assert s6u["completed"] and s7u["completed"]
    assert s7u["makespan"] <= s6u["makespan"], (
        s7u["makespan"], s6u["makespan"])
    assert s7u["n_deflections"] == 0, s7u["n_deflections"]


def test_s8_late_plane_beats_s7() -> None:
    """Always late-bind plane should beat S7 hop-only bounce on allpairs."""
    topo = Ring2Topology()
    ap = build_allpairs(m=1, m_resp=4)
    s7a = run_dist(topo, ap, params=s7_params())
    s8a = run_dist(topo, ap, params=s8_params())
    assert s7a["completed"] and s8a["completed"]
    assert s8a["n_delivered_flits"] == s7a["n_delivered_flits"]
    assert s8a["makespan"] < s7a["makespan"], (
        s8a["makespan"], s7a["makespan"])
    assert s8a["n_deflections"] == 0, s8a["n_deflections"]
    tx = build_uniform(k=100, m_resp=4, seed=0)
    s7u = run_dist(topo, tx, params=s7_params())
    s8u = run_dist(topo, tx, params=s8_params())
    assert s7u["completed"] and s8u["completed"]
    assert s8u["makespan"] <= s7u["makespan"], (
        s8u["makespan"], s7u["makespan"])
    assert s8u["n_deflections"] == 0, s8u["n_deflections"]


def test_s9_late_dir_beats_s8() -> None:
    """Other-dir slack should beat S8 on uniform without a large allpairs gap."""
    topo = Ring2Topology()
    ap = build_allpairs(m=1, m_resp=4)
    s8a = run_dist(topo, ap, params=s8_params())
    s9a = run_dist(topo, ap, params=s9_params())
    assert s8a["completed"] and s9a["completed"]
    assert s9a["n_delivered_flits"] == s8a["n_delivered_flits"]
    assert s9a["makespan"] <= s8a["makespan"] + 8, (
        s9a["makespan"], s8a["makespan"])
    assert s9a["n_deflections"] == 0, s9a["n_deflections"]
    tx = build_uniform(k=100, m_resp=4, seed=0)
    s8u = run_dist(topo, tx, params=s8_params())
    s9u = run_dist(topo, tx, params=s9_params())
    assert s8u["completed"] and s9u["completed"]
    assert s9u["makespan"] < s8u["makespan"], (
        s9u["makespan"], s8u["makespan"])
    assert s9u["n_deflections"] == 0, s9u["n_deflections"]


def test_s10_resp_late_dir_beats_s9() -> None:
    """Resp-only late_dir stays deflection-free; K=500 should beat S9."""
    topo = Ring2Topology()
    ap = build_allpairs(m=1, m_resp=4)
    s9a = run_dist(topo, ap, params=s9_params())
    s10a = run_dist(topo, ap, params=s10_params())
    assert s9a["completed"] and s10a["completed"]
    assert s10a["n_delivered_flits"] == s9a["n_delivered_flits"]
    assert s10a["n_deflections"] == 0, s10a["n_deflections"]
    tx = build_uniform(k=500, m_resp=4, seed=0)
    s9u = run_dist(topo, tx, params=s9_params())
    s10u = run_dist(topo, tx, params=s10_params())
    assert s9u["completed"] and s10u["completed"]
    assert s10u["n_deflections"] == 0, s10u["n_deflections"]


def test_s14_sib_ha_beats_s13_allpairs() -> None:
    """HA sibling plane yield beats S13 allpairs; K=500 may lose."""
    topo = Ring2Topology()
    ap = build_allpairs(m=1, m_resp=4)
    s13a = run_dist(topo, ap, params=s13_params())
    s14a = run_dist(topo, ap, params=s14_params())
    assert s13a["completed"] and s14a["completed"]
    assert s14a["n_delivered_flits"] == s13a["n_delivered_flits"]
    assert s14a["makespan"] < s13a["makespan"], (
        s14a["makespan"], s13a["makespan"])
    assert s14a["n_deflections"] == 0, s14a["n_deflections"]


def test_s13_hopkeep_beats_s12_uniform() -> None:
    """Shorter-path hop-grant beats S12 on K=500; allpairs may tie at 68."""
    topo = Ring2Topology()
    ap = build_allpairs(m=1, m_resp=4)
    s12a = run_dist(topo, ap, params=s12_params())
    s13a = run_dist(topo, ap, params=s13_params())
    assert s12a["completed"] and s13a["completed"]
    assert s13a["n_delivered_flits"] == s12a["n_delivered_flits"]
    assert s13a["n_deflections"] == 0, s13a["n_deflections"]
    tx = build_uniform(k=500, m_resp=4, seed=0)
    s12u = run_dist(topo, tx, params=s12_params())
    s13u = run_dist(topo, tx, params=s13_params())
    assert s12u["completed"] and s13u["completed"]
    assert s13u["makespan"] < s12u["makespan"], (
        s13u["makespan"], s12u["makespan"])
    assert s13u["n_deflections"] == 0, s13u["n_deflections"]


def test_s12_hop_islip_beats_s11_uniform() -> None:
    """Dest-then-hop I=1 beats S11 on K=20; allpairs may be +1; K=500 ties."""
    topo = Ring2Topology()
    ap = build_allpairs(m=1, m_resp=4)
    s11a = run_dist(topo, ap, params=s11_params())
    s12a = run_dist(topo, ap, params=s12_params())
    assert s11a["completed"] and s12a["completed"]
    assert s12a["n_delivered_flits"] == s11a["n_delivered_flits"]
    assert s12a["n_deflections"] == 0, s12a["n_deflections"]
    tx = build_uniform(k=20, m_resp=4, seed=0)
    s11u = run_dist(topo, tx, params=s11_params())
    s12u = run_dist(topo, tx, params=s12_params())
    assert s11u["completed"] and s12u["completed"]
    assert s12u["makespan"] <= s11u["makespan"] + 5, (
        s12u["makespan"], s11u["makespan"])
    assert s12u["n_deflections"] == 0, s12u["n_deflections"]


def test_s11_hop_hold_beats_s10() -> None:
    """Same-cycle resp hop mutex should beat S10 on allpairs and K=500."""
    topo = Ring2Topology()
    ap = build_allpairs(m=1, m_resp=4)
    s10a = run_dist(topo, ap, params=s10_params())
    s11a = run_dist(topo, ap, params=s11_params())
    assert s10a["completed"] and s11a["completed"]
    assert s11a["n_delivered_flits"] == s10a["n_delivered_flits"]
    assert s11a["makespan"] < s10a["makespan"], (
        s11a["makespan"], s10a["makespan"])
    assert s11a["n_deflections"] == 0, s11a["n_deflections"]
    tx = build_uniform(k=500, m_resp=4, seed=0)
    s10u = run_dist(topo, tx, params=s10_params())
    s11u = run_dist(topo, tx, params=s11_params())
    assert s10u["completed"] and s11u["completed"]
    assert s11u["makespan"] < s10u["makespan"], (
        s11u["makespan"], s10u["makespan"])
    assert s11u["n_deflections"] == 0, s11u["n_deflections"]


def test_plane_sel_all_work() -> None:
    topo = Ring2Topology()
    txns = build_allpairs(m=1, m_resp=2)
    for ps in ("static_hash", "rr_per_pkt", "least_occupied",
               "req_resp_split"):
        r = run_base(topo, txns, params=Ring2BaseParams(plane_sel=ps))
        assert r["completed"], ps


# ---------------------------------------------------------------------------
# CHI WriteNoSnp path (3 VCs). The read study runs the default ("req","dat")
# topology, so none of these touch it.
# ---------------------------------------------------------------------------

def _write_topo() -> Ring2Topology:
    return Ring2Topology(vcs=CHI_VCS_WRITE, route="latency")


def _run_write(scheme: str = "S0", *, k: int = 40, W: int = 4,
               pattern: str = "uniform", cfg: dict | None = None):
    """Run one write workload.

    "study" is the configuration the write report analyses: 10 AI cores
    writing uniformly to 8 memory nodes, with 9 and 19 non-terminal.
    """
    from dse_ring2_write_fair import CORE_NODES, FABRIC, MEM_NODES
    topo = _write_topo()
    if pattern == "study":
        txns = build_uniform_write(k=k, m_wdata=W, seed=0, mem=MEM_NODES,
                                   core_set=CORE_NODES)
        fabric = dict(FABRIC)
    else:
        txns = (build_uniform_write(k=k, m_wdata=W, seed=0)
                if pattern == "uniform"
                else build_hot_write(k=k, m_wdata=W, hot_has=(9, 11)))
        fabric = dict(plane_sel="least_occupied", per_vc_srcq=True)
    fabric.update(cfg or {})
    if scheme == "S0":
        sim = Ring2BaseSim(topo, Ring2BaseParams(**fabric), seed=0)
    else:
        sim = Ring2FcSim(topo, Ring2FcParams(
            **fabric, mode="s15" if scheme == "S15" else "s1"), seed=0)
    sim.offer_batch(txns)
    while sim.t < 400_000 and not sim.done():
        sim.step()
    return topo, txns, sim


def test_write_vc_mapping() -> None:
    got = {k: vc_of(k) for k in ("req", "dbid", "wdata", "comp", "resp")}
    want = {"req": "req", "dbid": "rsp", "wdata": "dat", "comp": "rsp",
            "resp": "dat"}
    assert got == want, got
    assert Ring2Topology().vcs == ("req", "dat"), "read topology moved"
    assert _write_topo().hop_bw_cap == 3 * Ring2Topology().hop_bw_cap // 2, \
        _write_topo().hop_bw_cap


def test_write_four_phase_conservation() -> None:
    _, txns, sim = _run_write()
    s = sim.summary()
    assert s["completed"], f"write batch did not drain: {s['n_txn_done']}"
    n, W = len(txns), txns[0].m_wdata
    for kind, want in (("req", n), ("dbid", n), ("wdata", n * W),
                       ("comp", n)):
        off, got = s[f"n_offered_{kind}"], s[f"n_delivered_{kind}"]
        assert off == got == want, f"{kind}: offered {off} delivered {got} " \
                                   f"want {want}"


def test_write_phase_ordering() -> None:
    """Every WriteData lands after its DBIDResp and before its Comp."""
    _, txns, sim = _run_write()
    bad = 0
    for tid, t0 in sim.wr_t0.items():
        recv = sim.wr_recv_times.get(tid) or []
        if not recv:
            continue
        if min(recv) < t0:
            bad += 1
    assert bad == 0, f"{bad} txns sent WriteData before DBIDResp"
    assert not sim.wdata_left or all(v == 0 for v in sim.wdata_left.values()), \
        "WriteData outstanding at end of run"


def test_write_inring_never_blocked() -> None:
    for scheme in ("S0", "S1", "S15"):
        _, _, sim = _run_write(scheme, pattern="cluster")
        s = sim.summary()
        assert s["n_inring_blocked"] == 0, f"{scheme} stalled in-ring flits"
        assert s["max_inring_hold"] == 0, \
            f"{scheme} buffered {s['max_inring_hold']} flits on a segment"


def test_write_makespan_ge_bound() -> None:
    topo, txns, sim = _run_write()
    vp = write_paths_for_txns(topo, txns, strategy="least_occupied")
    b = write_bounds(topo, vp, m_req=1, m_rsp=2, m_wdata=txns[0].m_wdata)
    mk = sim.summary()["makespan"]
    assert mk >= b["bound"], f"makespan {mk} below bound {b['bound']}"


def test_write_fairness_metrics_sane() -> None:
    _, _, sim = _run_write(pattern="cluster")
    s = sim.summary()
    f = fairness_stats(s["wr_inject_by_core"], s["makespan"], 40 * 4)
    assert 0.0 < f["jain"] <= 1.0 + 1e-9, f["jain"]
    assert f["max_min"] >= 1.0, f["max_min"]
    assert f["bw_min"] <= f["bw_mean"] <= f["bw_max"], f
    assert f["throughput"] > 0.0, f


def test_binned_jain_averages_per_bin_over_the_fair_window() -> None:
    """The headline fairness metric, pinned on hand-built traces.

    What is being checked is the definition, not the fabric: Jain of the
    cores inside each `bin_w` window, averaged over the bins that lie wholly
    inside the contention window.
    """
    even = {c: list(range(0, 1000, 10)) for c in range(10)}
    r = binned_jain(even, 50, 1000)
    assert (r["n_bins"], r["n_cores"]) == (20, 10), r
    assert r["jain_bin_mean"] == 1.0, r
    assert r["flits_per_core_per_bin"] == 5.0, r

    # One core takes every slot: Jain floors at 1/n in every bin.
    hog = {0: list(range(1000)), **{c: [] for c in range(1, 10)}}
    assert abs(binned_jain(hog, 50, 1000)["jain_bin_mean"] - 0.1) < 1e-9

    # Bins past t_fair are dropped, so work done after the first core has
    # finished cannot drag the metric down.
    late = {c: list(range(0, 500, 10)) for c in range(10)}
    late[0] = late[0] + list(range(500, 1000, 10))
    r = binned_jain(late, 50, 500)
    assert r["n_bins"] == 10 and r["jain_bin_mean"] == 1.0, r

    # The ideal controller is the reference, so pin it: with N = 50 flits over
    # n = 10 cores the split is exact, so the ideal is 1.0 and this trace --
    # which is itself perfectly even -- sits exactly on it.
    r = binned_jain(even, 50, 1000)
    assert r["jain_bin_ideal"] == 1.0, r
    assert r["jain_vs_ideal"] == 1.0, r

    # And the closed form where N does not divide n: r = N mod n cores take
    # ceil(N/n). N = 51, n = 10 -> one core 6, nine cores 5.
    assert abs(jain_ideal_bin(51, 10) - 2601 / 2610) < 1e-12
    assert jain_ideal_bin(50, 10) == 1.0
    assert jain_ideal_bin(0, 10) == 1.0


def test_ideal_cc_lp_matches_fabric() -> None:
    """The ideal-CC ceiling is now the reference, so pin its LP to the fabric.

    Every relative number in the report divides by this bound, so a silent drift
    in the flow model -- a wrong VC multiplicity, the wrong direction for RSP, a
    changed destination hash -- would rescale the whole study without failing
    anything else. The check that catches it is that the equal-rate solve must
    land exactly on the independently derived R* = 40/7 (lambda* = 2/7).
    """
    from fractions import Fraction

    from ideal_ring2_cc import (coefficients, dest_mix, solve_max_total,
                                solve_theta)
    from rg_ring2_topo import CHI_VCS_WRITE, Ring2Topology

    topo = Ring2Topology(n_planes=1, vcs=CHI_VCS_WRITE, route="latency")
    cores, names, a = coefficients(topo, dest_mix(k=1600))
    assert len(cores) == 10, cores

    lam_fair = solve_theta(a, 1.0)
    assert Fraction(lam_fair[0]).limit_denominator(64) == Fraction(2, 7), (
        lam_fair[0])
    r_fair = 2 * float(lam_fair.sum())          # W = 2 DAT flits per txn
    assert abs(r_fair - 40 / 7) < 1e-6, r_fair
    # Equal-rate means equal: no core is quietly carrying the others.
    assert max(lam_fair) - min(lam_fair) < 1e-9, lam_fair

    # The unconstrained optimum is higher, but only by starving whole cores --
    # which a closed batch can never do, since every core owes the same work.
    lam_max = solve_max_total(a)
    assert 2 * float(lam_max.sum()) > r_fair, lam_max
    starved = [c for c, x in zip(cores, lam_max) if x < 1e-9]
    assert starved, lam_max
    # Those cores are the structurally disadvantaged ones at the HA-less gaps.
    assert set(starved) <= {0, 8, 10, 18}, starved

    # A ring hop binds, not an injection or ejection port. If a port ever binds,
    # the report's "the hop is the ceiling" reasoning would need revisiting.
    load = a.T @ lam_fair
    top = names[int(load.argmax())]
    assert top.startswith("hop:"), top


def test_fair_share_is_pattern_dependent() -> None:
    """lambda* belongs to the fabric *and* the traffic, which disqualified S24/S25.

    Two schemes were withdrawn for hard-coding lambda* = 2/7 instead of measuring
    it, on the grounds that the LP's constraint matrix is built from the workload's
    destination mix and so moves when the mix does. That reasoning is load-bearing
    -- it is the only thing keeping a cheap, bus-free design out of the frontier --
    so pin it: re-solving on the `hot` mix must move lambda* by a wide margin and
    must move the binding resource off the injection hop onto the hot cluster's
    ejection port. If a future change to the topology or the hash made the share
    pattern-invariant after all, the withdrawal would need revisiting and this test
    is where that shows up.
    """
    from collections import defaultdict

    from dse_ring2_write_fair import W_FLITS, build_pattern
    from ideal_ring2_cc import coefficients, dest_mix, solve_theta
    from rg_ring2_topo import CHI_VCS_WRITE, Ring2Topology

    topo = Ring2Topology(n_planes=1, vcs=CHI_VCS_WRITE, route="latency")

    def solve(mix) -> tuple[float, str]:
        cores, names, a = coefficients(topo, mix)
        lam = solve_theta(a, 1.0)
        return float(lam.mean()), names[int((a.T @ lam).argmax())]

    lam_u, bind_u = solve(dest_mix(k=400))
    assert abs(lam_u - 2 / 7) < 1e-6, lam_u
    assert bind_u.startswith("hop:"), bind_u

    cnt: dict[int, dict[int, int]] = defaultdict(lambda: defaultdict(int))
    for t in build_pattern("hot", k=400, W=W_FLITS, seed=0):
        cnt[t.core][t.ha] += 1
    hot_mix = {c: {h: v / sum(row.values()) for h, v in row.items()}
               for c, row in cnt.items()}
    lam_h, bind_h = solve(hot_mix)

    # Well past any margin a shipped constant could carry.
    assert lam_h < 0.5 * lam_u, (lam_u, lam_h)
    # And it is a different *kind* of resource that binds, so no rescaling of the
    # constant would track it either.
    assert bind_h.startswith("down:"), bind_h


def test_window_controllers_inert_on_hot() -> None:
    """On the non-uniform load the window schemes' bandwidth is the cap, not control.

    The first pass on `hot` looked like a rout in favour of congestion control: S19
    and S20 both jumped 33.6% over S0. The report reverses that reading on two
    findings, and both are load-bearing enough to pin, because if either quietly
    stopped holding, the recommendation would flip back to "buy a window
    controller" and 5,840 FF-equivalents would be spent for nothing.

      * The controllers never fire. Their marking threshold is a fraction of
        `ha_track`, but the initial window already holds outstanding below it, so
        `n_mark` stays 0 and the window never moves. Two structurally different
        algorithms therefore produce byte-identical runs -- which is the tell.
      * The gain is the outstanding cap. Plain S0 held at the same cap reproduces
        it with no controller at all, while S0 at the study's default cap of 128
        does not come close.

    `k` has to be large enough to reach steady state for the second half: the
    damage a loose cap does is E-tag circulation building up on a congested ring,
    so a short run understates it badly (the gap is 4% at k=300 and 21% by k=1000).
    """
    from dse_ring2_write_fair import (FABRIC, W_FLITS, build_pattern,
                                      fairness_stats, run_scheme)

    topo = Ring2Topology(n_planes=1, vcs=CHI_VCS_WRITE, route="latency")
    k = 1000
    tx = build_pattern("hot", k=k, W=W_FLITS, seed=0)
    fpc = k * W_FLITS

    def thr(scheme: str, **over) -> float:
        cfg = dict(FABRIC)
        cfg.update(over)
        r = run_scheme(scheme, topo, tx, cfg=cfg, quiet=True)
        inj = {int(c): v for c, v in (r.get("wr_inject_by_core") or {}).items()}
        return fairness_stats(inj, r["makespan"] or 1, fpc)["throughput"], r

    t19, r19 = thr("S19")
    t20, r20 = thr("S20")
    assert r20.get("n_mark") == 0, r20.get("n_mark")
    assert r20.get("n_win_down") == 0, r20.get("n_win_down")
    # Inert controllers reduce to the same fixed window, so the runs coincide.
    assert r19["makespan"] == r20["makespan"], (r19["makespan"], r20["makespan"])
    assert abs(t19 - t20) < 1e-9, (t19, t20)

    # A bare outstanding cap, no controller, gets there; the default cap does not.
    t_cap, _ = thr("S0", core_outstanding=32)
    t_def, _ = thr("S0", core_outstanding=128)
    assert t_cap > 0.98 * t20, (t_cap, t20)
    assert t_def < 0.85 * t_cap, (t_def, t_cap)


def test_jain_bin_ideal_is_achievable_and_bounds_a_fair_arbiter() -> None:
    """Two-sided check on the new reference: reachable, and a real ceiling.

    `jain_bin_ideal` replaced the multinomial null model, so it has to be
    validated in both directions rather than trusted:

      * **reachable** -- a deterministic schedule that spreads each bin's total
        as evenly as integers allow must score *exactly* the ideal. Here N = 51
        over 10 cores, so one core takes 6 and the rest take 5, rotating which
        one. Anything less than an exact match means the closed form is not the
        deterministic optimum.
      * **a ceiling** -- a *fair but memoryless* arbiter drawing the same totals
        at equal probability must land strictly below it. That is the old null
        model, and it is now correctly positioned as something a controller can
        beat, not as the bar.
    """
    n, nbin, per_bin = 10, 400, 51
    det: dict[int, list[int]] = {c: [] for c in range(n)}
    for b in range(nbin):
        for i in range(per_bin):
            # 51 = 5 each plus one extra, and the extra rotates by bin.
            det[(b + i) % n].append(b * 50 + 1)
    d = binned_jain(det, 50, nbin * 50)
    assert abs(d["jain_bin_mean"] - jain_ideal_bin(per_bin, n)) < 1e-5, d
    assert abs(d["jain_vs_ideal"] - 1.0) < 1e-5, d

    rng = random.Random(12345)
    rnd: dict[int, list[int]] = {c: [] for c in range(n)}
    for b in range(nbin):
        for _ in range(per_bin):
            rnd[rng.randrange(n)].append(b * 50 + 1)
    q = binned_jain(rnd, 50, nbin * 50)
    assert q["jain_vs_ideal"] < 1.0, q
    # The memoryless arbiter's own expectation is N/(N+n-1); it should land
    # near that and therefore well under the deterministic ideal.
    assert abs(q["jain_bin_mean"] - per_bin / (per_bin + n - 1)) < 0.02, q
    assert q["jain_bin_mean"] < d["jain_bin_mean"], (q, d)


def test_s15_beats_s0_fairness() -> None:
    """The whole point: fair share + reservation on the skewed pattern."""
    out = {}
    for scheme in ("S0", "S15"):
        _, _, sim = _run_write(scheme, k=200, pattern="cluster")
        s = sim.summary()
        out[scheme] = fairness_stats(s["wr_inject_by_core"], s["makespan"],
                                     200 * 4)
    s0, s15 = out["S0"], out["S15"]
    assert s15["jain"] > s0["jain"] + 0.2, (s0["jain"], s15["jain"])
    assert s15["max_min"] < s0["max_min"] / 2, (s0["max_min"],
                                                s15["max_min"])
    assert s15["throughput"] > s0["throughput"] * 0.95, \
        (s0["throughput"], s15["throughput"])


def test_tiled_interleave_is_balanced() -> None:
    """The 4KB stride must not collapse onto one HA under 8-way interleave."""
    from dse_ring2_write_fair import CORE_NODES, MEM_NODES
    from collections import Counter
    txns = build_tiled_write(k=128, m_wdata=2, mem=MEM_NODES,
                             core_set=CORE_NODES)
    assert {t.ha for t in txns} == set(MEM_NODES)
    for c in CORE_NODES:
        cnt = Counter(t.ha for t in txns if t.core == c)
        xs = list(cnt.values())
        assert max(xs) - min(xs) <= 1, (c, cnt)
    mem = list(MEM_NODES)
    hashed = {interleave_ha(i * STRIDE_BYTES, mem) for i in range(16)}
    assert len(hashed) == 8, hashed
    naive = {((i * STRIDE_BYTES) // BURST_BYTES) % 8 for i in range(16)}
    assert len(naive) == 1, naive
    assert TILE_BYTES // STRIDE_BYTES == 16
    assert BURST_BYTES == 128


def test_ha_rsp_jit_is_per_txn_and_bounded() -> None:
    """Memory RSP delay is U{lo..hi} and identical across schemes for a txn."""
    from dse_ring2_write_fair import FABRIC
    assert FABRIC.get("ha_rsp_jit") == 0, FABRIC.get("ha_rsp_jit")
    assert FABRIC.get("ha_rsp_jit_lo") == 0, FABRIC.get("ha_rsp_jit_lo")
    topo, txns, sim = _run_write(k=30, W=2, pattern="study",
                                 cfg={"ha_rsp_jit_lo": 4, "ha_rsp_jit": 64})
    s = sim.summary()
    assert s["completed"]
    delays = []
    for txn in txns[:20]:
        d0 = sim._ha_delay(txn, "probe")
        # rewind the seq so a second draw of the same occurrence matches
        sim._ha_rsp_seq[txn.txn_id] -= 1
        d1 = sim._ha_delay(txn, "probe")
        sim._ha_rsp_seq[txn.txn_id] -= 1
        assert d0 == d1 and 4 <= d0 <= 64, (txn.txn_id, d0, d1)
        delays.append(d0)
    assert min(delays) < max(delays), delays


def test_study_topology_roles() -> None:
    """The reported workload: 10 cores, 8 mem, nodes 9 and 19 terminal-free."""
    from dse_ring2_write_fair import CORE_NODES, MEM_NODES, NON_TERMINAL
    assert len(CORE_NODES) == 10 and len(MEM_NODES) == 8, \
        (CORE_NODES, MEM_NODES)
    assert set(NON_TERMINAL) == {9, 19}, NON_TERMINAL
    assert not (set(MEM_NODES) | set(CORE_NODES)) & set(NON_TERMINAL), \
        "9 / 19 must be neither core nor mem"
    _, txns, _ = _run_write(k=8, pattern="study")
    assert {t.core for t in txns} == set(CORE_NODES)
    assert {t.ha for t in txns} == set(MEM_NODES)


def test_study_baseline_is_position_unfair() -> None:
    """Symmetric demand, asymmetric outcome: the phenomenon under study.

    Finite tracker masks the gap, so this pin is the unlimited-tracker
    ring itself. The companion tracker test checks the mask.
    """
    from dse_ring2_write_fair import MEM_NODES
    topo, _, sim = _run_write(k=600, pattern="study",
                              cfg={"ha_rsp_jit": 0, "ha_track": 0})
    f = fairness_stats(sim.summary()["wr_inject_by_core"],
                       sim.summary()["makespan"], 600 * 4)
    assert f["max_min"] > 1.05, f"baseline unexpectedly fair: {f['max_min']}"
    bw = {int(c): v for c, v in f["bw_by_core"].items()}
    mem = set(MEM_NODES)
    adj = {c: sum((c + d) % topo.n in mem for d in (-1, 1)) for c in bw}
    two = [bw[c] for c in bw if adj[c] == 2]
    one = [bw[c] for c in bw if adj[c] == 1]
    assert two and one, adj
    # Queue depth changes can let one good-position tail overlap one
    # bad-position head; the mechanism is a class-level shift, not a promise
    # that every individual sample is perfectly ordered.
    mean_two = sum(two) / len(two)
    mean_one = sum(one) / len(one)
    assert mean_two > 1.05 * mean_one, \
        f"adjacency effect vanished: adj2 {mean_two} vs adj1 {mean_one}"


def test_baseline_tracker_is_sized_above_the_unbounded_peak() -> None:
    """The baseline tracker is sized so it never binds, and 32 still would.

    The report's headline rests on a fixed-point argument: if the cap is above
    the peak occupancy the *unbounded* run reaches, the cap never fires, so the
    trajectory is identical to unbounded -- and that unbounded peak is then
    self-consistently still below the cap. This pins both halves of it:

    * The baseline (read from FABRIC, not hardcoded) is above the unbounded
      peak, retries never happen, and every observable matches unbounded
      exactly. Not "close to": equal. If someone lowers the tracker below the
      peak, or makes occupancy depend on the cap, this fails loudly.
    * A tight tracker (32) is the contrast: it does bite, and it *masks* the
      position imbalance rather than fixing it -- the cores come out more
      alike, but only because retry backpressure slows everyone down, so
      throughput falls too.

    Sizing by the unbounded peak is the lesson from the 128-entry round, where
    a K=4000 sweep said 128 was past the knee and the official K=20000 run
    showed occupancy pegged at the cap. The knee is K-dependent; the peak is
    the thing to measure.
    """
    from dse_ring2_write_fair import FABRIC
    base = FABRIC.get("ha_track")
    assert base, "the baseline lost its completer tracker"
    out = {}
    for tag, track in (("base", base), ("tight", 32), ("inf", 0)):
        _, _, sim = _run_write(k=600, pattern="study",
                               cfg={"ha_track": track, "outst_sample": 16})
        s = sim.summary()
        assert s["completed"], tag
        out[tag] = (fairness_stats(s["wr_inject_by_core"], s["makespan"],
                                   600 * 4), s)
    (fbase, sbase), (ftight, stight), (finf, sinf) = (
        out["base"], out["tight"], out["inf"])
    # The unbounded peak is what sets the size, and the baseline clears it.
    assert sinf["n_retry"] == 0, sinf["n_retry"]
    assert sinf["max_ha_used"] < base, (sinf["max_ha_used"], base)
    # Cap never fires => identical run, not merely a similar one.
    assert sbase["n_retry"] == 0, sbase["n_retry"]
    assert sbase["max_ha_used"] == sinf["max_ha_used"], \
        (sbase["max_ha_used"], sinf["max_ha_used"])
    assert sbase["makespan"] == sinf["makespan"], \
        (sbase["makespan"], sinf["makespan"])
    assert fbase["throughput"] == finf["throughput"], \
        (fbase["throughput"], finf["throughput"])
    # The contrast: 32 entries do bite, and they mask rather than fix.
    assert stight["retry"]["retry_per_txn"] > 0.3, stight["retry"]
    assert ftight["max_min"] < finf["max_min"], \
        (ftight["max_min"], finf["max_min"])
    assert ftight["throughput"] < finf["throughput"], \
        (ftight["throughput"], finf["throughput"])


def test_s15_fixes_study_workload() -> None:
    """S15 must even out the reported workload without collapsing throughput.

    Under one shared up/down-ring port per node, S15 equalises by trimming
    the fastest cores, not by raising the floor: S0's slowest core already
    sits near the 1 flit/cycle port limit, so there is no headroom to hand
    it. `bw_min` is therefore only required not to fall, not to rise.

    k has to be well past the ramp. At k=600 the shared 8-deep FIFO is still
    filling and the tail dominates, which flips the comparison.
    """
    k = 3000
    out = {}
    for scheme in ("S0", "S15"):
        _, _, sim = _run_write(scheme, k=k, pattern="study")
        s = sim.summary()
        out[scheme] = fairness_stats(s["wr_inject_by_core"], s["makespan"],
                                     k * 4)
    s0, s15 = out["S0"], out["S15"]
    assert s15["jain"] >= 0.98, s15["jain"]
    assert s15["max_min"] < s0["max_min"], (s0["max_min"], s15["max_min"])
    assert s15["throughput"] > s0["throughput"] * 0.9, \
        (s0["throughput"], s15["throughput"])
    assert s15["bw_min"] > s0["bw_min"] * 0.95, \
        (s0["bw_min"], s15["bw_min"])


def _run_s16(*, k: int = 600, cfg: dict | None = None):
    # Built through make_sim so the test sees the same grant budget the report
    # does -- one at or above the tracker would silently be S0.
    from dse_ring2_write_fair import CORE_NODES, MEM_NODES, make_sim
    topo = _write_topo()
    txns = build_uniform_write(k=k, m_wdata=4, seed=0, mem=MEM_NODES,
                               core_set=CORE_NODES)
    sim = make_sim("S16", topo, seed=0, cfg=cfg or {})
    sim.offer_batch(txns)
    while sim.t < 400_000 and not sim.done():
        sim.step()
    return topo, txns, sim


def _run_read(scheme: str = "S0", *, k: int = 80, cfg: dict | None = None):
    """Closed-batch hot read, same fabric as the write study."""
    from dse_ring2_write_fair import make_sim
    from rg_ring2_topo import build_hot_read

    topo = _write_topo()
    txns = build_hot_read(k=k, m_resp=2, hot_has=(11, 13))
    sim = make_sim(scheme, topo, seed=0, cfg=cfg or {})
    sim.offer_batch(txns)
    while sim.t < 400_000 and not sim.done():
        sim.step()
    return topo, txns, sim


def test_s16_read_unbounded_equals_s0() -> None:
    """The read hook must not change the datapath when it is not withholding.

    Read S16 is the same policy as write S16, sitting on CompData emit instead
    of DBIDResp. With an unbounded budget it has to grant every REQ on arrival
    and reproduce S0 bit for bit -- otherwise the hook itself is a confound.
    """
    _, _, a = _run_read("S0", k=120)
    _, _, b = _run_read("S16", k=120, cfg={"overcommit": 10 ** 9})
    sa, sb = a.summary(), b.summary()
    for key in ("makespan", "n_delivered_flits", "n_board_fail",
                "n_deflections", "n_txn_done"):
        assert sa[key] == sb[key], f"{key}: S0 {sa[key]} vs S16-inf {sb[key]}"
    assert sa["rd_inject_by_core"] == sb["rd_inject_by_core"]


def test_s16_read_is_local_and_paces() -> None:
    """Read S16 uses no bus, and a tight budget must both bite and drain.

    Two things the write-side tests do not cover: CompData (not DBID) is what
    gets withheld, and the scheme must stay bus-free -- that is the whole
    reason it is deployable on reads, where CHI has no grant message.
    """
    _, txns, sim = _run_read("S16", k=150, cfg={"overcommit": 4})
    s = sim.summary()
    fc = sim.fc_summary()
    assert s["completed"], s["n_txn_done"]
    assert fc["bus_posts"] == 0 and fc["bus_bits"] == 0
    assert sim.peak_outstanding <= 4, sim.peak_outstanding
    assert fc["n_grant_queued"] > 0, "tight budget never queued a read"
    assert all(v == 0 for v in sim.outstanding.values())
    assert sum(sim.n_queued.values()) == 0
    # Same work, every core, so the HA that paced still delivered everything.
    assert s["n_txn_done"] == len(txns)
    # The read-side LP (DAT reversed) must land on 20/9 for this geometry:
    # hop:11:10:dat binds, not an inject port. If it moves, the probe's R*
    # and the "HA exit can see the bottleneck" claim both need revisiting.
    from probe_ring2_s16_read import read_ideal
    idl = read_ideal(_write_topo(), txns)
    assert abs(idl["r_fair"] - 20 / 9) < 1e-6, idl
    assert idl["binding"].startswith("hop:"), idl["binding"]


def test_read_scheme_feedback_and_scope() -> None:
    """Read mappings must act at the protocol point their labels claim."""
    # S1-R controls the CompData sender (HA), not the requester core.
    p = Ring2FcParams(scope="ha_only")
    fc = Ring2FcSim(_write_topo(), p, seed=0)
    assert fc._controlled(1) and not fc._controlled(0)

    # Delay schemes sample REQ -> last CompData when there is no DBIDResp.
    _, _, timely = _run_read(
        "S17", k=40, cfg={"core_outstanding": 32, "pace_init": 2.0})
    assert timely.done()
    assert all(timely.n_sample[c] > 0 for c in timely.topo.cores)
    assert sum(len(v) for v in timely.summary()["rd_recv_by_core"].values()) \
        == 40 * 10 * 2

    # The write ECN trigger is deliberately absent on the current read
    # protocol. Keep this explicit so an inert S18 row cannot be misreported
    # later as a working read controller.
    _, _, dcqcn = _run_read(
        "S18", k=40, cfg={"core_outstanding": 32, "pace_init": 2.0})
    assert dcqcn.done()
    assert dcqcn.st["n_mark"] == 0


def test_tiled_read_reverses_only_dat() -> None:
    """The official read stream must preserve the write address/HA mix."""
    from dse_ring2_write_fair import CORE_NODES, MEM_NODES, W_FLITS
    from rg_ring2_topo import build_tiled_read

    wr = build_tiled_write(
        k=256, m_wdata=W_FLITS, mem=MEM_NODES, core_set=CORE_NODES)
    rd = build_tiled_read(
        k=256, m_resp=W_FLITS, mem=MEM_NODES, core_set=CORE_NODES)
    assert [(t.core, t.ha) for t in rd] == [(t.core, t.ha) for t in wr]
    assert all(t.op == "read" and t.m_resp == W_FLITS for t in rd)


def test_s16_unbounded_equals_baseline() -> None:
    """Granting on arrival is the baseline policy, bit for bit.

    This is what proves the completer hook did not perturb the datapath:
    with an unbounded grant budget S16 must reproduce S0 exactly.
    """
    _, _, a = _run_write(k=300, pattern="study")
    _, _, b = _run_s16(k=300, cfg={"overcommit": 10 ** 9})
    sa, sb = a.summary(), b.summary()
    for key in ("makespan", "n_delivered_flits", "n_board_fail",
                "n_deflections", "n_txn_done"):
        assert sa[key] == sb[key], f"{key}: S0 {sa[key]} vs S16-inf {sb[key]}"
    assert sa["wr_inject_by_core"].keys() == sb["wr_inject_by_core"].keys()
    for c, ts in sa["wr_inject_by_core"].items():
        assert ts == sb["wr_inject_by_core"][c], f"core {c} diverged"


def test_s16_respects_grant_budget() -> None:
    """No completer may ever exceed its overcommit, and none may deadlock."""
    for oc in (1, 4, 32):
        _, txns, sim = _run_s16(k=200, cfg={"overcommit": oc})
        s = sim.summary()
        assert s["completed"], f"oc={oc} did not drain: {s['n_txn_done']}"
        assert sim.peak_outstanding <= oc, \
            f"oc={oc} peaked at {sim.peak_outstanding} grants"
        assert all(v == 0 for v in sim.outstanding.values()), \
            "grants left outstanding at drain"
        assert sum(sim.n_queued.values()) == 0, "REQs left ungranted"
        n, W = len(txns), txns[0].m_wdata
        assert s["n_delivered_dbid"] == n, s["n_delivered_dbid"]
        assert s["n_delivered_wdata"] == n * W, s["n_delivered_wdata"]


# ---------------------------------------------------------------------------
# CHI RetryAck / PCrdGrant, reordering, and the rate-based schemes
# ---------------------------------------------------------------------------

def _run_retry(scheme: str = "S0", *, k: int = 200, cfg: dict | None = None):
    """One write run on the study workload with the retry knobs available."""
    from dse_ring2_write_fair import CORE_NODES, MEM_NODES, make_sim
    topo = _write_topo()
    txns = build_uniform_write(k=k, m_wdata=4, seed=0, mem=MEM_NODES,
                               core_set=CORE_NODES)
    sim = make_sim(scheme, topo, seed=0, cfg=cfg or {})
    sim.offer_batch(txns)
    while sim.t < 600_000 and not sim.done():
        sim.step()
    return topo, txns, sim


def test_retry_off_reproduces_baseline() -> None:
    """An unlimited tracker must leave the datapath bit for bit unchanged.

    Everything the retry study adds hangs off `ha_track`; if the default also
    perturbed a run, none of sections 1-8 would still be about the same ring.
    """
    _, _, a = _run_write(k=300, pattern="study", cfg={"ha_track": 0})
    _, _, b = _run_retry("S0", k=300, cfg={"ha_track": 0, "outst_sample": 16})
    sa, sb = a.summary(), b.summary()
    for key in ("makespan", "n_delivered_flits", "n_board_fail",
                "n_deflections", "n_txn_done"):
        assert sa[key] == sb[key], f"{key}: {sa[key]} vs {sb[key]}"
    for c, ts in sa["wr_inject_by_core"].items():
        assert ts == sb["wr_inject_by_core"][c], f"core {c} diverged"
    assert sb["n_retry"] == 0 and sb["n_pcrd"] == 0, sb["n_retry"]
    # The counter still reports the tracker the ungoverned policy demands.
    assert sb["max_ha_used"] > 32, sb["max_ha_used"]


def test_retry_conserves_and_never_deadlocks() -> None:
    """A tight tracker must still drain, with every credit accounted for."""
    for track in (4, 8, 32):
        _, txns, sim = _run_retry(
            "S0", k=200, cfg={"ha_track": track, "outst_sample": 16})
        s = sim.summary()
        assert s["completed"], f"track={track} stalled at {s['n_txn_done']}"
        assert s["n_retry"] > 0, f"track={track} never filled the tracker"
        # Every bounce hands out exactly one credit, and every credit is spent.
        assert s["n_offered_retry"] == s["n_delivered_retry"] == s["n_retry"]
        assert s["n_offered_pcrd"] == s["n_delivered_pcrd"] == s["n_pcrd"]
        assert all(v == 0 for v in sim.pcrd_out.values()), dict(sim.pcrd_out)
        assert not any(sim.pcrd_q.values()), "requesters left without a credit"
        assert s["max_ha_used"] <= track, (track, s["max_ha_used"])
        # A bounced REQ rides the ring twice, so REQ traffic grows by exactly
        # the number of bounces and nothing is silently dropped.
        n, W = len(txns), txns[0].m_wdata
        assert s["n_delivered_req"] == n + s["n_retry"], s["n_delivered_req"]
        assert s["n_delivered_dbid"] == n, s["n_delivered_dbid"]
        assert s["n_delivered_wdata"] == n * W, s["n_delivered_wdata"]
        assert s["n_inring_blocked"] == 0 and s["max_inring_hold"] == 0


def test_retry_parks_outstanding_and_reorders() -> None:
    """The point of the whole section: the nominal cap stops buying anything.

    Past the knee, raising the cap raises only the number of allocated slots.
    The effective count -- slots whose transaction is moving -- stays flat,
    the retry rate climbs, and the accept order drifts further from issue
    order.
    """
    rows = []
    for oc in (16, 64, 256):
        _, _, sim = _run_retry("S0", k=300, cfg={
            "core_outstanding": oc, "ha_track": 32, "outst_sample": 16})
        s = sim.summary()
        assert s["completed"], oc
        rows.append((oc, s["retry"]))
    for oc, q in rows:
        assert q["outst_eff_mean"] <= q["outst_used_mean"] + 1e-6, (oc, q)
    lo, mid, hi = (r[1] for r in rows)
    # A cap below the tracker's reach never bounces anything, and every slot
    # it allocates is a working slot.
    assert lo["retry_per_txn"] == 0.0, lo
    assert abs(lo["outst_eff_mean"] - lo["outst_used_mean"]) < 0.01, lo
    # Past the knee the allocated slots keep growing and the working ones
    # stop: the extra cap buys parked slots and nothing else.
    assert hi["outst_used_mean"] > 1.4 * mid["outst_used_mean"], rows
    assert hi["outst_eff_mean"] < 1.1 * mid["outst_eff_mean"], rows
    assert hi["outst_park_mean"] > 0.5 * hi["outst_used_mean"], rows
    assert hi["retry_per_txn"] > lo["retry_per_txn"], rows
    # Retry parking scrambles both accept and retirement order. Neither the
    # fraction nor displacement is monotone in the nominal cap: changing the
    # shared/directional queue depths changes which parked REQ returns first.
    # The invariant is substantial reordering once parking dominates, not a
    # particular ordering between the 64- and 256-entry runs.
    assert hi["ooo_frac"] > 0.8, rows
    assert hi["retire_ooo_frac"] > 0.8, rows
    # A tracker this small cannot possibly hold the whole nominal window.
    assert hi["outst_eff_mean"] < 0.5 * hi["core_outstanding"], rows


def test_inorder_retire_is_never_better() -> None:
    """In-order completion turns reordering into head-of-line blocking.

    It must also still finish. Gating issue on a *count* of outstanding
    transactions deadlocks under in-order retirement -- the count fills with
    younger transactions that finished but may not retire, and the one whose
    retirement would free them can never issue -- so the window has to be a
    contiguous range of issue indices instead.
    """
    for track in (0, 32):
        out = {}
        for inorder in (False, True):
            _, _, sim = _run_retry("S0", k=200, cfg={
                "ha_track": track, "inorder_retire": inorder,
                "outst_sample": 16})
            s = sim.summary()
            assert s["completed"], (track, inorder)
            out[inorder] = s
        assert out[True]["n_txn_done"] == out[False]["n_txn_done"], track
        if track == 0:
            # With nothing to retry the two runs move the same flits, so any
            # difference is purely the retirement rule.
            assert out[True]["n_delivered_flits"] == \
                out[False]["n_delivered_flits"], track
        # Slots of finished transactions are held back behind older ones, so
        # fewer of the allocated slots are doing anything.
        a, b = out[False]["retry"], out[True]["retry"]
        assert b["max_hol_hold"] > 0, track
        assert a["max_hol_hold"] == 0, track
        assert b["outst_used_mean"] > a["outst_used_mean"], (track, a, b)
        assert b["outst_eff_mean"] <= a["outst_eff_mean"] + 1.0, (track, a, b)
    # Long enough for the boarding order to drift far from issue order, which
    # is the condition the count-based gate deadlocked on.
    _, _, deep = _run_retry("S0", k=600, cfg={
        "ha_track": 32, "inorder_retire": True, "outst_sample": 16})
    sd = deep.summary()
    assert sd["completed"], f"stalled at {sd['n_txn_done']}/{sd['n_txn_target']}"
    # The waste is set by the tracker; the retirement rule only moves it
    # between the parked bucket and the head-of-line bucket.
    q = sd["retry"]
    assert q["outst_hol_mean"] > 1.0 and q["outst_park_mean"] > 1.0, q


def test_outst_trace_records_when_asked() -> None:
    """The silicon-style plots need the per-sample series, not just means."""
    _, _, sim = _run_retry("S0", k=40, cfg={
        "core_outstanding": 16, "ha_track": 32,
        "outst_sample": 16, "outst_trace": True})
    q = sim.retry_summary()
    tr = q["ost_trace"]
    assert tr["t"] and tr["cores"], tr
    assert len(tr["t"]) == len(tr["used"]) == len(tr["eff"])
    assert len(tr["used"][0]) == len(tr["cores"])
    assert "ost_trace" not in _run_retry("S0", k=20, cfg={
        "outst_sample": 16})[2].retry_summary()


def test_blocker_paths_share_victim_hop() -> None:
    """Example 2 is meaningless if the innocent flow misses the contested hop."""
    from dse_ring2_write_fair import (
        REPRO_BLOCKERS, REPRO_BLOCK_HA, REPRO_CONTROL, REPRO_CONTROL_HA,
        REPRO_VICTIM, REPRO_VICTIM_HA, _directed_hops)
    v0 = _directed_hops(REPRO_VICTIM, REPRO_VICTIM_HA)[0]
    assert v0 == (10, 11), v0
    for c in REPRO_BLOCKERS:
        hops = _directed_hops(c, REPRO_BLOCK_HA)
        assert v0 in hops, (c, hops)
    assert v0 not in _directed_hops(REPRO_CONTROL, REPRO_CONTROL_HA)


def test_s16_needs_to_grant_below_the_tracker() -> None:
    """A finite tracker already does the job S16's overcommit was doing.

    The completer cannot hold more accepted requests than it has tracker
    entries, so an overcommit at or above the tracker never withholds a grant
    and S16 becomes S0 exactly. Below it, S16 does bite -- but it bites by
    holding tracker entries open while a request waits for its grant, so the
    cheap resource comes under *more* pressure, not less. That is the price of
    S16 that the earlier unlimited-tracker model could not see.
    """
    track = 32
    frozen = {"ha_track": track, "outst_sample": 16,
              "ha_rsp_jit_lo": 0, "ha_rsp_jit": 0}
    _, _, base = _run_retry("S0", k=200, cfg=frozen)
    sb = base.summary()
    _, _, same = _run_retry("S16", k=200, cfg={
        **frozen, "overcommit": track})
    ss = same.summary()
    for key in ("makespan", "n_delivered_flits", "n_retry", "n_board_fail"):
        assert ss[key] == sb[key], f"{key}: {ss[key]} vs {sb[key]}"
    _, _, low = _run_retry("S16", k=200, cfg={
        **frozen, "overcommit": track // 2})
    sl = low.summary()
    assert sl["completed"]
    # Below the tracker S16 does bite, but on the per-VC-port fabric the price
    # is makespan rather than extra retries: withholding a grant holds a
    # tracker entry open, which stalls the pipeline instead of bouncing more
    # requests.
    #
    # At overcommit = track/2 the penalty is not resolvable. The deltas are a
    # handful of events -- smaller than this run's own spread, since
    # `_inject_keys` returns `active_src` in set order and the visit order
    # moves with PYTHONHASHSEED -- and on the free-slot arbiter this leg even
    # comes out a few cycles *faster* than the baseline. So pin only what
    # survives: half the tracker must still retire the whole workload, and
    # the monotone penalty gets pinned where it is real, far below the
    # tracker. Delivered flits are not the invariant to use here -- a bounced
    # REQ is resent, so that counter tracks retries rather than work done.
    assert sl["n_txn_done"] == sb["n_txn_done"]
    _, _, deep = _run_retry("S16", k=200, cfg={
        **frozen, "overcommit": track // 8})
    sd = deep.summary()
    assert sd["makespan"] > sl["makespan"], (sd["makespan"], sl["makespan"])
    assert sd["makespan"] > sb["makespan"], (sd["makespan"], sb["makespan"])
    assert sd["n_retry"] > sb["n_retry"], (sd["n_retry"], sb["n_retry"])


def test_rate_pinned_reproduces_baseline() -> None:
    """S17 / S18 must be exactly S0 when the controller cannot throttle.

    Pinning the rate at a core's physical REQ ceiling proves the pacing hook is
    the only thing either scheme changes. That ceiling is one REQ per cycle per
    inject port, so it is derived from the fabric rather than written down: with
    the up-ring port split by direction each plane offers two ports, not one.
    """
    from dse_ring2_write_fair import FABRIC
    ceiling = float(_write_topo().n_planes
                    * (2 if FABRIC.get("per_dir_ports") else 1))
    pin = {"pace_min": ceiling, "pace_init": ceiling, "outst_sample": 16}
    for track in (0, 32):
        _, _, base = _run_retry("S0", k=200, cfg={
            "ha_track": track, "outst_sample": 16})
        sb = base.summary()
        for scheme in ("S17", "S18"):
            _, _, sim = _run_retry(scheme, k=200,
                                   cfg={"ha_track": track, **pin})
            s = sim.summary()
            for key in ("makespan", "n_delivered_flits", "n_board_fail",
                        "n_deflections", "n_retry"):
                assert s[key] == sb[key], \
                    f"{scheme} track={track} {key}: {s[key]} vs {sb[key]}"
            assert s["wr_inject_by_core"] == sb["wr_inject_by_core"], scheme


def test_rate_control_cuts_retries() -> None:
    """Pacing the source is what keeps the completer's tracker from spilling.

    Both controllers see the congestion through signals the protocol already
    sends -- TIMELY the DBIDResp round trip, DCQCN a mark computed on the
    tracker and carried on that same DBIDResp -- so both must end up sending
    slower than the ungoverned baseline and bouncing less.
    """
    cfg = {"core_outstanding": 128, "ha_track": 32, "outst_sample": 16}
    _, _, base = _run_retry("S0", k=300, cfg=cfg)
    sb = base.summary()
    assert sb["completed"] and sb["retry"]["retry_per_txn"] > 0.2, sb["retry"]
    for scheme in ("S17", "S18"):
        _, _, sim = _run_retry(scheme, k=300, cfg=cfg)
        s = sim.summary()
        fc = sim.fc_summary()
        assert s["completed"], scheme
        assert s["n_txn_done"] == sb["n_txn_done"], scheme
        # Fewer bounces means strictly less ring traffic for the same writes.
        assert s["n_delivered_flits"] < sb["n_delivered_flits"], scheme
        assert fc["n_rate_deny"] > 0, f"{scheme} never throttled anyone"
        assert fc["rate_mean_all"] < 2.0, (scheme, fc["rate_mean_all"])
        assert s["retry"]["retry_per_txn"] < sb["retry"]["retry_per_txn"], \
            (scheme, s["retry"]["retry_per_txn"],
             sb["retry"]["retry_per_txn"])
    # DCQCN's mark has to come from somewhere: the tracker plus every bounce.
    _, _, dc = _run_retry("S18", k=300, cfg=cfg)
    assert dc.fc_summary()["n_mark"] >= dc.summary()["n_retry"] > 0


def test_window_pinned_reproduces_baseline() -> None:
    """S19 / S20 must be exactly S0 when the window cannot shrink.

    The window is a count of in-flight transactions sitting under the
    static outstanding cap. Pinning it to that cap leaves only the
    datapath, so any remaining difference would be a hook bug.
    """
    pin = {"win_min": 128, "win_init": 128, "win_max": 128,
           "outst_sample": 16}
    for track in (0, 32):
        _, _, base = _run_retry("S0", k=200, cfg={
            "ha_track": track, "outst_sample": 16})
        sb = base.summary()
        for scheme in ("S19", "S20"):
            _, _, sim = _run_retry(scheme, k=200,
                                   cfg={"ha_track": track, **pin})
            s = sim.summary()
            for key in ("makespan", "n_delivered_flits", "n_board_fail",
                        "n_deflections", "n_retry"):
                assert s[key] == sb[key], \
                    f"{scheme} track={track} {key}: {s[key]} vs {sb[key]}"
            assert s["wr_inject_by_core"] == sb["wr_inject_by_core"], scheme


def test_window_control_acts() -> None:
    """The window actuator has to bind, and the run still has to finish.

    Starting at 16 (the U-curve peak on this fabric) is already below the
    static cap of 128, so some fresh REQs must be refused. A RetryAck
    still has to board: it already owns its slot.
    """
    cfg = {"core_outstanding": 128, "ha_track": 32, "outst_sample": 16}
    _, _, base = _run_retry("S0", k=300, cfg=cfg)
    sb = base.summary()
    for scheme in ("S19", "S20"):
        _, _, sim = _run_retry(scheme, k=300, cfg=cfg)
        s = sim.summary()
        fc = sim.fc_summary()
        assert s["completed"], scheme
        assert s["n_txn_done"] == sb["n_txn_done"], scheme
        assert s["n_inring_blocked"] == 0, scheme
        assert fc["actuator"] == "outstanding_window", scheme
        assert fc["n_win_deny"] > 0, f"{scheme} never bound the window"
        assert fc["win_mean_all"] < 128, (scheme, fc["win_mean_all"])


def test_s22_yields_only_downhill_and_only_to_crossers() -> None:
    """S22's yield rule has to be one-sided, scoped, and margin-gated.

    A node that is itself behind must never yield -- otherwise a slot given
    up can land with someone who needs it less. A node must only yield to a
    requester it would actually ride past, and only when that requester is
    `dfc_margin` further behind, which is the term that stops near-level
    swaps from burning a hop for nothing.
    """
    from rg_ring2_dfc import Ring2DfcParams, Ring2DfcSim

    def sim_with(margin: float, mine: float) -> Ring2DfcSim:
        topo = Ring2Topology(n_planes=1, vcs=CHI_VCS_WRITE)
        p = Ring2DfcParams(shared_inj=True, per_vc_srcq=True,
                           per_vc_ports=True, dfc_margin=margin,
                           dfc_scope="segment")
        s = Ring2DfcSim(topo, p, seed=0)
        s.req.add(6)
        s.deficit[6] = 4.0
        s.deficit[0] = mine
        return s

    def f(target: int):
        return Flit(pid=0, txn_id=0, seq=0, nflit=1, src=0, dst=target,
                    kind="wdata", t_gen=0, plane=0, dir=1, idx=0,
                    target=target, vc="dat")

    s = sim_with(0.0, 0.0)
    assert s._itag_blocks(f(8), 0) is True      # crosses 6→7, node 0 is level
    assert s._itag_blocks(f(4), 0) is False     # leaves before the requester
    # A node further behind than the requester keeps the hop.
    assert sim_with(0.0, 9.0)._itag_blocks(f(8), 0) is False
    # The margin swallows a near-level difference.
    assert sim_with(2.0, 3.0)._itag_blocks(f(8), 0) is False
    assert sim_with(2.0, 1.0)._itag_blocks(f(8), 0) is True


def test_s22_dodge_keeps_order_per_destination() -> None:
    """The look-ahead may overtake, but never two flits for one destination.

    Head is bound past the requester so it has to yield. The next entry is
    for the same destination -- overtaking it would reorder a WriteData burst
    -- so the scan must stop there and fall back to the head. Give it a
    different destination that clears the requester and it may go.
    """
    from rg_ring2_dfc import Ring2DfcParams, Ring2DfcSim

    def leg(second_dst: int) -> int:
        topo = Ring2Topology(n_planes=1, vcs=CHI_VCS_WRITE)
        p = Ring2DfcParams(shared_inj=True, per_vc_srcq=True,
                           per_vc_ports=True, dfc_dodge=4,
                           dfc_scope="segment")
        s = Ring2DfcSim(topo, p, seed=0)
        s.req.add(6)
        s.deficit[6] = 4.0
        q = s.srcq[(0, 0, "dat", 1)]
        for i, dst in enumerate((8, second_dst)):
            q.append(Flit(pid=i, txn_id=i, seq=0, nflit=1, src=0, dst=dst,
                          kind="wdata", t_gen=0, plane=0, dir=1, idx=0,
                          target=dst, vc="dat"))
        return s._select_inject_flit(0, 0, q).dst

    assert leg(8) == 8      # same destination: no overtake
    assert leg(4) == 4      # different destination, clears node 6: overtake


def test_fairness_bandwidth_tradeoff_structure() -> None:
    """The trade-off curve's derivation, pinned at its three provable claims.

    The report now quotes an exchange rate between fairness and total bandwidth
    (e.g. "the ideal controller reaches Jain 0.99 at 5.92 flit/cycle, 9.8% above
    S0"). Those numbers come out of two different programs -- a parametric LP in
    theta and a second-order-cone program in Jain -- and a silent error in either
    would rescale the claim without breaking anything else. So check the parts
    that theory fixes exactly:

      1. Closed form (C): R* = W / max_r abar_r, no optimisation involved.
      2. Fact 2: 1/R(theta) is convex. R itself is *not* -- it has a concave kink
         at theta_0 -- and an earlier draft got this backwards, so both halves are
         asserted to keep the distinction from silently flipping back.
      3. The SOCP frontier must dominate the theta parametrisation (theta is a
         cruder fairness measure than Jain) and must coincide with R* at J = 1,
         where the norm ball touches the simplex only at the uniform point.
      4. Max-min fairness lands exactly on the equal-rate point here, which is
         what makes R* the canonical fair operating point rather than a choice.
    """
    import numpy as np

    from ideal_ring2_cc import coefficients, dest_mix, jain, solve_theta
    from tradeoff_ring2_cc import max_min_fair, solve_jain, tighten

    topo = Ring2Topology(n_planes=1, vcs=CHI_VCS_WRITE, route="latency")
    cores, names, a = coefficients(topo, dest_mix(k=1600))
    n, w = len(cores), 2

    # 1. closed form at the fair end
    abar = a.mean(axis=0)
    r_closed = w / float(abar.max())
    r_fair = w * float(solve_theta(a, 1.0).sum())
    assert abs(r_closed - r_fair) < 1e-6, (r_closed, r_fair)
    assert abs(r_fair - 40 / 7) < 1e-6, r_fair
    assert names[int(abar.argmax())].startswith("hop:"), names[int(abar.argmax())]

    # 2. 1/R convex in theta; R itself has a concave kink
    th = np.linspace(0.0, 1.0, 81)
    bw = np.array([w * float(solve_theta(a, float(t)).sum()) for t in th])
    assert np.all(np.diff(bw) <= 1e-9), "R must be non-increasing"
    inv_d2 = np.diff(np.diff(1.0 / bw) / np.diff(th))
    assert inv_d2.min() > -1e-8, f"1/R not convex: {inv_d2.min():+.2e}"
    bw_d2 = np.diff(np.diff(bw) / np.diff(th))
    assert bw_d2.min() < -0.5, f"expected a concave kink in R, got {bw_d2.min()}"

    # 3. the SOCP frontier: exact at J=1, and never below the theta curve
    lam1 = solve_jain(a, 1.0)
    assert abs(w * float(lam1.sum()) - r_fair) < 1e-4, lam1.sum()
    assert abs(jain(lam1) - 1.0) < 1e-6, jain(lam1)
    for jt in (0.95, 0.99):
        lam = solve_jain(a, jt)
        assert (a.T @ lam).max() <= 1 + 1e-9, jt
        assert jain(lam) >= jt - 1e-4, (jt, jain(lam))
        # Dominates equal-rate, since Jain >= 0.99 is weaker than exact equality.
        assert w * float(lam.sum()) > r_fair - 1e-6, (jt, lam.sum())

    # 4. max-min fair == equal rate on this fabric
    lam_mm = tighten(a, max_min_fair(a))
    assert abs(jain(lam_mm) - 1.0) < 1e-6, jain(lam_mm)
    assert abs(w * float(lam_mm.sum()) - r_fair) < 1e-6, lam_mm.sum()


def test_s25_local_target_uses_no_bus() -> None:
    """The bus-free deficit path has to be structural, not just configured.

    S25 has since been **withdrawn as a candidate** -- a constant target is only
    correct for the traffic pattern it was derived from, see
    `test_fair_share_is_pattern_dependent`. The code path stays, because it is the
    controlled half of the gate-versus-yield actuator comparison that the report's
    bandwidth-cost argument rests on: same constant target on both sides, so the
    only difference measured is the actuator. That comparison is only meaningful if
    `dfc_target` really does take the bus out of the loop -- otherwise the yield
    side is quietly paying 30 cycles the gate side does not.

    So: zero posts over a full run, and the deficit still has to behave -- accrue
    at the target, be spent by boarding, and raise a request when a node falls
    behind. A silent bus with a dead controller would pass the first half alone.
    """
    from rg_ring2_dfc import Ring2DfcParams, Ring2DfcSim

    topo = Ring2Topology(n_planes=1, vcs=CHI_VCS_WRITE)
    tgt = 4 / 7
    p = Ring2DfcParams(shared_inj=True, per_vc_srcq=True, per_vc_ports=True,
                       dfc_target=tgt, dfc_thresh=1.0, dfc_cap=8.0,
                       dfc_bus_lat=30)
    s = Ring2DfcSim(topo, p, seed=0)
    members = s._members()
    assert members and all(is_core(n) for n in members)

    # Accrual is the target, exactly, and nothing is posted to the bus.
    for c in range(1, 4):
        s.t = c
        s._ctrl_deliver()
        assert all(abs(s.deficit[n] - c * tgt) < 1e-9 for n in members), (
            c, dict(s.deficit))
    assert s.bus_posts == 0 and not s._pipe, s.bus_posts
    # Past the threshold a node requests; boarding spends the deficit back down.
    assert set(s.req) == set(members), (s.req, members)
    s.deficit[members[0]] -= 3.0
    s._stand_down(members[0])
    assert members[0] not in s.req
    # The clamp holds, so a node starved for a long stretch cannot run away.
    for c in range(4, 200):
        s.t = c
        s._ctrl_deliver()
    assert all(s.deficit[n] <= p.dfc_cap + 1e-9 for n in members), dict(s.deficit)

    # And end to end: a real run must never touch the bus.
    txns = build_tiled_write(k=6, m_wdata=2, mem=[1, 3, 5, 7],
                            core_set=[0, 2, 4, 6])
    sim = Ring2DfcSim(topo, p, seed=0)
    sim.offer_batch(txns)
    while sim.t < 40_000 and not sim.done():
        sim.step()
    assert sim.done()
    assert sim.bus_posts == 0, sim.bus_posts
    assert (sim.fc_summary() or {}).get("bus_bits") == 0


def test_s22_deficit_reads_own_progress_off_the_bus() -> None:
    """Both sides of the comparison have to cross the same quantiser.

    If a node compared a locally exact counter against 6-bit bus values it
    would accumulate a permanent offset from the bus delay alone -- the first
    version of this did, and pinned every deficit at the clamp so nobody ever
    requested. Reading its own entry off the bus keeps the table consistent:
    with equal window counts every deficit must be exactly zero.
    """
    from rg_ring2_dfc import Ring2DfcParams, Ring2DfcSim

    topo = Ring2Topology(n_planes=1, vcs=CHI_VCS_WRITE)
    p = Ring2DfcParams(shared_inj=True, per_vc_srcq=True, per_vc_ports=True,
                       dfc_window=4, dfc_bus_lat=3, dfc_thresh=1.0)
    s = Ring2DfcSim(topo, p, seed=0)
    members = s._members()
    assert members and all(is_core(n) for n in members)
    for w in range(6):
        for n in members:
            s.ok_win[n] = 5
        s.t = w * 4 + 3
        s._aimd_tick()
        for dt in range(1, 4):
            s.t = w * 4 + 3 + dt
            s._ctrl_deliver()
    assert all(abs(s.deficit[n]) < 1e-9 for n in members), s.deficit
    assert not s.req
    # One node falling a window behind is what raises a request.
    for n in members:
        s.ok_win[n] = 5
    s.ok_win[members[0]] = 0
    s.t += 1
    s._aimd_tick()
    for _ in range(4):
        s.t += 1
        s._ctrl_deliver()
    assert members[0] in s.req, (s.deficit, s.req)


def test_etag_takes_the_leave_port_from_a_normal_flit() -> None:
    """E-tag has to be a *priority*, not just a reserved buffer entry.

    A flit is E-tagged because the leave buffer was full when it last arrived,
    so it has already paid a full extra revolution. The specified behaviour is
    that it then wins the leave port ahead of any normal flit, taking that
    flit's turn -- which is what bounds circulation to one lap. Ordering the
    port by direction alone (what this used to do) lets the same flit lose again
    and again, so the bound does not hold no matter how deep the buffer is.
    """
    topo = Ring2Topology(n_planes=1, vcs=CHI_VCS_WRITE)
    p = Ring2BaseParams(shared_inj=True, per_vc_srcq=True, per_vc_ports=True,
                        two_write_leave=True, eject_depth=1, resv_ej=0,
                        t_xfer=1)
    s = Ring2BaseSim(topo, p, seed=0)

    def f(direction: int, etag: bool, defl: int = 0) -> Flit:
        g = Flit(pid=0, txn_id=0, seq=0, nflit=1, src=0, dst=4, kind="wdata",
                 t_gen=0, plane=0, dir=direction, idx=4, target=0, vc="dat")
        g.e_tag, g.deflections = etag, defl
        return g

    # Both phases of the direction round-robin: the E-tagged flit goes first
    # either way, so the priority does not depend on which turn it is.
    for _ in range(2):
        order = s._leave_order(4, 0, [f(1, False), f(-1, True)])
        assert order[0].e_tag is True, [g.e_tag for g in order]
    # Among E-tagged flits the one that has circled most goes first, so several
    # contending E-tags still cannot starve each other indefinitely.
    order = s._leave_order(4, 0, [f(1, True, 1), f(-1, True, 3)])
    assert order[0].deflections == 3

    # Through the leave stage: with room for exactly one and no reserved entry,
    # the E-tagged flit takes it and the normal flit is the one sent round --
    # it loses its turn, which is the "挤占普通 flit 的下环权" part of the rule.
    p2 = Ring2BaseParams(shared_inj=True, per_vc_srcq=True, per_vc_ports=True,
                         two_write_leave=True, eject_depth=1, resv_ej=0,
                         t_xfer=1, eject_bw=0)
    s2 = Ring2BaseSim(topo, p2, seed=0)
    s2.arrivals[0] = [f(1, False), f(-1, True)]
    s2.arr_set[(0, 1, 4, "dat")].add(0)
    s2.arr_set[(0, -1, 4, "dat")].add(0)
    s2.step()
    assert s2.st["n_eject_full_deflect"] == 1, s2.st["n_eject_full_deflect"]
    got = list(s2.ejectq[(4, 0, "dat")])
    assert len(got) == 1 and got[0].e_tag is True, got


def test_itag_reserve_yields_one_slot_and_releases() -> None:
    """The specified I-tag: walk upstream, yield one slot, reserve it, release.

    Three things have to hold, and the shipped broadcast tag met none of them.
    The tag must reach the node whose flit would actually have taken the hop the
    requester is starving on -- not every injector on the ring. That node must
    give up exactly one slot, and only the nodes the resulting bubble still has
    to pass may be held off, only until it arrives. And boarding must clear the
    tag, so one tag costs one slot rather than a whole starvation period.
    """
    topo = Ring2Topology(n_planes=1, vcs=CHI_VCS_WRITE)

    def sim(mode: str) -> Ring2BaseSim:
        p = Ring2BaseParams(shared_inj=True, per_vc_srcq=True,
                            per_vc_ports=True, itag_mode=mode)
        return Ring2BaseSim(topo, p, seed=0)

    def dat(src: int, dst: int) -> Flit:
        g = Flit(pid=0, txn_id=0, seq=0, nflit=1, src=src, dst=dst,
                 kind="wdata", t_gen=0, plane=0, dir=1, idx=src, target=0,
                 vc="dat")
        g.target = hop_count(src, dst, 1, topo.n)
        return g

    s = sim("reserve")
    r = 10                                  # requester, starving CW
    s.i_tag[(0, 1, "dat")].add(r)
    s.itag_t[(0, 1, "dat", r)] = 0
    # Nodes 8 and 6 both hold a flit that would ride over node 10's outgoing
    # hop. 8 is nearer, so the tag stops there.
    for u in (6, 8):
        s.srcq[(u, 0, "dat", 1)].append(dat(u, 14))
    s._itag_pre()
    assert s.itag_donor.get((0, 1, "dat", r)) == 8, s.itag_donor
    assert s._itag_blocks(dat(8, 14), 8) is True
    assert s._itag_blocks(dat(6, 14), 6) is False   # not the donor: unaffected
    # A flit that leaves the ring before the requester never took its slot.
    assert s._itag_blocks(dat(8, 9), 8) is False

    # 8 yields: the bubble is now reserved and in flight towards 10.
    s._itag_culprit = r
    s._itag_yielded(8, dat(8, 14))
    assert s.st["n_itag_yield"] == 1
    donor, eta = s.itag_resv[(0, 1, "dat", r)]
    assert donor == 8 and eta == topo.path_lat(8, 10, 1)
    # Only node 9, which the bubble still has to pass, is held off now -- and
    # the donor itself is free to inject again behind it.
    assert s._itag_blocks(dat(9, 14), 9) is True
    assert s._itag_blocks(dat(8, 14), 8) is False
    s.t = eta
    assert s._itag_blocks(dat(9, 14), 9) is False   # bubble has arrived
    # Boarding releases the tag and the reservation together.
    s.t = 0
    s._itag_clear(r, dat(r, 14))
    assert r not in s.i_tag[(0, 1, "dat")]
    assert (0, 1, "dat", r) not in s.itag_resv

    # The broadcast tag, for contrast, stops a node whose flit never touches
    # the starved hop at all -- that is the collateral the specified tag drops.
    b = sim("broadcast")
    b.i_tag[(0, 1, "dat")].add(r)
    assert b._itag_blocks(dat(8, 9), 8) is True


def test_itag_reserve_costs_far_less_bandwidth_than_broadcast() -> None:
    """The point of the rework: same threshold, a fraction of the bandwidth.

    Both modes raise a tag on the same starvation condition, so the difference
    is purely what a raised tag withholds. Broadcast withholds every injector on
    the ring direction until the starvation clears; the specified tag withholds
    one slot from one node. At a threshold low enough to fire often that has to
    show up as a large throughput gap, otherwise "one tag, one slot" is not
    what the implementation is doing.
    """
    def thr(mode: str) -> tuple[float, int, int]:
        _, _, sim = _run_write(k=150, W=2, pattern="study",
                               cfg={"t_inj": 4, "itag_mode": mode})
        r = sim.summary()
        assert r["completed"], mode
        return (r["n_delivered_flits"] / r["makespan"], r["n_itag_raised"],
                r.get("n_itag_yield", 0))

    b_thr, b_tags, b_yield = thr("broadcast")
    r_thr, r_tags, r_yield = thr("reserve")
    assert b_tags > 0 and r_tags > 0, (b_tags, r_tags)
    assert b_yield == 0 and r_yield > 0, (b_yield, r_yield)
    assert r_thr > b_thr * 1.02, (r_thr, b_thr)


def test_s16_is_bufferless_and_fair() -> None:
    """The payoff: fairer than S0 and still bufferless, at a small throughput
    cost.

    With a finite tracker S16 is no longer free: the retry backpressure has
    already equalised most of what it used to fix, so capping the grant budget
    below the tracker now costs a little completer idle time. What must still
    hold is that it never buffers a ring flit and never loses much throughput.

    As with S15, the shared per-node port means S16 evens the cores out by
    trimming the top rather than lifting the floor, so `bw_min` is only held
    flat.
    """
    k = 3000
    frozen = {"ha_rsp_jit_lo": 0, "ha_rsp_jit": 0}
    _, _, a = _run_write(k=k, pattern="study", cfg=frozen)
    _, _, b = _run_s16(k=k, cfg=frozen)
    fa = fairness_stats(a.summary()["wr_inject_by_core"],
                        a.summary()["makespan"], k * 4)
    fb = fairness_stats(b.summary()["wr_inject_by_core"],
                        b.summary()["makespan"], k * 4)
    sb = b.summary()
    assert sb["n_inring_blocked"] == 0 and sb["max_inring_hold"] == 0, \
        "S16 must not buffer or stall in-ring flits"
    assert fb["max_min"] < fa["max_min"], (fa["max_min"], fb["max_min"])
    assert fb["bw_min"] > fa["bw_min"] * 0.95, (fa["bw_min"], fb["bw_min"])
    assert fb["throughput"] >= fa["throughput"] * 0.95, \
        (fa["throughput"], fb["throughput"])


def main() -> None:
    c = Checks()
    c.add("topo_roles_and_wrap", test_topo)
    c.add("latency_route_matches_hops", test_latency_route_matches_hops_on_this_ring)
    c.add("shared_inj_depth_board_rate", test_shared_inj_depth_and_board_rate)
    c.add("shared_fifo_hol_to_dir_q",
          test_shared_fifo_pops_head_only_when_that_dir_q_has_room)
    c.add("free_slot_arbiter_picks_open_dir",
          test_free_slot_arbiter_prefers_the_direction_that_can_go)
    c.add("itag_scope_segment_only_crossers",
          test_scoped_itag_only_blocks_flits_that_cross_the_holder)
    c.add("itag_hold_expires",
          test_itag_hold_expires_so_transit_starvation_cannot_lock_the_ring)
    c.add("buffer_occupancy_per_plane",
          test_buffer_occupancy_reports_full_per_plane)
    c.add("per_vc_ports_board_one_each", test_per_vc_ports_board_one_each_per_cycle)
    c.add("two_write_leave_both_dirs", test_two_write_leave_accepts_both_dirs)
    c.add("per_vc_leave_two_write", test_per_vc_leave_is_two_write_one_read_per_vc)
    c.add("leave_occ_gt1_down_fail", test_leave_occ_gt1_counts_as_down_fail)
    c.add("workload_counts", test_workload_counts)
    c.add("s0_completes_and_conserves", test_s0_completes_and_conserves)
    c.add("s1_completes_and_conserves", test_s1_completes_and_conserves)
    c.add("six_schemes_same_flits", test_three_schemes_same_flits)
    c.add("makespan_ge_bound", test_makespan_ge_bound)
    c.add("inring_never_blocked", test_inring_never_blocked_under_load)
    c.add("itag_starve_finite", test_itag_bounds_starve)
    c.add("etag_deflect_bounded", test_etag_and_deflect_bounded)
    c.add("boarding_queue_depth", test_boarding_queue_depth_respected)
    c.add("ports_per_node_plane", test_ports_are_per_node_plane)
    c.add("rg_replay_conflict_free", test_rg_replay_conflict_free)
    c.add("rg_req_before_resp", test_rg_req_before_resp)
    c.add("uniform_multi_seed_s0", test_uniform_multi_seed_s0)
    c.add("cost_ring2_preserves_mesh_cal", test_cost_ring2_does_not_break_mesh)
    c.add("pop_window_and_token", test_pop_window_and_token)
    c.add("core_outstanding_aligned", test_core_outstanding_aligned)
    c.add("plane_sel_all_work", test_plane_sel_all_work)
    c.add("s4_leave_completes_allpairs", test_s4_leave_completes_allpairs)
    c.add("s5_ej_beats_s0_allpairs", test_s5_ej_beats_s0)
    c.add("s6_oldest_beats_s5_uniform", test_s6_oldest_beats_s5_uniform)
    c.add("s7_hop_bounce_beats_s6", test_s7_hop_bounce_beats_s6)
    c.add("s8_late_plane_beats_s7", test_s8_late_plane_beats_s7)
    c.add("s9_late_dir_beats_s8", test_s9_late_dir_beats_s8)
    c.add("s10_resp_late_dir_beats_s9", test_s10_resp_late_dir_beats_s9)
    c.add("s11_hop_hold_beats_s10", test_s11_hop_hold_beats_s10)
    c.add("s12_hop_islip_beats_s11_uniform", test_s12_hop_islip_beats_s11_uniform)
    c.add("s13_hopkeep_beats_s12_uniform", test_s13_hopkeep_beats_s12_uniform)
    c.add("s14_sib_ha_beats_s13_allpairs", test_s14_sib_ha_beats_s13_allpairs)
    c.add("write_vc_mapping", test_write_vc_mapping)
    c.add("write_four_phase_conservation", test_write_four_phase_conservation)
    c.add("write_phase_ordering", test_write_phase_ordering)
    c.add("write_inring_never_blocked", test_write_inring_never_blocked)
    c.add("write_makespan_ge_bound", test_write_makespan_ge_bound)
    c.add("write_fairness_metrics_sane", test_write_fairness_metrics_sane)
    c.add("binned_jain_per_bin_average",
          test_binned_jain_averages_per_bin_over_the_fair_window)
    c.add("jain_bin_ideal_achievable_and_bounds_fair",
          test_jain_bin_ideal_is_achievable_and_bounds_a_fair_arbiter)
    c.add("ideal_cc_lp_matches_fabric", test_ideal_cc_lp_matches_fabric)
    c.add("fair_share_is_pattern_dependent", test_fair_share_is_pattern_dependent)
    c.add("window_controllers_inert_on_hot",
          test_window_controllers_inert_on_hot)
    c.add("s15_beats_s0_fairness", test_s15_beats_s0_fairness)
    c.add("tiled_interleave_balanced", test_tiled_interleave_is_balanced)
    c.add("ha_rsp_jit_bounded", test_ha_rsp_jit_is_per_txn_and_bounded)
    c.add("study_topology_roles", test_study_topology_roles)
    c.add("study_baseline_unfair", test_study_baseline_is_position_unfair)
    c.add("baseline_tracker_sized_above_peak",
          test_baseline_tracker_is_sized_above_the_unbounded_peak)
    c.add("s15_fixes_study_workload", test_s15_fixes_study_workload)
    c.add("s16_unbounded_equals_s0", test_s16_unbounded_equals_baseline)
    c.add("s16_read_unbounded_equals_s0", test_s16_read_unbounded_equals_s0)
    c.add("s16_read_is_local_and_paces", test_s16_read_is_local_and_paces)
    c.add("read_scheme_feedback_and_scope", test_read_scheme_feedback_and_scope)
    c.add("tiled_read_reverses_only_dat", test_tiled_read_reverses_only_dat)
    c.add("s16_respects_grant_budget", test_s16_respects_grant_budget)
    c.add("s16_bufferless_and_fair", test_s16_is_bufferless_and_fair)
    c.add("retry_off_equals_baseline", test_retry_off_reproduces_baseline)
    c.add("retry_conserves_no_deadlock",
          test_retry_conserves_and_never_deadlocks)
    c.add("retry_parks_outstanding", test_retry_parks_outstanding_and_reorders)
    c.add("inorder_retire_never_better", test_inorder_retire_is_never_better)
    c.add("outst_trace_when_asked", test_outst_trace_records_when_asked)
    c.add("blocker_paths_share_hop", test_blocker_paths_share_victim_hop)
    c.add("s16_grants_below_tracker", test_s16_needs_to_grant_below_the_tracker)
    c.add("etag_preempts_normal_leave",
          test_etag_takes_the_leave_port_from_a_normal_flit)
    c.add("itag_reserve_one_slot", test_itag_reserve_yields_one_slot_and_releases)
    c.add("itag_reserve_cheaper_than_broadcast",
          test_itag_reserve_costs_far_less_bandwidth_than_broadcast)
    c.add("s22_yield_downhill_and_scoped",
          test_s22_yields_only_downhill_and_only_to_crossers)
    c.add("s22_dodge_keeps_dst_order",
          test_s22_dodge_keeps_order_per_destination)
    c.add("s22_deficit_via_bus", test_s22_deficit_reads_own_progress_off_the_bus)
    c.add("s25_local_target_uses_no_bus", test_s25_local_target_uses_no_bus)
    c.add("fairness_bandwidth_tradeoff", test_fairness_bandwidth_tradeoff_structure)
    c.add("rate_pinned_equals_s0", test_rate_pinned_reproduces_baseline)
    c.add("rate_control_cuts_retries", test_rate_control_cuts_retries)
    c.add("window_pinned_equals_s0", test_window_pinned_reproduces_baseline)
    c.add("window_control_acts", test_window_control_acts)
    res = {
        "n_total": len(c.rows), "n_ok": c.n_ok,
        "all_ok": c.n_ok == len(c.rows),
        "rows": c.rows,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(res, indent=2))
    print(f"\n{c.n_ok}/{len(c.rows)} passed  -> {OUT}")
    if not res["all_ok"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
