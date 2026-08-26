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
from rg_stack_fc import (StackAdaptParams, StackAdaptSim, StackAdaptTurnParams,
                         StackAdaptTurnSim, StackFairTurnSim,
                         StackFcParams, StackFcSim, StackGrantParams,
                         StackGrantSim, StackTurnParams)
from rg_stack_topo import (BURST_LEN, GROUP_COLS, N_COLS, N_TILES, STRIDE,
                           TILING_SIZE, TOP_BRIDGES, TXN_PER_CORE, V_LEN,
                           StackTopology, Txn, build_tiled_write,
                           build_uniform_write, ha_histogram)

M_REQ, M_RSP, M_WDATA = 1, 2, 4
# Per-core write outstanding. Held from REQ inject to Comp retire.
CORE_OUTSTANDING_WR = 512
# CHI request-tracker entries per HA. A completer that runs out answers
# RetryAck rather than silently queueing.
HA_POS_DEPTH = 32
BW_WINDOW = 50

# Crossing FIFOs plus the HPCA'22 SWAP bypass and a bounded D2D landing
# buffer. Depths are a hardware cost that has to be stated, not assumed.
FABRIC = dict(turn_depth=64, d2d_depth=128,
              swap_rule=True, d2d_land_depth=16,
              core_outstanding=CORE_OUTSTANDING_WR,
              ha_pos_depth=HA_POS_DEPTH,
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
    if name == "s18":
        return StackAdaptSim, StackAdaptParams(**f)
    if name == "s19":
        return StackAdaptTurnSim, StackAdaptTurnParams(**f)
    raise ValueError(name)


def group_stats(topo: StackTopology, inject_times: dict[int, list[int]],
                done_by_core: dict[int, int] | None = None,
                makespan: int = 0, m_wdata: int = M_WDATA) -> dict[str, Any]:
    """Write bandwidth per top die, treating each die's 10 cores as one group.

    A per-core number answers "is any single core starved". It is not the
    number an integrator can act on, because a core is not a schedulable unit:
    a top die is. Ten cores share one die's ring, one attach group and one set
    of eight D2D bridges, so if a die is short of bandwidth the whole die is,
    and no amount of scheduling inside it recovers the shortfall.

    Both levels are reported because they can disagree, and the disagreement
    is the interesting part: cores inside a die can be unfair to each other
    while every die gets an equal share, or every core inside a die can be
    treated identically while the dies themselves differ by a wide margin.
    Only the second is a topology problem.
    """
    by_die: dict[int, list[int]] = defaultdict(list)
    for c in inject_times:
        by_die[topo.nodes[c].die].append(c)
    dies = sorted(by_die)
    if not dies:
        return {}
    finish = {c: (max(ts) if ts else 0) for c, ts in inject_times.items()}
    # Same contention window as the per-core view: measure while every core
    # still has work, so the shares are comparable.
    t_fair = min(finish.values()) or 1
    got = {d: sum(1 for c in by_die[d] for t in inject_times[c] if t <= t_fair)
           for d in dies}
    bw = {d: got[d] / t_fair for d in dies}
    vals = [bw[d] for d in dies]
    lo, hi = min(vals), max(vals)
    mean = sum(vals) / len(vals)
    var = sum((v - mean) ** 2 for v in vals) / len(vals)
    # fairness among the cores *inside* each die, for the comparison above
    inner = {}
    for d in dies:
        cv = [sum(1 for t in inject_times[c] if t <= t_fair)
              for c in sorted(by_die[d])]
        inner[str(d)] = round(jain(cv), 5)
    # Retired write data per die over the whole run. This is the number to
    # compare between schemes: a collapsed run gets a short contention
    # window, which flatters its `bw_by_group`, but it cannot hide here.
    gp: dict[int, float] = {}
    gj = gmm = 0.0
    if done_by_core and makespan > 0:
        gp = {d: sum(done_by_core.get(c, 0) for c in by_die[d]) * m_wdata
                 / makespan for d in dies}
        gv = [gp[d] for d in dies]
        gj = round(jain(gv), 5)
        gmm = (round(max(gv) / min(gv), 4) if min(gv) > 0 else float("inf"))
    return {
        "n_groups": len(dies),
        "cores_per_group": len(by_die[dies[0]]),
        "goodput_by_group": {str(d): round(v, 5) for d, v in gp.items()},
        "goodput_total": round(sum(gp.values()), 5),
        "goodput_jain": gj,
        "goodput_max_min": gmm,
        "t_fair": t_fair,
        "bw_by_group": {str(d): round(bw[d], 5) for d in dies},
        "got_by_group": {str(d): got[d] for d in dies},
        "finish_by_group": {str(d): max(finish[c] for c in by_die[d])
                            for d in dies},
        "jain": round(jain(vals), 5),
        "max_min": round(hi / lo, 4) if lo > 0 else float("inf"),
        "cov": round(var ** 0.5 / mean, 5) if mean else 0.0,
        "best_group": max(dies, key=lambda d: bw[d]),
        "worst_group": min(dies, key=lambda d: bw[d]),
        "jain_within_group": inner,
        "jain_within_worst": round(min(inner.values()), 5),
    }


def group_bw_series(topo: StackTopology, inject_times: dict[int, list[int]],
                    window: int = BW_WINDOW, makespan: int = 0
                    ) -> dict[str, Any]:
    """Write bandwidth vs time, one series per top-die group.

    Each point is WriteData flits boarded by that die's 10 cores in a
    `window`-cycle bin, divided by the window -- so the y-axis is flit/cycle
    and the integral is the completed write traffic.
    """
    by_die: dict[int, list[int]] = defaultdict(list)
    for c, ts in inject_times.items():
        by_die[topo.nodes[int(c)].die].extend(ts)
    last = makespan or 0
    for ts in inject_times.values():
        if ts:
            last = max(last, max(ts))
    nwin = max(1, (last + window) // window)
    series: dict[str, list[float]] = {}
    for d in sorted(by_die):
        hist = [0] * nwin
        for t in by_die[d]:
            hist[min(max(0, t // window), nwin - 1)] += 1
        series[str(d)] = [round(c / window, 5) for c in hist]
    return {
        "window": window, "n_windows": nwin, "makespan": last,
        "t": [i * window for i in range(nwin)],
        "bw_by_group": series,
    }


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
    r["group"] = group_stats(topo, r["wr_inject_by_core"],
                             r.get("wr_done_by_core"), r["makespan"])
    r["scheme"] = name
    r["route"] = route
    r["wall_s"] = round(time.time() - t0, 1)
    r["max_core_outstanding"] = r.get("max_core_outstanding", 0)
    r["bw_series"] = group_bw_series(topo, r["wr_inject_by_core"],
                                     window=BW_WINDOW, makespan=r["makespan"])
    r.pop("wr_inject_by_core", None)
    r.pop("wr_done_by_core", None)
    if not keep_trace and "fc" in r:
        r["fc"].pop("trace", None)
    return r


def die_board_table(topo: StackTopology, board: dict[str, Any],
                    die: int = 0) -> list[dict[str, Any]]:
    """CW / CCW board counts for the 10 AI cores of one top die."""
    rows = []
    for c in topo.cores:
        nd = topo.nodes[c]
        if nd.die != die:
            continue
        rec = board.get(str(c), {})
        rows.append({
            "core": c, "idx": nd.idx,
            "ok_cw": rec.get("ok_cw", 0), "ok_ccw": rec.get("ok_ccw", 0),
            "fail_cw": rec.get("fail_cw", 0), "fail_ccw": rec.get("fail_ccw", 0),
        })
    return rows


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


def retry_sweep(k: int, seed: int, depths: Sequence[int],
                oc: int) -> list[dict[str, Any]]:
    """What the completer's request tracker does to the effective concurrency.

    A completer cannot queue a request it has no tracker entry for: CHI makes
    it answer RetryAck and hand out a P-Credit later, and the requester holds
    the request until then. So the outstanding register is an upper bound on
    *nominal* concurrency, and the concurrency that actually covers the round
    trip is smaller by however much is parked. This sweep varies the tracker
    depth at a fixed nominal limit and reports both numbers, the parking time
    that separates them, and what it costs in retry traffic.
    """
    out = []
    topo = StackTopology(route_mode="bound")
    txns = build_uniform_write(topo, k=k, seed=seed)
    for d in depths:
        r = run_scheme(topo, txns, "s0", route="bound", seed=seed,
                       core_outstanding=oc, ha_pos_depth=d, stall_after=20_000)
        q, f, g = r["retry"], r["fairness"], r["group"]
        out.append({
            "pos_depth": d, "outstanding": oc,
            "completed": r["completed"], "makespan": r["makespan"],
            "n_txn_done": r["n_txn_done"], "n_txn": len(txns),
            "thr": round(r["n_txn_done"] / max(1, r["makespan"]), 4),
            "n_retry": q["n_retry"], "n_req_resent": q["n_req_resent"],
            "retry_per_txn": q["retry_per_txn"],
            "nom_conc": q["nom_conc_mean"], "eff_conc": q["eff_conc_mean"],
            "eff_frac": q["eff_frac"], "max_parked": q["max_parked"],
            "park_mean": q["park_wait_mean"], "park_p99": q["park_wait_p99"],
            "reorder": q["reorder"],
            "jain": f.get("jain", 0.0), "max_min": f.get("max_min", 0.0),
            "group_jain": g.get("jain", 0.0),
            "group_max_min": g.get("max_min", 0.0),
        })
        print("      pos=%-4d %-8s done=%4d/%d retry=%-5d eff/nom=%.3f "
              "park=%4.0f jain=%.4f" %
              (d, "OK" if r["completed"] else "COLLAPSE", r["n_txn_done"],
               len(txns), q["n_retry"], q["eff_frac"], q["park_wait_mean"],
               f.get("jain", 0.0)), flush=True)
    return out


SCENARIOS: tuple[tuple[str, Any], ...] = (
    ("all 6 dies", None),
    ("3 dies", [0, 1, 2]),
    ("1 die", [0]),
)


def scenario_scan(k: int, seed: int, ocs: Sequence[int],
                  slacks: Sequence[float], pos_depth: int) -> dict[str, Any]:
    """Is one configured outstanding limit ever right for every scenario?

    The limit has two failure modes pulling in opposite directions. Set it
    high and a busy fabric collapses; set it low and a quiet fabric cannot
    keep enough writes in flight to cover the round trip, so the cores idle.
    The question that decides whether dynamic control is worth its cost is
    whether the safe range at full load still overlaps the useful range at
    light load. This scan measures both ranges directly, then runs the
    adaptive schemes over the same scenarios without retuning anything.
    """
    topo = StackTopology(route_mode="bound")
    rows: list[dict[str, Any]] = []
    best: dict[str, Any] = {}
    for lbl, dies in SCENARIOS:
        txns = build_uniform_write(topo, k=k, seed=seed, dies=dies)
        for oc in ocs:
            r = run_scheme(topo, txns, "s0", route="bound", seed=seed,
                           core_outstanding=oc, ha_pos_depth=pos_depth,
                           stall_after=20_000)
            thr = round(r["n_txn_done"] / max(1, r["makespan"]), 4)
            rows.append({
                "scenario": lbl, "n_cores": len(txns) // k, "n_txn": len(txns),
                "scheme": "s0", "outstanding": oc,
                "completed": r["completed"], "thr": thr,
                "makespan": r["makespan"],
                "jain": r["fairness"].get("jain", 0.0),
                "group_jain": r["group"].get("jain", 0.0),
            })
            if r["completed"] and thr > best.get(lbl, {}).get("thr", -1):
                best[lbl] = {"outstanding": oc, "thr": thr}
        print("      %-11s best static oc=%s at %.3f txn/cycle"
              % (lbl, best.get(lbl, {}).get("outstanding"),
                 best.get(lbl, {}).get("thr", 0)), flush=True)

    for lbl, dies in SCENARIOS:
        txns = build_uniform_write(topo, k=k, seed=seed, dies=dies)
        bt = max(1e-9, best.get(lbl, {}).get("thr", 0.0))
        for name in ("s18", "s19"):
            for sl in slacks:
                r = run_scheme(topo, txns, name, route="bound", seed=seed,
                               core_outstanding=CORE_OUTSTANDING_WR,
                               ha_pos_depth=pos_depth, rtt_slack=sl,
                               keep_trace=True, stall_after=20_000)
                thr = round(r["n_txn_done"] / max(1, r["makespan"]), 4)
                fc = r.get("fc", {})
                rows.append({
                    "scenario": lbl, "n_cores": len(txns) // k,
                    "n_txn": len(txns), "scheme": name, "outstanding": None,
                    "rtt_slack": sl, "completed": r["completed"], "thr": thr,
                    "makespan": r["makespan"],
                    "rel_best": round(thr / bt, 4),
                    "win_mean": fc.get("win_mean_final", 0.0),
                    "win_lo": fc.get("win_min_final", 0.0),
                    "win_hi": fc.get("win_max_final", 0.0),
                    "rtt_min": fc.get("rtt_min_mean", 0.0),
                    "n_win_cut": fc.get("n_win_cut", 0),
                    "n_retry_cut": fc.get("n_retry_cut", 0),
                    "jain": r["fairness"].get("jain", 0.0),
                    "max_min": r["fairness"].get("max_min", 0.0),
                    "group_jain": r["group"].get("jain", 0.0),
                    "group_max_min": r["group"].get("max_min", 0.0),
                })
                print("      %-11s %s slack=%.1f thr=%.3f (%3.0f%% of best) "
                      "win %.0f..%.0f" %
                      (lbl, name, sl, thr, 100 * thr / bt,
                       fc.get("win_min_final", 0), fc.get("win_max_final", 0)),
                      flush=True)

    # Worst relative throughput across scenarios: the number that decides
    # whether a single configured value is defensible at all.
    worst: dict[str, float] = {}
    for oc in ocs:
        vs = []
        for lbl, _ in SCENARIOS:
            bt = max(1e-9, best.get(lbl, {}).get("thr", 0.0))
            hit = [r for r in rows if r["scenario"] == lbl
                   and r["scheme"] == "s0" and r["outstanding"] == oc]
            vs.append((hit[0]["thr"] / bt) if hit else 0.0)
        worst[f"static_oc{oc}"] = round(min(vs), 4)
    for name in ("s18", "s19"):
        for sl in slacks:
            vs = []
            for lbl, _ in SCENARIOS:
                hit = [r for r in rows if r["scenario"] == lbl
                       and r["scheme"] == name and r.get("rtt_slack") == sl]
                vs.append(hit[0]["rel_best"] if hit else 0.0)
            worst[f"{name}_slack{sl}"] = round(min(vs), 4)
    return {"rows": rows, "best_static": best, "worst_rel": worst,
            "pos_depth": pos_depth, "k": k,
            "scenarios": [s[0] for s in SCENARIOS]}


def group_compare(topo: StackTopology, txns: Sequence[Txn], seed: int,
                  ocs: Sequence[int], names: Sequence[str],
                  pos_depth: int) -> list[dict[str, Any]]:
    """Write bandwidth per top die, which is the unit an integrator owns.

    Ten cores share one die's ring, one attach group and one set of bridges,
    so a shortfall at that granularity cannot be scheduled away from inside
    the die. This is also the granularity at which the fabric's asymmetry is
    visible: the per-core spread mixes it with noise, while the per-die spread
    is the same sign in every run.
    """
    out = []
    seen: set[str] = set()
    for oc in ocs:
        for name in names:
            kw: dict[str, Any] = {"core_outstanding": oc,
                                  "ha_pos_depth": pos_depth}
            if name in ("s18", "s19"):
                # These set their own limit, so sweeping it would just repeat
                # the same run under a different label.
                if name in seen:
                    continue
                seen.add(name)
                kw["core_outstanding"] = CORE_OUTSTANDING_WR
                kw["rtt_slack"] = 2.0
            r = run_scheme(topo, txns, name, route="bound", seed=seed,
                           stall_after=20_000, **kw)
            g, f = r["group"], r["fairness"]
            out.append({
                "outstanding": oc, "scheme": name,
                "completed": r["completed"], "makespan": r["makespan"],
                "n_txn_done": r["n_txn_done"],
                "bw_by_group": g["bw_by_group"],
                "goodput_by_group": g["goodput_by_group"],
                "goodput_total": g["goodput_total"],
                "goodput_jain": g["goodput_jain"],
                "goodput_max_min": g["goodput_max_min"],
                "finish_by_group": g["finish_by_group"],
                "group_jain": g["jain"], "group_max_min": g["max_min"],
                "group_cov": g["cov"],
                "worst_group": g["worst_group"], "best_group": g["best_group"],
                "jain_within_group": g["jain_within_group"],
                "jain_within_worst": g["jain_within_worst"],
                "core_jain": f.get("jain", 0.0),
                "core_max_min": f.get("max_min", 0.0),
            })
            print("      oc=%-4d %-4s %-8s grp_jain=%.4f grp_mm=%5.2f "
                  "gp_mm=%6.2f worst=die%s" %
                  (oc, name, "OK" if r["completed"] else "COLLAPSE",
                   g["jain"], g["max_min"], g["goodput_max_min"],
                   g["worst_group"]), flush=True)
    return out


def binding_mod4(topo: StackTopology) -> dict[str, Any]:
    """Check the bridge a die uses for a column is the one at (col mod 4).

    A die's eight bridges land on its own four columns, twice: once on each
    horizontal ring of its row gap. Reaching a column in the other half means
    landing on the same position of the far ring and riding four columns
    across. Either way the position within the group is the target column
    modulo four, which is what makes the binding a wiring rule rather than a
    table.
    """
    rows = []
    ok = True
    for die in range(topo.n_die):
        cols = topo.die_cols(die)
        near, far = topo.die_hrings(die)
        for col in range(N_COLS):
            idx = topo.ha_bridge(die, col)
            j = TOP_BRIDGES.index(idx)
            own = col in cols
            good = (j % GROUP_COLS == col % GROUP_COLS
                    and (j < GROUP_COLS) == own)
            ok = ok and good
            rows.append({
                "die": die, "col": col, "bridge": idx, "pos": j,
                "group_pos": j % GROUP_COLS, "col_mod4": col % GROUP_COLS,
                "row": "near" if j < GROUP_COLS else "far",
                "hring": near if own else far,
                "in_own_half": own, "matches_mod4": good,
            })
    return {"holds": ok, "rows": rows, "group_cols": GROUP_COLS,
            "n_checked": len(rows)}


def _run_focus(blob: dict[str, Any], topo: StackTopology, args: Any) -> None:
    """Tiled write + HA retry operating point.

    Each core writes dense 64 KB tiles (128 B burst, 4 KB stride). Line
    interleave already spreads every core across all 96 HAs. Outstanding
    is 128; an HA accepts 32 in-flight requests and RetryAcks the rest.
    S0 has no source-side rate control. S1 adds AIMD.
    """
    oc = blob["meta"]["core_outstanding"]
    pos = args.pos_depth
    n_tiles = getattr(args, "n_tiles", N_TILES)
    txns = build_tiled_write(topo, n_tiles=n_tiles, seed=args.seed)
    hist = ha_histogram(topo, txns)
    bound = topo.write_bounds(txns, m_req=M_REQ, m_rsp=M_RSP, m_wdata=M_WDATA)
    per: dict[str, Any] = {"bounds": bound, "n_txn": len(txns),
                           "outstanding": oc, "pos_depth": pos,
                           "ha_hist": hist}
    series: dict[str, Any] = {}
    stall = max(80_000, 160 * hist["per_core_txn"])
    print(f"[focus] outstanding={oc}  HA POS={pos}  "
          f"tiles={n_tiles}  txn/core={hist['per_core_txn']}  "
          f"ntxn={len(txns)}  HA cover={hist['covers_all_ha']}  "
          f"per-core HA max/min Δ={hist['per_core_max_min']}", flush=True)
    names = ("s0", "s1")
    board: dict[str, Any] = {}
    for name in names:
        r = run_scheme(topo, txns, name, route="bound", seed=args.seed,
                       keep_trace=(name == "s1"), core_outstanding=oc,
                       ha_pos_depth=pos, stall_after=stall)
        r["eff"] = round(bound["bound"] / max(1, r["makespan"]), 4)
        per[name] = r
        series[name] = r.get("bw_series", {})
        board[name] = die_board_table(topo, r.get("board_by_core_dir") or {},
                                      die=0)
        f, g, q = r["fairness"], r["group"], r.get("retry", {})
        print("      %-4s %-8s t=%6d done=%d/%d jain=%.4f "
              "grp_jain=%.4f gp_mm=%.2f retry=%d "
              "swap=%d (hv=%d d2d=%d) d2d_buf=%d"
              % (name, "OK" if r["completed"] else "COLLAPSE",
                 r["makespan"], r["n_txn_done"], len(txns),
                 f.get("jain", 0), g.get("jain", 0),
                 g.get("goodput_max_min", 0), q.get("n_retry", 0),
                 r.get("n_swaps", 0), r.get("n_swaps_hv", 0),
                 r.get("n_swaps_d2d", 0),
                 (r.get("fifo") or {}).get("d2d_buf_peak", 0)),
              flush=True)
    blob["schemes"] = {"mandated": per, "work": per}
    blob["group_series"] = series
    blob["fabric_series"] = {name: per[name].get("fabric_series", {})
                             for name in names}
    blob["die0_board"] = board
    blob["workload"] = {
        "kind": "tiled_write",
        "burst_len": BURST_LEN, "stride": STRIDE,
        "tiling_size": TILING_SIZE, "n_tiles": n_tiles,
        "ha_hist": hist,
    }
    blob["group"] = [{
        "outstanding": oc, "scheme": name,
        "completed": per[name]["completed"],
        "makespan": per[name]["makespan"],
        "n_txn_done": per[name]["n_txn_done"],
        **{k: per[name]["group"][k] for k in (
            "bw_by_group", "goodput_by_group", "goodput_total",
            "goodput_jain", "goodput_max_min", "finish_by_group",
            "worst_group", "best_group", "jain_within_group",
            "jain_within_worst") if k in per[name]["group"]},
        "group_jain": per[name]["group"].get("jain", 0),
        "group_max_min": per[name]["group"].get("max_min", 0),
        "group_cov": per[name]["group"].get("cov", 0),
        "core_jain": per[name]["fairness"].get("jain", 0),
        "core_max_min": per[name]["fairness"].get("max_min", 0),
    } for name in names]
    seats = vseat_load(topo, txns)
    blob["root_cause"] = {"mandated": root_cause(topo, per["s0"], seats),
                          "work": root_cause(topo, per["s0"], seats)}


def main() -> None:
    global CORE_OUTSTANDING_WR
    ap = argparse.ArgumentParser()
    ap.add_argument("--k", type=int, default=800,
                    help="write requests per AI core (closed-batch workload)")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--seeds", type=int, nargs="*", default=[0, 1, 2])
    ap.add_argument("--oc", type=int, default=CORE_OUTSTANDING_WR,
                    help="per-core write outstanding (in-flight cap)")
    ap.add_argument("--oc-work", type=int, default=5,
                    help="workable per-core outstanding limit")
    ap.add_argument("--pos-depth", type=int, default=HA_POS_DEPTH,
                    help="HA request tracker entries; 0 = unlimited")
    ap.add_argument("--n-tiles", type=int, default=N_TILES,
                    help=f"64 KB tiles per AI core (default {N_TILES} = "
                         f"{TXN_PER_CORE} WriteNoSnp)")
    ap.add_argument("--focus", action="store_true",
                    help="S0/S1 time series at the configured outstanding")
    ap.add_argument("--out", default="results/dse_stack_write_fair.json")
    args = ap.parse_args()

    topo0 = StackTopology()
    rtt = topo0.max_write_rtt(m_wdata=M_WDATA)
    CORE_OUTSTANDING_WR = args.oc
    FABRIC["core_outstanding"] = CORE_OUTSTANDING_WR
    FABRIC["ha_pos_depth"] = args.pos_depth

    oc_work = args.oc_work
    t_start = time.time()
    blob: dict[str, Any] = {
        "meta": {
            "k": args.k, "seed": args.seed, "seeds": args.seeds,
            "m_req": M_REQ, "m_rsp": M_RSP, "m_wdata": M_WDATA,
            "core_outstanding": CORE_OUTSTANDING_WR,
            "oc_work": oc_work, "pos_depth": args.pos_depth,
            "n_tiles": args.n_tiles,
            "txn_per_core": args.n_tiles * (TILING_SIZE // BURST_LEN),
            "burst_len": BURST_LEN, "stride": STRIDE,
            "tiling_size": TILING_SIZE,
            "fabric": dict(FABRIC),
            "route_label": ROUTE_LABEL,
            "rtt": rtt,
        },
    }
    blob["topology"] = {
        "n_nodes": topo0.n, "n_die": topo0.n_die,
        "n_cores": len(topo0.cores), "n_has": len(topo0.has),
        "n_attach": len(topo0.attaches), "n_bridges": len(topo0.bridges),
        "n_cols": N_COLS, "v_len": V_LEN, "group_cols": GROUP_COLS,
        "top_bridges": list(TOP_BRIDGES),
        "directed_links": topo0.directed_links,
        "capacity": topo0.capacity(),
        "top_link_lats": list(topo0.top_link_lats),
        "h_hop_lat": topo0.h_hop_lat, "v_hop_lat": topo0.v_hop_lat,
        "bot_hop_lat": topo0.bot_hop_lat, "d2d_lat": topo0.d2d_lat,
        "turn_lat": topo0.turn_lat,
        "vcs": list(topo0.vcs),
        "h_assign": topo0.h_assign,
        "d2d_bot_ifaces": 2,
        "d2d_bot_iface": ["h", "v"],
        "rtt": rtt,
    }
    blob["binding"] = binding_table(topo0)
    blob["binding_mod4"] = binding_mod4(topo0)
    blob["v_profile"] = v_ring_profile(topo0, col=0)
    blob["v_profile_right"] = v_ring_profile(topo0, col=N_COLS - 1)

    if args.focus:
        _run_focus(blob, topo0, args)
        blob["meta"]["wall_s"] = round(time.time() - t_start, 1)
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(blob, indent=1))
        print(f"wrote {out}  ({blob['meta']['wall_s']}s)")
        return

    print("[1/12] routing comparison", flush=True)
    blob["routing"] = routing_compare(args.k, args.seed)
    blob["hot_edges"] = {
        m: hot_edges(StackTopology(route_mode=m),
                     build_uniform_write(StackTopology(route_mode=m),
                                         k=args.k, seed=args.seed))
        for m in ("bound", "lat")}

    print("[2/12] horizontal-ring assignment", flush=True)
    blob["hassign"] = hassign_compare(args.k, args.seed,
                                      (2, oc_work, 32, CORE_OUTSTANDING_WR))

    print("[3/12] crossing-FIFO depth sweep", flush=True)
    blob["fifo_sweep"] = fifo_sweep(args.k, args.seed, (4, 16, 64, 128),
                                    core_outstanding=oc_work)

    print("[4/12] outstanding sweep", flush=True)
    blob["oc_sweep"] = outstanding_sweep(
        args.k, args.seed,
        (2, 3, 4, 5, 6, 8, 16, 32, 128, CORE_OUTSTANDING_WR))

    print("[5/12] schemes at the mandated and the workable concurrency",
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
                           keep_trace=(name == "s1"), core_outstanding=oc,
                           ha_pos_depth=args.pos_depth)
            r["eff"] = round(bound["bound"] / max(1, r["makespan"]), 4)
            per[name] = r
            f = r["fairness"]
            print("      %-8s oc=%-4d %-4s t=%6d eff=%.2f jain=%.4f "
                  "maxmin=%.2f %s"
                  % (tag, oc, name, r["makespan"], r["eff"],
                     f.get("jain", 0), f.get("max_min", 0),
                     "OK" if r["completed"] else "COLLAPSE"), flush=True)
        blob["schemes"][tag] = per

    print("[6/12] root cause", flush=True)
    seats = vseat_load(topo, txns)
    blob["root_cause"] = {
        tag: root_cause(topo, blob["schemes"][tag]["s0"], seats)
        for tag in ("mandated", "work")}

    print("[7/12] scheme knob sweeps", flush=True)
    blob["s16_sweep"] = oc_sweep_s16(topo, txns, "bound", args.seed,
                                     (1, 2, 4, 8, 16, 64),
                                     core_outstanding=oc_work)
    blob["s17_sweep"] = patience_sweep(topo, txns, "bound", args.seed,
                                       (0, 1, 2, 4, 8, 16),
                                       core_outstanding=oc_work)

    print("[8/12] saturation: does the 600 limit ever bind?", flush=True)
    blob["saturation"] = saturation_scan(args.seed, (50, 100, 200),
                                        CORE_OUTSTANDING_WR)

    print("[9/12] seed sweep", flush=True)
    blob["seeds_bound"] = seed_sweep(args.k, args.seeds,
                                     ("s0", "s1", "s16", "s17"), "bound",
                                     core_outstanding=oc_work)

    print("[10/12] completer retry: nominal vs effective outstanding",
          flush=True)
    blob["retry_sweep"] = retry_sweep(args.k, args.seed,
                                      (0, 64, 32, 16, 8, 4, 2),
                                      CORE_OUTSTANDING_WR)

    print("[11/12] one limit for every scenario?", flush=True)
    blob["scenario"] = scenario_scan(args.k, args.seed,
                                     (2, 3, 5, 8, 16, 32,
                                      CORE_OUTSTANDING_WR),
                                     (1.0, 2.0), args.pos_depth)

    print("[12/12] write bandwidth per top die", flush=True)
    blob["group"] = group_compare(topo, txns, args.seed,
                                  (CORE_OUTSTANDING_WR, oc_work),
                                  ("s0", "s1", "s16", "s17", "s18", "s19"),
                                  args.pos_depth)

    blob["meta"]["wall_s"] = round(time.time() - t_start, 1)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(blob, indent=1))
    print(f"wrote {out}  ({blob['meta']['wall_s']}s)")


if __name__ == "__main__":
    main()
