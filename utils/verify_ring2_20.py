#!/usr/bin/env python3
"""Executable assertions for the 20-node dual-plane ring study.

Each check is named; a failure prints the concrete quantity. Writes
results/verify_ring2_20.json.
"""

from __future__ import annotations

import json
import sys
import time
import traceback
from pathlib import Path
from typing import Any, Callable

_UTILS = Path(__file__).resolve().parent
if str(_UTILS) not in sys.path:
    sys.path.insert(0, str(_UTILS))

from rg_ring2_aimd import run_batch as run_aimd
from rg_ring2_base import Ring2BaseParams, Ring2BaseSim, run_batch as run_base
from rg_ring2_rg import RING2_ALGOS, RGConfig, replay_ok, run_batch as run_rg
from rg_ring2_rg import schedule
from rg_ring2_topo import (
    Ring2Topology, build_allpairs, build_uniform, hop_count, is_core, is_ha,
    paths_for_txns, shortest_dir,
)
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
    exp = _expected_flits(txns)
    for name, r in (("S0", s0), ("S1", s1), ("S2", s2)):
        assert r["completed"], name
        assert r["n_delivered_flits"] == exp, (name, r["n_delivered_flits"])
        assert r["n_txn_done"] == len(txns), name


def test_makespan_ge_bound() -> None:
    topo = Ring2Topology()
    txns = build_allpairs(m=1, m_resp=4)
    rp, sp = paths_for_txns(topo, txns, strategy="least_occupied")
    b = topo.analytic_bounds(rp, sp, m_req=1, m_resp=4)
    for name, r in (
            ("S0", run_base(topo, txns)),
            ("S1", run_aimd(topo, txns)),
            ("S2", run_rg(topo, txns, cfg=RGConfig(algo="greedy_ff")))):
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
    assert da["bits"] > db["bits"]
    assert dr["bits"] == 0


def test_plane_sel_all_work() -> None:
    topo = Ring2Topology()
    txns = build_allpairs(m=1, m_resp=2)
    for ps in ("static_hash", "rr_per_pkt", "least_occupied",
               "req_resp_split"):
        r = run_base(topo, txns, params=Ring2BaseParams(plane_sel=ps))
        assert r["completed"], ps


def main() -> None:
    c = Checks()
    c.add("topo_roles_and_wrap", test_topo)
    c.add("workload_counts", test_workload_counts)
    c.add("s0_completes_and_conserves", test_s0_completes_and_conserves)
    c.add("s1_completes_and_conserves", test_s1_completes_and_conserves)
    c.add("three_schemes_same_flits", test_three_schemes_same_flits)
    c.add("makespan_ge_bound", test_makespan_ge_bound)
    c.add("inring_never_blocked", test_inring_never_blocked_under_load)
    c.add("itag_starve_finite", test_itag_bounds_starve)
    c.add("etag_deflect_bounded", test_etag_and_deflect_bounded)
    c.add("rg_replay_conflict_free", test_rg_replay_conflict_free)
    c.add("rg_req_before_resp", test_rg_req_before_resp)
    c.add("uniform_multi_seed_s0", test_uniform_multi_seed_s0)
    c.add("cost_ring2_preserves_mesh_cal", test_cost_ring2_does_not_break_mesh)
    c.add("plane_sel_all_work", test_plane_sel_all_work)
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
