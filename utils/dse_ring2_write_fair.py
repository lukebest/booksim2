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
    CHI_VCS_WRITE, Ring2Topology, Txn, build_hot_write, build_uniform_write,
    cores, hop_count, shortest_dir, write_bounds, write_paths_for_txns,
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
    eff: dict[int, float] = {}
    for c in cores(topo.n):
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
        "own_cw": {c: own[(c, 1)] for c in cores(topo.n)},
        "own_ccw": {c: own[(c, -1)] for c in cores(topo.n)},
    }


def hop_latency_by_core(topo: Ring2Topology) -> dict[int, float]:
    """Mean latency of a core's two outgoing links."""
    return {c: (topo.hop_lat_from(c, 1) + topo.hop_lat_from(c, -1)) / 2
            for c in cores(topo.n)}


# ---------------------------------------------------------------------------
# Run one scheme
# ---------------------------------------------------------------------------

def base_params() -> Ring2BaseParams:
    """Shared datapath settings. Every write scheme rides the same fabric."""
    return Ring2BaseParams(plane_sel="least_occupied", per_vc_srcq=True)


def make_sim(scheme: str, topo: Ring2Topology, *, seed: int,
             cfg: dict[str, Any] | None = None) -> Ring2BaseSim:
    cfg = cfg or {}
    if scheme == "S0":
        return Ring2BaseSim(topo, base_params(), seed=seed)
    from rg_ring2_fc import Ring2FcParams, Ring2FcSim
    p = Ring2FcParams(plane_sel="least_occupied", per_vc_srcq=True,
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
    cs = cores(topo.n)
    bw = {int(c): v for c, v in s0["fairness"]["bw_by_core"].items()}
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
        "hops": sorted(hops, key=lambda h: h["lat"]),
        "corr_succ_lat": round(pearson([h["succ"] for h in hops],
                                       [float(h["lat"]) for h in hops]), 4),
    }


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def build_pattern(name: str, *, k: int, W: int, seed: int) -> list[Txn]:
    if name == "uniform":
        return build_uniform_write(k=k, m_wdata=W, seed=seed)
    if name == "cluster":
        return build_hot_write(k=k, m_wdata=W, hot_has=HOT_HAS)
    raise ValueError(f"unknown pattern {name}")


def run_pattern(pattern: str, topo: Ring2Topology, *, k: int, W: int,
                seed: int, bin_w: int, schemes: Sequence[str],
                sweep_s1: bool) -> dict[str, Any]:
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

    out: dict[str, Any] = {
        "pattern": pattern, "K": k, "W": W,
        "flits_per_core": flits_per_core, "n_txn": len(txns),
        "hot_has": list(HOT_HAS) if pattern == "cluster" else None,
        "bounds": bounds, "schemes": runs, "sweep": sweep,
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
    ap.add_argument("--schemes", default="S0,S1,S15")
    ap.add_argument("--patterns", default="uniform,cluster")
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
                       schemes=schemes, sweep_s1=args.sweep)
        for p in patterns
    }
    bp = base_params()
    payload = {
        "meta": {
            "K": k, "W": args.W, "seed": args.seed, "bin_w": bin_w,
            "patterns": patterns, "schemes": schemes,
            "hot_has": list(HOT_HAS), "plane_sel": bp.plane_sel,
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
