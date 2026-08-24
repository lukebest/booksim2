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
from rg_stack_topo import (N_COLS, TOP_BRIDGES, V_LEN, StackTopology, Txn,
                           build_uniform_write)

M_REQ, M_RSP, M_WDATA = 1, 2, 4
CORE_OUTSTANDING_WR = 128

# The crossing FIFOs are the one place strict bufferlessness does not hold,
# so their depth is a hardware cost that has to be stated, not assumed. The
# sweep below shows depth 4 -- the value the plan guessed -- livelocks.
FABRIC = dict(turn_depth=64, d2d_depth=128,
              core_outstanding=CORE_OUTSTANDING_WR,
              inj_depth=8, eject_depth=4, eject_bw=1, per_vc_srcq=True)

ROUTE_LABEL = {
    "lat": "最短路径（按时延）",
    "hops": "最短路径（按跳数）",
    "dor": "维序路由（先竖后横不换列）",
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
               **kw) -> dict[str, Any]:
    cls, params = _sim(name, route=route, **kw)
    t0 = time.time()
    r = run_batch(topo, txns, params=params, sim_cls=cls, seed=seed,
                  stall_after=20_000)
    n_per_core = (len(txns) // max(1, len(topo.cores))) * M_WDATA
    r["fairness"] = fairness_stats(r["wr_inject_by_core"], r["makespan"],
                                   n_per_core)
    r["scheme"] = name
    r["route"] = route
    r["wall_s"] = round(time.time() - t0, 1)
    r.pop("wr_inject_by_core", None)
    if not keep_trace and "fc" in r:
        r["fc"].pop("trace", None)
    return r


# ---------------------------------------------------------------------------
# structural / analytic tables
# ---------------------------------------------------------------------------

def v_ring_profile(topo: StackTopology) -> dict[str, Any]:
    """Analytic per-edge load on one vertical half ring.

    Every die sends the same amount to every HA, so one column stands for all
    eight. `dat` rides attach -> HA, `rsp` rides HA -> attach, and because the
    ring is unidirectional both travel the same way round.
    """
    nodes = [topo.nodes[topo._v_node(0, p)] for p in range(V_LEN)]
    ha = [p for p in range(V_LEN) if nodes[p].role == "ha"]
    attach = {n.hring: n.vpos for n in nodes if n.role == "attach"}

    def span(a: int, b: int) -> list[int]:
        out, c = [], a
        while c != b:
            out.append(c)
            c = (c + 1) % V_LEN
        return out

    dat: Counter = Counter()
    rsp: Counter = Counter()
    for die, a in attach.items():
        for q in ha:
            for p in span(a, q):
                dat[p] += M_WDATA
            for p in span(q, a):
                rsp[p] += M_RSP
    rows = []
    for p in range(V_LEN):
        n = nodes[p]
        rows.append({
            "vpos": p,
            "role": n.role,
            "label": (f"A(h{n.hring})" if n.role == "attach"
                      else f"HA r{n.row}"),
            "die": next((d for d, a in attach.items() if a == p), None),
            "dat": dat[p], "rsp": rsp[p], "tot": dat[p] + rsp[p],
        })
    return {
        "rows": rows,
        "attach_vpos": {str(d): a for d, a in sorted(attach.items())},
        "ha_vpos": ha,
        "inject_load": {str(d): {"vpos": a, "dat": dat[a],
                                 "tot": dat[a] + rsp[a]}
                        for d, a in sorted(attach.items())},
    }


def routing_compare(k: int, seed: int) -> dict[str, Any]:
    """Bounds and load concentration under each routing policy."""
    out: dict[str, Any] = {}
    for mode in ("lat", "hops", "dor"):
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
               prof: dict[str, Any]) -> dict[str, Any]:
    bw = {int(c): v for c, v in s0["fairness"]["bw_by_core"].items()}
    cs = sorted(bw)
    inj = prof["inject_load"]
    fails = s0.get("board_fail_by_src", {})

    def cause(c: int, key: str) -> int:
        return int(fails.get(f"{c}:dat", {}).get(key, 0))

    rows = []
    for c in cs:
        nd = topo.nodes[c]
        il = inj[str(nd.die)]
        ok, busy = cause(c, "ok"), cause(c, "hop_busy")
        budget, outst = cause(c, "fc_budget"), cause(c, "outstanding")
        tries = ok + busy + cause(c, "itag") + budget + outst
        rows.append({
            "core": c, "die": nd.die, "idx": nd.idx,
            "vpos": il["vpos"], "inj_dat": il["dat"], "inj_tot": il["tot"],
            "pair": 0 if nd.die % 2 == 0 else 1,
            "gap": nd.die // 2,
            "bw": bw[c],
            "ok": ok, "hop_busy": busy, "outstanding": outst,
            "succ_rate": round(ok / tries, 4) if tries else 0.0,
        })
    b = [r["bw"] for r in rows]
    corr = {
        "die": round(spearman([r["die"] for r in rows], b), 4),
        "vpos": round(spearman([r["vpos"] for r in rows], b), 4),
        "top_idx": round(spearman([r["idx"] for r in rows], b), 4),
        "inj_dat": round(spearman([r["inj_dat"] for r in rows], b), 4),
        "inj_dat_pearson": round(pearson([r["inj_dat"] for r in rows], b), 4),
        "inj_tot": round(spearman([r["inj_tot"] for r in rows], b), 4),
        "pair": round(spearman([r["pair"] for r in rows], b), 4),
        "gap": round(spearman([r["gap"] for r in rows], b), 4),
    }
    by_die: dict[str, Any] = {}
    for d in range(topo.n_die):
        vals = [r["bw"] for r in rows if r["die"] == d]
        if not vals:
            continue
        by_die[str(d)] = {
            "vpos": inj[str(d)]["vpos"], "inj_dat": inj[str(d)]["dat"],
            "n": len(vals), "mean": round(sum(vals) / len(vals), 5),
            "min": round(min(vals), 5), "max": round(max(vals), 5),
        }
    by_idx: dict[str, Any] = {}
    for i in sorted({r["idx"] for r in rows}):
        vals = [r["bw"] for r in rows if r["idx"] == i]
        by_idx[str(i)] = {"n": len(vals),
                          "mean": round(sum(vals) / len(vals), 5)}
    first = [r["bw"] for r in rows if r["pair"] == 0]
    second = [r["bw"] for r in rows if r["pair"] == 1]
    return {
        "rows": rows, "corr": corr, "by_die": by_die, "by_idx": by_idx,
        "pair_effect": {
            "first_mean": round(sum(first) / max(1, len(first)), 5),
            "second_mean": round(sum(second) / max(1, len(second)), 5),
            "ratio": round(sum(first) / max(1e-9, sum(second)), 4),
        },
    }


# ---------------------------------------------------------------------------
# sweeps
# ---------------------------------------------------------------------------

def fifo_sweep(k: int, seed: int, depths: Sequence[int]) -> list[dict]:
    out = []
    for route in ("lat", "dor"):
        topo = StackTopology(route_mode=route)
        txns = build_uniform_write(topo, k=k, seed=seed)
        for d in depths:
            r = run_scheme(topo, txns, "s0", route=route, seed=seed,
                           turn_depth=d, d2d_depth=2 * d)
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
    for route in ("lat", "dor"):
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
                 seed: int, ocs: Sequence[int]) -> list[dict]:
    out = []
    for oc in ocs:
        r = run_scheme(topo, txns, "s16", route=route, seed=seed,
                       overcommit=oc)
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
                   seed: int, pats: Sequence[int]) -> list[dict]:
    out = []
    for p in pats:
        r = run_scheme(topo, txns, "s17", route=route, seed=seed,
                       turn_patience=p)
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
               route: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name in schemes:
        rows = []
        for s in seeds:
            topo = StackTopology(route_mode=route)
            txns = build_uniform_write(topo, k=k, seed=s)
            bound = topo.write_bounds(txns, m_req=M_REQ, m_rsp=M_RSP,
                                      m_wdata=M_WDATA)["bound"]
            r = run_scheme(topo, txns, name, route=route, seed=s)
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

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--k", type=int, default=50,
                    help="write transactions per AI core")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--seeds", type=int, nargs="*", default=[0, 1, 2])
    ap.add_argument("--out", default="results/dse_stack_write_fair.json")
    args = ap.parse_args()

    t_start = time.time()
    blob: dict[str, Any] = {
        "meta": {
            "k": args.k, "seed": args.seed, "seeds": args.seeds,
            "m_req": M_REQ, "m_rsp": M_RSP, "m_wdata": M_WDATA,
            "core_outstanding": CORE_OUTSTANDING_WR,
            "fabric": dict(FABRIC),
            "route_label": ROUTE_LABEL,
        },
    }

    topo0 = StackTopology()
    blob["topology"] = {
        "n_nodes": topo0.n, "n_die": topo0.n_die,
        "n_cores": len(topo0.cores), "n_has": len(topo0.has),
        "n_attach": len(topo0.attaches), "n_bridges": len(topo0.bridges),
        "n_cols": N_COLS, "v_len": V_LEN,
        "top_bridges": list(TOP_BRIDGES),
        "directed_links": topo0.directed_links,
        "capacity": topo0.capacity(),
        "top_link_lats": list(topo0.top_link_lats),
        "bot_hop_lat": topo0.bot_hop_lat, "d2d_lat": topo0.d2d_lat,
        "vcs": list(topo0.vcs),
    }
    blob["v_profile"] = v_ring_profile(topo0)
    print("[1/7] routing comparison", flush=True)
    blob["routing"] = routing_compare(args.k, args.seed)
    blob["hot_edges"] = {
        m: hot_edges(StackTopology(route_mode=m),
                     build_uniform_write(StackTopology(route_mode=m),
                                         k=args.k, seed=args.seed))
        for m in ("lat", "dor")}

    print("[2/7] crossing-FIFO depth sweep", flush=True)
    blob["fifo_sweep"] = fifo_sweep(args.k, args.seed, (4, 16, 64, 128))

    print("[3/7] outstanding sweep", flush=True)
    blob["oc_sweep"] = outstanding_sweep(args.k, args.seed,
                                         (4, 8, 16, 24, 32, 64, 128))

    print("[4/7] schemes under both routings", flush=True)
    blob["schemes"] = {}
    for route in ("lat", "dor"):
        topo = StackTopology(route_mode=route)
        txns = build_uniform_write(topo, k=args.k, seed=args.seed)
        bound = topo.write_bounds(txns, m_req=M_REQ, m_rsp=M_RSP,
                                  m_wdata=M_WDATA)
        per: dict[str, Any] = {"bounds": bound, "n_txn": len(txns)}
        for name in ("s0", "s1", "s16", "s17"):
            r = run_scheme(topo, txns, name, route=route, seed=args.seed,
                           keep_trace=(name == "s1"))
            r["eff"] = round(bound["bound"] / max(1, r["makespan"]), 4)
            per[name] = r
            f = r["fairness"]
            print("      %-4s %-4s t=%6d eff=%.2f jain=%.4f maxmin=%.2f %s"
                  % (route, name, r["makespan"], r["eff"], f.get("jain", 0),
                     f.get("max_min", 0),
                     "OK" if r["completed"] else "COLLAPSE"), flush=True)
        blob["schemes"][route] = per

    print("[5/7] root cause", flush=True)
    blob["root_cause"] = {}
    for route in ("lat", "dor"):
        topo = StackTopology(route_mode=route)
        blob["root_cause"][route] = root_cause(
            topo, blob["schemes"][route]["s0"], blob["v_profile"])

    print("[6/7] scheme knob sweeps", flush=True)
    topo_d = StackTopology(route_mode="dor")
    txns_d = build_uniform_write(topo_d, k=args.k, seed=args.seed)
    blob["s16_sweep"] = oc_sweep_s16(topo_d, txns_d, "dor", args.seed,
                                     (4, 8, 16, 32, 64, 128))
    blob["s17_sweep"] = patience_sweep(topo_d, txns_d, "dor", args.seed,
                                       (0, 1, 2, 4, 8, 16, 32))

    print("[7/7] seed sweep", flush=True)
    blob["seeds_dor"] = seed_sweep(args.k, args.seeds,
                                   ("s0", "s1", "s16", "s17"), "dor")
    blob["seeds_lat"] = seed_sweep(args.k, args.seeds, ("s0",), "lat")

    blob["meta"]["wall_s"] = round(time.time() - t_start, 1)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(blob, indent=1))
    print(f"wrote {out}  ({blob['meta']['wall_s']}s)")


if __name__ == "__main__":
    main()
