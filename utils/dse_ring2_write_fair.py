#!/usr/bin/env python3
"""Per-core write-bandwidth fairness on the bufferless 20-node dual-plane ring.

Workload: every AI core issues `K` CHI WriteNoSnp transactions to a
uniform-random memory HA. One transaction is the full four-phase handshake

    REQ (core->HA) -> DBIDResp (HA->core) -> WriteData xW (core->HA)
    -> Comp (HA->core)

so the run instantiates three CHI VCs (REQ / RSP / DAT) and the directed-hop
bandwidth cap is 80 x 3 = 240 flit/cycle.

The ring is bufferless with strict in-ring priority: `_launch` never stalls a
flit already on the ring, it only occupies the outgoing slot, and `_can_board`
refuses a local injection whenever that slot is taken or reserved. The
question this driver answers is whether that priority makes the achieved
write bandwidth depend on where a core sits on the ring, and whether a source
rate controller can repair it.

Schemes
-------
S0   baseline: RR inject + I-tag / E-tag, no flow control.
S1   the 4-part congestion-control spec (detect / propagate / feed back /
     AIMD on an integer per-window injection budget).
S15  max-min fair share over the same bus plus bounded slot reservation.

Writes results/ring2_write_fair.json; plotting lives in
gen_ring2_write_report.py so the plots can be redrawn without re-simulating.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Sequence

_UTILS = Path(__file__).resolve().parent
if str(_UTILS) not in sys.path:
    sys.path.insert(0, str(_UTILS))

from rg_ring2_base import Ring2BaseParams, Ring2BaseSim
from rg_ring2_topo import (
    CHI_VCS_WRITE, N_NODES, Ring2Topology, Txn, build_hot_write,
    build_uniform_write, cores, has, hop_count, shortest_dir, write_bounds,
    write_paths_for_txns,
)

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results" / "ring2_write_fair.json"
K_PER_CORE = 2_000
W_FLITS = 4
BIN_W = 128
T_MAX = 4_000_000
HOT_HAS = (9, 11)

# Flits per VC per transaction: REQ 1, RSP 2 (DBIDResp + Comp), DAT W.
M_REQ, M_RSP = 1, 2


# ---------------------------------------------------------------------------
# Fairness math
# ---------------------------------------------------------------------------

def jain(xs: Sequence[float]) -> float:
    """(sum x)^2 / (n * sum x^2). 1.0 = perfectly equal, 1/n = one winner."""
    xs = [float(x) for x in xs]
    if not xs:
        return 0.0
    s2 = sum(x * x for x in xs)
    if s2 <= 0:
        return 0.0
    return (sum(xs) ** 2) / (len(xs) * s2)


def cov(xs: Sequence[float]) -> float:
    xs = [float(x) for x in xs]
    if len(xs) < 2:
        return 0.0
    mu = sum(xs) / len(xs)
    if mu == 0:
        return 0.0
    var = sum((x - mu) ** 2 for x in xs) / len(xs)
    return math.sqrt(var) / mu


def pearson(xs: Sequence[float], ys: Sequence[float]) -> float:
    n = min(len(xs), len(ys))
    if n < 2:
        return 0.0
    mx = sum(xs[:n]) / n
    my = sum(ys[:n]) / n
    dx = [x - mx for x in xs[:n]]
    dy = [y - my for y in ys[:n]]
    den = math.sqrt(sum(a * a for a in dx) * sum(b * b for b in dy))
    if den == 0:
        return 0.0
    return sum(a * b for a, b in zip(dx, dy)) / den


def _ranks(xs: Sequence[float]) -> list[float]:
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    out = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        avg = (i + j) / 2 + 1
        for k in range(i, j + 1):
            out[order[k]] = avg
        i = j + 1
    return out


def spearman(xs: Sequence[float], ys: Sequence[float]) -> float:
    """Rank correlation: the bandwidth / pass-through relation is monotone
    but hyperbolic, so Pearson understates how tight it is."""
    return pearson(_ranks(list(xs)), _ranks(list(ys)))


def fairness_stats(inject_times: dict[int, list[int]], makespan: int,
                   n_flits_per_core: int) -> dict[str, Any]:
    """Per-core write bandwidth, measured while every core still has work.

    In a closed batch every core eventually injects the same `K*W` WriteData
    flits, so end-of-run counts are equal by construction and say nothing.
    The contention window is what matters: `t_fair` is the cycle the first
    core runs out of work, and up to that point all ten cores are competing
    for the same hops. Bandwidth over `[0, t_fair]` is therefore the honest
    per-core share. `finish` (each core's own last WriteData board) is
    reported alongside as the end-to-end view.
    """
    cs = sorted(inject_times)
    if not cs:
        return {}
    finish = {c: (max(ts) if ts else 0) for c, ts in inject_times.items()}
    t_fair = min(finish.values()) or 1
    got = {c: sum(1 for t in inject_times[c] if t <= t_fair) for c in cs}
    bw = {c: got[c] / t_fair for c in cs}
    vals = [bw[c] for c in cs]
    lo, hi = min(vals), max(vals)
    # Full-run view: a starved core simply takes longer to push its own quota.
    bw_run = {c: (n_flits_per_core / finish[c] if finish[c] else 0.0)
              for c in cs}
    vals_run = [bw_run[c] for c in cs]
    return {
        "t_fair": t_fair,
        "bw_by_core": {str(c): round(bw[c], 5) for c in cs},
        "got_by_core": {str(c): got[c] for c in cs},
        "finish_by_core": {str(c): finish[c] for c in cs},
        "bw_run_by_core": {str(c): round(bw_run[c], 5) for c in cs},
        "jain": round(jain(vals), 5),
        "max_min": round(hi / lo, 4) if lo > 0 else float("inf"),
        "cov": round(cov(vals), 5),
        "bw_min": round(lo, 5), "bw_max": round(hi, 5),
        "bw_mean": round(sum(vals) / len(vals), 5),
        "jain_run": round(jain(vals_run), 5),
        "max_min_run": (round(max(vals_run) / min(vals_run), 4)
                        if min(vals_run) > 0 else float("inf")),
        "throughput": round(len(cs) * n_flits_per_core / max(1, makespan), 4),
    }


# ---------------------------------------------------------------------------
# Analytic contention: who has to ride past whom
# ---------------------------------------------------------------------------

def pass_through_load(topo: Ring2Topology, txns: Sequence[Txn]
                      ) -> dict[str, Any]:
    """Flits that board elsewhere and then cross a node's outgoing hops.

    Walk each transaction's three legs along its shortest path. The first
    edge of a leg is the source's own on-ramp; every later edge is
    pass-through traffic, which under strict in-ring priority occupies an
    intermediate node's outgoing slot before that node can inject anything of
    its own. Planes are collapsed and divided by `n_planes`, because plane
    choice is a policy, not a physical fact.

    A core does not care equally about both of its outgoing hops: it only
    contends on the ones its own traffic routes over. `pt_eff` therefore
    weights each direction by the share of the core's own WriteData that
    leaves that way, which is the load the core actually has to fight.
    """
    n_planes = max(1, topo.n_planes)
    per_vc: dict[str, dict[tuple[int, int], float]] = {
        vc: defaultdict(float) for vc in ("req", "rsp", "dat")}
    own: dict[tuple[int, int], float] = defaultdict(float)
    for t in txns:
        legs = (("req", t.core, t.ha, M_REQ),
                ("rsp", t.ha, t.core, M_RSP),
                ("dat", t.core, t.ha, t.m_wdata))
        for vc, src, dst, m in legs:
            if src == dst or m <= 0:
                continue
            d = shortest_dir(src, dst, topo.n)
            if vc == "dat":
                own[(src, d)] += m
            node = src
            for i in range(hop_count(src, dst, d, topo.n)):
                if i:                       # i == 0 is the source's own board
                    per_vc[vc][(node, d)] += m / n_planes
                node = (node + d) % topo.n

    by_vc = {vc: {n: round(tbl[(n, 1)] + tbl[(n, -1)], 2)
                  for n in range(topo.n)}
             for vc, tbl in per_vc.items()}
    by_vc["all"] = {n: round(sum(by_vc[v][n] for v in ("req", "rsp", "dat")),
                             2)
                    for n in range(topo.n)}
    # Injectors come from the workload: an odd node that is not memory can be
    # a core, so index parity is not the right thing to iterate over.
    srcs = sorted({t.core for t in txns}) or cores(topo.n)
    eff: dict[int, float] = {}
    for c in srcs:
        tot = own[(c, 1)] + own[(c, -1)]
        if tot <= 0:
            eff[c] = 0.0
            continue
        eff[c] = round(sum(
            (own[(c, d)] / tot) * sum(per_vc[v][(c, d)]
                                      for v in ("req", "rsp", "dat"))
            for d in (1, -1)), 2)
    return {
        "by_vc": by_vc,
        "eff": eff,
        "dat_cw": {n: round(per_vc["dat"][(n, 1)], 2) for n in range(topo.n)},
        "dat_ccw": {n: round(per_vc["dat"][(n, -1)], 2)
                    for n in range(topo.n)},
        "own_cw": {c: own[(c, 1)] for c in srcs},
        "own_ccw": {c: own[(c, -1)] for c in srcs},
    }


def hop_latency_by_core(topo: Ring2Topology) -> dict[int, float]:
    """Mean latency of each node's two outgoing links."""
    return {c: (topo.hop_lat_from(c, 1) + topo.hop_lat_from(c, -1)) / 2
            for c in range(topo.n)}


# ---------------------------------------------------------------------------
# Run one scheme
# ---------------------------------------------------------------------------

CORE_OUTSTANDING_WR = 128     # write study only; the read study keeps 100

# Every scheme rides the same fabric: shortest-path routing, both planes
# available and picked by occupancy, one board and one leave port per
# (node, plane), per-VC boarding queues.
FABRIC = dict(plane_sel="least_occupied", per_vc_srcq=True,
              core_outstanding=CORE_OUTSTANDING_WR)


def base_params() -> Ring2BaseParams:
    """Shared datapath settings. Every write scheme rides the same fabric."""
    return Ring2BaseParams(**FABRIC)


def make_sim(scheme: str, topo: Ring2Topology, *, seed: int,
             cfg: dict[str, Any] | None = None) -> Ring2BaseSim:
    cfg = cfg or {}
    if scheme == "S0":
        return Ring2BaseSim(topo, base_params(), seed=seed)
    if scheme == "S16":
        from rg_ring2_grant import Ring2GrantParams, Ring2GrantSim
        return Ring2GrantSim(topo, Ring2GrantParams(**FABRIC, **cfg),
                             seed=seed)
    from rg_ring2_fc import Ring2FcParams, Ring2FcSim
    p = Ring2FcParams(**FABRIC,
                      mode="s1" if scheme == "S1" else "s15", **cfg)
    return Ring2FcSim(topo, p, seed=seed)


def run_scheme(scheme: str, topo: Ring2Topology, txns: Sequence[Txn], *,
               seed: int = 0, cfg: dict[str, Any] | None = None,
               t_max: int = T_MAX, quiet: bool = False) -> dict[str, Any]:
    t0 = time.perf_counter()
    sim = make_sim(scheme, topo, seed=seed, cfg=cfg)
    sim.offer_batch(txns)
    last_count, last_progress = 0, 0
    while sim.t < t_max and not sim.done():
        sim.step()
        if sim.st["n_delivered_flits"] != last_count:
            last_count = sim.st["n_delivered_flits"]
            last_progress = sim.t
        elif sim.t - last_progress > 100_000:
            break
    r = sim.summary()
    r["scheme"] = scheme
    r["stall_detected"] = not r["completed"]
    r["hop_starts"] = list(sim.hop_starts)
    r["cfg"] = dict(cfg or {})
    r["wall_secs"] = round(time.perf_counter() - t0, 1)
    extra = getattr(sim, "fc_summary", None)
    if callable(extra):
        r["fc"] = extra()
    if not quiet:
        print(f"    {scheme} mk={r['makespan']} ok={r['completed']} "
              f"{r['wall_secs']}s", flush=True)
    return r


def digest(r: dict[str, Any], *, flits_per_core: int, bin_w: int
           ) -> dict[str, Any]:
    """Trim a raw run down to what the report needs."""
    inj = {int(c): v for c, v in (r.get("wr_inject_by_core") or {}).items()}
    fair = fairness_stats(inj, r.get("makespan") or 1, flits_per_core)
    t_max = r.get("makespan") or 1
    binned = {}
    for c, ts in sorted(inj.items()):
        xs, ys = bin_rate(ts, t_max, bin_w)
        binned[str(c)] = {"t": xs, "rate": [round(y, 4) for y in ys],
                          "n": len(ts)}
    hop_xs, hop_ys = bin_rate(r.get("hop_starts") or [], t_max, bin_w)
    out = {
        "scheme": r["scheme"], "cfg": r.get("cfg", {}),
        "makespan": r.get("makespan"), "completed": r.get("completed"),
        "n_delivered_flits": r.get("n_delivered_flits"),
        "n_txn_done": r.get("n_txn_done"), "n_txn_target": r.get("n_txn_target"),
        "n_board_fail": r.get("n_board_fail", 0),
        "n_deflections": r.get("n_deflections", 0),
        "n_inring_blocked": r.get("n_inring_blocked", 0),
        "max_inring_hold": r.get("max_inring_hold", 0),
        "n_itag_raised": r.get("n_itag_raised", 0),
        "n_etag_raised": r.get("n_etag_raised", 0),
        "n_outst_wait": r.get("n_outst_wait", 0),
        "max_core_outstanding": r.get("max_core_outstanding", 0),
        "max_srcq": r.get("max_srcq"), "max_ejectq": r.get("max_ejectq"),
        "lat_p50": r.get("lat_p50"), "lat_p99": r.get("lat_p99"),
        "lat_max": r.get("lat_max"),
        "wall_secs": r.get("wall_secs"),
        "fairness": fair,
        "wr_binned": binned,
        "board_fail_by_src": r.get("board_fail_by_src", {}),
        "inj_by_hop": r.get("inj_by_hop", {}),
        "wr_recv_by_ha": {str(h): len(v)
                          for h, v in (r.get("wr_recv_by_ha") or {}).items()},
        "hop_bw": {"t": hop_xs, "rate": [round(y, 3) for y in hop_ys],
                   "n_hops": len(r.get("hop_starts") or [])},
    }
    if "fc" in r:
        out["fc"] = r["fc"]
    return out


def bin_rate(times: Sequence[int], t_max: int, bin_w: int = BIN_W
             ) -> tuple[list[int], list[float]]:
    nbin = max(1, (t_max + bin_w) // bin_w)
    rate = [0.0] * nbin
    for t in times:
        rate[min(max(int(t), 0) // bin_w, nbin - 1)] += 1.0 / bin_w
    return [i * bin_w for i in range(nbin)], rate


# ---------------------------------------------------------------------------
# Root-cause table
# ---------------------------------------------------------------------------

def root_cause(topo: Ring2Topology, txns: Sequence[Txn],
               s0: dict[str, Any]) -> dict[str, Any]:
    pt = pass_through_load(topo, txns)
    lat = hop_latency_by_core(topo)
    bw = {int(c): v for c, v in s0["fairness"]["bw_by_core"].items()}
    # Which nodes inject is a property of the workload, not of index parity:
    # once some odd node stops being memory it can be a core instead.
    cs = sorted(bw) or cores(topo.n)
    fails = s0.get("board_fail_by_src", {})

    def cause(c: int, key: str) -> int:
        return int(fails.get(f"{c}:dat", {}).get(key, 0))

    rows = []
    for c in cs:
        ok = cause(c, "ok")
        busy, itag = cause(c, "hop_busy"), cause(c, "itag")
        budget, outst = cause(c, "fc_budget"), cause(c, "outstanding")
        tries = ok + busy + itag + budget + outst
        rows.append({
            "core": c,
            "bw": bw.get(c, 0.0),
            "pt_eff": pt["eff"][c],
            "pt_dat": pt["by_vc"]["dat"][c],
            "pt_all": pt["by_vc"]["all"][c],
            "lat_out": lat[c],
            "ok": ok, "hop_busy": busy, "itag": itag,
            "fc_budget": budget, "outstanding": outst,
            "succ_rate": round(ok / tries, 4) if tries else 0.0,
        })
    bws = [r["bw"] for r in rows]
    # How many memory nodes sit right next to each core. A write to a
    # neighbour occupies one segment and then leaves the ring; everything
    # else has to ride deeper, blocking more nodes and being blocked more.
    # Mean distance to memory cannot see this -- remove an antipodal pair of
    # memory nodes and every core's mean distance is still identical.
    mem = sorted({t.ha for t in txns})
    adj = [sum(1 for h in mem
               if min((h - c) % topo.n, (c - h) % topo.n) == 1) for c in cs]
    mean_hop = [sum(min((h - c) % topo.n, (c - h) % topo.n)
                    for h in mem) / max(1, len(mem)) for c in cs]
    for r, a, mh in zip(rows, adj, mean_hop):
        r["adj_mem"] = a
        r["mean_hop_to_mem"] = round(mh, 2)
    hops = []
    for key, v in (s0.get("inj_by_hop") or {}).items():
        tot = v["ok"] + v["fail"]
        if tot:
            hops.append({"hop": key, "lat": v["lat"], "ok": v["ok"],
                         "fail": v["fail"],
                         "succ": round(v["ok"] / tot, 4)})
    return {
        "rows": rows,
        "pass_through": {
            "by_vc": {vc: {str(k): v for k, v in d.items()}
                      for vc, d in pt["by_vc"].items()},
            "eff": {str(k): v for k, v in pt["eff"].items()},
            "dat_cw": {str(k): v for k, v in pt["dat_cw"].items()},
            "dat_ccw": {str(k): v for k, v in pt["dat_ccw"].items()},
        },
        "corr_bw_pt_eff": round(pearson(bws, [r["pt_eff"] for r in rows]), 4),
        "rank_bw_pt_eff": round(spearman(bws, [r["pt_eff"] for r in rows]), 4),
        "rank_bw_succ": round(spearman(bws, [r["succ_rate"] for r in rows]), 4),
        "corr_bw_pt_dat": round(pearson(bws, [r["pt_dat"] for r in rows]), 4),
        "corr_bw_pt_all": round(pearson(bws, [r["pt_all"] for r in rows]), 4),
        "corr_bw_lat": round(pearson(bws, [r["lat_out"] for r in rows]), 4),
        "corr_bw_succ": round(pearson(bws, [r["succ_rate"] for r in rows]), 4),
        "corr_bw_adjmem": round(pearson(bws, [float(a) for a in adj]), 4),
        "rank_bw_adjmem": round(spearman(bws, [float(a) for a in adj]), 4),
        "corr_bw_meanhop": round(pearson(bws, mean_hop), 4),
        "mem": mem,
        "hops": sorted(hops, key=lambda h: h["lat"]),
        "corr_succ_lat": round(pearson([h["succ"] for h in hops],
                                       [float(h["lat"]) for h in hops]), 4),
    }


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

# Node 9 and node 19 are not memory. Two readings of that, both measured,
# because they give materially different answers: either those two nodes are
# simply not write destinations (the ring still has 10 cores), or they are
# compute like every other non-memory node (12 cores).
# Nodes 9 and 19 are neither memory nor AI core. They still sit on the ring
# and still forward, but they never source or sink a write, so the memory set
# is the eight remaining odd nodes and the ring is no longer rotationally
# symmetric about the memory placement.
NON_TERMINAL = (9, 19)
MEM_NODES = tuple(h for h in has(N_NODES) if h not in NON_TERMINAL)
CORE_NODES = tuple(cores(N_NODES))


def build_pattern(name: str, *, k: int, W: int, seed: int) -> list[Txn]:
    """The one workload under study: 10 AI cores writing uniformly to 8 mem."""
    if name == "uniform":
        return build_uniform_write(k=k, m_wdata=W, seed=seed, mem=MEM_NODES,
                                   core_set=CORE_NODES)
    raise ValueError(f"unknown pattern {name}")


def seed_sweep(pattern: str, topo: Ring2Topology, *, k: int, W: int,
               seeds: Sequence[int], schemes: Sequence[str]
               ) -> list[dict[str, Any]]:
    """Re-run the headline schemes on other seeds.

    The reservation mechanism is discrete, so a single seed can flatter a
    tuning point. Report the spread instead.
    """
    rows: list[dict[str, Any]] = []
    for sd in seeds:
        txns = build_pattern(pattern, k=k, W=W, seed=sd)
        row: dict[str, Any] = {"seed": sd}
        thr0 = None
        for scheme in schemes:
            r = run_scheme(scheme, topo, txns, seed=sd, quiet=True)
            f = fairness_stats(r["wr_inject_by_core"], r["makespan"], k * W)
            thr0 = f["throughput"] if scheme == "S0" else thr0
            row[scheme] = {
                "jain": f["jain"], "max_min": f["max_min"],
                "throughput": f["throughput"],
                "thr_delta_pct": (
                    round(100.0 * (f["throughput"] - thr0) / thr0, 2)
                    if thr0 else 0.0),
            }
        rows.append(row)
        print(f"    seed {sd}: " + "  ".join(
            f"{s} jain={row[s]['jain']} mm={row[s]['max_min']} "
            f"({row[s]['thr_delta_pct']:+.2f}%)" for s in schemes), flush=True)
    return rows


def run_pattern(pattern: str, topo: Ring2Topology, *, k: int, W: int,
                seed: int, bin_w: int, schemes: Sequence[str],
                sweep_s1: bool, seeds: Sequence[int] = ()) -> dict[str, Any]:
    txns = build_pattern(pattern, k=k, W=W, seed=seed)
    flits_per_core = k * W
    vp = write_paths_for_txns(topo, txns, strategy="least_occupied")
    bounds = write_bounds(topo, vp, m_req=M_REQ, m_rsp=M_RSP, m_wdata=W)
    print(f"\n[{pattern}] K={k} W={W} txns={len(txns)} "
          f"wdata/core={flits_per_core} bound={bounds['bound']}", flush=True)

    runs: dict[str, dict[str, Any]] = {}
    for scheme in schemes:
        print(f"  running {scheme} ...", flush=True)
        r = run_scheme(scheme, topo, txns, seed=seed)
        runs[scheme] = digest(r, flits_per_core=flits_per_core, bin_w=bin_w)

    sweep_oc: list[dict[str, Any]] = []
    if sweep_s1 and "S16" in schemes:
        print("  sweeping S16 overcommit ...", flush=True)
        for oc in (2, 4, 8, 16, 24, 32, 48, 64, 128):
            r = run_scheme("S16", topo, txns, seed=seed,
                           cfg={"overcommit": oc}, quiet=True)
            d = digest(r, flits_per_core=flits_per_core, bin_w=bin_w)
            f = d["fairness"]
            sweep_oc.append({
                "overcommit": oc, "makespan": d["makespan"],
                "jain": f["jain"], "max_min": f["max_min"],
                "cov": f["cov"], "throughput": f["throughput"],
                "peak_grants": (d.get("fc") or {}).get("peak_grants"),
                "grant_delay_mean": (d.get("fc") or {}).get(
                    "grant_delay_mean"),
                "lat_p99": d.get("lat_p99"),
            })
            print(f"    sweep S16 oc={oc} mk={d['makespan']} "
                  f"jain={f['jain']} max/min={f['max_min']} "
                  f"thr={f['throughput']}", flush=True)
        # Grant-on-arrival is exactly the baseline policy, so it also
        # measures the completer buffering the baseline silently needs.
        r = run_scheme("S16", topo, txns, seed=seed,
                       cfg={"overcommit": 10 ** 9}, quiet=True)
        d = digest(r, flits_per_core=flits_per_core, bin_w=bin_w)
        sweep_oc.append({
            "overcommit": None, "makespan": d["makespan"],
            "jain": d["fairness"]["jain"],
            "max_min": d["fairness"]["max_min"],
            "cov": d["fairness"]["cov"],
            "throughput": d["fairness"]["throughput"],
            "peak_grants": (d.get("fc") or {}).get("peak_grants"),
            "grant_delay_mean": (d.get("fc") or {}).get("grant_delay_mean"),
            "lat_p99": d.get("lat_p99"),
        })
        print(f"    sweep S16 oc=inf (=S0 policy) "
              f"peak_grants={(d.get('fc') or {}).get('peak_grants')}",
              flush=True)

    ablate: list[dict[str, Any]] = []
    if sweep_s1 and "S16" in schemes:
        for tag, cfg in (("least_served + eager", {}),
                         ("round_robin", {"policy": "round_robin"}),
                         ("no eager grant", {"eager": False})):
            r = run_scheme("S16", topo, txns, seed=seed, cfg=cfg, quiet=True)
            d = digest(r, flits_per_core=flits_per_core, bin_w=bin_w)
            f = d["fairness"]
            ablate.append({
                "variant": tag, "makespan": d["makespan"],
                "jain": f["jain"], "max_min": f["max_min"],
                "throughput": f["throughput"],
                "grant_delay_mean": (d.get("fc") or {}).get(
                    "grant_delay_mean"),
            })
            print(f"    ablate S16 {tag}: jain={f['jain']} "
                  f"max/min={f['max_min']} thr={f['throughput']}", flush=True)

    sweep: list[dict[str, Any]] = []
    if sweep_s1:
        for window in (64, 128):
            for band in ("spec", "harsh", "gentle"):
                cfg = {"window": window, "band": band}
                r = run_scheme("S1", topo, txns, seed=seed, cfg=cfg,
                               quiet=True)
                d = digest(r, flits_per_core=flits_per_core, bin_w=bin_w)
                f = d["fairness"]
                sweep.append({
                    "window": window, "band": band,
                    "makespan": d["makespan"], "jain": f["jain"],
                    "max_min": f["max_min"], "cov": f["cov"],
                    "throughput": f["throughput"],
                    "bw_min": f["bw_min"], "bw_max": f["bw_max"],
                })
                print(f"    sweep S1 w={window} band={band} "
                      f"mk={d['makespan']} jain={f['jain']} "
                      f"max/min={f['max_min']}", flush=True)

    seeds_out: list[dict[str, Any]] = []
    if seeds:
        print("  seed robustness ...", flush=True)
        seeds_out = seed_sweep(pattern, topo, k=k, W=W, seeds=seeds,
                               schemes=[s for s in ("S0", "S15", "S16")
                                        if s in schemes])

    out: dict[str, Any] = {
        "pattern": pattern, "K": k, "W": W,
        "flits_per_core": flits_per_core, "n_txn": len(txns),
        "mem": sorted({t.ha for t in txns}),
        "core_set": sorted({t.core for t in txns}),
        "bounds": bounds, "schemes": runs, "sweep": sweep,
        "sweep_oc": sweep_oc, "ablate": ablate,
        "seed_sweep": seeds_out,
    }
    if "S0" in runs:
        out["root_cause"] = root_cause(topo, txns, runs["S0"])
    return out


def _report(pat: dict[str, Any]) -> None:
    print(f"\n[{pat['pattern']}]  bound={pat['bounds']['bound']}")
    print(f"{'scheme':6} {'mk':>8} {'ok':>3} {'jain':>8} {'max/min':>8} "
          f"{'cov':>8} {'bwmin':>8} {'bwmax':>8} {'thr':>7} {'fail':>9}")
    for name, d in pat["schemes"].items():
        f = d["fairness"]
        print(f"{name:6} {d['makespan']:>8} {int(bool(d['completed'])):>3} "
              f"{f['jain']:>8} {f['max_min']:>8} {f['cov']:>8} "
              f"{f['bw_min']:>8} {f['bw_max']:>8} {f['throughput']:>7} "
              f"{d['n_board_fail']:>9}")
    rc = pat.get("root_cause")
    if rc:
        print(f"  bw vs effective pass-through: r={rc['corr_bw_pt_eff']} "
              f"rank={rc['rank_bw_pt_eff']}   "
              f"bw vs out-link λ: r={rc['corr_bw_lat']}   "
              f"inject-success vs λ: r={rc['corr_succ_lat']}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--k", type=int, default=K_PER_CORE)
    ap.add_argument("--W", type=int, default=W_FLITS)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--quick", action="store_true", help="K=200 smoke run")
    ap.add_argument("--schemes", default="S0,S1,S15,S16")
    ap.add_argument("--patterns", default="uniform")
    ap.add_argument("--seeds", default="",
                    help="extra seeds for the S0/S15 robustness sweep")
    ap.add_argument("--sweep", action="store_true",
                    help="also sweep the S1 window / alpha-beta bands")
    ap.add_argument("--out", default=str(OUT))
    args = ap.parse_args()

    k = 200 if args.quick else args.k
    bin_w = 32 if args.quick else BIN_W
    schemes = [s for s in args.schemes.split(",") if s]
    patterns = [s for s in args.patterns.split(",") if s]

    topo = Ring2Topology(vcs=CHI_VCS_WRITE)
    t0 = time.perf_counter()
    out_pats = {
        p: run_pattern(p, topo, k=k, W=args.W, seed=args.seed, bin_w=bin_w,
                       schemes=schemes, sweep_s1=args.sweep,
                       seeds=[int(x) for x in args.seeds.split(",") if x])
        for p in patterns
    }
    bp = base_params()
    payload = {
        "meta": {
            "K": k, "W": args.W, "seed": args.seed, "bin_w": bin_w,
            "patterns": patterns, "schemes": schemes,
            "plane_sel": bp.plane_sel, "routing": "shortest_path",
            "mem_nodes": list(MEM_NODES), "core_nodes": list(CORE_NODES),
            "non_terminal": list(NON_TERMINAL),
            "n_planes": topo.n_planes, "sigma": topo.sigma,
            "board_ports": topo.board_ports,
            "leave_ports": topo.leave_ports,
            "t_xfer": bp.t_xfer, "eject_bw": bp.eject_bw,
            "vcs": list(topo.vcs), "n_vc": topo.n_vc,
            "hop_bw_cap": topo.hop_bw_cap,
            "link_lats": list(topo.link_lats),
            "inj_depth": bp.inj_depth, "eject_depth": bp.eject_depth,
            "t_inj": bp.t_inj, "per_vc_srcq": bp.per_vc_srcq,
            "core_outstanding": bp.core_outstanding,
            "m_req": M_REQ, "m_rsp": M_RSP,
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        },
        "patterns": out_pats,
        "wall_secs": round(time.perf_counter() - t0, 1),
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=1))
    print(f"\nwrote {out}  {payload['wall_secs']}s")
    for pat in out_pats.values():
        _report(pat)


if __name__ == "__main__":
    main()
