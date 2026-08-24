#!/usr/bin/env python3
"""Per-core write-bandwidth fairness on the 3D-stacked fabric.

Runs the whole study and writes one JSON blob for the report generator:

  * the capacity model and the four bounds, per routing policy;
  * the crossing-FIFO depth sweep, which is what decides whether the fabric
    livelocks at all;
  * the admission (outstanding) sweep, which locates the collapse knee;
  * S0 / S1 / S16 / S17 under both the literal "shortest path" reading and
    dimension-ordered routing;
  * root-cause tables: per-die and per-core bandwidth against V-ring
    insertion position, injection-edge load, die index and top-ring index;
  * a seed sweep, so no claim rests on one draw of the destination sequence.

Every scheme runs on the same datapath and the same workload; only the named
mechanism changes.
"""

from __future__ import annotations

import argparse
import json
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Sequence

from dse_ring2_write_fair import cov, fairness_stats, jain, pearson, spearman
from rg_stack_base import StackBaseParams, StackBaseSim, run_batch
from rg_stack_fc import (StackFairTurnSim, StackFcParams, StackFcSim,
                         StackGrantParams, StackGrantSim, StackTurnParams)
from rg_stack_topo import (GROUP_COLS, N_COLS, TOP_BRIDGES, V_LEN,
                           StackTopology, Txn, build_uniform_write)

M_REQ, M_RSP, M_WDATA = 1, 2, 4
CORE_OUTSTANDING_WR = 600

# The crossing FIFOs are the one place strict bufferlessness does not hold,
# so their depth is a hardware cost that has to be stated, not assumed. The
# sweep below shows depth 4 -- the value the plan guessed -- livelocks.
FABRIC = dict(turn_depth=64, d2d_depth=128,
              core_outstanding=CORE_OUTSTANDING_WR,
              inj_depth=8, eject_depth=4, eject_bw=1, per_vc_srcq=True)

ROUTE_LABEL = {
    "bound": "目的地绑定路由（硬件规定）",
    "lat": "自由最短路径（按时延，不可实现）",
    "hops": "自由最短路径（按跳数，不可实现）",
}


def _sim(name: str, *, route: str, **kw) -> tuple[type, Any]:
    """Scheme name -> (sim class, params). One datapath, one changed knob."""
    f = dict(FABRIC)
    f.update(kw)
    if name == "s0":
        return StackBaseSim, StackBaseParams(**f)
    if name == "s1":
        return StackFcSim, StackFcParams(**f)
    if name == "s16":
        return StackGrantSim, StackGrantParams(**f)
    if name == "s17":
        return StackFairTurnSim, StackTurnParams(**f)
    raise ValueError(name)


def run_scheme(topo: StackTopology, txns: Sequence[Txn], name: str, *,
               route: str, seed: int = 0, keep_trace: bool = False,
               stall_after: int = 6_000, **kw) -> dict[str, Any]:
    cls, params = _sim(name, route=route, **kw)
    t0 = time.time()
    r = run_batch(topo, txns, params=params, sim_cls=cls, seed=seed,
                  stall_after=stall_after)
    n_per_core = (len(txns) // max(1, len(topo.cores))) * M_WDATA
    r["fairness"] = fairness_stats(r["wr_inject_by_core"], r["makespan"],
                                   n_per_core)
    r["scheme"] = name
    r["route"] = route
    r["wall_s"] = round(time.time() - t0, 1)
    r["max_core_outstanding"] = r.get("max_core_outstanding", 0)
    r.pop("wr_inject_by_core", None)
    if not keep_trace and "fc" in r:
        r["fc"].pop("trace", None)
    return r


# ---------------------------------------------------------------------------
# structural / analytic tables
# ---------------------------------------------------------------------------

def arrival_vpos(topo: StackTopology, die: int, col: int) -> int:
    """Where die `die`'s traffic enters column `col`'s vertical ring.

    The HA-to-bridge binding decides this, not the router: the bridge bound to
    `col` lands on the near ring when `col` is one of the die's own columns and
    on the far ring otherwise, in which case the flit rides the horizontal ring
    to `col` and turns in at the far ring's attach point.
    """
    near, far = topo.die_hrings(die)
    h = near if col in topo.die_cols(die) else far
    return topo.nodes[topo.attach(h, col)].vpos


def v_ring_profile(topo: StackTopology, col: int = 0) -> dict[str, Any]:
    """Analytic per-edge load on one column's vertical half ring.

    Columns are no longer interchangeable: under the 2x4 grouping a column is
    reached at the near ring by the die that owns it and at the far ring by
    the die that has to cross for it, so a left-half and a right-half column
    see different arrival positions. `col` selects which one to profile.
    """
    nodes = [topo.nodes[topo._v_node(col, p)] for p in range(V_LEN)]
    ha = [p for p in range(V_LEN) if nodes[p].role == "ha"]
    arrive: dict[int, int] = {d: arrival_vpos(topo, d, col)
                              for d in range(topo.n_die)}

    def span(a: int, b: int) -> list[int]:
        out, c = [], a
        while c != b:
            out.append(c)
            c = (c + 1) % V_LEN
        return out

    dat: Counter = Counter()
    rsp: Counter = Counter()
    for die, a in arrive.items():
        for q in ha:
            for p in span(a, q):
                dat[p] += M_WDATA
            for p in span(q, a):
                rsp[p] += M_RSP
    rows = []
    for p in range(V_LEN):
        n = nodes[p]
        here = sorted(d for d, a in arrive.items() if a == p)
        rows.append({
            "vpos": p,
            "role": n.role,
            "label": (f"A(h{n.hring})" if n.role == "attach"
                      else f"HA r{n.row}"),
            "dies": here,
            "die": here[0] if here else None,
            "dat": dat[p], "rsp": rsp[p], "tot": dat[p] + rsp[p],
        })
    return {
        "col": col,
        "half": "left" if col < N_COLS // 2 else "right",
        "rows": rows,
        "arrive": {str(d): a for d, a in sorted(arrive.items())},
        "ha_vpos": ha,
        "inject_load": {str(d): {"vpos": a, "dat": dat[a],
                                 "tot": dat[a] + rsp[a]}
                        for d, a in sorted(arrive.items())},
    }


def vseat_load(topo: StackTopology, txns: Sequence[Txn]
               ) -> dict[str, Any]:
    """Measured DAT load on the vertical edge each core has to squeeze into.

    A core writes to all 8 columns, 4 of them near and 4 far, so its
    experience is the *average* over the eight entry edges rather than a
    single position. This is the variable the fairness result is tested
    against in `root_cause`.
    """
    load: Counter = Counter()
    occ: dict[int, int] = {}
    routes: dict[tuple[int, int], tuple[int, ...]] = {}
    for x in txns:
        pl = topo.pick_plane(x.core, x.ha, occupancy=occ)
        fwd = topo.route(x.core, x.ha, pl)
        routes[(x.core, x.ha)] = fwd
        for e in fwd:
            load[e] += M_WDATA
        for e in topo.route(x.ha, x.core, pl):
            load[e] += M_RSP
    # first vertical edge of each route = the seat the flit must win
    seat: dict[int, list[int]] = defaultdict(list)
    hhops: dict[int, list[int]] = defaultdict(list)
    for (core, _ha), path in routes.items():
        v = next((e for e in path if topo.fabric_of(e) == "v"), None)
        if v is not None:
            seat[core].append(load[v])
        hhops[core].append(sum(1 for e in path if topo.fabric_of(e) == "h"))
    return {
        "seat_mean": {c: round(sum(v) / len(v), 1)
                      for c, v in seat.items() if v},
        "seat_max": {c: max(v) for c, v in seat.items() if v},
        "h_hops_mean": {c: round(sum(v) / len(v), 3)
                        for c, v in hhops.items() if v},
    }


def routing_compare(k: int, seed: int) -> dict[str, Any]:
    """Bounds and load concentration under each routing policy."""
    out: dict[str, Any] = {}
    for mode in ("bound", "lat", "hops"):
        topo = StackTopology(route_mode=mode)
        txns = build_uniform_write(topo, k=k, seed=seed)
        b = topo.write_bounds(txns, m_req=M_REQ, m_rsp=M_RSP,
                              m_wdata=M_WDATA)
        load: Counter = Counter()
        fab: Counter = Counter()
        occ: dict[int, int] = {}
        hops = []
        for x in txns:
            pl = topo.pick_plane(x.core, x.ha, occupancy=occ)
            fwd = topo.route(x.core, x.ha, pl)
            rev = topo.route(x.ha, x.core, pl)
            hops.append(len(fwd))
            for vc, path, m in (("req", fwd, M_REQ), ("dat", fwd, M_WDATA),
                                ("rsp", rev, M_RSP)):
                for e in path:
                    load[(e, vc)] += m
                    fab[(topo.fabric_of(e), vc)] += m
        vd = [n for (e, vc), n in load.items()
              if vc == "dat" and topo.fabric_of(e) == "v"]
        mean_v = sum(vd) / max(1, len(vd))
        out[mode] = {
            "bounds": b,
            "max_txn_per_cycle": round(len(txns) / max(1, b["bound"]), 4),
            "mean_fwd_hops": round(sum(hops) / max(1, len(hops)), 2),
            "v_dat_max": max(vd) if vd else 0,
            "v_dat_mean": round(mean_v, 1),
            "v_concentration": round(max(vd) / mean_v, 3) if mean_v else 0.0,
            "dat_hops_per_txn": {kk[0]: round(v / len(txns), 2)
                                 for kk, v in sorted(fab.items())
                                 if kk[1] == "dat"},
            "capacity": topo.capacity(),
        }
    return out


def hot_edges(topo: StackTopology, txns: Sequence[Txn], n: int = 12
              ) -> list[dict[str, Any]]:
    load: Counter = Counter()
    occ: dict[int, int] = {}
    for x in txns:
        pl = topo.pick_plane(x.core, x.ha, occupancy=occ)
        fwd = topo.route(x.core, x.ha, pl)
        rev = topo.route(x.ha, x.core, pl)
        for vc, path, m in (("req", fwd, M_REQ), ("dat", fwd, M_WDATA),
                            ("rsp", rev, M_RSP)):
            for e in path:
                load[(e, vc)] += m

    def nm(nd) -> str:
        if nd.role == "ha":
            return f"HA(r{nd.row},c{nd.col})@v{nd.vpos}"
        if nd.role == "attach":
            return f"A(h{nd.hring},c{nd.col})@v{nd.vpos}"
        return f"{nd.role}(d{nd.die},i{nd.idx})"

    out = []
    for (e, vc), cnt in load.most_common(n):
        u, v = topo.edges[e]
        out.append({"flits": cnt, "vc": vc, "fabric": topo.fabric_of(e),
                    "src": nm(topo.nodes[u]), "dst": nm(topo.nodes[v])})
    return out


# ---------------------------------------------------------------------------
# root cause
# ---------------------------------------------------------------------------

def root_cause(topo: StackTopology, s0: dict[str, Any],
               seats: dict[str, Any]) -> dict[str, Any]:
    """Test per-core bandwidth against the structural variables that remain.

    The 2x4 grouping removes the old "first vs second of an adjacent pair"
    story: every die now reaches 4 columns near and 4 far, so a core's
    experience is an average over both. What is left to explain the spread is
    the row gap the die sits in, the column half it owns, the measured load on
    the vertical seats it competes for, and its position on the top ring.
    """
    bw = {int(c): v for c, v in s0["fairness"]["bw_by_core"].items()}
    cs = sorted(bw)
    fails = s0.get("board_fail_by_src", {})
    seat_mean = seats["seat_mean"]
    seat_max = seats["seat_max"]
    hh = seats["h_hops_mean"]

    def cause(c: int, key: str) -> int:
        return int(fails.get(f"{c}:dat", {}).get(key, 0))

    rows = []
    for c in cs:
        nd = topo.nodes[c]
        d = nd.die
        ok, busy = cause(c, "ok"), cause(c, "hop_busy")
        budget, outst = cause(c, "fc_budget"), cause(c, "outstanding")
        tries = ok + busy + cause(c, "itag") + budget + outst
        near, far = topo.die_hrings(d)
        rows.append({
            "core": c, "die": d, "idx": nd.idx,
            "gap": topo.die_gap(d), "half": topo.die_half(d),
            "near_h": near, "far_h": far,
            "near_vpos": arrival_vpos(topo, d, topo.die_cols(d)[0]),
            "far_vpos": arrival_vpos(
                topo, d, (topo.die_cols(d)[0] + N_COLS // 2) % N_COLS),
            "seat": seat_mean.get(c, 0), "seat_max": seat_max.get(c, 0),
            "h_hops": hh.get(c, 0.0),
            "bw": bw[c],
            "ok": ok, "hop_busy": busy, "outstanding": outst,
            "succ_rate": round(ok / tries, 4) if tries else 0.0,
        })
    b = [r["bw"] for r in rows]

    def sp(key: str) -> float:
        return round(spearman([r[key] for r in rows], b), 4)

    corr = {
        "die": sp("die"), "gap": sp("gap"), "half": sp("half"),
        "near_vpos": sp("near_vpos"), "far_vpos": sp("far_vpos"),
        "seat": sp("seat"), "seat_max": sp("seat_max"),
        "seat_pearson": round(pearson([r["seat"] for r in rows], b), 4),
        "top_idx": sp("idx"), "h_hops": sp("h_hops"),
    }
    by_die: dict[str, Any] = {}
    for d in range(topo.n_die):
        sub = [r for r in rows if r["die"] == d]
        if not sub:
            continue
        vals = [r["bw"] for r in sub]
        by_die[str(d)] = {
            "gap": sub[0]["gap"], "half": sub[0]["half"],
            "near_h": sub[0]["near_h"], "far_h": sub[0]["far_h"],
            "near_vpos": sub[0]["near_vpos"], "far_vpos": sub[0]["far_vpos"],
            "seat": round(sum(r["seat"] for r in sub) / len(sub), 1),
            "n": len(vals), "mean": round(sum(vals) / len(vals), 5),
            "min": round(min(vals), 5), "max": round(max(vals), 5),
        }
    by_idx: dict[str, Any] = {}
    for i in sorted({r["idx"] for r in rows}):
        vals = [r["bw"] for r in rows if r["idx"] == i]
        by_idx[str(i)] = {"n": len(vals),
                          "mean": round(sum(vals) / len(vals), 5)}
    by_gap: dict[str, Any] = {}
    for g in sorted({r["gap"] for r in rows}):
        vals = [r["bw"] for r in rows if r["gap"] == g]
        by_gap[str(g)] = {"n": len(vals),
                          "mean": round(sum(vals) / len(vals), 5),
                          "min": round(min(vals), 5),
                          "max": round(max(vals), 5)}
    return {"rows": rows, "corr": corr, "by_die": by_die,
            "by_idx": by_idx, "by_gap": by_gap}


# ---------------------------------------------------------------------------
# sweeps
# ---------------------------------------------------------------------------

def fifo_sweep(k: int, seed: int, depths: Sequence[int],
               **kw) -> list[dict]:
    out = []
    for route in ("bound",):
        topo = StackTopology(route_mode=route)
        txns = build_uniform_write(topo, k=k, seed=seed)
        for d in depths:
            r = run_scheme(topo, txns, "s0", route=route, seed=seed,
                           turn_depth=d, d2d_depth=2 * d, **kw)
            out.append({
                "route": route, "turn_depth": d, "d2d_depth": 2 * d,
                "completed": r["completed"], "makespan": r["makespan"],
                "n_txn_done": r["n_txn_done"],
                "n_deflections": r["n_deflections"],
                "turn_peak": r["fifo"]["turn_peak"],
                "jain": r["fairness"].get("jain", 0.0),
                "thr": round(r["n_txn_done"] / max(1, r["makespan"]), 4),
            })
    return out


def outstanding_sweep(k: int, seed: int, ocs: Sequence[int]) -> list[dict]:
    out = []
    for route in ("bound",):
        topo = StackTopology(route_mode=route)
        txns = build_uniform_write(topo, k=k, seed=seed)
        bound = topo.write_bounds(txns, m_req=M_REQ, m_rsp=M_RSP,
                                  m_wdata=M_WDATA)["bound"]
        for oc in ocs:
            r = run_scheme(topo, txns, "s0", route=route, seed=seed,
                           core_outstanding=oc)
            f = r["fairness"]
            out.append({
                "route": route, "outstanding": oc,
                "completed": r["completed"], "makespan": r["makespan"],
                "eff": round(bound / max(1, r["makespan"]), 4),
                "thr": round(r["n_txn_done"] / max(1, r["makespan"]), 4),
                "jain": f.get("jain", 0.0), "max_min": f.get("max_min", 0.0),
                "cov": f.get("cov", 0.0),
                "n_deflections": r["n_deflections"],
            })
    return out


def oc_sweep_s16(topo: StackTopology, txns: Sequence[Txn], route: str,
                 seed: int, ocs: Sequence[int], **kw) -> list[dict]:
    out = []
    for oc in ocs:
        r = run_scheme(topo, txns, "s16", route=route, seed=seed,
                       overcommit=oc, **kw)
        f, fc = r["fairness"], r.get("fc", {})
        out.append({
            "overcommit": oc, "completed": r["completed"],
            "makespan": r["makespan"], "jain": f.get("jain", 0.0),
            "max_min": f.get("max_min", 0.0), "cov": f.get("cov", 0.0),
            "peak_grants": fc.get("peak_grants", 0),
            "peak_buf_flits": fc.get("peak_buf_flits", 0),
            "grant_delay_mean": fc.get("grant_delay_mean", 0.0),
            "net_p99": r.get("net_p99"),
        })
    return out


def patience_sweep(topo: StackTopology, txns: Sequence[Txn], route: str,
                   seed: int, pats: Sequence[int], **kw) -> list[dict]:
    out = []
    for p in pats:
        r = run_scheme(topo, txns, "s17", route=route, seed=seed,
                       turn_patience=p, **kw)
        f, fc = r["fairness"], r.get("fc", {})
        out.append({
            "patience": p, "completed": r["completed"],
            "makespan": r["makespan"], "jain": f.get("jain", 0.0),
            "max_min": f.get("max_min", 0.0), "cov": f.get("cov", 0.0),
            "n_turn_yield": fc.get("n_turn_yield", 0),
            "n_turn_win": fc.get("n_turn_win", 0),
            "latch_flits": fc.get("latch_flits", 0),
            "net_p99": r.get("net_p99"),
        })
    return out


def seed_sweep(k: int, seeds: Sequence[int], schemes: Sequence[str],
               route: str, **kw) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name in schemes:
        rows = []
        for s in seeds:
            topo = StackTopology(route_mode=route)
            txns = build_uniform_write(topo, k=k, seed=s)
            bound = topo.write_bounds(txns, m_req=M_REQ, m_rsp=M_RSP,
                                      m_wdata=M_WDATA)["bound"]
            r = run_scheme(topo, txns, name, route=route, seed=s,
                           **kw)
            f = r["fairness"]
            rows.append({
                "seed": s, "completed": r["completed"],
                "makespan": r["makespan"],
                "eff": round(bound / max(1, r["makespan"]), 4),
                "jain": f.get("jain", 0.0),
                "max_min": f.get("max_min", 0.0),
                "cov": f.get("cov", 0.0),
            })
        ok = [r for r in rows if r["completed"]]
        out[name] = {
            "runs": rows,
            "jain_min": round(min((r["jain"] for r in rows), default=0), 5),
            "jain_mean": round(sum(r["jain"] for r in rows) / len(rows), 5),
            "max_min_worst": round(max((r["max_min"] for r in rows
                                        if r["max_min"] != float("inf")),
                                       default=float("inf")), 4),
            "eff_min": round(min((r["eff"] for r in rows), default=0), 4),
            "n_completed": len(ok), "n_runs": len(rows),
        }
    return out


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def binding_table(topo: StackTopology) -> list[dict[str, Any]]:
    """The HA-to-bridge binding, as the hardware would document it."""
    out = []
    for d in range(topo.n_die):
        near, far = topo.die_hrings(d)
        for idx in TOP_BRIDGES:
            land = topo.nodes[topo.bridge_landing(d, idx)]
            tc = topo.bridge_target_col(d, idx)
            out.append({
                "die": d, "bridge_idx": idx,
                "land_h": land.hring, "land_col": land.col,
                "land_vpos": land.vpos, "target_col": tc,
                "h_hops": (tc - land.col) % N_COLS,
                "kind": "near" if land.col == tc else "far",
                "near_h": near, "far_h": far,
            })
    return out


def hassign_compare(k: int, seed: int, ocs: Sequence[int]) -> list[dict]:
    """Does it matter which of a gap's two rings carries the far traffic?

    Both choices move the same number of flit-hops, so the analytic bound is
    identical; what differs is whether the two dies of a gap land on the same
    attach point of a column or on adjacent ones.
    """
    out = []
    for ha in ("split", "stack"):
        topo = StackTopology(h_assign=ha)
        txns = build_uniform_write(topo, k=k, seed=seed)
        bd = topo.write_bounds(txns, m_req=M_REQ, m_rsp=M_RSP,
                               m_wdata=M_WDATA)
        arrive = {str(d): arrival_vpos(topo, d, 0) for d in range(topo.n_die)}
        for oc in ocs:
            r = run_scheme(topo, txns, "s0", route="bound", seed=seed,
                           core_outstanding=oc)
            f = r["fairness"]
            out.append({
                "h_assign": ha, "outstanding": oc, "bound": bd["bound"],
                "col0_arrival": arrive,
                "completed": r["completed"], "makespan": r["makespan"],
                "eff": round(bd["bound"] / max(1, r["makespan"]), 4),
                "thr": round(r["n_txn_done"] / max(1, r["makespan"]), 4),
                "jain": f.get("jain", 0.0), "max_min": f.get("max_min", 0.0),
                "cov": f.get("cov", 0.0),
                "n_deflections": r["n_deflections"],
            })
            print("      %-5s oc=%-4d %s t=%6d eff=%.2f jain=%.4f"
                  % (ha, oc, "OK      " if r["completed"] else "COLLAPSE",
                     r["makespan"], out[-1]["eff"], out[-1]["jain"]),
                  flush=True)
    return out


def saturation_scan(seed: int, ks: Sequence[int], oc: int) -> list[dict]:
    """Does the verdict at oc=600 depend on the batch being long enough?

    A closed batch of `k` writes per core cannot put more than `k` in flight,
    so a limit above `k` never actually binds. This scan raises `k` and reports
    the peak in-flight count the fabric really reached, which is what decides
    whether the number 600 is doing any work in the result.
    """
    out = []
    for k in ks:
        topo = StackTopology(route_mode="bound")
        txns = build_uniform_write(topo, k=k, seed=seed)
        bd = topo.write_bounds(txns, m_req=M_REQ, m_rsp=M_RSP,
                               m_wdata=M_WDATA)["bound"]
        r = run_scheme(topo, txns, "s0", route="bound", seed=seed,
                       core_outstanding=oc, stall_after=20_000)
        peak = r.get("max_core_outstanding", 0)
        out.append({
            "k": k, "n_txn": len(txns), "outstanding": oc,
            "peak_in_flight": peak, "limit_binds": bool(peak >= oc),
            "completed": r["completed"], "makespan": r["makespan"],
            "n_txn_done": r["n_txn_done"], "bound": bd,
            "eff": round(bd / max(1, r["makespan"]), 4),
            "thr": round(r["n_txn_done"] / max(1, r["makespan"]), 4),
            "jain": r["fairness"].get("jain", 0.0),
            "n_deflections": r["n_deflections"],
        })
        print("      k=%-4d ntxn=%-6d peak_inflight=%-4d binds=%-5s %s "
              "done=%d/%d thr=%.3f"
              % (k, len(txns), peak, out[-1]["limit_binds"],
                 "OK" if r["completed"] else "COLLAPSE", r["n_txn_done"],
                 len(txns), out[-1]["thr"]), flush=True)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--k", type=int, default=50,
                    help="write transactions per AI core")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--seeds", type=int, nargs="*", default=[0, 1, 2])
    ap.add_argument("--oc-work", type=int, default=5,
                    help="workable per-core outstanding limit")
    ap.add_argument("--out", default="results/dse_stack_write_fair.json")
    args = ap.parse_args()

    oc_work = args.oc_work
    t_start = time.time()
    blob: dict[str, Any] = {
        "meta": {
            "k": args.k, "seed": args.seed, "seeds": args.seeds,
            "m_req": M_REQ, "m_rsp": M_RSP, "m_wdata": M_WDATA,
            "core_outstanding": CORE_OUTSTANDING_WR,
            "oc_work": oc_work,
            "fabric": dict(FABRIC),
            "route_label": ROUTE_LABEL,
        },
    }

    topo0 = StackTopology()
    blob["topology"] = {
        "n_nodes": topo0.n, "n_die": topo0.n_die,
        "n_cores": len(topo0.cores), "n_has": len(topo0.has),
        "n_attach": len(topo0.attaches), "n_bridges": len(topo0.bridges),
        "n_cols": N_COLS, "v_len": V_LEN, "group_cols": GROUP_COLS,
        "top_bridges": list(TOP_BRIDGES),
        "directed_links": topo0.directed_links,
        "capacity": topo0.capacity(),
        "top_link_lats": list(topo0.top_link_lats),
        "bot_hop_lat": topo0.bot_hop_lat, "d2d_lat": topo0.d2d_lat,
        "vcs": list(topo0.vcs),
        "h_assign": topo0.h_assign,
    }
    blob["binding"] = binding_table(topo0)
    blob["v_profile"] = v_ring_profile(topo0, col=0)
    blob["v_profile_right"] = v_ring_profile(topo0, col=N_COLS - 1)

    print("[1/9] routing comparison", flush=True)
    blob["routing"] = routing_compare(args.k, args.seed)
    blob["hot_edges"] = {
        m: hot_edges(StackTopology(route_mode=m),
                     build_uniform_write(StackTopology(route_mode=m),
                                         k=args.k, seed=args.seed))
        for m in ("bound", "lat")}

    print("[2/9] horizontal-ring assignment", flush=True)
    blob["hassign"] = hassign_compare(args.k, args.seed,
                                      (2, oc_work, 32, CORE_OUTSTANDING_WR))

    print("[3/9] crossing-FIFO depth sweep", flush=True)
    blob["fifo_sweep"] = fifo_sweep(args.k, args.seed, (4, 16, 64, 128),
                                    core_outstanding=oc_work)

    print("[4/9] outstanding sweep", flush=True)
    blob["oc_sweep"] = outstanding_sweep(
        args.k, args.seed,
        (2, 3, 4, 5, 6, 8, 16, 32, 128, CORE_OUTSTANDING_WR))

    print("[5/9] schemes at the mandated and the workable concurrency",
          flush=True)
    blob["schemes"] = {}
    topo = StackTopology(route_mode="bound")
    txns = build_uniform_write(topo, k=args.k, seed=args.seed)
    bound = topo.write_bounds(txns, m_req=M_REQ, m_rsp=M_RSP, m_wdata=M_WDATA)
    for tag, oc in (("mandated", CORE_OUTSTANDING_WR), ("work", oc_work)):
        per: dict[str, Any] = {"bounds": bound, "n_txn": len(txns),
                               "outstanding": oc}
        for name in ("s0", "s1", "s16", "s17"):
            r = run_scheme(topo, txns, name, route="bound", seed=args.seed,
                           keep_trace=(name == "s1"), core_outstanding=oc)
            r["eff"] = round(bound["bound"] / max(1, r["makespan"]), 4)
            per[name] = r
            f = r["fairness"]
            print("      %-8s oc=%-4d %-4s t=%6d eff=%.2f jain=%.4f "
                  "maxmin=%.2f %s"
                  % (tag, oc, name, r["makespan"], r["eff"],
                     f.get("jain", 0), f.get("max_min", 0),
                     "OK" if r["completed"] else "COLLAPSE"), flush=True)
        blob["schemes"][tag] = per

    print("[6/9] root cause", flush=True)
    seats = vseat_load(topo, txns)
    blob["root_cause"] = {
        tag: root_cause(topo, blob["schemes"][tag]["s0"], seats)
        for tag in ("mandated", "work")}

    print("[7/9] scheme knob sweeps", flush=True)
    blob["s16_sweep"] = oc_sweep_s16(topo, txns, "bound", args.seed,
                                     (1, 2, 4, 8, 16, 64),
                                     core_outstanding=oc_work)
    blob["s17_sweep"] = patience_sweep(topo, txns, "bound", args.seed,
                                       (0, 1, 2, 4, 8, 16),
                                       core_outstanding=oc_work)

    print("[8/9] saturation: does the 600 limit ever bind?", flush=True)
    blob["saturation"] = saturation_scan(args.seed, (50, 100, 200),
                                        CORE_OUTSTANDING_WR)

    print("[9/9] seed sweep", flush=True)
    blob["seeds_bound"] = seed_sweep(args.k, args.seeds,
                                     ("s0", "s1", "s16", "s17"), "bound",
                                     core_outstanding=oc_work)

    blob["meta"]["wall_s"] = round(time.time() - t_start, 1)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(blob, indent=1))
    print(f"wrote {out}  ({blob['meta']['wall_s']}s)")


if __name__ == "__main__":
    main()
