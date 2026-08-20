#!/usr/bin/env python3
"""Fifteen-scheme makespan comparison on the 20-node dual-plane ring.

Schemes
-------
Common datapath (all fifteen): per-link hop delays (1–4 cycles), 8-deep boarding queue per
(node, plane), point-to-point credit FC + I-tag + E-tag, and a 512
outstanding-read cap per AI core.
S0  ring2_base   RR inject on that datapath, no source rate control
S1  ring2_aimd   S0 + piggybacked failure counts + AIMD token bucket
S2  ring2_rg     same datapath + request-grant (default islip, interval, arc)
S3  ring2_pop    same datapath + read-request-as-POP (HA schedules resps)
S4  ring2_dist   same datapath + kind-aware leave (resp at core, req at HA)
S5  ring2_ej     dest leave-slot reservation, node-id dest clash
S6  ring2_ej     S5 + oldest-first among same-cycle dest candidates
S7  ring2_ej     S6 + hop_bounce (late-bind plane if first hop is busy)
S8  ring2_ej     S7 + always late-bind plane (hop+dest, occupancy tie)
S9  ring2_ej     S8 + late_dir slack (other ring dir if hop busy, ≤+2)
S10 ring2_ej     S9 + late_dir only for responses
S11 ring2_ej     S10 + same-cycle first-hop mutex (resp, oldest)
S12 ring2_ej     S11 + dest-then-hop request-grant (I=1, leftover dest)
S13 ring2_ej     S12 + hop-grant prefers shorter remaining path
S14 ring2_ej     S13 + HA sibling plane yield on same-node hop clash

Workloads: allpairs (deterministic 10x10 x m) and uniform (K per core,
uniform HA, multi-seed). Makespan is the cycle the last response flit is
drained at the requesting core. Analytic bounds ride along as a floor.

Writes results/ring2_20node.json.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

_UTILS = Path(__file__).resolve().parent
if str(_UTILS) not in sys.path:
    sys.path.insert(0, str(_UTILS))

from rg_ring2_aimd import run_batch as run_aimd
from rg_ring2_base import Ring2BaseParams, run_batch as run_base
from rg_ring2_dist import (
    Ring2DistParams, run_batch as run_dist, s5_params, s6_params,
    s7_params, s8_params, s9_params, s10_params, s11_params, s12_params,
    s13_params, s14_params,
)
from rg_ring2_pop import run_batch as run_pop
from rg_ring2_rg import RGConfig, run_batch as run_rg
from rg_ring2_topo import (
    Ring2Topology, build_allpairs, build_uniform, paths_for_txns,
)

OUT = Path(__file__).resolve().parents[1] / "results" / "ring2_20node.json"

PLANE_SELS = ("static_hash", "rr_per_pkt", "least_occupied", "req_resp_split")
SCHEMES = ("S0", "S1", "S2", "S3", "S4", "S5", "S6", "S7", "S8", "S9",
           "S10", "S11", "S12", "S13", "S14")


def _bounds(topo: Ring2Topology, txns, plane_sel: str, m_resp: int) -> dict:
    rp, sp = paths_for_txns(topo, txns, strategy=plane_sel)
    return topo.analytic_bounds(rp, sp, m_req=1, m_resp=m_resp)


def _row(scheme: str, pattern: str, **kw) -> dict[str, Any]:
    return {"scheme": scheme, "pattern": pattern, **kw}


def run_one(scheme: str, topo: Ring2Topology, txns, *,
            plane_sel: str, params: Ring2BaseParams,
            rg_cfg: RGConfig | None = None,
            seed: int = 0) -> dict[str, Any]:
    if scheme == "S0":
        r = run_base(topo, txns, params=params, seed=seed)
    elif scheme == "S1":
        r = run_aimd(topo, txns, params=params, seed=seed)
    elif scheme == "S2":
        cfg = rg_cfg or RGConfig(plane_sel=plane_sel, seed=seed)
        r = run_rg(topo, txns, cfg=cfg)
    elif scheme == "S3":
        r = run_pop(topo, txns, params=params, seed=seed)
    elif scheme == "S4":
        dp = Ring2DistParams(plane_sel=params.plane_sel,
                             eject_depth=params.eject_depth,
                             core_outstanding=params.core_outstanding,
                             leave_useful=True)
        r = run_dist(topo, txns, params=dp, seed=seed)
    elif scheme == "S5":
        dp = s5_params(plane_sel=params.plane_sel,
                       eject_depth=params.eject_depth,
                       core_outstanding=params.core_outstanding)
        r = run_dist(topo, txns, params=dp, seed=seed)
    elif scheme == "S6":
        dp = s6_params(plane_sel=params.plane_sel,
                       eject_depth=params.eject_depth,
                       core_outstanding=params.core_outstanding)
        r = run_dist(topo, txns, params=dp, seed=seed)
    elif scheme == "S7":
        dp = s7_params(plane_sel=params.plane_sel,
                       eject_depth=params.eject_depth,
                       core_outstanding=params.core_outstanding)
        r = run_dist(topo, txns, params=dp, seed=seed)
    elif scheme == "S8":
        dp = s8_params(plane_sel=params.plane_sel,
                       eject_depth=params.eject_depth,
                       core_outstanding=params.core_outstanding)
        r = run_dist(topo, txns, params=dp, seed=seed)
    elif scheme == "S9":
        dp = s9_params(plane_sel=params.plane_sel,
                       eject_depth=params.eject_depth,
                       core_outstanding=params.core_outstanding)
        r = run_dist(topo, txns, params=dp, seed=seed)
    elif scheme == "S10":
        dp = s10_params(plane_sel=params.plane_sel,
                        eject_depth=params.eject_depth,
                        core_outstanding=params.core_outstanding)
        r = run_dist(topo, txns, params=dp, seed=seed)
    elif scheme == "S11":
        dp = s11_params(plane_sel=params.plane_sel,
                        eject_depth=params.eject_depth,
                        core_outstanding=params.core_outstanding)
        r = run_dist(topo, txns, params=dp, seed=seed)
    elif scheme == "S12":
        dp = s12_params(plane_sel=params.plane_sel,
                        eject_depth=params.eject_depth,
                        core_outstanding=params.core_outstanding)
        r = run_dist(topo, txns, params=dp, seed=seed)
    elif scheme == "S13":
        dp = s13_params(plane_sel=params.plane_sel,
                        eject_depth=params.eject_depth,
                        core_outstanding=params.core_outstanding)
        r = run_dist(topo, txns, params=dp, seed=seed)
    elif scheme == "S14":
        dp = s14_params(plane_sel=params.plane_sel,
                        eject_depth=params.eject_depth,
                        core_outstanding=params.core_outstanding)
        r = run_dist(topo, txns, params=dp, seed=seed)
    else:
        raise ValueError(scheme)
    keep = ("completed", "makespan", "makespan_des", "n_txn_done",
            "n_txn_target", "n_delivered_flits", "n_deflections",
            "n_board_fail", "n_etag_raised", "n_itag_raised",
            "n_inring_blocked", "n_eject_full_deflect", "n_aimd_increase",
            "n_aimd_decrease", "lat_p50", "lat_p99", "lat_max",
            "stall_detected", "replay_ok", "n_conflicts", "rate_mean",
            "n_pull_issued", "n_pull_wait", "max_pull_outstanding",
            "n_outst_wait", "max_core_outstanding", "core_outstanding")
    return {k: r.get(k) for k in keep}


def _summary_from_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    keys = set()
    summary: list[dict[str, Any]] = []
    for r in rows:
        if r.get("plane_sel") != "least_occupied":
            continue
        if r.get("eject_depth", 4) not in (4, None):
            continue
        keys.add((r["scheme"], r["pattern"], r.get("R"),
                  r.get("m"), r.get("K")))
    for key in sorted(keys, key=lambda x: (x[0], x[1], str(x[2]))):
        scheme, pat, R, m, K = key
        grp = [r for r in rows
               if r["scheme"] == scheme and r["pattern"] == pat
               and r.get("R") == R and r.get("m") == m and r.get("K") == K
               and r.get("plane_sel") == "least_occupied"
               and r.get("eject_depth", 4) in (4, None)
               and r.get("makespan") is not None]
        if not grp:
            continue
        mks = [r["makespan"] for r in grp]
        summary.append({
            "scheme": scheme, "pattern": pat, "R": R, "m": m, "K": K,
            "n": len(mks),
            "makespan_mean": round(sum(mks) / len(mks), 1),
            "makespan_min": min(mks), "makespan_max": max(mks),
            "all_completed": all(r.get("completed") for r in grp),
            "bound": grp[0].get("bound"),
        })
    return summary


def sweep(*, quick: bool = False, only: str = "") -> dict[str, Any]:
    topo = Ring2Topology()
    rows: list[dict[str, Any]] = []
    t0 = time.perf_counter()
    schemes = (only,) if only else SCHEMES

    if quick:
        allpairs_m = (1,)
        uniform_k = (20,)
        resp_r = (4,)
        plane_sels = ("least_occupied", "req_resp_split")
        seeds = (0,)
        aimd_cfgs = [dict(alpha=0.15, beta=0.85, epoch=64, rate_min=0.30,
                          aimd_scope="core_only")]
        ejects = (4,)
    else:
        allpairs_m = (1, 2, 4)
        uniform_k = (20, 50, 100)
        resp_r = (1, 4, 8)
        plane_sels = PLANE_SELS
        seeds = (0, 1, 2)
        aimd_cfgs = [
            dict(alpha=0.15, beta=0.85, epoch=64, rate_min=0.30,
                 aimd_scope="core_only"),
            dict(alpha=0.15, beta=0.90, epoch=64, rate_min=0.40,
                 aimd_scope="core_only"),
            dict(alpha=0.15, beta=0.85, epoch=64, rate_min=0.30,
                 aimd_scope="both"),
        ]
        ejects = (2, 4, 8)

    # --- allpairs ----------------------------------------------------------
    for m in allpairs_m:
        for R in resp_r:
            txns = build_allpairs(m=m, m_resp=R)
            for ps in plane_sels:
                for ed in ejects:
                    p = Ring2BaseParams(plane_sel=ps, eject_depth=ed)
                    b = _bounds(topo, txns, ps, R)
                    for scheme in schemes:
                        extra = {}
                        if scheme == "S1":
                            for ac in aimd_cfgs:
                                pp = Ring2BaseParams(plane_sel=ps,
                                                     eject_depth=ed, **ac)
                                r = run_one(scheme, topo, txns, plane_sel=ps,
                                            params=pp)
                                rows.append(_row(
                                    scheme, "allpairs", m=m, R=R,
                                    plane_sel=ps, eject_depth=ed,
                                    bound=b["bound"], **ac, **r))
                                print(f"  S1 allpairs m={m} R={R} {ps} "
                                      f"ed={ed} {ac['aimd_scope']} "
                                      f"mk={r.get('makespan')} "
                                      f"ok={r.get('completed')}", flush=True)
                            continue
                        if scheme == "S2":
                            extra["algo"] = "islip"
                            extra["iters"] = 2
                            cfg = RGConfig(algo="islip", iters=2,
                                           plane_sel=ps, seed=0)
                            r = run_one(scheme, topo, txns, plane_sel=ps,
                                        params=p, rg_cfg=cfg)
                        else:
                            r = run_one(scheme, topo, txns, plane_sel=ps,
                                        params=p)
                        rows.append(_row(scheme, "allpairs", m=m, R=R,
                                         plane_sel=ps, eject_depth=ed,
                                         bound=b["bound"], **extra, **r))
                        print(f"  {scheme} allpairs m={m} R={R} {ps} "
                              f"ed={ed} mk={r.get('makespan')} "
                              f"ok={r.get('completed')}", flush=True)

    # --- uniform -----------------------------------------------------------
    for k in uniform_k:
        for R in resp_r:
            for seed in seeds:
                txns = build_uniform(k=k, m_resp=R, seed=seed)
                for ps in plane_sels:
                    p = Ring2BaseParams(plane_sel=ps)
                    b = _bounds(topo, txns, ps, R)
                    for scheme in schemes:
                        if scheme == "S1":
                            ac = aimd_cfgs[0]
                            pp = Ring2BaseParams(plane_sel=ps, **ac)
                            r = run_one(scheme, topo, txns, plane_sel=ps,
                                        params=pp, seed=seed)
                            rows.append(_row(
                                scheme, "uniform", K=k, R=R, seed=seed,
                                plane_sel=ps, bound=b["bound"], **ac, **r))
                        elif scheme == "S2":
                            cfg = RGConfig(algo="islip", iters=2,
                                           plane_sel=ps, seed=seed)
                            r = run_one(scheme, topo, txns, plane_sel=ps,
                                        params=p, rg_cfg=cfg, seed=seed)
                            rows.append(_row(
                                scheme, "uniform", K=k, R=R, seed=seed,
                                plane_sel=ps, bound=b["bound"],
                                algo="islip", iters=2, **r))
                        else:
                            r = run_one(scheme, topo, txns, plane_sel=ps,
                                        params=p, seed=seed)
                            rows.append(_row(
                                scheme, "uniform", K=k, R=R, seed=seed,
                                plane_sel=ps, bound=b["bound"], **r))
                        print(f"  {scheme} uniform K={k} R={R} seed={seed} "
                              f"{ps} mk={r.get('makespan')} "
                              f"ok={r.get('completed')}", flush=True)

    summary = _summary_from_rows(rows)

    return {
        "meta": {
            "n": topo.n, "n_planes": topo.n_planes,
            "n_directed": len(topo.directed_links),
            "hop_lat": topo.hop_lat,
            "link_lats": list(topo.link_lats), "quick": quick,
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        },
        "rows": rows,
        "summary": summary,
        "wall_secs": round(time.perf_counter() - t0, 1),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--only", type=str, default="",
                    help="run only this scheme and merge into --out")
    ap.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args()
    res = sweep(quick=args.quick, only=args.only)
    if args.only and args.out.exists():
        prior = json.loads(args.out.read_text())
        keep = [r for r in (prior.get("rows") or [])
                if r.get("scheme") != args.only]
        res["rows"] = keep + res["rows"]
        res["summary"] = _summary_from_rows(res["rows"])
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(res, indent=1, default=str))
    print(f"\nwrote {args.out}  rows={len(res['rows'])}  "
          f"{res['wall_secs']}s")
    print(f"{'scheme':4} {'pat':8} {'R':>3} {'m/K':>6} {'mean':>8} "
          f"{'min':>6} {'max':>6} {'bound':>6} ok")
    for s in res["summary"]:
        mk = s.get("m") if s["pattern"] == "allpairs" else s.get("K")
        print(f"{s['scheme']:4} {s['pattern']:8} {s['R']:>3} {str(mk):>6} "
              f"{s['makespan_mean']:>8} {s['makespan_min']:>6} "
              f"{s['makespan_max']:>6} {s.get('bound'):>6} "
              f"{int(s['all_completed'])}")


if __name__ == "__main__":
    main()
